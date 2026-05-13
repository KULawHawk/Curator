"""Focused coverage tests for mcp/server.py.

Sub-ship v1.7.122 of Round 2 Tier 2.

Closes lines 85-88, 105-171, 266-267 in `mcp/server.py`:

* 85-88: `_has_configured_keys` KeyFileError fallback
* 105-171: most of `main()` body — argument dispatch paths
* 266-267: `_load_keys_safe` KeyFileError fallback

All tests heavily mock `build_runtime`, `server.run`, and uvicorn to
exercise dispatch logic without actually starting servers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import curator.mcp.server as server_mod


# ---------------------------------------------------------------------------
# _has_configured_keys (85-88)
# ---------------------------------------------------------------------------


def test_has_configured_keys_returns_true_when_keys_exist(monkeypatch):
    from curator.mcp.auth import StoredKey
    fake_keys = [
        StoredKey(name="k1", key_hash="h", created_at="2026-01-01T00:00:00Z", last_used_at=None),
    ]
    import curator.mcp.auth as auth_mod
    monkeypatch.setattr(auth_mod, "load_keys", lambda path: fake_keys)

    assert server_mod._has_configured_keys(Path("/x.json")) is True


def test_has_configured_keys_returns_false_when_empty(monkeypatch):
    import curator.mcp.auth as auth_mod
    monkeypatch.setattr(auth_mod, "load_keys", lambda path: [])
    assert server_mod._has_configured_keys(Path("/x.json")) is False


def test_has_configured_keys_returns_false_on_keyfile_error(monkeypatch):
    # Lines 85-88: KeyFileError → return False.
    import curator.mcp.auth as auth_mod

    def boom(path):
        raise auth_mod.KeyFileError("corrupt")
    monkeypatch.setattr(auth_mod, "load_keys", boom)

    assert server_mod._has_configured_keys(Path("/x.json")) is False


# ---------------------------------------------------------------------------
# _load_keys_safe (266-267)
# ---------------------------------------------------------------------------


def test_load_keys_safe_returns_keys_on_success(monkeypatch):
    from curator.mcp.auth import StoredKey
    fake_keys = [
        StoredKey(name="k1", key_hash="h", created_at="2026-01-01T00:00:00Z", last_used_at=None),
    ]
    import curator.mcp.auth as auth_mod
    monkeypatch.setattr(auth_mod, "load_keys", lambda path: fake_keys)

    assert server_mod._load_keys_safe(Path("/x.json")) == fake_keys


def test_load_keys_safe_returns_empty_on_keyfile_error(monkeypatch):
    # Lines 266-267: KeyFileError → return [].
    import curator.mcp.auth as auth_mod

    def boom(path):
        raise auth_mod.KeyFileError("corrupt")
    monkeypatch.setattr(auth_mod, "load_keys", boom)

    assert server_mod._load_keys_safe(Path("/x.json")) == []


# ---------------------------------------------------------------------------
# main() dispatch paths (105-171)
# ---------------------------------------------------------------------------


def test_main_stdio_default(monkeypatch):
    # Lines 105-171: with no args, build runtime + run stdio transport.
    fake_runtime = MagicMock()
    fake_server = MagicMock()
    monkeypatch.setattr(server_mod, "create_server", lambda rt: fake_server)
    monkeypatch.setattr(
        "curator.cli.runtime.build_runtime",
        lambda **kw: fake_runtime,
    )
    monkeypatch.setattr(
        "curator.config.Config.load",
        staticmethod(lambda: MagicMock()),
    )

    rc = server_mod.main([])
    assert rc == 0
    fake_server.run.assert_called_once_with(transport="stdio")


def test_main_http_with_no_auth_on_loopback(monkeypatch):
    # _run_http with --no-auth: loopback OK, runs streamable-http.
    fake_runtime = MagicMock()
    fake_server = MagicMock()
    monkeypatch.setattr(server_mod, "create_server", lambda rt: fake_server)
    monkeypatch.setattr(
        "curator.cli.runtime.build_runtime",
        lambda **kw: fake_runtime,
    )
    monkeypatch.setattr(
        "curator.config.Config.load",
        staticmethod(lambda: MagicMock()),
    )

    rc = server_mod.main(["--http", "--no-auth", "--host", "127.0.0.1"])
    assert rc == 0
    fake_server.run.assert_called_once()
    kwargs = fake_server.run.call_args.kwargs
    assert kwargs["transport"] == "streamable-http"


def test_main_http_with_no_auth_on_non_loopback_refuses(monkeypatch):
    # Lines 195-202: --no-auth + non-loopback host → error, exit 2.
    fake_runtime = MagicMock()
    fake_server = MagicMock()
    monkeypatch.setattr(server_mod, "create_server", lambda rt: fake_server)
    monkeypatch.setattr(
        "curator.cli.runtime.build_runtime",
        lambda **kw: fake_runtime,
    )
    monkeypatch.setattr(
        "curator.config.Config.load",
        staticmethod(lambda: MagicMock()),
    )

    rc = server_mod.main(["--http", "--no-auth", "--host", "0.0.0.0"])
    assert rc == 2


def test_main_http_auth_required_without_keys_refuses(monkeypatch):
    # Lines 215-223: auth required but no keys configured → error, exit 2.
    fake_runtime = MagicMock()
    fake_server = MagicMock()
    monkeypatch.setattr(server_mod, "create_server", lambda rt: fake_server)
    monkeypatch.setattr(
        "curator.cli.runtime.build_runtime",
        lambda **kw: fake_runtime,
    )
    monkeypatch.setattr(
        "curator.config.Config.load",
        staticmethod(lambda: MagicMock()),
    )
    # No configured keys.
    monkeypatch.setattr(server_mod, "_has_configured_keys", lambda kf: False)

    rc = server_mod.main(["--http"])
    assert rc == 2


def test_main_http_auth_required_with_keys_runs_uvicorn(monkeypatch):
    # Lines 225-258: auth path with configured keys → wraps middleware
    # and runs uvicorn.
    fake_runtime = MagicMock()
    fake_runtime.audit = MagicMock()
    fake_server = MagicMock()
    monkeypatch.setattr(server_mod, "create_server", lambda rt: fake_server)
    monkeypatch.setattr(
        "curator.cli.runtime.build_runtime",
        lambda **kw: fake_runtime,
    )
    monkeypatch.setattr(
        "curator.config.Config.load",
        staticmethod(lambda: MagicMock()),
    )
    monkeypatch.setattr(server_mod, "_has_configured_keys", lambda kf: True)
    monkeypatch.setattr(server_mod, "_load_keys_safe", lambda kf: [MagicMock()])

    # Intercept uvicorn.run so we don't actually start a server.
    uvicorn_calls = []
    import uvicorn

    def fake_uvicorn_run(*args, **kwargs):
        uvicorn_calls.append((args, kwargs))
    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    rc = server_mod.main(["--http", "--host", "127.0.0.1"])
    assert rc == 0
    assert len(uvicorn_calls) == 1


def test_main_http_auth_required_with_keys_non_loopback_runs(monkeypatch):
    # Lines 247-253: non-loopback with auth → different log message,
    # uvicorn still runs.
    fake_runtime = MagicMock()
    fake_runtime.audit = None  # also covers the `or None` arm at line 227
    fake_server = MagicMock()
    monkeypatch.setattr(server_mod, "create_server", lambda rt: fake_server)
    monkeypatch.setattr(
        "curator.cli.runtime.build_runtime",
        lambda **kw: fake_runtime,
    )
    monkeypatch.setattr(
        "curator.config.Config.load",
        staticmethod(lambda: MagicMock()),
    )
    monkeypatch.setattr(server_mod, "_has_configured_keys", lambda kf: True)
    monkeypatch.setattr(server_mod, "_load_keys_safe", lambda kf: [])

    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

    rc = server_mod.main(["--http", "--host", "192.168.1.50"])
    assert rc == 0
