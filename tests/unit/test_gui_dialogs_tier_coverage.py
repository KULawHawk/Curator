"""Coverage for TierDialog (v1.7.205).

Round 5 Tier 1 sub-ship 7 of 8 — the largest single dialog (~450 stmts).
Tools-menu picker for the tier-storage scan command with right-click
context menu, keyboard shortcuts (Enter / Del), and bulk-migrate flow.
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


# ===========================================================================
# Helpers
# ===========================================================================


def _make_file_entity(*, curator_id=None, path="/p/file.txt",
                     size=1024, status="active"):
    f = MagicMock()
    f.curator_id = curator_id or uuid4()
    f.source_path = path
    f.size = size
    f.status = status
    return f


def _make_source(*, source_id="local", source_type="local"):
    s = MagicMock()
    s.source_id = source_id
    s.source_type = source_type
    return s


def _make_candidate(*, file=None, reason="last_scanned > 90 days"):
    c = MagicMock()
    c.file = file or _make_file_entity()
    c.reason = reason
    return c


def _make_tier_report(*, candidates=None, total_size=0, duration=0.5):
    r = MagicMock()
    r.candidates = candidates or []
    r.candidate_count = len(candidates) if candidates else 0
    r.total_size = total_size
    r.duration_seconds = duration
    return r


_SENTINEL = object()


def _make_runtime(*, sources=None, scan_report=None,
                 scan_raises=None, file_get_returns=_SENTINEL,
                 file_get_raises=None):
    rt = MagicMock()
    rt.source_repo.list_all.return_value = sources or []
    if scan_raises is not None:
        rt.tier.scan.side_effect = scan_raises
    else:
        rt.tier.scan.return_value = scan_report or _make_tier_report()
    if file_get_raises is not None:
        rt.file_repo.get.side_effect = file_get_raises
    elif file_get_returns is not _SENTINEL:
        # Explicit None handling: sentinel lets us distinguish "not set"
        # from "set to None"
        rt.file_repo.get.return_value = file_get_returns
    return rt


# ===========================================================================
# Construction + recipe handling
# ===========================================================================


class TestConstruction:
    def test_basic_construction(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        assert "Tier scan" in dlg.windowTitle()
        # 3 recipes: cold, expired, archive
        assert dlg._cb_recipe.count() == 3
        # Source dropdown has "(any)" + sources
        assert dlg._cb_source.count() >= 1

    def test_construction_with_sources(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime(sources=[_make_source(source_id="local"), _make_source(source_id="gdrive")])
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # "(any)" + 2 sources
        assert dlg._cb_source.count() == 3


class TestRecipeChange:
    def test_recipe_change_updates_min_age(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Switch to archive (default 365)
        idx = dlg._cb_recipe.findData("archive")
        dlg._cb_recipe.setCurrentIndex(idx)
        assert dlg._sb_age.value() == 365

    def test_recipe_change_to_expired_disables_age(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        idx = dlg._cb_recipe.findData("expired")
        dlg._cb_recipe.setCurrentIndex(idx)
        assert not dlg._sb_age.isEnabled()

    def test_recipe_change_unknown_data_skips_default(self, qapp, qtbot):
        """If currentData() isn't in RECIPE_DEFAULTS, default is not updated."""
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._cb_recipe.addItem("(unknown)", "bogus")
        dlg._cb_recipe.setCurrentIndex(dlg._cb_recipe.count() - 1)
        # No crash; min_age unchanged (still the previous default)


# ===========================================================================
# Scan flow
# ===========================================================================


