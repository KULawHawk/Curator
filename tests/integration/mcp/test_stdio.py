"""Integration tests for the curator-mcp script + stdio server.

P1 (v1.2.0 release): focused smoke tests confirming the script entry
point works end-to-end (subprocess can launch, --help works, the
import path doesn't crash).

P2 (this commit): adds a full subprocess-based MCP protocol roundtrip
test (initialize → tools/list → tools/call) using the official
MCP client SDK against a live curator-mcp subprocess pointed at a
tmp config + tmp DB.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def _run_curator_mcp(*args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run ``python -m curator.mcp.server <args>`` and return the result.

    Uses ``-m`` rather than the ``curator-mcp`` console script because
    the script lives in pip's bin dir which may not be on PATH. The
    underlying ``main()`` path is identical.
    """
    return subprocess.run(
        [sys.executable, "-m", "curator.mcp.server", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestScriptEntryPoint:
    """The curator-mcp script entry point should be invokable and
    expose the expected --help text."""

    def test_help_exits_zero(self):
        result = _run_curator_mcp("--help")
        assert result.returncode == 0, (
            f"--help exited {result.returncode}; "
            f"stderr: {result.stderr[:500]}"
        )

    def test_help_describes_server(self):
        result = _run_curator_mcp("--help")
        # Help text should mention key concepts so users know what they got
        assert "curator-mcp" in result.stdout.lower() or "curator-mcp" in result.stderr.lower()
        assert "mcp" in result.stdout.lower()

    def test_help_lists_http_flag(self):
        """--http is a documented opt-in transport; it must appear in help."""
        result = _run_curator_mcp("--help")
        assert "--http" in result.stdout

    def test_help_lists_port_flag(self):
        result = _run_curator_mcp("--help")
        assert "--port" in result.stdout

    def test_help_lists_host_flag(self):
        result = _run_curator_mcp("--help")
        assert "--host" in result.stdout

    def test_invalid_arg_exits_nonzero(self):
        """argparse should reject unknown args."""
        result = _run_curator_mcp("--this-flag-does-not-exist")
        assert result.returncode != 0


class TestImportPath:
    """Importing curator.mcp without the [mcp] extra installed should
    fail loudly; with the extra, it should work cleanly."""

    def test_curator_mcp_module_imports(self):
        """The module imports without errors when [mcp] is installed."""
        result = subprocess.run(
            [sys.executable, "-c", "from curator.mcp import main, create_server; print('ok')"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        assert result.returncode == 0, (
            f"import failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "ok" in result.stdout

    def test_create_server_callable(self):
        """create_server is the public API for embedding/testing."""
        from curator.mcp import create_server

        # We don't construct a runtime here (that's runtime-state-heavy);
        # just verify the symbol is importable and callable
        assert callable(create_server)

    def test_main_callable(self):
        """main is the console entry point referenced by pyproject.toml."""
        from curator.mcp import main

        assert callable(main)


# ===========================================================================
# Full subprocess-based MCP protocol roundtrip (P2)
# ===========================================================================
#
# Spawns a real `python -m curator.mcp.server` subprocess pointed at a tmp
# config + tmp DB, then talks to it over stdio using the official MCP
# client SDK. Exercises: initialize handshake → tools/list → tools/call
# for representative read-only tools.


@pytest.fixture
def tmp_curator_config(tmp_path):
    """Create a tmp config.toml + DB path. Returns the env dict for the
    subprocess (sets CURATOR_CONFIG to the tmp config)."""
    db_path = tmp_path / "mcp_integration.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'database_path = "{db_path.as_posix()}"\n'
        '[sources.local]\n'
        'kind = "local"\n'
        f'roots = ["{tmp_path.as_posix()}"]\n'
    )
    env = os.environ.copy()
    env["CURATOR_CONFIG"] = str(config_path)
    return env


class TestMcpProtocolRoundtrip:
    """Full async MCP-client-to-subprocess-server protocol exercise."""

    @pytest.mark.integration
    def test_initialize_and_list_tools(self, tmp_curator_config):
        """Spawn curator-mcp; do MCP handshake; confirm 9 tools listed."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            pytest.skip("mcp client SDK not available")

        async def run():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "curator.mcp.server"],
                env=tmp_curator_config,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    return [t.name for t in tools_result.tools]

        names = asyncio.run(asyncio.wait_for(run(), timeout=30.0))
        assert set(names) == {
            "health_check", "list_sources", "query_audit_log",
            "query_files", "inspect_file", "get_lineage",
            "find_duplicates", "list_trashed", "get_migration_status",
        }, f"expected 9 tools; got {len(names)}: {names}"

    @pytest.mark.integration
    def test_call_health_check_over_stdio(self, tmp_curator_config):
        """Spawn curator-mcp; call health_check; confirm structured payload."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            pytest.skip("mcp client SDK not available")

        async def run():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "curator.mcp.server"],
                env=tmp_curator_config,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool("health_check", {})

        result = asyncio.run(asyncio.wait_for(run(), timeout=30.0))
        # Result is CallToolResult; check structured content has expected fields
        assert result is not None
        # FastMCP exposes structuredContent (newer MCP) or content[0].text JSON
        text = None
        if hasattr(result, "content") and result.content:
            text = getattr(result.content[0], "text", None)
        assert text is not None, f"no text content; result={result!r}"
        import json
        data = json.loads(text)
        assert "status" in data
        assert "curator_version" in data
        assert "plugin_count" in data

    @pytest.mark.integration
    def test_call_query_audit_log_over_stdio(self, tmp_curator_config):
        """Confirm query_audit_log is callable over stdio (the headline
        atrium-safety read-back use case)."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            pytest.skip("mcp client SDK not available")

        async def run():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "curator.mcp.server"],
                env=tmp_curator_config,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool(
                        "query_audit_log",
                        {"actor": "curatorplug.atrium_safety", "limit": 5},
                    )

        result = asyncio.run(asyncio.wait_for(run(), timeout=30.0))
        assert result is not None
        # Empty audit log -> empty list (no error)
        text = None
        if hasattr(result, "content") and result.content:
            text = getattr(result.content[0], "text", None)
        # Some MCP responses for empty list-returns may be just "[]" or have
        # structured content; either way no error is the success criterion
        assert not getattr(result, "isError", False), f"tool errored: {result}"
