"""Focused coverage tests for mcp/middleware.py.

Sub-ship v1.7.119 of Round 2 Tier 2.

Closes lines 148-149, 195-196, 234 + 1 partial branch — all
defensive boundaries that the existing http-auth tests don't reach:

* 148-149: `update_last_used` raises → caught with logger.warning,
  success path continues.
* 195-196: `_emit_failure`'s `audit_emitter` raises → caught with
  logger.warning, response still returned.
* 234: `_remote_addr` returns None when `request.client` is None
  (loopback connections).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from curator.mcp.middleware import BearerAuthMiddleware


# ---------------------------------------------------------------------------
# _remote_addr None branch (234)
# ---------------------------------------------------------------------------


def test_remote_addr_returns_none_when_client_is_none():
    # Line 234: request.client is None → return None.
    fake_request = MagicMock()
    fake_request.client = None
    assert BearerAuthMiddleware._remote_addr(fake_request) is None


def test_remote_addr_returns_client_host_when_available():
    # Branch coverage of the True arm (already covered by other tests
    # but explicit here for documentation).
    fake_request = MagicMock()
    fake_request.client.host = "127.0.0.1"
    assert BearerAuthMiddleware._remote_addr(fake_request) == "127.0.0.1"


# ---------------------------------------------------------------------------
# update_last_used exception swallow (148-149)
# ---------------------------------------------------------------------------


def test_dispatch_swallows_update_last_used_exception(monkeypatch):
    # Lines 148-149: update_last_used raises → logger.warning, continue
    # to _emit_success + call_next.
    # Run the async dispatch via asyncio.run since pytest-asyncio isn't
    # installed in this environment.
    import asyncio
    import curator.mcp.middleware as mw_mod
    from curator.mcp.auth import StoredKey
    from datetime import datetime

    fake_stored = StoredKey(
        name="test_key",
        key_hash="hashed_sha256_hex",
        created_at="2026-05-13T00:00:00Z",
        last_used_at=None,
    )
    monkeypatch.setattr(mw_mod, "validate_key", lambda token, path=None: fake_stored)

    def boom_update(*args, **kwargs):
        raise RuntimeError("simulated update failure")
    monkeypatch.setattr(mw_mod, "update_last_used", boom_update)

    fake_request = MagicMock()
    fake_request.headers.get.return_value = "Bearer valid_token"
    fake_request.client.host = "127.0.0.1"
    fake_request.method = "POST"
    fake_request.url.path = "/mcp"

    async def fake_call_next(request):
        return "ok_response"

    mw = BearerAuthMiddleware(app=MagicMock(), audit_emitter=None)
    result = asyncio.run(mw.dispatch(fake_request, fake_call_next))
    assert result == "ok_response"


# ---------------------------------------------------------------------------
# _emit_failure audit_emitter exception swallow (195-196)
# ---------------------------------------------------------------------------


def test_emit_failure_swallows_audit_emitter_exception():
    # Lines 195-196: audit_emitter raises inside _emit_failure → logger.warning,
    # don't propagate.
    def boom_emitter(action, **details):
        raise RuntimeError("simulated emitter failure")

    mw = BearerAuthMiddleware(app=MagicMock(), audit_emitter=boom_emitter)
    fake_request = MagicMock()
    fake_request.client.host = "10.0.0.1"
    fake_request.method = "GET"
    fake_request.url.path = "/health"

    # Must not raise.
    mw._emit_failure(fake_request, reason="invalid_key", key_prefix="abcdef0123")


def test_emit_success_swallows_audit_emitter_exception():
    # Branch coverage on the success-path emitter exception (lines
    # 222-226 in the body, which mirrors the _emit_failure pattern).
    def boom_emitter(action, **details):
        raise RuntimeError("simulated emitter failure")

    mw = BearerAuthMiddleware(app=MagicMock(), audit_emitter=boom_emitter)
    fake_request = MagicMock()
    fake_request.client.host = "10.0.0.1"
    fake_request.method = "GET"
    fake_request.url.path = "/health"

    # Must not raise.
    mw._emit_success(fake_request, "test_key")
