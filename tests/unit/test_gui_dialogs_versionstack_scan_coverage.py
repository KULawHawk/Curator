"""Coverage for VersionStackDialog + ScanDialog (v1.7.199).

Round 5 Tier 1 sub-ship 1 of 8 — first dialog ship in Round 5. Covers
two dialogs:

* ``VersionStackDialog`` (read-only viewer; lines 3167-3373; ~130 stmts)
* ``ScanDialog`` (worker-spawning dialog; lines 1230-1600; ~225 stmts)

ScanDialog uses the ``_SyncWorker`` pattern from Round 4 Tier 3 — stubs
``ScanWorker`` + ``ScanProgressBridge`` so tests stay synchronous and
deterministic.
"""

from __future__ import annotations

import os
from datetime import datetime
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


# ===========================================================================
# VersionStackDialog
# ===========================================================================


def _make_file(*, path="/p/file.txt", size=1024, mtime=None, file_type="text"):
    f = MagicMock()
    f.source_path = path
    f.size = size
    f.mtime = mtime or datetime(2026, 5, 1, 12, 0)
    f.file_type = file_type
    return f


class TestVersionStackDialog:
    def test_basic_construction_no_stacks(self, qapp, qtbot):
        from curator.gui.dialogs import VersionStackDialog
        rt = MagicMock()
        rt.lineage.find_version_stacks.return_value = []
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        assert "Version stacks" in dlg.windowTitle()
        assert dlg.last_stacks == []

    def test_construction_with_stacks(self, qapp, qtbot):
        from curator.gui.dialogs import VersionStackDialog
        rt = MagicMock()
        stack1 = [_make_file(path="/a/newest.txt"), _make_file(path="/a/older.txt")]
        rt.lineage.find_version_stacks.return_value = [stack1]
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg.last_stacks == [stack1]

    def test_refresh_no_kinds_picked_shows_error(self, qapp, qtbot):
        """Uncheck both kind checkboxes → status text shows error."""
        from curator.gui.dialogs import VersionStackDialog
        rt = MagicMock()
        rt.lineage.find_version_stacks.return_value = []
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        dlg._cb_near_dup.setChecked(False)
        dlg._cb_version_of.setChecked(False)
        dlg._refresh_stacks()
        assert "Pick at least one edge kind" in dlg._lbl_status.text()

    def test_refresh_exception_shows_error(self, qapp, qtbot):
        from curator.gui.dialogs import VersionStackDialog
        rt = MagicMock()
        rt.lineage.find_version_stacks.side_effect = RuntimeError("lineage down")
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        assert "Error:" in dlg._lbl_status.text() or "RuntimeError" in dlg._lbl_status.text()

    def test_render_zero_stacks_shows_no_stacks_found(self, qapp, qtbot):
        from curator.gui.dialogs import VersionStackDialog
        rt = MagicMock()
        rt.lineage.find_version_stacks.return_value = []
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        # Status text should mention "No stacks found"
        assert "No stacks found" in dlg._lbl_status.text()

    def test_render_populated_shows_summary(self, qapp, qtbot):
        from curator.gui.dialogs import VersionStackDialog
        rt = MagicMock()
        s1 = [_make_file(path="/a/x.txt"), _make_file(path="/a/y.txt")]
        s2 = [_make_file(path="/b/x.txt")]
        rt.lineage.find_version_stacks.return_value = [s1, s2]
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        # Status text mentions 2 stacks containing 3 files
        text = dlg._lbl_status.text()
        assert "2 stack" in text
        assert "3" in text

    def test_refresh_button_triggers_re_query(self, qapp, qtbot):
        from curator.gui.dialogs import VersionStackDialog
        from PySide6.QtCore import Qt
        rt = MagicMock()
        rt.lineage.find_version_stacks.return_value = []
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        rt.lineage.find_version_stacks.reset_mock()
        qtbot.mouseClick(dlg._btn_refresh, Qt.MouseButton.LeftButton)
        assert rt.lineage.find_version_stacks.call_count >= 1

    def test_close_button_rejects(self, qapp, qtbot):
        from curator.gui.dialogs import VersionStackDialog
        from PySide6.QtCore import Qt
        rt = MagicMock()
        rt.lineage.find_version_stacks.return_value = []
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        dlg.reject = MagicMock()
        qtbot.mouseClick(dlg._btn_close, Qt.MouseButton.LeftButton)
        dlg.reject.assert_called_once()

    def test_confidence_value_passed(self, qapp, qtbot):
        """Confidence spin-box value passed to find_version_stacks."""
        from curator.gui.dialogs import VersionStackDialog
        rt = MagicMock()
        rt.lineage.find_version_stacks.return_value = []
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        dlg._sp_confidence.setValue(0.85)
        dlg._refresh_stacks()
        # Last call kwargs include min_confidence=0.85
        kwargs = rt.lineage.find_version_stacks.call_args.kwargs
        assert kwargs["min_confidence"] == 0.85

    def test_kind_filter_near_duplicate_only(self, qapp, qtbot):
        from curator.gui.dialogs import VersionStackDialog
        from curator.models.lineage import LineageKind
        rt = MagicMock()
        rt.lineage.find_version_stacks.return_value = []
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        dlg._cb_version_of.setChecked(False)
        dlg._refresh_stacks()
        kinds = rt.lineage.find_version_stacks.call_args.kwargs["kinds"]
        assert LineageKind.NEAR_DUPLICATE in kinds
        assert LineageKind.VERSION_OF not in kinds


