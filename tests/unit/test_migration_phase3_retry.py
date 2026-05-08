"""Tracer Phase 3 P1 retry decorator tests.

Covers ``src/curator/services/migration_retry.py`` per design v0.2 §3 DM-1,
DM-2, DM-3 and §5 P1 acceptance criteria.

Test layout:

* ``TestIsRetryable``       -- error-classification matrix per DM-1
* ``TestRetryDecorator``    -- decorator behavior per DM-2 + DM-3
* ``TestServiceIntegration`` -- _max_retries clamping + set_max_retries
"""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import MagicMock

import pytest

from curator.services.migration_retry import (
    _is_retryable,
    retry_transient_errors,
)


# ---------------------------------------------------------------------------
# Synthetic exception builders (avoid hard dependency on googleapiclient)
# ---------------------------------------------------------------------------


class _FakeResp(dict):
    """Mimics googleapiclient's httplib2.Response: dict-like with .status + .reason."""

    def __init__(self, status: int, headers: dict[str, str] | None = None):
        super().__init__(headers or {})
        self.status = status
        self.reason = f"HTTP {status}"  # googleapiclient.errors.HttpError reads this


def _make_http_error(status: int, headers: dict[str, str] | None = None) -> Exception:
    """Build a googleapiclient.errors.HttpError if installed; else a stand-in.

    The decorator's _is_retryable checks isinstance(exc, HttpError), so when
    googleapiclient is installed we use the real class. Without it, we
    fabricate a class with the same shape that _is_retryable can handle —
    but in that case the path through `from googleapiclient.errors import
    HttpError` returns False (ImportError silenced), so this fallback exists
    only to keep test discovery from breaking.
    """
    try:
        from googleapiclient.errors import HttpError  # type: ignore

        return HttpError(_FakeResp(status, headers), b"")
    except ImportError:
        # Fabricate a class so tests can still construct/assert on it
        class _StandInHttpError(Exception):
            def __init__(self, resp):
                self.resp = resp
                super().__init__(f"HttpError {resp.status}")

        return _StandInHttpError(_FakeResp(status, headers))


def _googleapiclient_available() -> bool:
    try:
        import googleapiclient.errors  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# TestIsRetryable -- DM-1 error-classification matrix
# ---------------------------------------------------------------------------


class TestIsRetryable:
    """The conservative whitelist from DM-1.

    Retryable: HttpError 403/429/500/502/503/504, ConnectionError, Timeout,
    socket.timeout, urllib3 ProtocolError. Non-retryable: OSError, ValueError,
    plain Exception, RuntimeError, etc.
    """

    @pytest.mark.skipif(not _googleapiclient_available(),
                        reason="googleapiclient required for this assertion")
    def test_is_retryable_recognizes_http_429(self):
        exc = _make_http_error(429)
        is_retry, retry_after = _is_retryable(exc)
        assert is_retry is True
        assert retry_after is None  # no header on this fake

    def test_is_retryable_rejects_oserror(self):
        is_retry, retry_after = _is_retryable(OSError("disk full"))
        assert is_retry is False
        assert retry_after is None

    def test_is_retryable_rejects_value_error(self):
        is_retry, _ = _is_retryable(ValueError("bad input"))
        assert is_retry is False

    @pytest.mark.skipif(not _googleapiclient_available(),
                        reason="googleapiclient required for this assertion")
    def test_is_retryable_parses_retry_after_header(self):
        exc = _make_http_error(429, headers={"retry-after": "12.5"})
        is_retry, retry_after = _is_retryable(exc)
        assert is_retry is True
        assert retry_after == 12.5

    def test_is_retryable_socket_timeout(self):
        is_retry, _ = _is_retryable(socket.timeout("read timeout"))
        assert is_retry is True

    @pytest.mark.skipif(not _googleapiclient_available(),
                        reason="googleapiclient required for this assertion")
    def test_is_retryable_rejects_http_404(self):
        """404 isn't on the retryable whitelist (it's a permanent miss)."""
        exc = _make_http_error(404)
        is_retry, _ = _is_retryable(exc)
        assert is_retry is False


