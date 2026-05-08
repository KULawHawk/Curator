"""Curator MCP (Model Context Protocol) server package.

v1.2.0+. Exposes a curated set of read-only Curator tools to LLM
clients (Claude Desktop, Claude Code, third-party MCP agents) over
stdio (default) or HTTP (opt-in via ``--http``).

Public surface:

- :func:`main` — console entry point for ``curator-mcp``.
- :func:`create_server` — programmatic factory; returns a ready-to-run
  ``FastMCP`` instance bound to a given ``CuratorRuntime``. Useful for
  tests and embedding.

Module structure:

- :mod:`curator.mcp.server` — FastMCP construction + transport selection.
- :mod:`curator.mcp.tools` — read-only tool implementations.

See ``Curator/docs/CURATOR_MCP_SERVER_DESIGN.md`` v0.2 for the design.
"""

from __future__ import annotations

from curator.mcp.server import create_server, main

__all__ = ["create_server", "main"]