# ===========================================================================
# ScanDialog
# ===========================================================================


def _make_source(*, source_id="local", source_type="local"):
    s = MagicMock()
    s.source_id = source_id
    s.source_type = source_type
    return s


def _make_scan_report(*, files_seen=10, files_new=5, files_updated=2,
                     errors=0, error_paths=None):
    r = MagicMock()
    r.job_id = uuid4()
    r.source_id = "local"
    r.root = "/r"
    r.started_at = datetime(2026, 5, 1, 10, 0)
    r.completed_at = datetime(2026, 5, 1, 10, 5)
    r.files_seen = files_seen
    r.files_new = files_new
    r.files_updated = files_updated
    r.files_unchanged = 3
    r.files_hashed = 8
    r.cache_hits = 2
    r.bytes_read = 4096
    r.fuzzy_hashes_computed = 1
    r.classifications_assigned = 3
    r.lineage_edges_created = 0
    r.files_deleted = 0
    r.errors = errors
    r.error_paths = error_paths or []
    return r


def _make_sync_worker_classes(scan_completes_with=None, scan_fails_with=None):
    """Build stub ScanWorker + ScanProgressBridge classes that fire signals
    synchronously when .start() is called.

    Pass scan_completes_with=<report> to fire scan_completed.
    Pass scan_fails_with=<exception> to fire scan_failed.
    """
    from PySide6.QtCore import QObject, Signal, QThread

    class _StubBridge(QObject):
        scan_started = Signal(object)
        scan_completed = Signal(object)
        scan_failed = Signal(object)
        scan_progress = Signal(object)

        def __init__(self, parent=None):
            super().__init__(parent)

    class _StubWorker(QThread):
        def __init__(self, *, runtime, source_id, root, options, bridge, parent=None):
            super().__init__(parent)
            self._bridge = bridge
            self._sid = source_id
            self._root = root

        def start(self):
            # Synchronously fire signals — bypass QThread machinery
            self._bridge.scan_started.emit((self._sid, self._root))
            if scan_fails_with is not None:
                self._bridge.scan_failed.emit(scan_fails_with)
            elif scan_completes_with is not None:
                self._bridge.scan_completed.emit(scan_completes_with)

        def isRunning(self):
            return False

    return _StubWorker, _StubBridge