# ---------------------------------------------------------------------------
# TestRetryDecorator -- DM-2 + DM-3 decorator behavior
# ---------------------------------------------------------------------------


class _FakeService:
    """Minimal stand-in for MigrationService that the decorator can read.

    Provides ``_max_retries``, ``_retry_backoff_cap``, and ``audit`` (an
    object exposing ``log()``). Enough surface for the stateless decorator.
    """

    def __init__(self, *, max_retries: int = 3, backoff_cap: float = 60.0,
                 audit: Any = None):
        self._max_retries = max_retries
        self._retry_backoff_cap = backoff_cap
        self.audit = audit


class TestRetryDecorator:
    """Decorator wraps a callable and re-tries on retryable errors."""

    def test_retry_decorator_no_failure_passes_through(self, monkeypatch):
        """A function that succeeds first try returns its result with no retry."""
        # Avoid actual sleep regardless
        monkeypatch.setattr("curator.services.migration_retry.time.sleep", lambda _: None)

        call_count = {"n": 0}

        @retry_transient_errors
        def noop_succeeds(self):
            call_count["n"] += 1
            return "ok"

        svc = _FakeService(max_retries=3)
        result = noop_succeeds(svc)
        assert result == "ok"
        assert call_count["n"] == 1

    def test_retry_decorator_retryable_error_then_success(self, monkeypatch):
        """Fail twice with retryable error, then succeed: total 3 attempts."""
        monkeypatch.setattr("curator.services.migration_retry.time.sleep", lambda _: None)

        call_count = {"n": 0}

        @retry_transient_errors
        def flaky(self):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise socket.timeout("transient")  # retryable
            return "ok"

        svc = _FakeService(max_retries=3)
        result = flaky(svc)
        assert result == "ok"
        assert call_count["n"] == 3  # 1 initial + 2 retries (then success)

    def test_retry_decorator_max_retries_then_fail(self, monkeypatch):
        """All retries exhausted: re-raises the last exception."""
        monkeypatch.setattr("curator.services.migration_retry.time.sleep", lambda _: None)

        call_count = {"n": 0}

        @retry_transient_errors
        def always_fails(self):
            call_count["n"] += 1
            raise socket.timeout("never recovers")

        svc = _FakeService(max_retries=3)
        with pytest.raises(socket.timeout):
            always_fails(svc)
        # 1 initial + 3 retries = 4 total attempts
        assert call_count["n"] == 4

    def test_retry_decorator_non_retryable_error_immediate_fail(self, monkeypatch):
        """OSError is not retryable -- raises immediately, no retry."""
        monkeypatch.setattr("curator.services.migration_retry.time.sleep", lambda _: None)

        call_count = {"n": 0}

        @retry_transient_errors
        def fails_with_oserror(self):
            call_count["n"] += 1
            raise OSError("disk full")  # non-retryable

        svc = _FakeService(max_retries=3)
        with pytest.raises(OSError, match="disk full"):
            fails_with_oserror(svc)
        assert call_count["n"] == 1  # no retries on non-retryable

    @pytest.mark.skipif(not _googleapiclient_available(),
                        reason="googleapiclient required for Retry-After test")
    def test_retry_decorator_respects_retry_after_header(self, monkeypatch):
        """When response carries Retry-After, sleep duration uses it as floor."""
        sleeps_recorded: list[float] = []
        monkeypatch.setattr(
            "curator.services.migration_retry.time.sleep",
            lambda s: sleeps_recorded.append(s),
        )

        call_count = {"n": 0}

        @retry_transient_errors
        def retry_after(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First attempt: 429 with Retry-After=10
                raise _make_http_error(429, headers={"retry-after": "10"})
            return "ok"

        svc = _FakeService(max_retries=3)
        result = retry_after(svc)
        assert result == "ok"
        # Backoff for attempt 0 would be min(60, 1*2^0)=1, but Retry-After
        # is 10s which becomes the floor → 10s sleep.
        assert len(sleeps_recorded) == 1
        assert sleeps_recorded[0] == 10.0

    def test_retry_decorator_audit_logs_each_attempt(self, monkeypatch):
        """Each retry attempt emits a migration.retry audit event."""
        monkeypatch.setattr("curator.services.migration_retry.time.sleep", lambda _: None)

        audit_calls: list[dict] = []

        class _CapturingAudit:
            def log(self, *, actor, action, details):  # noqa: D401
                audit_calls.append({
                    "actor": actor, "action": action, "details": dict(details),
                })

        @retry_transient_errors
        def fails_twice_then_ok(self):
            # Caller's call index lives outside; use closure
            count_holder["n"] += 1
            if count_holder["n"] < 3:
                raise socket.timeout("transient")
            return "ok"

        count_holder = {"n": 0}
        svc = _FakeService(max_retries=3, audit=_CapturingAudit())
        result = fails_twice_then_ok(svc)
        assert result == "ok"
        # 2 retry events emitted (attempts 1 and 2 failed; attempt 3 succeeded)
        assert len(audit_calls) == 2
        assert all(c["action"] == "migration.retry" for c in audit_calls)
        assert all(c["actor"] == "curator.migrate" for c in audit_calls)
        assert audit_calls[0]["details"]["attempt"] == 1
        assert audit_calls[1]["details"]["attempt"] == 2
        # In Python 3.10+ socket.timeout is aliased to TimeoutError
        assert audit_calls[0]["details"]["error_class"] in ("timeout", "TimeoutError")

    def test_retry_decorator_audit_failures_dont_block(self, monkeypatch):
        """Audit-emission failures must not break the retry path."""
        monkeypatch.setattr("curator.services.migration_retry.time.sleep", lambda _: None)

        class _BrokenAudit:
            def log(self, *, actor, action, details):
                raise RuntimeError("audit DB fell over")

        call_count = {"n": 0}

        @retry_transient_errors
        def flaky(self):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise socket.timeout("transient")
            return "ok"

        svc = _FakeService(max_retries=3, audit=_BrokenAudit())
        result = flaky(svc)
        assert result == "ok"  # still recovered despite audit failures


# ---------------------------------------------------------------------------
# TestServiceIntegration -- set_max_retries clamping + integration
# ---------------------------------------------------------------------------


class TestServiceIntegration:
    """MigrationService.set_max_retries clamps to [0, 10] per DM-2."""

    def test_max_retries_zero_disables_retry(self, monkeypatch):
        """max_retries=0: no retries, immediate fail on first error."""
        monkeypatch.setattr("curator.services.migration_retry.time.sleep", lambda _: None)

        call_count = {"n": 0}

        @retry_transient_errors
        def flaky(self):
            call_count["n"] += 1
            raise socket.timeout("transient")

        svc = _FakeService(max_retries=0)
        with pytest.raises(socket.timeout):
            flaky(svc)
        assert call_count["n"] == 1  # 0 retries = 1 total attempt

    def test_max_retries_capped_at_10(self):
        """MigrationService.set_max_retries clamps to 10 even if 999 passed."""
        # Build a real MigrationService and exercise the clamp
        from curator.services.migration import MigrationService

        # Construct minimal service with mocks (constructor doesn't touch the
        # mocked deps for set_max_retries)
        svc = MigrationService(
            file_repo=MagicMock(),
            safety=MagicMock(),
            audit=None,
        )

        svc.set_max_retries(999)
        assert svc._max_retries == 10

        svc.set_max_retries(-5)
        assert svc._max_retries == 0

        svc.set_max_retries(7)
        assert svc._max_retries == 7
