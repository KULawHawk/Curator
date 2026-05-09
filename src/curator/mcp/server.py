"""FastMCP server construction + transport selection.

v1.2.0+. Loads ``CuratorRuntime`` and exposes the read-only tools
defined in :mod:`curator.mcp.tools` over stdio (default) or HTTP.

v1.5.0+ adds Bearer-token authentication for HTTP transport per
``docs/CURATOR_MCP_HTTP_AUTH_DESIGN.md`` v0.2 RATIFIED:

* Default behavior: ``curator-mcp --http`` requires authentication.
  Connections without a valid API key receive 401 Unauthorized.
* ``--no-auth`` opts out of authentication; in that mode the server
  still refuses to bind to non-loopback addresses (matches the
  v1.2.0 hard restriction).
* Non-loopback binding is allowed when auth is configured.

stdio transport (the default; used by Claude Desktop / Claude Code)
is unchanged from v1.2.0.

See ``Curator/docs/CURATOR_MCP_SERVER_DESIGN.md`` v0.2 \u00a74 for the
v1.2.0 design and ``Curator/docs/CURATOR_MCP_HTTP_AUTH_DESIGN.md``
v0.2 for the v1.5.0 auth additions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from curator.cli.runtime import CuratorRuntime


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def create_server(runtime: "CuratorRuntime") -> "FastMCP":
    """Construct a FastMCP server with all v1.2.0 tools registered.

    Tools are bound to the provided runtime via closures; multiple
    servers with different runtimes can coexist (e.g., one per test
    case using its own tmp DB).

    Args:
        runtime: The ``CuratorRuntime`` whose repos and services the
            tools will read from. Read-only -- no tool in v1.2.0 mutates
            runtime state.

    Returns:
        A ``FastMCP`` instance with tools registered, ready to run via
        ``server.run(transport=...)``.
    """
    # Lazy import: ``mcp`` is an optional dependency (``[mcp]`` extra).
    # Importing only inside this function means ``import curator`` works
    # even when the user hasn't installed the extra.
    from mcp.server.fastmcp import FastMCP

    from curator.mcp.tools import register_tools

    mcp = FastMCP(
        name="curator",
        instructions=(
            "Read-only access to a Curator file index. Use these tools "
            "to query the user's file metadata, audit log, lineage "
            "graph, sources, and migration history. None of these "
            "tools mutate state -- they are safe to call freely. "
            "Write operations (scan, migrate, trash, organize) are not "
            "exposed in v1.2.0; use the `curator` CLI for those."
        ),
    )
    register_tools(mcp, runtime)
    return mcp


def _has_configured_keys(keys_file: Path) -> bool:
    """Check whether the keys file has at least one entry."""
    from curator.mcp.auth import load_keys, KeyFileError
    try:
        return len(load_keys(keys_file)) > 0
    except KeyFileError:
        # Corrupt file -- treat as no keys; CLI's ``mcp keys list`` will
        # surface the actual error to the user with better diagnostics.
        return False


def main(argv: list[str] | None = None) -> int:
    """Console entry point for ``curator-mcp``.

    Parses CLI args, loads ``CuratorRuntime``, builds the FastMCP
    server, runs over the selected transport.

    Defaults to stdio transport (the canonical mode for Claude Desktop
    and Claude Code). HTTP transport is opt-in via ``--http``; v1.5.0+
    requires authentication for HTTP unless ``--no-auth`` is passed.

    Returns:
        Process exit code (0 = success, non-zero = error). The function
        only returns when the server's transport loop exits.
    """
    parser = argparse.ArgumentParser(
        prog="curator-mcp",
        description=(
            "Curator MCP server -- exposes Curator's read-only API "
            "to LLM clients (Claude Desktop, Claude Code, etc.) "
            "via the Model Context Protocol."
        ),
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help=(
            "Use HTTP transport instead of stdio (default). HTTP requires "
            "authentication unless --no-auth is also passed; see "
            "'curator mcp keys generate' to create a key."
        ),
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help=(
            "Disable authentication for HTTP transport. ONLY valid with "
            "--host pointing at a loopback address (127.0.0.1 / localhost / "
            "::1). Use only for local development."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help=(
            "Port for HTTP transport (default: 8765). Ignored for stdio."
        ),
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help=(
            "Host/interface for HTTP transport (default: 127.0.0.1). "
            "Non-loopback binding requires authentication (no --no-auth)."
        ),
    )
    args = parser.parse_args(argv)

    # Build the runtime via the same path the CLI uses. This shares
    # the user's DB at the user's config path; no MCP-specific runtime
    # fork. (DESIGN.md §2 invariant 1.)
    from curator.cli.runtime import build_runtime
    from curator.config import Config

    config = Config.load()
    runtime = build_runtime(
        config=config,
        json_output=False,
        no_color=True,
        verbosity=0,
    )

    server = create_server(runtime)

    if args.http:
        return _run_http(server, runtime, args)

    logger.debug("curator-mcp starting in stdio mode")
    server.run(transport="stdio")
    return 0


def _run_http(server: "FastMCP", runtime: "CuratorRuntime", args) -> int:
    """Run the FastMCP server over HTTP with auth enforcement.

    Returns the process exit code. Separate from ``main`` to keep the
    auth wiring + transport selection contained.
    """
    from curator.mcp.auth import default_keys_file
    from curator.mcp.middleware import (
        BearerAuthMiddleware,
        make_audit_emitter,
    )

    keys_file = default_keys_file()
    is_loopback = args.host in _LOOPBACK_HOSTS

    # ---- Auth gating ----

    if args.no_auth:
        # --no-auth is the explicit "I want unauthenticated HTTP" path.
        # Only legal for loopback. Combination with non-loopback is a
        # configuration error worth refusing loudly.
        if not is_loopback:
            logger.error(
                "Refusing to bind to non-loopback address {host} with "
                "--no-auth. Either remove --no-auth (and configure a key "
                "via 'curator mcp keys generate') or use --host 127.0.0.1.",
                host=args.host,
            )
            return 2
        logger.warning(
            "curator-mcp HTTP transport with --no-auth has NO authentication. "
            "Bound to {host}:{port}. Do NOT expose to untrusted networks.",
            host=args.host, port=args.port,
        )
        # No middleware wrapping; behave like v1.2.0.
        server.run(
            transport="streamable-http", host=args.host, port=args.port,
        )
        return 0

    # Auth-required path: must have at least one configured key.
    if not _has_configured_keys(keys_file):
        logger.error(
            "HTTP transport requires authentication. No API keys found at "
            "{kf}. Generate one with 'curator mcp keys generate <name>' "
            "first, or pass --no-auth (loopback-only) for unauthenticated "
            "local-development use.",
            kf=keys_file,
        )
        return 2

    # Build the audit emitter (ties into Curator's audit log)
    audit_emitter = (
        make_audit_emitter(runtime.audit) if runtime.audit else None
    )

    # Wrap the FastMCP Starlette app with our auth middleware
    # before handing it to uvicorn.
    asgi_app = server.streamable_http_app()
    asgi_app.add_middleware(
        BearerAuthMiddleware,
        keys_file=keys_file,
        audit_emitter=audit_emitter,
    )

    if is_loopback:
        logger.info(
            "curator-mcp HTTP starting on loopback {host}:{port} "
            "with Bearer auth ({n} key(s) loaded).",
            host=args.host, port=args.port,
            n=len(_load_keys_safe(keys_file)),
        )
    else:
        logger.info(
            "curator-mcp HTTP starting on {host}:{port} with Bearer auth "
            "({n} key(s) loaded). Non-loopback binding -- ensure your "
            "network position is appropriate.",
            host=args.host, port=args.port,
            n=len(_load_keys_safe(keys_file)),
        )

    import uvicorn

    uvicorn.run(asgi_app, host=args.host, port=args.port, log_level="warning")
    return 0


def _load_keys_safe(keys_file: Path) -> list:
    """Load keys for the startup log message; return empty list on error."""
    from curator.mcp.auth import load_keys, KeyFileError
    try:
        return load_keys(keys_file)
    except KeyFileError:
        return []


if __name__ == "__main__":
    sys.exit(main())
