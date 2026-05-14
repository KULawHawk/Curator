"""Pragma audit close for `curator.gui.dialogs` (v1.7.206).

Round 5 Tier 1 sub-ship 8 (FINAL) — closes the residual gaps via small
targeted tests, leaves genuinely uncoverable lines as `# pragma: no
cover` with documented Lesson #91 justifications.

Targeted gaps:
- CleanupDialog._render_find_report else-fallback (line 2483-2484)
- SourceAddDialog edit-mode prefill for missing field (line 3127, 3135)
- TierDialog._handle_{enter,delete}_shortcut with actual file_ent
- TierDialog._get_selected_file_entities with sel_model=None
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


@pytest.fixture
def silence_qmessagebox(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", MagicMock())
    monkeypatch.setattr(QMessageBox, "warning", MagicMock())
    monkeypatch.setattr(QMessageBox, "critical", MagicMock())
    monkeypatch.setattr(QMessageBox, "question",
                        MagicMock(return_value=QMessageBox.StandardButton.Yes))


def _make_finding(*, path="/p/x", size=1024, details=None):
    f = MagicMock()
    f.path = path
    f.size = size
    f.details = details if details is not None else {}
    return f


# ===========================================================================
# CleanupDialog._render_find_report else-fallback (line 2483-2484)
# ===========================================================================


def _stubs_cleanup():
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

    class _Find(QThread):
        def __init__(self, *, runtime, mode, root, patterns=None,
                     ignore_system_junk=True, bridge, parent=None):
            super().__init__(parent)
            self._bridge = bridge

        def start(self):
            r = MagicMock()
            r.findings = [_make_finding()]
            r.errors = []
            self._bridge.find_completed.emit(r)

        def isRunning(self):
            return False

    class _Apply(QThread):
        def __init__(self, *, runtime, report, use_trash, bridge, parent=None):
            super().__init__(parent)

        def start(self):
            pass

        def isRunning(self):
            return False

    return _Find, _Apply, _Bridge


class TestCleanupRenderElseFallback:
    def test_unknown_mode_renders_else_branch(
        self, qapp, qtbot, monkeypatch, tmp_path,
    ):
        """If current_mode is not junk/empty_dirs/broken_symlinks (only
        reachable by direct mutation), the else branch renders generic
        columns."""
        from curator.gui.dialogs import CleanupDialog
        rt = MagicMock()
        find, apply_w, bridge = _stubs_cleanup()
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "CleanupFindWorker", find)
        monkeypatch.setattr(cs, "CleanupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "CleanupProgressBridge", bridge)
        dlg = CleanupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_path.setText(str(tmp_path))
        # Mutate current_mode to a value that bypasses all 3 if-elif arms
        dlg._current_mode = "unreachable_mode"
        dlg._on_find_clicked()
        # Render the report using the else branch via direct call
        r = MagicMock()
        r.findings = [_make_finding(details="not a dict")]
        r.errors = []
        dlg._render_find_report(r)


# ===========================================================================
# SourceAddDialog edit-mode prefill with missing field (line 3127, 3135)
# ===========================================================================


def _make_plugin_hook_result(plugins):
    results = []
    for plugin in plugins:
        results.append([(k, v) for k, v in plugin.items()])
    return results


class TestSourceAddPrefillMissingField:
    def test_prefill_with_unknown_field_skipped(self, qapp, qtbot):
        """v1.7.40 edit-mode prefill: a key in src.config that doesn't
        exist in the schema is silently skipped (line 3127)."""
        from curator.gui.dialogs import SourceAddDialog
        rt = MagicMock()
        rt.pm.hook.curator_source_register.return_value = _make_plugin_hook_result([
            {
                "source_type": "local",
                "display_name": "Local",
                "config_schema": {
                    "properties": {
                        "root_path": {"type": "string"},
                    },
                    "required": ["root_path"],
                },
            },
        ])
        src = MagicMock()
        src.source_id = "x"
        src.source_type = "local"
        src.display_name = "X"
        src.enabled = True
        src.config = {
            "root_path": "/p",
            "removed_field_from_schema": "stale value",  # → skipped silently
        }
        src.share_visibility = "private"
        src.created_at = datetime(2026, 1, 1)
        dlg = SourceAddDialog(rt, editing_source=src)
        qtbot.addWidget(dlg)
        # Just verify no crash; root_path filled, the stale key skipped
        assert dlg._config_widgets["root_path"].text() == "/p"

    def test_prefill_value_is_none(self, qapp, qtbot):
        """If a config value is None, str-coerce to empty rather than
        crashing on str(None) → 'None'."""
        from curator.gui.dialogs import SourceAddDialog
        rt = MagicMock()
        rt.pm.hook.curator_source_register.return_value = _make_plugin_hook_result([
            {
                "source_type": "local",
                "display_name": "L",
                "config_schema": {
                    "properties": {"root_path": {"type": "string"}},
                    "required": ["root_path"],
                },
            },
        ])
        src = MagicMock()
        src.source_id = "x"
        src.source_type = "local"
        src.display_name = "X"
        src.enabled = True
        src.config = {"root_path": None}
        src.share_visibility = "private"
        src.created_at = datetime(2026, 1, 1)
        dlg = SourceAddDialog(rt, editing_source=src)
        qtbot.addWidget(dlg)
        # None coerced to empty string
        assert dlg._config_widgets["root_path"].text() == ""


# ===========================================================================
# TierDialog._handle_{enter,delete}_shortcut with selected row
# ===========================================================================


def _populate_tier_row(dlg, curator_id, path="/p/x.txt"):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem
    dlg._table.setRowCount(1)
    item = QTableWidgetItem(path)
    item.setData(Qt.ItemDataRole.UserRole, str(curator_id))
    dlg._table.setItem(0, 0, item)
    dlg._table.setCurrentCell(0, 0)


class TestTierShortcutsWithRow:
    def test_enter_shortcut_with_row_invokes_inspect(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        # File found in repo
        file_ent = MagicMock(status="active")
        rt.file_repo.get.return_value = file_ent
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Stub FileInspectDialog to avoid real construction
        from curator.gui import dialogs as dialogs_mod
        stub = MagicMock()
        stub.return_value = MagicMock()
        monkeypatch.setattr(dialogs_mod, "FileInspectDialog", stub)
        _populate_tier_row(dlg, uuid4())
        dlg._handle_enter_shortcut()
        # Inspect dialog stub called
        stub.assert_called_once()

    def test_delete_shortcut_with_row_invokes_trash(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        file_ent = MagicMock(status="active")
        rt.file_repo.get.return_value = file_ent
        # _action_send_to_trash invokes _on_scan_clicked at the tail
        # which formats numeric fields on the tier report — give them
        # real numeric values.
        report = MagicMock()
        report.candidates = []
        report.candidate_count = 0
        report.total_size = 0
        report.duration_seconds = 0.5
        rt.tier.scan.return_value = report
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        _populate_tier_row(dlg, uuid4())
        dlg._handle_delete_shortcut()
        rt.trash.send_to_trash.assert_called_once()


# ===========================================================================
# TierDialog._get_selected_file_entities edge cases (line 4090)
# ===========================================================================


class TestTierGetSelectedEdgeCases:
    def test_with_unresolvable_rows_skipped(self, qapp, qtbot):
        """Rows that fail row→entity resolution are silently dropped."""
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        # file_repo.get returns None for everything
        rt.file_repo.get.return_value = None
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Add 2 rows; both will fail resolution
        dlg._table.setRowCount(2)
        for r in range(2):
            item = QTableWidgetItem(f"/p/{r}")
            item.setData(Qt.ItemDataRole.UserRole, str(uuid4()))
            dlg._table.setItem(r, 0, item)
        dlg._table.selectAll()
        # Resolution returns None for all → empty result
        from PySide6.QtWidgets import QMessageBox
        original_warning = QMessageBox.warning
        QMessageBox.warning = MagicMock()
        try:
            result = dlg._get_selected_file_entities()
        finally:
            QMessageBox.warning = original_warning
        assert result == []

    def test_with_resolved_rows(self, qapp, qtbot):
        """Rows that resolve to FileEntity are returned (covers line 4096)."""
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        # file_repo.get returns a real-ish FileEntity for every call
        file_ent = MagicMock(status="active")
        rt.file_repo.get.return_value = file_ent
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._table.setRowCount(2)
        for r in range(2):
            item = QTableWidgetItem(f"/p/{r}")
            item.setData(Qt.ItemDataRole.UserRole, str(uuid4()))
            dlg._table.setItem(r, 0, item)
        dlg._table.selectAll()
        result = dlg._get_selected_file_entities()
        assert len(result) == 2

    def test_with_sel_model_none(self, qapp, qtbot, monkeypatch):
        """Defensive line 4090: if selectionModel() returns None, return []."""
        from curator.gui.dialogs import TierDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Force selectionModel to return None
        dlg._table.selectionModel = MagicMock(return_value=None)
        assert dlg._get_selected_file_entities() == []


# ===========================================================================
# VersionStackDialog backslash path basename (line 3278-3281)
# ===========================================================================


class TestVersionStackBackslashBasename:
    def test_stack_with_backslash_path(self, qapp, qtbot):
        """Stack with backslash-separated path → basename extracted via chr(92) split."""
        from curator.gui.dialogs import VersionStackDialog
        f1 = MagicMock()
        f1.source_path = r"C:\Users\jmlee\file.txt"
        f1.size = 1024
        f1.mtime = datetime(2026, 1, 1)
        f1.file_type = "text"
        rt = MagicMock()
        rt.lineage.find_version_stacks.return_value = [[f1]]
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        # Just verify no crash — basename was extracted via the
        # chr(92).split path

    def test_clear_stacks_display_with_existing_widgets(self, qapp, qtbot):
        """_clear_stacks_display loop body (lines 3278-3281) only runs
        when there are pre-existing widgets in the layout."""
        from curator.gui.dialogs import VersionStackDialog
        f1 = MagicMock()
        f1.source_path = "/p/x.txt"
        f1.size = 1024
        f1.mtime = datetime(2026, 1, 1)
        f1.file_type = "text"
        rt = MagicMock()
        rt.lineage.find_version_stacks.return_value = [[f1]]
        dlg = VersionStackDialog(rt)
        qtbot.addWidget(dlg)
        # First refresh added a group widget; second refresh clears it
        dlg._refresh_stacks()


# ===========================================================================
# SourceAddDialog QPlainTextEdit prefill with non-list (line 3135)
# ===========================================================================


class TestSourceAddPrefillScalarToQPlainText:
    def test_prefill_array_field_with_scalar_value(self, qapp, qtbot):
        """If a config field is array-typed but stored as scalar (e.g.
        config corruption), the prefill str()-coerces it (line 3135)."""
        from curator.gui.dialogs import SourceAddDialog
        rt = MagicMock()
        rt.pm.hook.curator_source_register.return_value = _make_plugin_hook_result([
            {
                "source_type": "x",
                "display_name": "X",
                "config_schema": {
                    "properties": {"tags": {"type": "array"}},
                },
            },
        ])
        src = MagicMock()
        src.source_id = "x_src"
        src.source_type = "x"
        src.display_name = "X Src"
        src.enabled = True
        src.config = {"tags": "scalar_not_list"}  # corrupt: array stored as string
        src.share_visibility = "private"
        src.created_at = datetime(2026, 1, 1)
        dlg = SourceAddDialog(rt, editing_source=src)
        qtbot.addWidget(dlg)
        # QPlainTextEdit text is the str-coerced scalar
        assert dlg._config_widgets["tags"].toPlainText() == "scalar_not_list"


# ===========================================================================
# GroupDialog cell mutations (line 1947, partial branches)
# ===========================================================================


def _make_finding_for_group(*, path="/p/dup.txt", size=1024,
                            dupset_id="abc", kept_path="/p/keep.txt",
                            kept_reason="shortest_path"):
    f = MagicMock()
    f.path = path
    f.size = size
    f.details = {
        "dupset_id": dupset_id,
        "kept_path": kept_path,
        "kept_reason": kept_reason,
    }
    return f


def _stubs_group(find_completes=None):
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

    class _Find(QThread):
        def __init__(self, *, runtime, source_id, root_prefix,
                     keep_strategy, keep_under, match_kind,
                     similarity_threshold, bridge, parent=None):
            super().__init__(parent)
            self._bridge = bridge

        def start(self):
            if find_completes is not None:
                self._bridge.find_completed.emit(find_completes)

        def isRunning(self):
            return False

    class _Apply(QThread):
        def __init__(self, *, runtime, report, use_trash, bridge, parent=None):
            super().__init__(parent)

        def start(self):
            pass

        def isRunning(self):
            return False

    return _Find, _Apply, _Bridge


class TestGroupCellMutations:
    def test_render_with_duplicate_rows_cell_mutation(
        self, qapp, qtbot, monkeypatch,
    ):
        """Cells in rows with status='duplicate' get yellow foreground
        (line 1947 + partial branches 1951/1956/1959)."""
        from curator.gui.dialogs import GroupDialog
        rt = MagicMock()
        rt.source_repo.list_all.return_value = []
        # Build a report with 2 findings sharing the same dupset_id
        report = MagicMock()
        report.findings = [
            _make_finding_for_group(path="/p/dup1.txt", size=1024,
                                     dupset_id="abc",
                                     kept_path="/p/keep.txt"),
            _make_finding_for_group(path="/p/dup2.txt", size=1024,
                                     dupset_id="abc",
                                     kept_path="/p/keep.txt"),
        ]
        find, apply_w, bridge = _stubs_group(find_completes=report)
        import curator.gui.cleanup_signals as cs
        monkeypatch.setattr(cs, "GroupFindWorker", find)
        monkeypatch.setattr(cs, "GroupApplyWorker", apply_w)
        monkeypatch.setattr(cs, "GroupProgressBridge", bridge)
        dlg = GroupDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_find_clicked()
        # Renders: 1 KEEPER row + 2 duplicate rows = 3 rows
        # Cell foreground/font set per row → exercises lines 1944-1960

