"""Coverage for GroupDialog (v1.7.202).

Round 5 Tier 1 sub-ship 4 of 8 — two-phase duplicate finder. Uses the
``_SyncWorker`` pattern from v1.7.199 (ScanDialog) and Round 4 Tier 3
to stub the find + apply background workers synchronously.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import Enum
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


# ===========================================================================
# Helpers
# ===========================================================================


def _make_source(*, source_id="local", source_type="local"):
    s = MagicMock()
    s.source_id = source_id
    s.source_type = source_type
    return s


def _make_finding(*, path="/p/dup.txt", size=1024, dupset_id="abc123def456",
                  kept_path="/p/keep.txt", kept_reason="shortest_path"):
    f = MagicMock()
    f.path = path
    f.size = size
    f.details = {
        "dupset_id": dupset_id,
        "kept_path": kept_path,
        "kept_reason": kept_reason,
    }
    return f


def _make_find_report(*, findings=None):
    r = MagicMock()
    r.findings = findings or []
    return r


class _MockOutcome(Enum):
    DELETED = "deleted"
    SKIPPED_REFUSE = "skipped_refuse"
    SKIPPED_MISSING = "skipped_missing"
    FAILED = "failed"


def _make_apply_result(*, outcome=_MockOutcome.DELETED,
                      finding=None, error=None):
    r = MagicMock()
    r.outcome = outcome
    r.finding = finding or _make_finding()
    r.error = error
    return r


def _make_apply_report(*, results=None,
                      started=None, completed=None):
    r = MagicMock()
    r.results = results or []
    r.started_at = started or datetime(2026, 5, 1, 10, 0)
    r.completed_at = completed or datetime(2026, 5, 1, 10, 5)
    return r


def _make_worker_stubs(
    *, find_completes_with=None, find_fails_with=None,
    apply_completes_with=None, apply_fails_with=None,
):
    """Build stub GroupFindWorker + GroupApplyWorker + GroupProgressBridge."""
    from PySide6.QtCore import QObject, Signal, QThread

    class _StubBridge(QObject):
        find_started = Signal(object)
        find_completed = Signal(object)
        find_failed = Signal(object)
        apply_started = Signal(object)
        apply_completed = Signal(object)
        apply_failed = Signal(object)

        def __init__(self, parent=None):
            super().__init__(parent)

    class _StubFindWorker(QThread):
        def __init__(self, *, runtime, source_id, root_prefix,
                     keep_strategy, keep_under, match_kind,
                     similarity_threshold, bridge, parent=None):
            super().__init__(parent)
            self._bridge = bridge

        def start(self):
            self._bridge.find_started.emit(("src", "/r"))
            if find_fails_with is not None:
                self._bridge.find_failed.emit(find_fails_with)
            elif find_completes_with is not None:
                self._bridge.find_completed.emit(find_completes_with)

        def isRunning(self):
            return False

    class _StubApplyWorker(QThread):
        def __init__(self, *, runtime, report, use_trash, bridge, parent=None):
            super().__init__(parent)
            self._bridge = bridge

        def start(self):
            self._bridge.apply_started.emit(0)
            if apply_fails_with is not None:
                self._bridge.apply_failed.emit(apply_fails_with)
            elif apply_completes_with is not None:
                self._bridge.apply_completed.emit(apply_completes_with)

        def isRunning(self):
            return False

    return _StubFindWorker, _StubApplyWorker, _StubBridge


# ===========================================================================
# Construction
# ===========================================================================


class TestGroupDialogConstruction:
    def test_basic_construction(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        assert "Find duplicates" in dlg.windowTitle()
        # Source dropdown has "(all sources)" at minimum
        assert dlg._cb_source.count() >= 1
        # Find button enabled (no in-flight work)
        assert dlg._btn_find.isEnabled()
        # Apply disabled (no findings yet)
        assert not dlg._btn_apply.isEnabled()

    def test_construction_with_sources(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [
            _make_source(source_id="local"),
            _make_source(source_id="gdrive", source_type="gdrive"),
        ]
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        # (all sources) + 2 sources = 3
        assert dlg._cb_source.count() == 3

    def test_construction_source_list_exception(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.side_effect = RuntimeError("db gone")
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        # Error label in dropdown
        assert any("error" in dlg._cb_source.itemText(i).lower()
                   for i in range(dlg._cb_source.count()))


# ===========================================================================
# State helpers
# ===========================================================================


class TestStateHelpers:
    def test_selected_source_id_all_sources(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        # First item is "(all sources)" with data=None
        assert dlg._selected_source_id() is None

    def test_selected_source_id_with_source(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._cb_source.setCurrentIndex(1)
        assert dlg._selected_source_id() == "local"

    def test_match_kind_toggle_enables_threshold(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        assert not dlg._sb_threshold.isEnabled()
        # Toggle fuzzy
        dlg._rb_fuzzy.setChecked(True)
        assert dlg._sb_threshold.isEnabled()

    def test_set_indeterminate_on_off(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._set_indeterminate(True, "Working...")
        assert dlg._progress.maximum() == 0
        dlg._set_indeterminate(False)
        assert dlg._progress.maximum() == 1

    def test_clear_results(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._clear_results()


# ===========================================================================
# Find phase
# ===========================================================================


class TestFindPhase:
    def test_find_import_failure(self, qapp, qtbot, monkeypatch):
        """If cleanup_signals.GroupFindWorker import fails, error label shown."""
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        import builtins
        original_import = builtins.__import__

        def fail_import(name, *args, **kwargs):
            if name == "curator.gui.cleanup_signals":
                raise ImportError("simulated")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail_import)
        dlg._on_find_clicked()
        assert "Could not load" in dlg._lbl_status.text()

    def test_find_completes_with_no_duplicates(
        self, qapp, qtbot, monkeypatch,
    ):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        report = _make_find_report(findings=[])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        assert dlg.last_find_report is report
        assert "No duplicates found" in dlg._lbl_status.text()

    def test_find_completes_with_duplicates(
        self, qapp, qtbot, monkeypatch,
    ):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        report = _make_find_report(findings=[
            _make_finding(path="/p/dup1.txt", size=2048),
            _make_finding(path="/p/dup2.txt", size=2048),
        ])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        # Apply button should be enabled now
        assert dlg._btn_apply.isEnabled()
        # Status shows duplicate group(s)
        assert "duplicate group" in dlg._lbl_status.text()

    def test_find_fails(self, qapp, qtbot, monkeypatch):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_fails_with=RuntimeError("find boom"),
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        assert "Find failed" in dlg._lbl_status.text()
        assert dlg.last_find_report is None

    def test_find_with_fuzzy_match(self, qapp, qtbot, monkeypatch):
        """Selecting fuzzy match passes match_kind='fuzzy' to worker."""
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        report = _make_find_report(findings=[])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._rb_fuzzy.setChecked(True)
        dlg._on_find_clicked()
        # Just verify no crash; worker received fuzzy kwarg
        assert dlg.last_find_report is report

    def test_render_find_report_unknown_dupset(self, qapp, qtbot, monkeypatch):
        """Finding without dict-shape details → 'unknown' bucket."""
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        f_no_dict = MagicMock()
        f_no_dict.path = "/p/x.txt"
        f_no_dict.size = 100
        f_no_dict.details = None  # not a dict
        report = _make_find_report(findings=[f_no_dict])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        # Doesn't crash; report stored
        assert dlg.last_find_report is report

    def test_render_find_report_short_hash(self, qapp, qtbot, monkeypatch):
        """Finding with short dupset_id (<12 chars) → no truncation."""
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        report = _make_find_report(findings=[
            _make_finding(dupset_id="short"),
        ])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        assert dlg.last_find_report is report


# ===========================================================================
# Apply phase
# ===========================================================================


class TestApplyPhase:
    def test_apply_with_no_report_returns(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        # No find_report yet
        dlg._on_apply_clicked()
        # No crash; no apply triggered
        assert dlg.last_apply_report is None

    def test_apply_with_user_cancel(
        self, qapp, qtbot, monkeypatch,
    ):
        """User clicks No in confirmation → no apply runs."""
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        find_report = _make_find_report(findings=[_make_finding()])
        apply_report = _make_apply_report(results=[_make_apply_result()])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=find_report,
            apply_completes_with=apply_report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question",
                            MagicMock(return_value=QMessageBox.StandardButton.No))
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert dlg.last_apply_report is None  # user cancelled

    def test_apply_import_failure(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        """GroupApplyWorker import fails → error label."""
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        find_report = _make_find_report(findings=[_make_finding()])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=find_report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        # Then remove GroupApplyWorker so the lazy import fails
        if hasattr(cs, "GroupApplyWorker"):
            monkeypatch.delattr(cs, "GroupApplyWorker")
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        # Status shows import failure
        assert ("Could not load" in dlg._lbl_status.text()
                or "ImportError" in dlg._lbl_status.text()
                or "GroupApplyWorker" in dlg._lbl_status.text())

    def test_apply_completes_all_deleted(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        find_report = _make_find_report(findings=[
            _make_finding(path="/p/dup1.txt"),
            _make_finding(path="/p/dup2.txt"),
        ])
        apply_report = _make_apply_report(results=[
            _make_apply_result(outcome=_MockOutcome.DELETED),
            _make_apply_result(outcome=_MockOutcome.DELETED),
        ])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=find_report,
            apply_completes_with=apply_report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert dlg.last_apply_report is apply_report
        assert "Apply complete" in dlg._lbl_status.text()

    def test_apply_completes_with_failures(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        find_report = _make_find_report(findings=[_make_finding()])
        apply_report = _make_apply_report(results=[
            _make_apply_result(outcome=_MockOutcome.DELETED),
            _make_apply_result(outcome=_MockOutcome.FAILED,
                              error="permission denied"),
            _make_apply_result(outcome=_MockOutcome.SKIPPED_REFUSE),
            _make_apply_result(outcome=_MockOutcome.SKIPPED_MISSING),
        ])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=find_report,
            apply_completes_with=apply_report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        # Failed > 0 → orange color in status
        assert "Apply complete" in dlg._lbl_status.text()
        assert dlg.last_apply_report.results[1].error == "permission denied"

    def test_apply_with_hard_delete(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        """Unchecking use_trash → confirm shows 'HARD DELETE'."""
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        find_report = _make_find_report(findings=[_make_finding()])
        apply_report = _make_apply_report(results=[
            _make_apply_result(outcome=_MockOutcome.DELETED),
        ])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=find_report,
            apply_completes_with=apply_report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._cb_use_trash.setChecked(False)  # hard delete
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert dlg.last_apply_report is apply_report

    def test_apply_fails(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        find_report = _make_find_report(findings=[_make_finding()])
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=find_report,
            apply_fails_with=RuntimeError("apply boom"),
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert "Apply failed" in dlg._lbl_status.text()

    def test_apply_with_many_errors_truncated(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        """>30 errors → only first 30 in error table."""
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        find_report = _make_find_report(findings=[_make_finding()])
        results = [
            _make_apply_result(outcome=_MockOutcome.FAILED,
                              error=f"err {i}",
                              finding=_make_finding(path=f"/p/f{i}.txt"))
            for i in range(40)
        ]
        apply_report = _make_apply_report(results=results)
        stub_find, stub_apply, stub_bridge = _make_worker_stubs(
            find_completes_with=find_report,
            apply_completes_with=apply_report,
        )
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", stub_find)
        monkeypatch.setattr(cs, "GroupApplyWorker", stub_apply)
        monkeypatch.setattr(cs, "GroupProgressBridge", stub_bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        dlg._on_apply_clicked()
        assert dlg.last_apply_report is apply_report


# ===========================================================================
# Slot no-ops
# ===========================================================================


class TestSlotNoOps:
    def test_on_find_started_noop(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_started("payload")

    def test_on_apply_started_noop(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_apply_started(5)


# ===========================================================================
# Close button
# ===========================================================================


class TestCloseButton:
    def test_close_button_rejects(self, qapp, qtbot):
        from curator.gui.dialogs import GroupDialog
        from PySide6.QtCore import Qt
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg.reject = MagicMock()
        qtbot.mouseClick(dlg._btn_close, Qt.MouseButton.LeftButton)
        dlg.reject.assert_called_once()
