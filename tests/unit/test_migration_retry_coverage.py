"""Focused coverage tests for services/migration_retry.py.

Sub-ship v1.7.104 of the Coverage Sweep arc.

Closes 14 uncovered lines + branches across the `_is_retryable`
helper and the `retry_transient_errors` decorator:

* Lines 70-71: `exc.resp.get` raises `AttributeError` / `KeyError`.
* Lines 75-76: `float(retry_after_raw)` raises `ValueError`.
* Lines 79-80: googleapiclient not installed (ImportError).
* Line 87: requests ConnectionError / Timeout → retryable.
* Lines 88-89: requests not installed (ImportError).
* Lines 100-102: urllib3 not installed (ImportError).
* Line 132: `max_retries < 0` clamped to 0.
* Line 134: `max_retries > 10` clamped to 10.

Companion source refactor: removed the provably-unreachable
end-of-loop defensive block (formerly lines 179-183) per
doctrine item 1 / Lesson #91 — the for-loop always exits via
return or raise, never falls through.
"""

from __future__ import annotations

import socket
import sys
from unittest.mock import MagicMock

import pytest

from curator.services.migration_retry import (
    _is_retryable,
    retry_transient_errors,
)


# ---------------------------------------------------------------------------
# _is_retryable: HttpError retry-after parsing (70-71, 75-76, 79-80)
# ---------------------------------------------------------------------------


def test_is_retryable_http_error_resp_get_raises():
    # Lines 67-71: exc.resp.get("retry-after") raises AttributeError
    # or KeyError → caught, retry_after_raw stays None, return
    # (True, None).
    try:
        from googleapiclient.errors import HttpError  # type: ignore
    except ImportError:
        pytest.skip("googleapiclient not installed")

    # Build an HttpError whose resp.get raises. Need `reason` and
    # `status` for HttpError.__init__ to succeed.
    class _BadResp:
        status = 503
        reason = "Service Unavailable"

        def get(self, *args, **kwargs):
            raise AttributeError("no headers")

    exc = HttpError(_BadResp(), b"")
    is_retryable, retry_after = _is_retryable(exc)
    assert is_retryable is True
    assert retry_after is None


def test_is_retryable_http_error_retry_after_unparseable():
    # Lines 72-76: retry_after_raw present but float() fails → caught,
    # return (True, None).
    try:
        from googleapiclient.errors import HttpError  # type: ignore
    except ImportError:
        pytest.skip("googleapiclient not installed")

    class _BadResp:
        status = 429
        reason = "Too Many Requests"

        def get(self, key, *args, **kwargs):
            if key == "retry-after":
                return "not-a-number"
            return None

    exc = HttpError(_BadResp(), b"")
    is_retryable, retry_after = _is_retryable(exc)
    assert is_retryable is True
    assert retry_after is None


def test_is_retryable_googleapiclient_missing(monkeypatch):
    # Lines 79-80: googleapiclient not installed → ImportError caught,
    # fall through to next check.
    monkeypatch.setitem(sys.modules, "googleapiclient", None)
    monkeypatch.setitem(sys.modules, "googleapiclient.errors", None)
    # Non-HttpError socket timeout still hits the socket branch and
    # returns retryable.
    is_retryable, retry_after = _is_retryable(socket.timeout())
    assert is_retryable is True


# ---------------------------------------------------------------------------
# _is_retryable: requests / urllib3 paths (87, 88-89, 100-102)
# ---------------------------------------------------------------------------


def test_is_retryable_requests_connection_error():
    # Line 87: isinstance(exc, requests.exceptions.ConnectionError)
    # True → return (True, None).
    try:
        import requests
    except ImportError:
        pytest.skip("requests not installed")

    is_retryable, retry_after = _is_retryable(
        requests.exceptions.ConnectionError("network down"),
    )
    assert is_retryable is True


def test_is_retryable_requests_missing(monkeypatch):
    # Lines 88-89: requests not installed → ImportError caught, fall
    # through. socket.timeout still hits its branch.
    monkeypatch.setitem(sys.modules, "googleapiclient", None)
    monkeypatch.setitem(sys.modules, "googleapiclient.errors", None)
    monkeypatch.setitem(sys.modules, "requests", None)
    is_retryable, retry_after = _is_retryable(socket.timeout())
    assert is_retryable is True


def test_is_retryable_urllib3_protocol_error():
    # Line 100: urllib3 installed and exc is a ProtocolError → retryable.
    try:
        import urllib3.exceptions
    except ImportError:
        pytest.skip("urllib3 not installed")

    is_retryable, retry_after = _is_retryable(
        urllib3.exceptions.ProtocolError("connection broken"),
    )
    assert is_retryable is True


def test_is_retryable_urllib3_missing(monkeypatch):
    # Lines 101-102: urllib3 not installed → ImportError caught.
    # Non-retryable non-socket exception falls through to final
    # return False.
    monkeypatch.setitem(sys.modules, "googleapiclient", None)
    monkeypatch.setitem(sys.modules, "googleapiclient.errors", None)
    monkeypatch.setitem(sys.modules, "requests", None)
    monkeypatch.setitem(sys.modules, "urllib3", None)
    monkeypatch.setitem(sys.modules, "urllib3.exceptions", None)
    is_retryable, retry_after = _is_retryable(ValueError("nope"))
    assert is_retryable is False


# ---------------------------------------------------------------------------
# retry_transient_errors decorator: max_retries clamping (132, 134)
# ---------------------------------------------------------------------------


def _make_service_stub(max_retries: int):
    """Build a minimal stand-in for MigrationService that
    `retry_transient_errors` can read attributes off."""
    svc = MagicMock()
    svc._max_retries = max_retries
    svc._retry_backoff_cap = 0.0  # zero backoff for speed
    svc.audit = None
    return svc


def test_retry_clamps_negative_max_retries_to_zero():
    # Line 131-132: max_retries < 0 → clamped to 0. With max_retries=0,
    # a retryable error raises on first attempt instead of retrying.
    svc = _make_service_stub(max_retries=-5)
    call_count = [0]

    @retry_transient_errors
    def boom(self):
        call_count[0] += 1
        raise socket.timeout()

    with pytest.raises(socket.timeout):
        boom(svc)
    # Called exactly once (no retries because clamped to 0).
    assert call_count[0] == 1


def test_retry_clamps_excessive_max_retries_to_ten():
    # Line 133-134: max_retries > 10 → clamped to 10. Hard to count
    # exactly without burning real time; instead assert the call
    # eventually fails (after 11 attempts = 1 initial + 10 retries).
    svc = _make_service_stub(max_retries=999)
    call_count = [0]

    @retry_transient_errors
    def boom(self):
        call_count[0] += 1
        raise socket.timeout()

    with pytest.raises(socket.timeout):
        boom(svc)
    # Clamped to 10 retries → 11 total attempts.
    assert call_count[0] == 11