class TestScanDialogConstruction:
    def test_basic_construction_no_sources(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        assert "Scan folder" in dlg.windowTitle()
        # Empty sources → dropdown disabled + "no sources" label
        assert not dlg._cb_source.isEnabled()

    def test_construction_with_sources(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [
            _make_source(source_id="local"),
            _make_source(source_id="gdrive", source_type="gdrive"),
        ]
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg._cb_source.count() == 2

    def test_source_list_exception_disables_dropdown(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.side_effect = RuntimeError("db gone")
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        assert not dlg._cb_source.isEnabled()
        # Error text in the first item
        assert "error" in dlg._cb_source.itemText(0).lower()


class TestScanDialogState:
    def test_selected_source_id_when_disabled(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg._selected_source_id() is None

    def test_selected_source_id_with_valid_selection(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg._selected_source_id() == "local"

    def test_selected_source_id_non_string_data(self, qapp, qtbot):
        """If currentData() returns non-string, returns None."""
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        # Add an item with no data; select it
        dlg._cb_source.addItem("(no data)", None)
        dlg._cb_source.setCurrentIndex(dlg._cb_source.count() - 1)
        assert dlg._selected_source_id() is None

    def test_scan_disabled_initially(self, qapp, qtbot, tmp_path):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        # No path set yet → button disabled
        assert not dlg._btn_scan.isEnabled()

    def test_scan_enabled_when_path_and_source_set(
        self, qapp, qtbot, tmp_path,
    ):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._update_scan_enabled()
        assert dlg._btn_scan.isEnabled()


class TestScanDialogBrowse:
    def test_browse_clicked_no_path_uses_home(
        self, qapp, qtbot, monkeypatch,
    ):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QFileDialog
        # Monkeypatch the static getExistingDirectory
        called_with = {}

        def stub_getExistingDirectory(parent, title, start_dir):
            called_with["start_dir"] = start_dir
            return ""

        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory",
            stub_getExistingDirectory,
        )
        dlg._on_browse_clicked()
        # Home dir used as starting point
        assert called_with["start_dir"] == str(Path.home())

    def test_browse_clicked_existing_path_used_as_start(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        from PySide6.QtWidgets import QFileDialog
        called_with = {}

        def stub_getExistingDirectory(parent, title, start_dir):
            called_with["start_dir"] = start_dir
            return ""

        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory",
            stub_getExistingDirectory,
        )
        dlg._on_browse_clicked()
        assert called_with["start_dir"] == str(tmp_path)

    def test_browse_clicked_with_chosen_updates_path(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        chosen = str(tmp_path / "subdir")
        from PySide6.QtWidgets import QFileDialog
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory",
            lambda *a, **kw: chosen,
        )
        dlg._on_browse_clicked()
        assert dlg._le_path.text() == chosen


class TestScanDialogScanFlow:
    def test_scan_click_with_no_sid_short_circuits(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []  # no sources
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_scan_clicked()  # should return early
        assert dlg._worker is None

    def test_scan_click_with_no_path_short_circuits(
        self, qapp, qtbot, tmp_path,
    ):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        # No path set
        dlg._on_scan_clicked()
        assert dlg._worker is None

    def test_scan_completes_renders_report(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui import dialogs as dialogs_mod
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]

        report = _make_scan_report()
        stub_worker, stub_bridge = _make_sync_worker_classes(
            scan_completes_with=report,
        )
        # Patch the import at the call site
        import curator.gui.scan_signals as scan_signals
        monkeypatch.setattr(scan_signals, "ScanWorker", stub_worker)
        monkeypatch.setattr(scan_signals, "ScanProgressBridge", stub_bridge)

        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_scan_clicked()
        # Last report stored
        assert dlg.last_report is report
        # Status updated
        assert "Scan complete" in dlg._lbl_status.text()

    def test_scan_failed_shows_error(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]

        stub_worker, stub_bridge = _make_sync_worker_classes(
            scan_fails_with=RuntimeError("scan boom"),
        )
        import curator.gui.scan_signals as scan_signals
        monkeypatch.setattr(scan_signals, "ScanWorker", stub_worker)
        monkeypatch.setattr(scan_signals, "ScanProgressBridge", stub_bridge)

        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_scan_clicked()
        assert "Scan failed" in dlg._lbl_status.text()
        # Last report NOT set
        assert dlg.last_report is None

    def test_scan_worker_import_failure(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]

        # Make the inner `from curator.gui.scan_signals import ...` raise
        import sys
        # Remove the module so the import inside _on_scan_clicked refetches
        import builtins
        original_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            if name == "curator.gui.scan_signals":
                raise ImportError("simulated")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", failing_import)

        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_scan_clicked()
        assert "Could not load ScanWorker" in dlg._lbl_status.text()

    def test_scan_complete_with_errors_renders_red(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """ScanReport.errors > 0 → Errors cell colored red + bold."""
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]
        report = _make_scan_report(errors=3, error_paths=["/p/a", "/p/b", "/p/c"])
        stub_worker, stub_bridge = _make_sync_worker_classes(
            scan_completes_with=report,
        )
        import curator.gui.scan_signals as scan_signals
        monkeypatch.setattr(scan_signals, "ScanWorker", stub_worker)
        monkeypatch.setattr(scan_signals, "ScanProgressBridge", stub_bridge)

        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_scan_clicked()
        assert dlg.last_report is report

    def test_scan_complete_with_many_error_paths_truncates(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """error_paths with >50 entries shows truncation message."""
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]
        report = _make_scan_report(
            errors=60,
            error_paths=[f"/p/{i}" for i in range(60)],
        )
        stub_worker, stub_bridge = _make_sync_worker_classes(
            scan_completes_with=report,
        )
        import curator.gui.scan_signals as scan_signals
        monkeypatch.setattr(scan_signals, "ScanWorker", stub_worker)
        monkeypatch.setattr(scan_signals, "ScanProgressBridge", stub_bridge)

        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        dlg._on_scan_clicked()
        assert dlg.last_report is report

    def test_close_button_rejects(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        from PySide6.QtCore import Qt
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg.reject = MagicMock()
        qtbot.mouseClick(dlg._btn_close, Qt.MouseButton.LeftButton)
        dlg.reject.assert_called_once()


class TestScanDialogProgressHelpers:
    def test_set_indeterminate_on(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._set_indeterminate(True)
        assert dlg._progress.minimum() == 0
        assert dlg._progress.maximum() == 0

    def test_set_indeterminate_off(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._set_indeterminate(False)
        # Format cleared
        assert dlg._progress.format() == ""

    def test_clear_results_replaces_content(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._clear_results()  # should not raise

    def test_on_scan_started_slot(self, qapp, qtbot):
        """The started slot is a no-op (pass) — just verify it doesn't raise."""
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_scan_started(("local", "/r"))  # noqa

    def test_reenable_controls_no_sources(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        # Source dropdown is in "no sources" state; _reenable_controls
        # should NOT re-enable it
        dlg._reenable_controls()
        assert not dlg._cb_source.isEnabled()

    def test_reenable_controls_normal_source(self, qapp, qtbot):
        from curator.gui.dialogs import ScanDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = [_make_source(source_id="local")]
        dlg = ScanDialog(rt)
        qtbot.addWidget(dlg)
        # Disable then re-enable
        dlg._cb_source.setEnabled(False)
        dlg._reenable_controls()
        assert dlg._cb_source.isEnabled()
