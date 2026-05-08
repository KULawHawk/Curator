"""FastMCP server construction + transport selection.

v1.2.0+. Loads ``CuratorRuntime`` and exposes the read-only tools
defined in :mod:`curator.mcp.tools` over stdio (default) or HTTP.

See ``Curator/docs/CURATOR_MCP_SERVER_DESIGN.md`` v0.2 §4 for the
specification this implements.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from curator.cli.runtime import CuratorRuntime


def create_server(runtime: "CuratorRuntime") -> "FastMCP":
    """Construct a FastMCP server with all v1.2.0 tools registered.

    Tools are bound to the provided runtime via closures; multiple
    servers with different runtimes can coexist (e.g., one per test
    case using its own tmp DB).

    Args:
        runtime: The ``CuratorRuntime`` whose repos and services the
            tools will read from. Read-only — no tool in v1.2.0 mutates
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


def main(argv: list[str] | None = None) -> int:
    """Console entry point for ``curator-mcp``.

    Parses CLI args, loads ``CuratorRuntime``, builds the FastMCP
    server, runs over the selected transport.

    Defaults to stdio transport (the canonical mode for Claude Desktop
    and Claude Code). HTTP transport is opt-in via ``--http``; see
    DESIGN.md §4.4 for the rationale and §3 DM-5 for the
    no-authentication-yet caveat.

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
            "Use HTTP transport instead of stdio (default). HTTP binds "
            "to 127.0.0.1 by default; v1.2.0 has NO authentication for "
            "HTTP -- use only for local-network development."
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
            "Binding to non-loopback addresses is strongly discouraged "
            "until v1.3.0 adds API key auth."
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
        # DM-5 ratified: HTTP without auth is a v1.2.0-acceptable
        # development mode but warns loudly. v1.3.0 will add API keys.
        logger.warning(
            "curator-mcp HTTP transport in v1.2.0 has NO authentication. "
            "Bound to {host}:{port}. Do NOT expose to untrusted networks.",
            host=args.host, port=args.port,
        )
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            logger.error(
                "Refusing to bind to non-loopback address {host} without "
                "authentication. Use --host 127.0.0.1 for local development "
                "or wait for v1.3.0 API key support.",
                host=args.host,
            )
            return 2
        # FastMCP exposes streamable-http as the canonical HTTP transport.
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        logger.debug("curator-mcp starting in stdio mode")
        server.run(transport="stdio")

    return 0


if __name__ == "__main__":
    sys.exit(main())