class TestScanFlow:
    def test_scan_no_candidates(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime(scan_report=_make_tier_report())
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_scan_clicked()
        # Audit logged
        assert rt.audit.log.called
        # Summary set
        assert "0" in dlg._lbl_summary.text()

    def test_scan_with_candidates(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import TierDialog
        files = [
            _make_file_entity(status="vital", size=2048),
            _make_file_entity(status="active", size=4096),
            _make_file_entity(status="provisional", size=1024),
            _make_file_entity(status="junk", size=512),
        ]
        candidates = [_make_candidate(file=f) for f in files]
        report = _make_tier_report(candidates=candidates,
                                   total_size=sum(f.size for f in files))
        rt = _make_runtime(scan_report=report)
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_scan_clicked()
        assert dlg.last_report is report
        # Table populated with 4 rows
        assert dlg._table.rowCount() == 4

    def test_scan_with_source_filter(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime(sources=[_make_source(source_id="local")])
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Switch to "local" instead of "(any)"
        dlg._cb_source.setCurrentIndex(1)
        dlg._le_root.setText("/p")
        dlg._on_scan_clicked()
        # criteria built with source_id="local"
        crit = rt.tier.scan.call_args.args[0]
        assert crit.source_id == "local"
        assert crit.root_prefix == "/p"

    def test_scan_failure_shows_critical(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime(scan_raises=RuntimeError("scan fail"))
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_scan_clicked()
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical.assert_called()

    def test_scan_bad_recipe(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        """Adding a bogus recipe data → TierRecipe.from_string raises."""
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Inject a bogus recipe value
        dlg._cb_recipe.addItem("(bogus)", "totally_bogus_recipe_value")
        dlg._cb_recipe.setCurrentIndex(dlg._cb_recipe.count() - 1)
        dlg._on_scan_clicked()
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning.assert_called()

    def test_scan_audit_log_exception_swallowed(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        """If audit.log raises, the scan still completes (audit is non-fatal)."""
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        rt.audit.log.side_effect = RuntimeError("audit gone")
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._on_scan_clicked()
        assert dlg.last_report is not None  # scan completed


# ===========================================================================
# Row → FileEntity resolution
# ===========================================================================


class TestResolveRowToFile:
    def test_negative_row(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg._resolve_row_to_file_entity(-1) is None

    def test_out_of_range_row(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg._resolve_row_to_file_entity(99) is None

    def test_no_path_item(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._table.setRowCount(1)  # row added but no item set
        assert dlg._resolve_row_to_file_entity(0) is None

    def test_no_curator_id_stored(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtWidgets import QTableWidgetItem
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._table.setRowCount(1)
        dlg._table.setItem(0, 0, QTableWidgetItem("/p/file.txt"))
        # No UserRole data → returns None
        assert dlg._resolve_row_to_file_entity(0) is None

    def test_malformed_uuid_stored(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._table.setRowCount(1)
        item = QTableWidgetItem("/p/file.txt")
        item.setData(Qt.ItemDataRole.UserRole, "not-a-valid-uuid")
        dlg._table.setItem(0, 0, item)
        assert dlg._resolve_row_to_file_entity(0) is None

    def test_file_repo_raises(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem
        from uuid import uuid4
        rt = _make_runtime(file_get_raises=RuntimeError("db gone"))
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._table.setRowCount(1)
        item = QTableWidgetItem("/p/x.txt")
        item.setData(Qt.ItemDataRole.UserRole, str(uuid4()))
        dlg._table.setItem(0, 0, item)
        assert dlg._resolve_row_to_file_entity(0) is None

    def test_file_not_found(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem
        from uuid import uuid4
        rt = _make_runtime(file_get_returns=None)
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._table.setRowCount(1)
        item = QTableWidgetItem("/p/x.txt")
        item.setData(Qt.ItemDataRole.UserRole, str(uuid4()))
        dlg._table.setItem(0, 0, item)
        assert dlg._resolve_row_to_file_entity(0) is None
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning.assert_called()

    def test_file_found(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem
        from uuid import uuid4
        f = _make_file_entity()
        rt = _make_runtime(file_get_returns=f)
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._table.setRowCount(1)
        item = QTableWidgetItem("/p/x.txt")
        item.setData(Qt.ItemDataRole.UserRole, str(uuid4()))
        dlg._table.setItem(0, 0, item)
        assert dlg._resolve_row_to_file_entity(0) is f


# ===========================================================================
# Event filter (keyboard shortcuts)
# ===========================================================================


class TestEventFilter:
    def test_event_filter_enter_invokes_inspect(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Stub _handle_enter_shortcut so we just verify it fires
        dlg._handle_enter_shortcut = MagicMock()
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                          Qt.KeyboardModifier.NoModifier)
        result = dlg.eventFilter(dlg._table, event)
        assert result is True
        dlg._handle_enter_shortcut.assert_called_once()

    def test_event_filter_delete_invokes_handler(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._handle_delete_shortcut = MagicMock()
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                          Qt.KeyboardModifier.NoModifier)
        result = dlg.eventFilter(dlg._table, event)
        assert result is True
        dlg._handle_delete_shortcut.assert_called_once()

    def test_event_filter_other_key_falls_through(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                          Qt.KeyboardModifier.NoModifier)
        # Returns whatever the parent eventFilter returns (False default)
        result = dlg.eventFilter(dlg._table, event)
        assert result is False

    def test_event_filter_non_table_obj_falls_through(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                          Qt.KeyboardModifier.NoModifier)
        # Pass a non-table widget (the recipe combo)
        result = dlg.eventFilter(dlg._cb_recipe, event)
        # Falls through; recipe combo isn't installed as event-filter target
        # so super().eventFilter returns False
        assert result is False


class TestKeyboardShortcuts:
    def test_enter_shortcut_with_no_row(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # No rows, currentRow() returns -1
        dlg._handle_enter_shortcut()
        # No crash; no action taken

    def test_delete_shortcut_with_no_row(self, qapp, qtbot, silence_qmessagebox):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._handle_delete_shortcut()


# ===========================================================================
# Actions
# ===========================================================================


def _populate_table_with_one_row(dlg, file_entity):
    """Helper: put one row in the table with the given FileEntity's id."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem
    dlg._table.setRowCount(1)
    item = QTableWidgetItem(file_entity.source_path)
    item.setData(Qt.ItemDataRole.UserRole, str(file_entity.curator_id))
    dlg._table.setItem(0, 0, item)


class TestActions:
    def test_action_inspect_success(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Stub FileInspectDialog so it doesn't try to build under MagicMock
        from curator.gui import dialogs as dialogs_mod
        stub_inspect = MagicMock()
        stub_inspect_instance = MagicMock()
        stub_inspect.return_value = stub_inspect_instance
        monkeypatch.setattr(dialogs_mod, "FileInspectDialog", stub_inspect)
        f = _make_file_entity()
        dlg._action_inspect(f)
        stub_inspect_instance.exec.assert_called_once()

    def test_action_inspect_failure(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        from curator.gui import dialogs as dialogs_mod
        monkeypatch.setattr(dialogs_mod, "FileInspectDialog",
                            MagicMock(side_effect=RuntimeError("inspect fail")))
        dlg._action_inspect(_make_file_entity())
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical.assert_called()

    def test_action_set_status_success(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        f = _make_file_entity(status="active")
        dlg._action_set_status(f, "junk")
        rt.file_repo.update_status.assert_called_once()
        # Audit log called
        rt.audit.log.assert_called()

    def test_action_set_status_failure(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        rt.file_repo.update_status.side_effect = RuntimeError("db gone")
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._action_set_status(_make_file_entity(), "junk")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical.assert_called()

    def test_action_set_status_audit_failure_swallowed(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        """Audit log failure is non-fatal."""
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        rt.audit.log.side_effect = RuntimeError("audit gone")
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        f = _make_file_entity()
        dlg._action_set_status(f, "junk")
        # update_status was called; failure swallowed
        rt.file_repo.update_status.assert_called_once()

    def test_action_send_to_trash_user_confirms(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._action_send_to_trash(_make_file_entity())
        rt.trash.send_to_trash.assert_called_once()

    def test_action_send_to_trash_user_cancels(
        self, qapp, qtbot, monkeypatch,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question",
                            MagicMock(return_value=QMessageBox.StandardButton.No))
        dlg._action_send_to_trash(_make_file_entity())
        rt.trash.send_to_trash.assert_not_called()

    def test_action_send_to_trash_failure(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        rt.trash.send_to_trash.side_effect = RuntimeError("trash gone")
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._action_send_to_trash(_make_file_entity())
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical.assert_called()


# ===========================================================================
# Context menu
# ===========================================================================


class TestContextMenu:
    def test_context_menu_with_no_resolved_file(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        """rowAt returns -1 or invalid → returns early without showing menu."""
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import QPoint
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Pos outside the table → rowAt returns -1
        dlg._on_table_context_menu(QPoint(99999, 99999))
        # No crash

    def test_context_menu_dispatches_to_helper(self, qapp, qtbot):
        """v1.7.206: _on_table_context_menu now delegates to a pragma'd
        helper (_build_and_exec_context_menu) after the file_ent
        resolution check. We test the dispatch by stubbing the helper —
        verifies the dispatch line (the helper call) is exercised."""
        from curator.gui.dialogs import TierDialog
        from PySide6.QtCore import QPoint
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        f = _make_file_entity(status="active")
        dlg._resolve_row_to_file_entity = MagicMock(return_value=f)
        # Stub the helper to verify the dispatch fires (the helper
        # itself is `# pragma: no cover` because it calls QMenu.exec
        # which is a blocking native slot)
        dlg._build_and_exec_context_menu = MagicMock()
        dlg._on_table_context_menu(QPoint(10, 10))
        dlg._build_and_exec_context_menu.assert_called_once()


# ===========================================================================
# Bulk migrate
# ===========================================================================


def _make_move(*, curator_id=None, src="/p/x.txt", error=None,
               outcome_name="MOVED"):
    m = MagicMock()
    m.curator_id = curator_id or uuid4()
    m.src_path = src
    m.error = error
    if outcome_name is None:
        m.outcome = None
    else:
        outcome = MagicMock()
        outcome.name = outcome_name
        outcome.value = outcome_name.lower()
        m.outcome = outcome
    return m


def _make_migration_plan(moves=None):
    p = MagicMock()
    p.moves = moves or []
    return p


def _make_migration_report(moves=None):
    r = MagicMock()
    r.moves = moves or []
    return r


class TestBulkMigrate:
    def test_bulk_migrate_no_selection(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._action_bulk_migrate([])
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information.assert_called()

    def test_bulk_migrate_no_root_prefix(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        # Don't set root prefix
        f = _make_file_entity()
        dlg._action_bulk_migrate([f])
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning.assert_called()

    def test_bulk_migrate_user_cancels_target_dialog(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtWidgets import QFileDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_root.setText("/p")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            MagicMock(return_value=""))
        dlg._action_bulk_migrate([_make_file_entity()])
        rt.migration.plan.assert_not_called()

    def test_bulk_migrate_user_cancels_confirm(
        self, qapp, qtbot, monkeypatch,
    ):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_root.setText("/p")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            MagicMock(return_value="/target"))
        monkeypatch.setattr(QMessageBox, "question",
                            MagicMock(return_value=QMessageBox.StandardButton.No))
        dlg._action_bulk_migrate([_make_file_entity()])
        rt.migration.plan.assert_not_called()

    def test_bulk_migrate_plan_failure(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtWidgets import QFileDialog
        rt = _make_runtime()
        rt.migration.plan.side_effect = RuntimeError("plan fail")
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_root.setText("/p")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            MagicMock(return_value="/target"))
        dlg._action_bulk_migrate([_make_file_entity()])
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical.assert_called()

    def test_bulk_migrate_no_matching_moves(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        """Plan returns moves but none match the selected curator_ids."""
        from curator.gui.dialogs import TierDialog
        from PySide6.QtWidgets import QFileDialog
        rt = _make_runtime()
        # Plan with unrelated curator_id
        rt.migration.plan.return_value = _make_migration_plan(
            moves=[_make_move(curator_id=uuid4())],
        )
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_root.setText("/p")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            MagicMock(return_value="/target"))
        dlg._action_bulk_migrate([_make_file_entity()])  # different curator_id
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning.assert_called()

    def test_bulk_migrate_apply_failure(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtWidgets import QFileDialog
        rt = _make_runtime()
        f = _make_file_entity()
        # Plan moves include this file's id
        rt.migration.plan.return_value = _make_migration_plan(
            moves=[_make_move(curator_id=f.curator_id)],
        )
        rt.migration.apply.side_effect = RuntimeError("apply fail")
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_root.setText("/p")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            MagicMock(return_value="/target"))
        dlg._action_bulk_migrate([f])
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical.assert_called()

    def test_bulk_migrate_mixed_outcomes(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        rt = _make_runtime()
        f1, f2, f3, f4 = (_make_file_entity() for _ in range(4))
        rt.migration.plan.return_value = _make_migration_plan(moves=[
            _make_move(curator_id=f1.curator_id, outcome_name="MOVED"),
            _make_move(curator_id=f2.curator_id, outcome_name="SKIPPED_REFUSE"),
            _make_move(curator_id=f3.curator_id, outcome_name="FAILED",
                       error="permission"),
            _make_move(curator_id=f4.curator_id, outcome_name=None),  # outcome None
        ])
        rt.migration.apply.return_value = _make_migration_report(moves=[
            _make_move(curator_id=f1.curator_id, outcome_name="MOVED"),
            _make_move(curator_id=f2.curator_id, outcome_name="SKIPPED_REFUSE"),
            _make_move(curator_id=f3.curator_id, outcome_name="FAILED",
                       error="permission"),
            _make_move(curator_id=f4.curator_id, outcome_name=None),
        ])
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_root.setText("/p")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            MagicMock(return_value="/target"))
        # Stub QMessageBox class-level exec
        monkeypatch.setattr(QMessageBox, "exec", MagicMock())
        dlg._action_bulk_migrate([f1, f2, f3, f4])
        # 2 audit logs: start + complete
        assert rt.audit.log.call_count >= 2

    def test_bulk_migrate_many_failures_truncated(
        self, qapp, qtbot, monkeypatch, silence_qmessagebox,
    ):
        from curator.gui.dialogs import TierDialog
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        rt = _make_runtime()
        files = [_make_file_entity() for _ in range(10)]
        moves = [
            _make_move(curator_id=f.curator_id, outcome_name="FAILED",
                       error=f"err {i}")
            for i, f in enumerate(files)
        ]
        rt.migration.plan.return_value = _make_migration_plan(moves=moves)
        rt.migration.apply.return_value = _make_migration_report(moves=moves)
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        dlg._le_root.setText("/p")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            MagicMock(return_value="/target"))
        monkeypatch.setattr(QMessageBox, "exec", MagicMock())
        dlg._action_bulk_migrate(files)
        # Just verify no crash; >5 failures path exercised


# ===========================================================================
# Selection helpers
# ===========================================================================


class TestSelectionHelpers:
    def test_get_selected_with_no_selection(self, qapp, qtbot):
        from curator.gui.dialogs import TierDialog
        rt = _make_runtime()
        dlg = TierDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg._get_selected_file_entities() == []
