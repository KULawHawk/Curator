"""Coverage closure for ``curator.cli.mcp_orphans`` (v1.7.154).

Existing ``tests/unit/test_mcp_orphans.py`` covers:
- Pure helpers (`_orphans_only`, `_format_age`, `_emit_json`,
  `_render_table`)
- The Typer command via monkeypatched `_enumerate_curator_mcp_processes`
  and `_kill_orphans`

Remaining uncovered surface:
- Lines 63-109: `_enumerate_curator_mcp_processes` body (needs psutil
  mocking — process_iter + Process)
- Lines 181-207: `_kill_orphans` body (needs psutil.Process mocking
  with terminate/wait/kill side effects)
- Line 279: empty-orphans branch in JSON mode (no-orphans return after
  JSON emit)
- Line 310: JSON-mode kill output payload (the `console.print(json.dumps({...
  "killed": ..., "failed": ...}, indent=2))` block)
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.cli.mcp_orphans import (
    _ProcInfo,
    _enumerate_curator_mcp_processes,
    _kill_orphans,
)


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# _enumerate_curator_mcp_processes — psutil mocking
# ---------------------------------------------------------------------------


class _FakeProc:
    """Stand-in for psutil.Process — attribute-based, supports info dict."""

    def __init__(self, **info):
        self.info = info
        self._is_running = info.pop("_running", True)
        self._name = info.get("name", "<unknown>")

    def is_running(self):
        return self._is_running

    def name(self):
        return self._name


def _build_fake_psutil(processes, parent_lookup=None, parent_lookup_raises=None):
    """Build a fake `psutil` module with controllable process_iter +
    Process(pid)."""
    fake = types.ModuleType("psutil")
    fake.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    fake.AccessDenied = type("AccessDenied", (Exception,), {})
    fake.TimeoutExpired = type("TimeoutExpired", (Exception,), {})

    def _process_iter(_keys):
        return iter(processes)

    def _Process(pid):
        if parent_lookup_raises and pid in parent_lookup_raises:
            raise parent_lookup_raises[pid]
        if parent_lookup and pid in parent_lookup:
            return parent_lookup[pid]
        # Default: NoSuchProcess
        raise fake.NoSuchProcess(f"no such pid {pid}")

    fake.process_iter = _process_iter
    fake.Process = _Process
    return fake


class TestEnumerateCuratorMcpProcesses:
    def test_psutil_import_error_raises_with_helpful_message(self, monkeypatch):
        """Lines 63-73: psutil not installed -> ImportError with hint."""
        monkeypatch.setitem(sys.modules, "psutil", None)
        with pytest.raises(ImportError, match="psutil is required"):
            _enumerate_curator_mcp_processes()

    def test_filters_to_curator_mcp_processes_only(self, monkeypatch):
        """Lines 75-109: process_iter filters by name; non-matching
        skipped (continue), matching are converted to _ProcInfo."""
        # 3 processes: 2 curator-mcp.exe, 1 other
        live_parent = _FakeProc(name="claude.exe", _running=True)

        procs = [
            _FakeProc(  # match #1: curator-mcp.exe with live parent
                pid=100, name="curator-mcp.exe", ppid=999,
                create_time=1700000000.0,
                cmdline=["curator-mcp.exe", "--stdio"],
            ),
            _FakeProc(  # non-match: chrome.exe
                pid=101, name="chrome.exe", ppid=1,
                create_time=1700000100.0, cmdline=["chrome.exe"],
            ),
            _FakeProc(  # match #2: bare curator-mcp (POSIX style)
                pid=102, name="curator-mcp", ppid=0,  # no parent at all
                create_time=1700000200.0, cmdline=["curator-mcp"],
            ),
        ]
        fake = _build_fake_psutil(procs, parent_lookup={999: live_parent})
        monkeypatch.setitem(sys.modules, "psutil", fake)

        result = _enumerate_curator_mcp_processes()
        assert len(result) == 2
        # Sort by create_time, so 100 comes before 102
        assert result[0].pid == 100
        assert result[0].parent_alive is True
        assert result[0].parent_name == "claude.exe"
        assert result[1].pid == 102
        # ppid=0 means we never even look up parent -> parent_alive=False
        assert result[1].parent_alive is False
        assert result[1].parent_name == "<dead>"

    def test_parent_lookup_no_such_process_marked_dead(self, monkeypatch):
        """Lines 91-93: psutil.Process(ppid) raises NoSuchProcess ->
        parent_alive=False, parent_name='<dead>'."""
        procs = [
            _FakeProc(
                pid=200, name="curator-mcp.exe", ppid=42,
                create_time=1700000000.0, cmdline=["curator-mcp.exe"],
            ),
        ]
        fake = _build_fake_psutil(procs)  # parent_lookup empty -> raises
        # Make sure the raised type IS NoSuchProcess (default in _build_fake_psutil)
        monkeypatch.setitem(sys.modules, "psutil", fake)
        result = _enumerate_curator_mcp_processes()
        assert len(result) == 1
        assert result[0].parent_alive is False
        assert result[0].parent_name == "<dead>"

    def test_parent_lookup_access_denied_marked_dead(self, monkeypatch):
        """Lines 91-93: psutil.AccessDenied also -> parent_alive=False."""
        procs = [
            _FakeProc(
                pid=200, name="curator-mcp.exe", ppid=42,
                create_time=1700000000.0, cmdline=["curator-mcp.exe"],
            ),
        ]
        fake = _build_fake_psutil(procs)
        # Override: AccessDenied instead of NoSuchProcess
        access_denied_exc = fake.AccessDenied("simulated AD")
        fake.Process = MagicMock(side_effect=access_denied_exc)
        monkeypatch.setitem(sys.modules, "psutil", fake)
        result = _enumerate_curator_mcp_processes()
        assert result[0].parent_alive is False
        assert result[0].parent_name == "<dead>"

    def test_process_iter_no_such_process_skipped(self, monkeypatch):
        """Lines 104-105: NoSuchProcess in the process_iter loop body
        -> continue (skip this process). Test by raising on .info access."""

        class _ExplodingProc:
            """Proc whose .info access raises NoSuchProcess."""
            @property
            def info(self):
                raise fake.NoSuchProcess("info access raised")

        fake = _build_fake_psutil([])
        exploder = _ExplodingProc()
        # Re-bind process_iter to return our exploder
        good = _FakeProc(
            pid=300, name="curator-mcp.exe", ppid=0,
            create_time=1700000000.0, cmdline=[],
        )
        fake.process_iter = lambda _keys: iter([exploder, good])
        monkeypatch.setitem(sys.modules, "psutil", fake)

        result = _enumerate_curator_mcp_processes()
        # Exploding one skipped, good one returned
        assert len(result) == 1
        assert result[0].pid == 300


# ---------------------------------------------------------------------------
# _kill_orphans — psutil.Process mocking
# ---------------------------------------------------------------------------


def _make_orphan(pid: int = 1234) -> _ProcInfo:
    return _ProcInfo(
        pid=pid, parent_pid=0, parent_alive=False, parent_name="<dead>",
        create_time=datetime(2026, 5, 11, 12, 0, 0),
        cmdline=("curator-mcp.exe",),
    )


class TestKillOrphans:
    def test_graceful_terminate_succeeds(self, monkeypatch):
        """Lines 187-199: terminate -> wait succeeds -> killed += 1."""
        fake = _build_fake_psutil([])
        proc_instance = MagicMock()
        proc_instance.terminate = MagicMock()
        proc_instance.wait = MagicMock(return_value=None)
        fake.Process = MagicMock(return_value=proc_instance)
        monkeypatch.setitem(sys.modules, "psutil", fake)

        killed, failures = _kill_orphans([_make_orphan(pid=1)])
        assert killed == 1
        assert failures == []
        proc_instance.terminate.assert_called_once()

    def test_timeout_falls_back_to_kill(self, monkeypatch):
        """Lines 192-198: TimeoutExpired -> kill -> 2nd wait succeeds."""
        fake = _build_fake_psutil([])
        proc_instance = MagicMock()
        proc_instance.terminate = MagicMock()
        # First wait raises TimeoutExpired, second succeeds
        proc_instance.wait = MagicMock(
            side_effect=[fake.TimeoutExpired("term timeout"), None],
        )
        proc_instance.kill = MagicMock()
        fake.Process = MagicMock(return_value=proc_instance)
        monkeypatch.setitem(sys.modules, "psutil", fake)

        killed, failures = _kill_orphans([_make_orphan(pid=2)])
        assert killed == 1
        assert failures == []
        proc_instance.kill.assert_called_once()

    def test_kill_timeout_marks_failure(self, monkeypatch):
        """Lines 194-198: kill() also times out -> failure."""
        fake = _build_fake_psutil([])
        proc_instance = MagicMock()
        proc_instance.terminate = MagicMock()
        # Both waits raise TimeoutExpired
        proc_instance.wait = MagicMock(side_effect=fake.TimeoutExpired("both timed out"))
        proc_instance.kill = MagicMock()
        fake.Process = MagicMock(return_value=proc_instance)
        monkeypatch.setitem(sys.modules, "psutil", fake)

        killed, failures = _kill_orphans([_make_orphan(pid=3)])
        assert killed == 0
        assert len(failures) == 1
        assert failures[0][0] == 3
        assert "did not exit" in failures[0][1]

    def test_no_such_process_counted_as_killed_idempotent(self, monkeypatch):
        """Lines 200-201: NoSuchProcess in Process(pid) -> already gone,
        count as killed (idempotent)."""
        fake = _build_fake_psutil([])
        fake.Process = MagicMock(side_effect=fake.NoSuchProcess("gone"))
        monkeypatch.setitem(sys.modules, "psutil", fake)

        killed, failures = _kill_orphans([_make_orphan(pid=4)])
        assert killed == 1
        assert failures == []

    def test_access_denied_marks_failure(self, monkeypatch):
        """Lines 202-203: AccessDenied -> failure."""
        fake = _build_fake_psutil([])
        fake.Process = MagicMock(side_effect=fake.AccessDenied("perm denied"))
        monkeypatch.setitem(sys.modules, "psutil", fake)

        killed, failures = _kill_orphans([_make_orphan(pid=5)])
        assert killed == 0
        assert len(failures) == 1
        assert "access denied" in failures[0][1]

    def test_generic_exception_marks_failure(self, monkeypatch):
        """Lines 204-205: any other exception -> failure."""
        fake = _build_fake_psutil([])
        fake.Process = MagicMock(side_effect=RuntimeError("unexpected boom"))
        monkeypatch.setitem(sys.modules, "psutil", fake)

        killed, failures = _kill_orphans([_make_orphan(pid=6)])
        assert killed == 0
        assert len(failures) == 1
        assert "RuntimeError" in failures[0][1]
        assert "unexpected boom" in failures[0][1]


# ---------------------------------------------------------------------------
# Command-level: lines 279 + 310 (JSON mode branches)
# ---------------------------------------------------------------------------


class TestCommandJsonModeBranches:
    def test_json_mode_no_orphans_returns_after_json_emit(
        self, runner, monkeypatch,
    ):
        """Line 279: empty-orphans branch after JSON emit. Path:
        json_mode=True, orphans=[] -> JSON written -> `if not orphans:
        return` fires (line 278-279)."""
        # Patch enumerate to return procs with all-live parents (no orphans)
        live_proc = _ProcInfo(
            pid=100, parent_pid=5, parent_alive=True, parent_name="claude.exe",
            create_time=datetime(2026, 5, 11), cmdline=("curator-mcp.exe",),
        )
        monkeypatch.setattr(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            lambda: [live_proc],
        )
        result = runner.invoke(app, ["--json", "mcp", "cleanup-orphans"])
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # JSON payload present
        assert '"total": 1' in combined
        assert '"orphans": 0' in combined

    def test_json_mode_kill_emits_killed_payload(self, runner, monkeypatch):
        """Line 309-313: JSON mode after kill emits killed + failed payload."""
        orphan = _ProcInfo(
            pid=200, parent_pid=0, parent_alive=False, parent_name="<dead>",
            create_time=datetime(2026, 5, 11), cmdline=("curator-mcp.exe",),
        )
        monkeypatch.setattr(
            "curator.cli.mcp_orphans._enumerate_curator_mcp_processes",
            lambda: [orphan],
        )
        monkeypatch.setattr(
            "curator.cli.mcp_orphans._kill_orphans",
            lambda orphans, timeout=3.0: (1, []),
        )
        result = runner.invoke(
            app, ["--json", "mcp", "cleanup-orphans", "--kill", "--yes"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Both JSON payloads (the initial enumeration + the kill result)
        # should appear; line 310 is the kill-result payload
        assert '"killed": 1' in combined
        assert '"failed": []' in combined
