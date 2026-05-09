"""Integration tests for MCP HTTP Bearer auth (v1.5.0 P3).

Covers DESIGN \u00a75.3 acceptance criteria:

* HTTP request with valid Bearer key returns the tool result.
* HTTP request with invalid Bearer key returns 401.
* HTTP request without Authorization header returns 401.
* HTTP request with malformed Authorization header returns 401.
* Auth events land in audit log under ``actor='curator-mcp'``.
* Successful-auth audit emission throttling works.
* Failed-auth audit emission is NEVER throttled.
* ``--no-auth --host 0.0.0.0`` exits 2 with refusal.
* ``--no-auth --host 127.0.0.1`` works without auth (loopback dev).

Strategy: use Starlette's ``TestClient`` against an ASGI app built
from a ``BaseHTTPMiddleware``-wrapped trivial Starlette app. We do
NOT spin up a real FastMCP server here -- that would couple this
test to FastMCP internals + require a real Curator runtime + DB.
The middleware itself is what we're testing; FastMCP is just an
upstream consumer.

CLI-side behavior of ``curator-mcp --no-auth --host 0.0.0.0`` etc.
is tested by directly invoking ``curator.mcp.server._run_http``
with stub args (no actual server bind).
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from curator.mcp.auth import KEYS_FILE_NAME, add_key
from curator.mcp.middleware import BearerAuthMiddleware, make_audit_emitter


@pytest.fixture
def keys_file(tmp_path):
    """Empty keys file path for tests to populate."""
    return tmp_path / KEYS_FILE_NAME


@pytest.fixture
def captured_audit():
    """A list that audit emissions get appended to. Use as the
    audit_emitter parameter of BearerAuthMiddleware to capture events
    for assertions."""
    captured = []

    def emit(action, **details):
        captured.append({"action": action, "details": details})

    return captured, emit


def _build_test_app(keys_file: Path, audit_emitter=None, throttle_seconds: float = 60.0) -> Starlette:
    """Build a minimal Starlette app wrapped with BearerAuthMiddleware.

    The downstream "tool" is a single endpoint that returns ``OK`` --
    just enough to confirm the middleware passed the request through.
    """
    async def ok(request):
        return PlainTextResponse("OK")

    app = Starlette(routes=[Route("/{path:path}", ok)])
    app.add_middleware(
        BearerAuthMiddleware,
        keys_file=keys_file,
        audit_emitter=audit_emitter,
        throttle_seconds=throttle_seconds,
    )
    return app


# ---------------------------------------------------------------------------
# Header-shape rejections
# ---------------------------------------------------------------------------


class TestHeaderRejections:
    def test_missing_authorization_header_returns_401(self, keys_file):
        add_key("test-key", path=keys_file)
        client = TestClient(_build_test_app(keys_file))
        response = client.get("/anything")
        assert response.status_code == 401
        assert response.json()["error"] == "unauthorized"
        assert "WWW-Authenticate" in response.headers
        assert response.headers["WWW-Authenticate"].startswith("Bearer")

    def test_wrong_scheme_returns_401(self, keys_file):
        # "Basic" instead of "Bearer"
        add_key("test-key", path=keys_file)
        client = TestClient(_build_test_app(keys_file))
        response = client.get("/anything", headers={"Authorization": "Basic abc123"})
        assert response.status_code == 401

    def test_empty_bearer_token_returns_401(self, keys_file):
        add_key("test-key", path=keys_file)
        client = TestClient(_build_test_app(keys_file))
        response = client.get("/anything", headers={"Authorization": "Bearer "})
        assert response.status_code == 401
        assert "Empty bearer token" in response.json()["message"]

    def test_invalid_key_returns_401(self, keys_file):
        add_key("real-key", path=keys_file)
        client = TestClient(_build_test_app(keys_file))
        response = client.get("/anything", headers={"Authorization": "Bearer curm_fakekey_definitely_not_in_store_123456"})
        assert response.status_code == 401
        assert response.json()["message"] == "Invalid API key."

    def test_valid_key_with_wrong_prefix_returns_401(self, keys_file):
        add_key("test-key", path=keys_file)
        client = TestClient(_build_test_app(keys_file))
        # No curm_ prefix
        response = client.get("/anything", headers={"Authorization": "Bearer not-a-curator-key"})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Successful auth
# ---------------------------------------------------------------------------


class TestSuccessfulAuth:
    def test_valid_key_returns_200(self, keys_file):
        plaintext = add_key("test-key", path=keys_file)
        client = TestClient(_build_test_app(keys_file))
        response = client.get(
            "/anything",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 200
        assert response.text == "OK"

    def test_valid_key_updates_last_used(self, keys_file):
        from curator.mcp.auth import load_keys
        plaintext = add_key("test-key", path=keys_file)
        assert load_keys(keys_file)[0].last_used_at is None

        client = TestClient(_build_test_app(keys_file))
        client.get(
            "/anything",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        loaded = load_keys(keys_file)
        assert loaded[0].last_used_at is not None

    def test_valid_key_among_multiple(self, keys_file):
        plaintext_a = add_key("key-a", path=keys_file)
        plaintext_b = add_key("key-b", path=keys_file)
        client = TestClient(_build_test_app(keys_file))

        ra = client.get("/x", headers={"Authorization": f"Bearer {plaintext_a}"})
        rb = client.get("/y", headers={"Authorization": f"Bearer {plaintext_b}"})

        assert ra.status_code == 200
        assert rb.status_code == 200


# ---------------------------------------------------------------------------
# Audit emission
# ---------------------------------------------------------------------------


class TestAuditEmission:
    def test_failure_emits_audit(self, keys_file, captured_audit):
        captured, emit = captured_audit
        add_key("real-key", path=keys_file)
        client = TestClient(_build_test_app(keys_file, audit_emitter=emit))

        client.get(
            "/anything",
            headers={"Authorization": "Bearer curm_definitely_invalid_token_123456789"},
        )

        assert len(captured) == 1
        assert captured[0]["action"] == "mcp.auth_failure"
        assert captured[0]["details"]["reason"] == "invalid_key"
        # Key prefix recorded for forensics; first 10 chars only
        assert captured[0]["details"]["key_prefix"] == "curm_defin"

    def test_failure_no_header_emits_with_correct_reason(self, keys_file, captured_audit):
        captured, emit = captured_audit
        add_key("real-key", path=keys_file)
        client = TestClient(_build_test_app(keys_file, audit_emitter=emit))

        client.get("/anything")

        assert len(captured) == 1
        assert captured[0]["details"]["reason"] == "missing_header"
        # No key was presented, so prefix is None (the make_audit_emitter
        # factory strips None values before persisting; at the middleware
        # layer they're passed through verbatim).
        assert captured[0]["details"]["key_prefix"] is None

    def test_success_emits_audit_with_key_name(self, keys_file, captured_audit):
        captured, emit = captured_audit
        plaintext = add_key("my-named-key", path=keys_file)
        client = TestClient(_build_test_app(keys_file, audit_emitter=emit))

        client.get(
            "/anything",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert len(captured) == 1
        assert captured[0]["action"] == "mcp.auth_success"
        assert captured[0]["details"]["key_name"] == "my-named-key"
        # Plaintext key NEVER appears in audit details
        assert plaintext not in str(captured[0]["details"])

    def test_failure_never_throttled(self, keys_file, captured_audit):
        # Even with a 60s throttle window, all 5 failures emit
        captured, emit = captured_audit
        add_key("real-key", path=keys_file)
        client = TestClient(_build_test_app(
            keys_file, audit_emitter=emit, throttle_seconds=60.0,
        ))

        for _ in range(5):
            client.get(
                "/anything",
                headers={"Authorization": "Bearer curm_invalid_key_for_throttle_test_xyz"},
            )

        # All 5 failures emitted; no throttling applied
        failures = [c for c in captured if c["action"] == "mcp.auth_failure"]
        assert len(failures) == 5

    def test_success_throttled(self, keys_file, captured_audit):
        # With a 60s throttle, only the first of 5 success emissions lands
        captured, emit = captured_audit
        plaintext = add_key("test-key", path=keys_file)
        client = TestClient(_build_test_app(
            keys_file, audit_emitter=emit, throttle_seconds=60.0,
        ))

        for _ in range(5):
            client.get(
                "/anything",
                headers={"Authorization": f"Bearer {plaintext}"},
            )

        successes = [c for c in captured if c["action"] == "mcp.auth_success"]
        assert len(successes) == 1, f"expected 1 throttled success, got {len(successes)}"

    def test_success_not_throttled_when_window_zero(self, keys_file, captured_audit):
        # With throttle_seconds=0, every success emits
        captured, emit = captured_audit
        plaintext = add_key("test-key", path=keys_file)
        client = TestClient(_build_test_app(
            keys_file, audit_emitter=emit, throttle_seconds=0.0,
        ))

        for _ in range(5):
            client.get(
                "/anything",
                headers={"Authorization": f"Bearer {plaintext}"},
            )

        successes = [c for c in captured if c["action"] == "mcp.auth_success"]
        assert len(successes) == 5

    def test_audit_emitter_exception_does_not_block_auth(self, keys_file):
        # If the audit emitter throws, the request still succeeds
        # (auth + 200 returned to client; emission failure is logged)
        def broken_emit(action, **details):
            raise RuntimeError("audit DB unavailable")

        plaintext = add_key("test-key", path=keys_file)
        client = TestClient(_build_test_app(
            keys_file, audit_emitter=broken_emit,
        ))

        response = client.get(
            "/anything",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# make_audit_emitter factory
# ---------------------------------------------------------------------------


class TestMakeAuditEmitter:
    def test_emitter_calls_audit_repo_log(self):
        repo = MagicMock()
        emit = make_audit_emitter(repo)

        emit("mcp.auth_success", key_name="my-key", remote_addr="127.0.0.1")

        repo.log.assert_called_once()
        call_kwargs = repo.log.call_args.kwargs
        assert call_kwargs["actor"] == "curator-mcp"
        assert call_kwargs["action"] == "mcp.auth_success"
        assert call_kwargs["entity_type"] == "mcp_auth"
        assert call_kwargs["details"]["key_name"] == "my-key"

    def test_emitter_strips_none_values_from_details(self):
        repo = MagicMock()
        emit = make_audit_emitter(repo)

        emit(
            "mcp.auth_failure",
            reason="invalid_key",
            key_prefix=None,  # should be stripped
            remote_addr="127.0.0.1",
        )

        details = repo.log.call_args.kwargs["details"]
        assert "key_prefix" not in details
        assert details["reason"] == "invalid_key"
        assert details["remote_addr"] == "127.0.0.1"

    def test_emitter_swallows_repo_exceptions(self):
        # If audit_repo.log raises, the emitter must not propagate.
        repo = MagicMock()
        repo.log.side_effect = RuntimeError("DB locked")
        emit = make_audit_emitter(repo)

        # Must not raise
        emit("mcp.auth_failure", reason="invalid_key")


# ---------------------------------------------------------------------------
# CLI argument behavior in server._run_http
# ---------------------------------------------------------------------------


class TestRunHttpArgValidation:
    """Tests for ``_run_http`` arg validation. We don't actually start
    a server here -- we mock uvicorn.run + server.streamable_http_app
    so the function returns synchronously after its argument-validation
    branches."""

    def _make_args(self, host="127.0.0.1", port=8765, no_auth=False, http=True):
        return argparse.Namespace(
            host=host, port=port, no_auth=no_auth, http=http,
        )

    def test_no_auth_with_loopback_runs(self, tmp_path, monkeypatch):
        """``--no-auth --host 127.0.0.1`` is allowed; runs without
        middleware."""
        from curator.mcp import server

        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        args = self._make_args(host="127.0.0.1", no_auth=True)
        mock_server = MagicMock()
        mock_runtime = MagicMock()

        # _run_http will call mock_server.run(transport='streamable-http', ...)
        result = server._run_http(mock_server, mock_runtime, args)

        assert result == 0
        mock_server.run.assert_called_once()
        kwargs = mock_server.run.call_args.kwargs
        assert kwargs["transport"] == "streamable-http"
        assert kwargs["host"] == "127.0.0.1"

    def test_no_auth_with_non_loopback_exits_2(self, tmp_path, monkeypatch):
        from curator.mcp import server

        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        args = self._make_args(host="0.0.0.0", no_auth=True)
        mock_server = MagicMock()
        mock_runtime = MagicMock()

        result = server._run_http(mock_server, mock_runtime, args)

        assert result == 2
        # Should NOT have run the server
        mock_server.run.assert_not_called()

    def test_auth_required_no_keys_exits_2(self, tmp_path, monkeypatch):
        # Default: auth required. If no keys configured, exit 2.
        from curator.mcp import server

        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        args = self._make_args(host="127.0.0.1", no_auth=False)
        mock_server = MagicMock()
        mock_runtime = MagicMock()

        result = server._run_http(mock_server, mock_runtime, args)

        assert result == 2
        mock_server.run.assert_not_called()

    def test_auth_required_with_keys_starts_uvicorn(self, tmp_path, monkeypatch):
        from curator.mcp import server

        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        # Configure a key so the auth-required check passes
        add_key("test-key", path=tmp_path / "mcp" / KEYS_FILE_NAME)

        args = self._make_args(host="127.0.0.1", no_auth=False)
        mock_server = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.audit = None  # no audit repo for this test

        # Mock streamable_http_app to return a Starlette app we can
        # add middleware to (and uvicorn.run so we don't actually bind)
        from starlette.applications import Starlette
        mock_server.streamable_http_app.return_value = Starlette()

        with patch("uvicorn.run") as mock_uvicorn:
            result = server._run_http(mock_server, mock_runtime, args)

        assert result == 0
        mock_uvicorn.assert_called_once()
        # Confirm host + port forwarded
        kwargs = mock_uvicorn.call_args.kwargs
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 8765

    def test_auth_required_with_keys_non_loopback_starts_uvicorn(
        self, tmp_path, monkeypatch,
    ):
        # Auth IS configured, so non-loopback binding is allowed.
        from curator.mcp import server

        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        add_key("test-key", path=tmp_path / "mcp" / KEYS_FILE_NAME)

        args = self._make_args(host="0.0.0.0", no_auth=False)
        mock_server = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.audit = None

        from starlette.applications import Starlette
        mock_server.streamable_http_app.return_value = Starlette()

        with patch("uvicorn.run") as mock_uvicorn:
            result = server._run_http(mock_server, mock_runtime, args)

        assert result == 0
        mock_uvicorn.assert_called_once()
