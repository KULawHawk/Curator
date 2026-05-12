"""Tests for v1.7.52 ``curator mcp cleanup-orphans`` command (closes A4).

The implementation in ``curator.cli.mcp_orphans`` has two surfaces:

  1. **Pure helpers** -- ``_orphans_only``, ``_format_age``,
     ``_emit_json``, ``_render_table`` -- testable without psutil
     by constructing ``_ProcInfo`` instances directly.
  2. **The Typer command** -- tested via ``CliRunner`` with the
     ``_enumerate_curator_mcp_processes`` and ``_kill_orphans``
     functions monkeypatched. We never call into real psutil during
     tests, so we never risk killing real processes.

Tests cover:

  * Pure helpers (_orphans_only filter, _format_age formatting, JSON shape)
  * Dry-run mode lists processes + identifies orphans
  * Dry-run mode with no curator-mcp.exe processes outputs the empty-state
  * --kill without --yes prompts; declining aborts; accepting kills
  * --kill --yes skips prompt and kills
  * Failed kills are reported and produce exit code 1
  * --json output structure
  * --json + --kill without --yes exits 2 (no interactive prompt available)
  * psutil ImportError surfaces a helpful message and exits 2
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.cli.mcp_orphans import (
    _ProcInfo,
    _emit_json,
    _format_age,
    _orphans_only,
)


# ---------------------------------------------------------------------------
# Pure helpers (no psutil, no CLI)
# ---------------------------------------------------------------------------


def _make_proc(
    pid: int = 1234,
    parent_pid: int = 5678,
    parent_alive: bool = True,
    parent_name: str = "claude.exe",
    create_time: datetime | None = None,
    cmdline: tuple[str, ...] = ("curator-mcp.exe",),
) -> _ProcInfo:
    """Helper: build a _ProcInfo for testing."""
    return _ProcInfo(
        pid=pid,
        parent_pid=parent_pid,
        parent_alive=parent_alive,
        parent_name=parent_name,
        create_time=create_time or datetime(2026, 5, 11, 12, 0, 0),
        cmdline=cmdline,
    )


class TestOrphansOnly:
    """v1.7.52: _orphans_only filters to parent_alive=False."""

    def test_empty_input(self):
        assert _orphans_only([]) == []

    def test_all_alive_returns_empty(self):
        procs = [_make_proc(parent_alive=True), _make_proc(parent_alive=True)]
        assert _orphans_only(procs) == []

    def test_all_dead_returns_all(self):
        procs = [
            _make_proc(pid=1, parent_alive=False),
            _make_proc(pid=2, parent_alive=False),
        ]
        result = _orphans_only(procs)
        assert len(result) == 2

    def test_mixed_returns_only_dead(self):
        alive = _make_proc(pid=1, parent_alive=True)
        dead = _make_proc(pid=2, parent_alive=False)
        result = _orphans_only([alive, dead])
        assert len(result) == 1
        assert result[0].pid == 2


class TestFormatAge:
    """v1.7.52: _format_age renders human-friendly age strings."""

    def test_seconds_only(self):
        now = datetime(2026, 5, 11, 12, 0, 0)
        created = now - timedelta(seconds=42)
        assert _format_age(created, now) == "42s ago"

    def test_minutes_and_seconds(self):
        now = datetime(2026, 5, 11, 12, 0, 0)
        created = now - timedelta(minutes=4, seconds=12)
        assert _format_age(created, now) == "4m12s ago"

    def test_hours_and_minutes(self):
        now = datetime(2026, 5, 11, 12, 0, 0)
        created = now - timedelta(hours=2, minutes=30)
        assert _format_age(created, now) == "2h30m ago"

    def test_zero_seconds(self):
        now = datetime(2026, 5, 11, 12, 0, 0)
        assert _format_age(now, now) == "0s ago"


class TestEmitJson:
    """v1.7.52: _emit_json produces valid, complete JSON."""

    def test_empty_lists(self):
        result = _emit_json([], [])
        data = json.loads(result)
        assert data["total"] == 0
        assert data["orphans"] == 0
        assert data["processes"] == []

    def test_one_orphan_one_alive(self):
        alive = _make_proc(pid=1, parent_alive=True, parent_name="claude.exe")
        dead = _make_proc(pid=2, parent_alive=False, parent_name="<dead>")
        result = _emit_json([alive, dead], [dead])
        data = json.loads(result)
        assert data["total"] == 2
        assert data["orphans"] == 1
        assert len(data["processes"]) == 2

        # Find the orphan entry and verify its shape
        orphan_entry = next(p for p in data["processes"] if p["pid"] == 2)
        assert orphan_entry["parent_alive"] is False
        assert orphan_entry["is_orphan"] is True
        assert orphan_entry["parent_name"] == "<dead>"

    def test_includes_cmdline(self):
        proc = _make_proc(cmdline=("curator-mcp.exe", "--port", "9000"))
        data = json.loads(_emit_json([proc], []))
        assert data["processes"][0]["cmdline"] == ["curator-mcp.exe", "--port", "9000"]


# ---------------------------------------------------------------------------
# CLI command (via CliRunner + patched psutil layer)
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    # Newer Click versions removed the mix_stderr kwarg; stderr now
    # appears in result.output alongside stdout, so we adapt downstream
    # assertions accordingly.
    return CliRunner()


class TestCleanupOrphansCli:
    """v1.7.52: end-to-end CLI behavior with mocked process layer."""

    def test_no_processes_dry_run(self, runner):
        """No curator-mcp.exe processes -> friendly empty-state message."""
        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            return_value=[],
        ):
            result = runner.invoke(app, ["mcp", "cleanup-orphans"])
        assert result.exit_code == 0, result.stdout
        assert "No curator-mcp.exe processes found" in result.stdout

    def test_all_alive_dry_run(self, runner):
        """All processes have live parents -> 'No orphans to clean up'."""
        alive = _make_proc(pid=100, parent_alive=True)
        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            return_value=[alive],
        ):
            result = runner.invoke(app, ["mcp", "cleanup-orphans"])
        assert result.exit_code == 0, result.stdout
        # Table is rendered with the process
        assert "100" in result.stdout  # PID
        assert "No orphans to clean up" in result.stdout

    def test_orphans_present_dry_run(self, runner):
        """Orphans listed; dry-run output prompts user to re-run with --kill."""
        orphan = _make_proc(pid=200, parent_alive=False, parent_name="<dead>")
        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            return_value=[orphan],
        ):
            result = runner.invoke(app, ["mcp", "cleanup-orphans"])
        assert result.exit_code == 0, result.stdout
        assert "200" in result.stdout
        assert "ORPHAN" in result.stdout
        assert "--kill" in result.stdout  # suggestion to re-run

    def test_kill_with_yes_terminates_orphans(self, runner):
        """--kill --yes skips prompt and calls _kill_orphans."""
        orphan = _make_proc(pid=300, parent_alive=False, parent_name="<dead>")
        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            return_value=[orphan],
        ), patch(
            "curator.cli.mcp_orphans._kill_orphans",
            return_value=(1, []),
        ) as mock_kill:
            result = runner.invoke(app, ["mcp", "cleanup-orphans", "--kill", "--yes"])
        assert result.exit_code == 0, result.stdout
        # _kill_orphans was called with our orphan
        mock_kill.assert_called_once()
        called_with = mock_kill.call_args[0][0]
        assert len(called_with) == 1
        assert called_with[0].pid == 300
        assert "Terminated 1 orphan" in result.stdout

    def test_kill_with_failures_exits_1(self, runner):
        """--kill with some failures -> exit code 1 and failure details."""
        orphan = _make_proc(pid=400, parent_alive=False, parent_name="<dead>")
        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            return_value=[orphan],
        ), patch(
            "curator.cli.mcp_orphans._kill_orphans",
            return_value=(0, [(400, "access denied")]),
        ):
            result = runner.invoke(app, ["mcp", "cleanup-orphans", "--kill", "--yes"])
        assert result.exit_code == 1, f"stdout={result.stdout}"
        assert "400" in result.stdout
        assert "access denied" in result.stdout

    def test_json_mode_dry_run(self, runner):
        """--json mode emits valid JSON with totals and per-process details."""
        alive = _make_proc(pid=100, parent_alive=True, parent_name="claude.exe")
        orphan = _make_proc(pid=200, parent_alive=False, parent_name="<dead>")
        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            return_value=[alive, orphan],
        ):
            result = runner.invoke(app, ["--json", "mcp", "cleanup-orphans"])
        assert result.exit_code == 0, result.stdout
        # Should be parseable JSON
        data = json.loads(result.stdout)
        assert data["total"] == 2
        assert data["orphans"] == 1

    def test_json_mode_kill_without_yes_exits_2(self, runner):
        """--json + --kill without --yes is ambiguous; require --yes."""
        orphan = _make_proc(pid=500, parent_alive=False, parent_name="<dead>")
        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            return_value=[orphan],
        ):
            result = runner.invoke(app, ["--json", "mcp", "cleanup-orphans", "--kill"])
        assert result.exit_code == 2, f"output={result.output}"
        # Error message should mention --yes
        assert "--yes" in result.output.lower() or "yes" in result.output.lower()

    def test_psutil_missing_exits_2(self, runner):
        """When psutil import fails, command exits 2 with helpful message."""
        def raise_import_error():
            raise ImportError("psutil required; install via pip install psutil>=5.9")

        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            side_effect=ImportError(
                "psutil is required for `curator mcp cleanup-orphans`. "
                "Install via: pip install psutil>=5.9"
            ),
        ):
            result = runner.invoke(app, ["mcp", "cleanup-orphans"])
        assert result.exit_code == 2, f"output={result.output}"
        assert "psutil" in result.output.lower()

    def test_kill_aborted_by_user(self, runner):
        """--kill without --yes prompts; user declines -> no kill called."""
        orphan = _make_proc(pid=600, parent_alive=False, parent_name="<dead>")
        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            return_value=[orphan],
        ), patch(
            "curator.cli.mcp_orphans._kill_orphans",
        ) as mock_kill:
            # Simulate "n" answer to typer.confirm
            result = runner.invoke(
                app, ["mcp", "cleanup-orphans", "--kill"], input="n\n",
            )
        assert result.exit_code == 0, result.stdout
        assert "Aborted" in result.stdout
        mock_kill.assert_not_called()

    def test_kill_confirmed_by_user(self, runner):
        """--kill without --yes prompts; user accepts -> kill called."""
        orphan = _make_proc(pid=700, parent_alive=False, parent_name="<dead>")
        with patch(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            return_value=[orphan],
        ), patch(
            "curator.cli.mcp_orphans._kill_orphans",
            return_value=(1, []),
        ) as mock_kill:
            result = runner.invoke(
                app, ["mcp", "cleanup-orphans", "--kill"], input="y\n",
            )
        assert result.exit_code == 0, result.stdout
        mock_kill.assert_called_once()
        assert "Terminated 1 orphan" in result.stdout
