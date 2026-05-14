"""Coverage for CleanupDialog (v1.7.203).

Round 5 Tier 1 sub-ship 5 of 8 — three-mode cleanup picker
(junk / empty_dirs / broken_symlinks) with worker stubbing.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def silence_qmessagebox(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", MagicMock())
    monkeypatch.setattr(QMessageBox, "warning", MagicMock())
    monkeypatch.setattr(QMessageBox, "critical", MagicMock())
    monkeypatch.setattr(QMessageBox, "question",
                        MagicMock(return_value=QMessageBox.StandardButton.Yes))


def _make_finding(*, path="/p/x.tmp", size=1024, details=None):
    f = MagicMock()
    f.path = path
    f.size = size
    f.details = details if details is not None else {}
    return f


def _make_report(*, findings=None, errors=None):
    r = MagicMock()
    r.findings = findings or []
    r.errors = errors or []
    return r


class _Outcome(Enum):
    DELETED = "deleted"
    SKIPPED_REFUSE = "skipped_refuse"
    SKIPPED_MISSING = "skipped_missing"
    FAILED = "failed"


def _make_result(*, outcome=_Outcome.DELETED, error=None, path="/x"):
    r = MagicMock()
    r.outcome = outcome
    r.finding = _make_finding(path=path)
    r.error = error
    return r


def _make_apply_report(*, results=None):
    r = MagicMock()
    r.results = results or []
    r.started_at = datetime(2026, 5, 1, 10, 0)
    r.completed_at = datetime(2026, 5, 1, 10, 5)
    return r


def _stubs(*, find_completes=None, find_fails=None,
           apply_completes=None, apply_fails=None):
    from PySide6.QtCore import QObject, Signal, QThread

    class _Bridge(QObject):
        find_started = Signal(object)
        find_completed = Signal(object)
        find_failed = Signal(object)
        apply_started = Signal(object)
        apply_completed = Signal(object)
        apply_failed = Signal(object)

        def __init__(self, parent=None):
            super().__init__(parent)

    class _FindW(QThread):
        def __init__(self, *, runtime, mode, root, patterns=None,
                     ignore_system_junk=True, bridge, parent=None):
            super().__init__(parent)
            self._bridge = bridge

        def start(self):
            self._bridge.find_started.emit(("mode", "/r"))
            if find_fails is not None:
                self._bridge.find_failed.emit(find_fails)
            elif find_completes is not None:
                self._bridge.find_completed.emit(find_completes)

        def isRunning(self):
            return False

    class _ApplyW(QThread):
        def __init__(self, *, runtime, report, use_trash, bridge, parent=None):
            super().__init__(parent)
            self._bridge = bridge

        def start(self):
            self._bridge.apply_started.emit(0)
            if apply_fails is not None:
                self._bridge.apply_failed.emit(apply_fails)
            elif apply_completes is not None:
                self._bridge.apply_completed.emit(apply_completes)

        def isRunning(self):
            return False

    return _FindW, _ApplyW, _Bridge


# ===========================================================================
# Construction + mode switching
# ===========================================================================


class TestCleanupConstruction:
    def test_basic_construction(self, qapp, qtbot):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        assert "Cleanup" in dlg.windowTitle()
        # Default mode is junk
        assert dlg.current_mode == "junk"
        # Junk row visible, strict hidden
        assert dlg._w_junk.isVisible() or not dlg.isVisible()  # visibility check
        assert dlg._rb_junk.isChecked()

    def test_mode_switch_to_empty_dirs(self, qapp, qtbot):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._rb_empty.setChecked(True)
        assert dlg.current_mode == "empty_dirs"
        assert "rmdir" in dlg._btn_apply.text().lower() or "empty" in dlg._btn_apply.text().lower()

    def test_mode_switch_to_symlinks(self, qapp, qtbot):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._rb_symlinks.setChecked(True)
        assert dlg.current_mode == "broken_symlinks"
        assert "unlink" in dlg._btn_apply.text().lower() or "symlink" in dlg._btn_apply.text().lower()


# ===========================================================================
# Mode helpers
# ===========================================================================


class TestModeHelpers:
    def test_set_indeterminate(self, qapp, qtbot):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._set_indeterminate(True, "x")
        assert dlg._progress.maximum() == 0
        dlg._set_indeterminate(False)
        assert dlg._progress.maximum() == 1

    def test_clear_results(self, qapp, qtbot):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._clear_results()

    def test_update_button_states_no_path(self, qapp, qtbot):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._update_button_states()
        # No path → Find disabled
        assert not dlg._btn_find.isEnabled()

    def test_update_button_states_valid_path(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._update_button_states()
        assert dlg._btn_find.isEnabled()


# ===========================================================================
# Browse
# ===========================================================================


class TestBrowse:
    def test_browse_no_path_uses_home(self, qapp, qtbot, monkeypatch):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QFileDialog
        called = {}

        def stub(parent, title, start):
            called["start"] = start
            return ""

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", stub)
        dlg._on_browse_clicked()
        assert called["start"] == str(Path.home())

    def test_browse_existing_path(self, qapp, qtbot, monkeypatch, tmp_path):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        from PySide6.QtWidgets import QFileDialog
        called = {}

        def stub(parent, title, start):
            called["start"] = start
            return ""

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", stub)
        dlg._on_browse_clicked()
        assert called["start"] == str(tmp_path)

    def test_browse_chosen_updates(self, qapp, qtbot, monkeypatch, tmp_path):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QFileDialog
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            lambda *a, **kw: str(tmp_path / "sub"))
        dlg._on_browse_clicked()
        assert dlg._le_path.text() == str(tmp_path / "sub")


# ===========================================================================
# Find phase (all 3 modes)
# ===========================================================================


class TestFindPhase:
    def test_find_import_failure(self, qapp, qtbot, monkeypatch, tmp_path):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        import builtins
        orig = builtins.__import__

        def fail(name, *a, **kw):
            if name == "curator.gui.cleanup_signals":
                raise ImportError("simulated")
            return orig(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fail)
        dlg._on_find_clicked()
        assert "Could not load" in dlg._lbl_status.text()

    def test_find_no_path_short_circuits(self, qapp, qtbot, monkeypatch):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        # Don't need imports to actually work — empty path returns
        # before they run
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        assert dlg._find_worker is None

    def test_find_junk_with_no_findings(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        find, apply_w, bridge = _stubs(find_completes=_make_report(findings=[]))
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        assert "Nothing to clean" in dlg._lbl_status.text()

    def test_find_junk_with_patterns(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        report = _make_report(findings=[
            _make_finding(path="/p/Thumbs.db", size=512,
                         details={"matched_pattern": "Thumbs.db"}),
        ])
        find, apply_w, bridge = _stubs(find_completes=report)
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        # User provides custom patterns
        dlg._le_junk_patterns.setText("*.tmp, *.bak")
        dlg._on_find_clicked()
        assert dlg.last_find_report is report

    def test_find_empty_dirs_strict(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        report = _make_report(findings=[
            _make_finding(path="/p/empty", size=0,
                         details={"system_junk_present": False}),
        ])
        find, apply_w, bridge = _stubs(find_completes=report)
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._rb_empty.setChecked(True)
        dlg._cb_strict.setChecked(True)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        assert dlg.last_find_report is report

    def test_find_empty_dirs_with_system_junk(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        report = _make_report(findings=[
            _make_finding(path="/p/d", size=0,
                         details={"system_junk_present": True}),
        ])
        find, apply_w, bridge = _stubs(find_completes=report)
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._rb_empty.setChecked(True)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        assert dlg.last_find_report is report

    def test_find_broken_symlinks(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        report = _make_report(findings=[
            _make_finding(path="/p/link", size=0,
                         details={"target": "/nonexistent"}),
        ])
        find, apply_w, bridge = _stubs(find_completes=report)
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._rb_symlinks.setChecked(True)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        assert dlg.last_find_report is report

    def test_find_with_errors(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """Report with scan errors → error block rendered."""
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        report = _make_report(
            findings=[_make_finding(details={"matched_pattern": "*.tmp"})],
            errors=["permission denied: /protected/file.tmp",
                    "read error: /broken/file.tmp"],
        )
        find, apply_w, bridge = _stubs(find_completes=report)
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        assert "error" in dlg._lbl_status.text().lower()

    def test_find_fails(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        find, apply_w, bridge = _stubs(find_fails=RuntimeError("find boom"))
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        assert "Find failed" in dlg._lbl_status.text()


# ===========================================================================
# Apply phase
# ===========================================================================


class TestApplyPhase:
    def test_apply_no_report_returns(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_apply_clicked()
        assert dlg.last_apply_report is None

    def test_apply_user_cancel(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        report = _make_report(findings=[_make_finding()])
        find, apply_w, bridge = _stubs(
            find_completes=report,
            apply_completes=_make_apply_report(),
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question",
                            MagicMock(return_value=QMessageBox.StandardButton.No))
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert dlg.last_apply_report is None  # cancelled

    def test_apply_import_failure(
        self, qapp, qtbot, monkeypatch, tmp_path, silence_qmessagebox,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        report = _make_report(findings=[_make_finding()])
        find, apply_w, bridge = _stubs(find_completes=report)
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        if hasattr(cs, "CleanupApplyWorker"):
            monkeypatch.delattr(cs, "CleanupApplyWorker")
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert ("Could not load" in dlg._lbl_status.text()
                or "CleanupApplyWorker" in dlg._lbl_status.text())

    def test_apply_completes_all_deleted(
        self, qapp, qtbot, monkeypatch, tmp_path, silence_qmessagebox,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        find_report = _make_report(findings=[_make_finding(), _make_finding()])
        apply_report = _make_apply_report(results=[
            _make_result(outcome=_Outcome.DELETED),
            _make_result(outcome=_Outcome.DELETED),
        ])
        find, apply_w, bridge = _stubs(
            find_completes=find_report,
            apply_completes=apply_report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert dlg.last_apply_report is apply_report
        assert "Apply complete" in dlg._lbl_status.text()

    def test_apply_with_failures_and_errors(
        self, qapp, qtbot, monkeypatch, tmp_path, silence_qmessagebox,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        find_report = _make_report(findings=[_make_finding()])
        apply_report = _make_apply_report(results=[
            _make_result(outcome=_Outcome.DELETED),
            _make_result(outcome=_Outcome.FAILED, error="permission denied"),
            _make_result(outcome=_Outcome.SKIPPED_REFUSE),
            _make_result(outcome=_Outcome.SKIPPED_MISSING),
        ])
        find, apply_w, bridge = _stubs(
            find_completes=find_report,
            apply_completes=apply_report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert dlg.last_apply_report is apply_report

    def test_apply_hard_delete(
        self, qapp, qtbot, monkeypatch, tmp_path, silence_qmessagebox,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        find_report = _make_report(findings=[_make_finding()])
        apply_report = _make_apply_report(results=[_make_result()])
        find, apply_w, bridge = _stubs(
            find_completes=find_report, apply_completes=apply_report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._cb_use_trash.setChecked(False)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert dlg.last_apply_report is apply_report

    def test_apply_fails(
        self, qapp, qtbot, monkeypatch, tmp_path, silence_qmessagebox,
    ):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        find_report = _make_report(findings=[_make_finding()])
        find, apply_w, bridge = _stubs(
            find_completes=find_report,
            apply_fails=RuntimeError("apply fail"),
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert "Apply failed" in dlg._lbl_status.text()


# ===========================================================================
# Open GroupDialog button
# ===========================================================================


class TestOpenGroupDialog:
    def test_open_group_dialog_success(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        """Click the Open GroupDialog button → constructs + execs GroupDialog."""
        from curator.gui import dialogs as dialogs_mod
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        # Stub GroupDialog entirely (constructor + exec) to avoid the real
        # dialog being built (which would crash on a bare MagicMock runtime)
        stub_group = MagicMock()
        stub_group_instance = MagicMock()
        stub_group.return_value = stub_group_instance
        monkeypatch.setattr(dialogs_mod, "GroupDialog", stub_group)
        # Stub self.reject so we don't actually close
        dlg.reject = MagicMock()
        dlg._on_open_group_clicked()
        dlg.reject.assert_called_once()
        stub_group_instance.exec.assert_called_once()

    def test_open_group_dialog_construction_fails(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        """If GroupDialog construction raises, surfaces a QMessageBox.critical."""
        from curator.gui import dialogs as dialogs_mod
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        # Make GroupDialog __init__ raise
        monkeypatch.setattr(dialogs_mod, "GroupDialog",
                            MagicMock(side_effect=RuntimeError("init fail")))
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QMessageBox
        dlg._on_open_group_clicked()
        QMessageBox.critical.assert_called()


# ===========================================================================
# Slot no-ops
# ===========================================================================


class TestSlotNoOps:
    def test_on_find_started_noop(self, qapp, qtbot):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_started("payload")

    def test_on_apply_started_noop(self, qapp, qtbot):
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_apply_started(5)


class TestCloseButton:
    def test_close_button_rejects(self, qapp, qtbot):
        from curator.gui.dialogs import CleanupDialog
        from PySide6.QtCore import Qt
        rt = MagicMock()
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg.reject = MagicMock()
        qtbot.mouseClick(dlg._btn_close, Qt.MouseButton.LeftButton)
        dlg.reject.assert_called_once()
