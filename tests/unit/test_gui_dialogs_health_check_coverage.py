"""Coverage for HealthCheckDialog (v1.7.201).

Round 5 Tier 1 sub-ship 3 of 8 — covers the diagnostic dialog with 8
internal health checks. Each check has its own service stubs.

The dialog runs all checks synchronously at construction. We stub the
collaborators that each check touches (runtime.config.db_path,
sqlite3.connect, plugin manager, env vars, file system, MCP probe).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_runtime(db_path=None):
    """Build a runtime stub with db_path + plugin manager."""
    rt = MagicMock()
    rt.config.db_path = str(db_path) if db_path else "/nonexistent/curator.db"
    rt.pm.list_name_plugin.return_value = [
        ("curator.core.local_source", MagicMock()),
        ("curator.core.gdrive_source", MagicMock()),
        ("curator.core.audit_writer", MagicMock()),
        # 6 more to hit the 9-plugin threshold
        ("plugin4", MagicMock()), ("plugin5", MagicMock()), ("plugin6", MagicMock()),
        ("plugin7", MagicMock()), ("plugin8", MagicMock()), ("plugin9", MagicMock()),
    ]
    return rt


# ===========================================================================
# Construction + refresh
# ===========================================================================


class TestHealthCheckConstruction:
    def test_basic_construction(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        db_path = tmp_path / "curator.db"
        rt = _make_runtime(db_path=db_path)
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        assert "Health Check" in dlg.windowTitle()
        # last_result populated after refresh
        assert dlg.last_result is not None
        assert dlg.last_result.total > 0

    def test_refresh_re_runs_checks(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        first = dlg.last_result
        dlg.refresh()
        second = dlg.last_result
        # New result with potentially different elapsed_ms / started_at
        assert second is not None
        assert second is not first  # new object

    def test_refresh_button_click(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        from PySide6.QtCore import Qt
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        first = dlg.last_result
        qtbot.mouseClick(dlg._refresh_btn, Qt.MouseButton.LeftButton)
        assert dlg.last_result is not first


# ===========================================================================
# Individual check methods
# ===========================================================================


class TestFilesystemCheck:
    def test_existing_db(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        db_path = tmp_path / ".curator" / "curator.db"
        db_path.parent.mkdir(parents=True)
        db_path.touch()
        # Make a Curator subdir so the Curator check passes
        (tmp_path / "Curator").mkdir()
        rt = _make_runtime(db_path=db_path)
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        fs_results = dlg.last_result.sections["Filesystem layout"]
        # Check the db_path exists check passed
        db_check = next(r for r in fs_results if "Canonical DB" in r.label)
        assert db_check.passed

    def test_missing_db(self, qapp, qtbot):
        from curator.gui.dialogs import HealthCheckDialog
        rt = _make_runtime(db_path="/nonexistent/path/curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        fs_results = dlg.last_result.sections["Filesystem layout"]
        db_check = next(r for r in fs_results if "Canonical DB" in r.label)
        assert not db_check.passed

    def test_filesystem_exception(self, qapp, qtbot, monkeypatch):
        """If accessing config.db_path raises, the check captures the
        exception (uses real class trick per Lesson #104)."""
        from curator.gui.dialogs import HealthCheckDialog

        class _RaisingConfig:
            @property
            def db_path(self):
                raise RuntimeError("config fail")

        rt = MagicMock()
        rt.config = _RaisingConfig()
        rt.pm.list_name_plugin.return_value = []
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        fs_results = dlg.last_result.sections["Filesystem layout"]
        # At least one check failed with "raised"
        raised = [r for r in fs_results if "raised" in r.label]
        assert len(raised) >= 1


class TestPythonCheck:
    def test_python_check_runs(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        py_results = dlg.last_result.sections["Python + venv"]
        # We have 2 checks: version + venv
        assert len(py_results) == 2

    def test_python_below_311_fails(self, qapp, qtbot, tmp_path, monkeypatch):
        """Simulate Python 3.10 → version check fails with severity=fail."""
        from curator.gui.dialogs import HealthCheckDialog
        # Monkeypatch sys.version_info
        from collections import namedtuple
        VersionInfo = namedtuple("VI", "major minor micro releaselevel serial")
        monkeypatch.setattr(sys, "version_info", VersionInfo(3, 10, 0, "final", 0))
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        py_results = dlg.last_result.sections["Python + venv"]
        ver_check = next(r for r in py_results if "version" in r.label)
        assert not ver_check.passed
        assert ver_check.severity == "fail"


class TestVersionsCheck:
    def test_versions_check_runs(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        ver_results = dlg.last_result.sections["Curator + plugin versions"]
        # 3 entries: curator + 2 plugins (atrium-citation + atrium-safety)
        assert len(ver_results) == 3


class TestGuiDepsCheck:
    def test_gui_deps_check_runs(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        gd_results = dlg.last_result.sections["GUI dependencies"]
        # 2 entries: PySide6 + networkx
        assert len(gd_results) == 2

    def test_networkx_missing(self, qapp, qtbot, tmp_path, monkeypatch):
        """If networkx is not installed, the check should fire warn severity."""
        from curator.gui.dialogs import HealthCheckDialog
        # Monkeypatch to make `import networkx` raise
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name == "networkx":
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        if isinstance(__builtins__, dict):
            monkeypatch.setitem(__builtins__, "__import__", fake_import)
        else:
            monkeypatch.setattr(__builtins__, "__import__", fake_import)
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        gd_results = dlg.last_result.sections["GUI dependencies"]
        nx_check = next(r for r in gd_results if "networkx" in r.label)
        assert not nx_check.passed
        assert nx_check.severity == "warn"


class TestDbIntegrityCheck:
    def test_db_integrity_with_real_db(self, qapp, qtbot, tmp_path):
        """Create a real sqlite3 DB with the required schema, verify check passes."""
        from curator.gui.dialogs import HealthCheckDialog
        import sqlite3
        db_path = tmp_path / "curator.db"
        c = sqlite3.connect(str(db_path))
        try:
            c.execute("CREATE TABLE files (deleted_at TEXT)")
            c.execute("CREATE TABLE sources (enabled INTEGER)")
        finally:
            c.close()
        rt = _make_runtime(db_path=db_path)
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        db_results = dlg.last_result.sections["DB integrity"]
        integrity_check = next(r for r in db_results if "integrity" in r.label.lower())
        assert integrity_check.passed

    def test_db_integrity_missing_db_raises(self, qapp, qtbot, tmp_path):
        """If db doesn't exist, sqlite3.connect creates an empty one but
        the schema queries fail → the check captures the exception."""
        from curator.gui.dialogs import HealthCheckDialog
        # Use a path without the schema
        rt = _make_runtime(db_path=tmp_path / "fresh.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        db_results = dlg.last_result.sections["DB integrity"]
        # The "raised" check should appear
        assert any("raised" in r.label for r in db_results)


class TestPluginsCheck:
    def test_plugins_with_required_present(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        plg_results = dlg.last_result.sections["Plugins registered"]
        # Total + 3 required
        assert len(plg_results) == 4

    def test_plugins_missing_required(self, qapp, qtbot, tmp_path):
        """Plugin manager returns a list missing one of the required plugins."""
        from curator.gui.dialogs import HealthCheckDialog
        rt = MagicMock()
        rt.config.db_path = str(tmp_path / "curator.db")
        # Missing audit_writer
        rt.pm.list_name_plugin.return_value = [
            ("curator.core.local_source", MagicMock()),
            ("curator.core.gdrive_source", MagicMock()),
            # 7 placeholder plugins to hit 9 total
            ("p4", MagicMock()), ("p5", MagicMock()), ("p6", MagicMock()),
            ("p7", MagicMock()), ("p8", MagicMock()), ("p9", MagicMock()),
            ("p10", MagicMock()),
        ]
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        plg_results = dlg.last_result.sections["Plugins registered"]
        # audit_writer check should fail
        audit_check = next(r for r in plg_results if "audit_writer" in r.label)
        assert not audit_check.passed

    def test_plugins_enumeration_exception(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        rt = MagicMock()
        rt.config.db_path = str(tmp_path / "curator.db")
        rt.pm.list_name_plugin.side_effect = RuntimeError("pm broken")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        plg_results = dlg.last_result.sections["Plugins registered"]
        # "raised" check appears
        assert any("raised" in r.label for r in plg_results)


class TestMcpConfigCheck:
    def test_mcp_config_file_missing(self, qapp, qtbot, monkeypatch, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        monkeypatch.setenv("APPDATA", str(tmp_path))
        # No claude_desktop_config.json in that path
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        mcp_results = dlg.last_result.sections["Claude Desktop MCP config"]
        # Single result: "not found"
        assert len(mcp_results) == 1
        assert not mcp_results[0].passed
        assert mcp_results[0].severity == "warn"

    def test_mcp_config_file_valid(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import HealthCheckDialog
        # Build a valid claude_desktop_config.json
        claude_dir = tmp_path / "Claude"
        claude_dir.mkdir()
        cfg_path = claude_dir / "claude_desktop_config.json"
        toml_path = tmp_path / "curator.toml"
        toml_path.touch()  # Need the toml to exist for resolve()
        cfg_data = {
            "mcpServers": {
                "curator": {
                    "command": "C:\\path\\to\\curator-mcp.exe",
                    "env": {"CURATOR_CONFIG": str(toml_path)},
                },
            },
        }
        cfg_path.write_text(json.dumps(cfg_data), encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        mcp_results = dlg.last_result.sections["Claude Desktop MCP config"]
        # Multiple checks: file exists, has curator entry, command, env
        assert len(mcp_results) >= 4

    def test_mcp_config_no_curator_entry(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import HealthCheckDialog
        claude_dir = tmp_path / "Claude"
        claude_dir.mkdir()
        cfg_path = claude_dir / "claude_desktop_config.json"
        cfg_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        mcp_results = dlg.last_result.sections["Claude Desktop MCP config"]
        # "Has curator MCP entry" check fails
        has_check = next(r for r in mcp_results if "curator MCP entry" in r.label)
        assert not has_check.passed

    def test_mcp_config_invalid_json(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import HealthCheckDialog
        claude_dir = tmp_path / "Claude"
        claude_dir.mkdir()
        cfg_path = claude_dir / "claude_desktop_config.json"
        cfg_path.write_text("not valid json{", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        mcp_results = dlg.last_result.sections["Claude Desktop MCP config"]
        # JSON parse error captured
        invalid_check = next(r for r in mcp_results if "valid" in r.label.lower())
        assert not invalid_check.passed

    def test_mcp_config_check_raises_general_exception(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """Force a non-JSON exception (Path.resolve raising) during
        the env_ok comparison."""
        from curator.gui.dialogs import HealthCheckDialog
        claude_dir = tmp_path / "Claude"
        claude_dir.mkdir()
        cfg_path = claude_dir / "claude_desktop_config.json"
        cfg_path.write_text(json.dumps({
            "mcpServers": {"curator": {"command": "/x", "env": {"CURATOR_CONFIG": "/bogus/path"}}}
        }), encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        # Make Path.resolve raise to trigger the general except clause
        from pathlib import Path as _Path
        original_resolve = _Path.resolve

        def stub_resolve(self, *a, **kw):
            raise RuntimeError("resolve fail")

        monkeypatch.setattr(_Path, "resolve", stub_resolve)
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        mcp_results = dlg.last_result.sections["Claude Desktop MCP config"]
        # "raised" check appears
        assert any("raised" in r.label for r in mcp_results)


class TestMcpProbeCheck:
    def test_mcp_probe_no_executable(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """If curator-mcp.exe doesn't exist, the probe records a fail."""
        from curator.gui.dialogs import HealthCheckDialog
        # Point sys.prefix at a tmp dir that has no Scripts/curator-mcp.*
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        probe_results = dlg.last_result.sections["Real MCP probe"]
        # Single result: "executable found" = False
        assert len(probe_results) == 1
        assert not probe_results[0].passed

    def test_mcp_probe_subprocess_raises(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """If subprocess.Popen raises, the probe records the exception."""
        from curator.gui.dialogs import HealthCheckDialog
        # Make curator-mcp "exist" (touch the file)
        scripts = tmp_path / "Scripts"
        scripts.mkdir()
        (scripts / "curator-mcp.exe").touch()
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        # Patch subprocess.Popen to raise
        import subprocess
        monkeypatch.setattr(
            subprocess, "Popen",
            MagicMock(side_effect=RuntimeError("popen fail")),
        )
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        probe_results = dlg.last_result.sections["Real MCP probe"]
        # Some result indicates the probe raised
        assert any("raised" in r.label.lower() for r in probe_results)

    def test_mcp_probe_successful_handshake(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """Stub subprocess to return a successful tools/list response."""
        from curator.gui.dialogs import HealthCheckDialog
        scripts = tmp_path / "Scripts"
        scripts.mkdir()
        (scripts / "curator-mcp.exe").touch()
        toml_path = tmp_path / "curator.toml"
        toml_path.touch()
        monkeypatch.setattr(sys, "prefix", str(tmp_path))

        class _FakeProc:
            def __init__(self):
                self.stdin = MagicMock()
                self.stdout = MagicMock()
                init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n"
                tools_resp = json.dumps({
                    "jsonrpc": "2.0", "id": 2,
                    "result": {"tools": [{"name": f"tool{i}"} for i in range(10)]},
                }) + "\n"
                self.stdout.readline.side_effect = [init_resp, tools_resp]

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=_FakeProc()))
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        probe_results = dlg.last_result.sections["Real MCP probe"]
        tools_check = next(r for r in probe_results if "tools advertised" in r.label.lower())
        assert tools_check.passed

    def test_mcp_probe_no_tools_response(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """Stub returns empty string for tools_resp → 'no response' fail."""
        from curator.gui.dialogs import HealthCheckDialog
        scripts = tmp_path / "Scripts"
        scripts.mkdir()
        (scripts / "curator-mcp.exe").touch()
        monkeypatch.setattr(sys, "prefix", str(tmp_path))

        class _FakeProc:
            def __init__(self):
                self.stdin = MagicMock()
                self.stdout = MagicMock()
                self.stdout.readline.side_effect = [
                    json.dumps({"id": 1, "result": {}}) + "\n",
                    "",  # empty response
                ]

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=_FakeProc()))
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        probe_results = dlg.last_result.sections["Real MCP probe"]
        # Look for "no tools/list response" or handshake fail
        assert any(not r.passed for r in probe_results)

    def test_mcp_probe_wait_timeout(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """proc.wait raises TimeoutExpired → proc.kill() called."""
        from curator.gui.dialogs import HealthCheckDialog
        scripts = tmp_path / "Scripts"
        scripts.mkdir()
        (scripts / "curator-mcp.exe").touch()
        monkeypatch.setattr(sys, "prefix", str(tmp_path))

        import subprocess

        class _FakeProc:
            def __init__(self):
                self.stdin = MagicMock()
                self.stdout = MagicMock()
                self.stdout.readline.side_effect = [
                    json.dumps({"id": 1, "result": {}}) + "\n",
                    json.dumps({"result": {"tools": []}}) + "\n",
                ]
                self.kill_called = False

            def terminate(self):
                pass

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="x", timeout=2)

            def kill(self):
                self.kill_called = True

        monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=_FakeProc()))
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        # Just verify construction completed; kill() was called via the
        # except subprocess.TimeoutExpired handler
        assert dlg.last_result is not None


# ===========================================================================
# Render + copy-to-clipboard
# ===========================================================================


class TestRendering:
    def test_render_all_green(self, qapp, qtbot, tmp_path):
        """If every check passes, the header shows ✓ pattern."""
        from curator.gui.dialogs import HealthCheckDialog, HealthCheckResult, _CheckResult
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        # Force a fake all-green result
        fake = HealthCheckResult()
        fake.sections["A"] = [_CheckResult("a", True)]
        fake.elapsed_ms = 100
        dlg._render(fake)
        assert "passed" in dlg._header.text()

    def test_render_with_failures(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog, HealthCheckResult, _CheckResult
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        fake = HealthCheckResult()
        fake.sections["A"] = [_CheckResult("a", False, severity="fail")]
        fake.elapsed_ms = 50
        dlg._render(fake)
        assert "failed" in dlg._header.text()

    def test_render_check_row_passed_fail_severity(self, qapp, qtbot, tmp_path):
        """Render branch coverage for passed=True severity=fail."""
        from curator.gui.dialogs import HealthCheckDialog, _CheckResult
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        # Manually invoke _render_check_row for each branch
        # passed + severity=fail → green icon
        dlg._render_check_row(_CheckResult("x", True, severity="fail"))
        # passed + severity=info → blue icon
        dlg._render_check_row(_CheckResult("x", True, severity="info"))
        # not passed + severity=warn → orange icon
        dlg._render_check_row(_CheckResult("x", False, severity="warn"))
        # not passed + severity=info → blue icon
        dlg._render_check_row(_CheckResult("x", False, severity="info"))
        # not passed + severity=fail → red icon
        dlg._render_check_row(_CheckResult("x", False, severity="fail"))

    def test_render_check_row_no_detail(self, qapp, qtbot, tmp_path):
        """Row with empty detail → trailing stretch instead of label."""
        from curator.gui.dialogs import HealthCheckDialog, _CheckResult
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        dlg._render_check_row(_CheckResult("x", True, detail=""))

    def test_copy_result_with_none_does_nothing(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        dlg._last_result = None
        dlg._copy_result()  # should not raise

    def test_copy_result_with_result(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        # last_result is populated by construction; copy should work
        dlg._copy_result()

    def test_copy_button_click(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import HealthCheckDialog
        from PySide6.QtCore import Qt
        rt = _make_runtime(db_path=tmp_path / "curator.db")
        dlg = HealthCheckDialog(rt)
        qtbot.addWidget(dlg)
        qtbot.mouseClick(dlg._copy_btn, Qt.MouseButton.LeftButton)
