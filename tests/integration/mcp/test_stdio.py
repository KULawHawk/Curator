"""Integration tests for the curator-mcp script + stdio server.

v1.2.0 P1: focused smoke tests confirming the script entry point
works end-to-end (subprocess can launch, --help works, the import
path doesn't crash). Full subprocess-based MCP protocol roundtrip
testing (initialize → tools/list → tools/call) is deferred to P2
since it requires an async MCP client harness.

The unit tests in ``tests/unit/mcp/test_tools.py`` already exercise
the full FastMCP stack (call_tool round trips through the same code
path the stdio server uses), so the marginal value of subprocess
testing is just confirming the script entry point + argparse layer.
"""

from __future__ import annotations

import subprocess
import sys

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
