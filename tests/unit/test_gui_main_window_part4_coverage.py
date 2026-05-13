"""Coverage for ``curator.gui.main_window`` Part 4 (v1.7.194).

Round 4 Tier 3 sub-ship 4 of 5 — covers the remaining slot handlers:
context menus, trash/restore/inspect/dissolve slots, bundle create/edit
slots, ``_perform_*`` helpers, ``_show_result_dialog``.

Part 5 closes with the pragma audit.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.unit.test_gui_main_window_part1_coverage import make_runtime_stub


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def silence_qmessagebox(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    captured = {}
    for name in ("about", "critical", "warning", "information"):
        mock = MagicMock()
        monkeypatch.setattr(QMessageBox, name, mock)
        captured[name] = mock
    yes_mock = MagicMock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "question", yes_mock)
    captured["question"] = yes_mock
    return captured


@pytest.fixture
def window(qapp, qtbot):
    from curator.gui.main_window import CuratorMainWindow
    rt = make_runtime_stub()
    w = CuratorMainWindow(rt)
    qtbot.addWidget(w)
    return w


def _make_file(*, curator_id=None, source_path="/p/file.txt"):
    f = MagicMock()
    f.curator_id = curator_id or uuid4()
    f.source_path = source_path
    f.source_id = "local"
    f.size = 1024
    f.mtime = datetime(2026, 5, 1, 12, 0)
    f.extension = "txt"
    f.xxhash3_128 = "abc"
    return f


def _make_trash_record(*, curator_id=None, original_path="/old/file.txt"):
    r = MagicMock()
    r.curator_id = curator_id or uuid4()
    r.original_path = original_path
    r.original_source_id = "local"
    r.reason = "duplicate"
    r.trashed_by = "user"
    r.trashed_at = datetime(2026, 5, 1, 12, 0)
    return r


def _make_bundle(*, bundle_id=None, name="MyBundle", bundle_type="manual"):
    b = MagicMock()
    b.bundle_id = bundle_id or uuid4()
    b.name = name
    b.bundle_type = bundle_type
    b.description = "desc"
    b.confidence = 1.0
    b.created_at = datetime(2026, 5, 1, 12, 0)
    return b


# ===========================================================================
# Selection slots (no selection / with selection paths)
# ===========================================================================


class TestSelectionSlots:
    def test_trash_selected_no_selection(self, window, silence_qmessagebox):
        window._files_view.selectionModel().clear()
        window._slot_trash_at_row = MagicMock()
        window._slot_trash_selected()
        silence_qmessagebox["information"].assert_called_once()
        window._slot_trash_at_row.assert_not_called()

    def test_trash_selected_with_selection(self, window):
        window.runtime.file_repo.query.return_value = [_make_file()]
        window._files_model.refresh()
        window._files_view.selectRow(0)
        window._slot_trash_at_row = MagicMock()
        window._slot_trash_selected()
        window._slot_trash_at_row.assert_called_once_with(0)

    def test_restore_selected_no_selection(self, window, silence_qmessagebox):
        window._trash_view.selectionModel().clear()
        window._slot_restore_at_row = MagicMock()
        window._slot_restore_selected()
        silence_qmessagebox["information"].assert_called_once()
        window._slot_restore_at_row.assert_not_called()

    def test_restore_selected_with_selection(self, window):
        window.runtime.trash_repo.list.return_value = [_make_trash_record()]
        window._trash_model.refresh()
        window._trash_view.selectRow(0)
        window._slot_restore_at_row = MagicMock()
        window._slot_restore_selected()
        window._slot_restore_at_row.assert_called_once_with(0)

    def test_dissolve_selected_no_selection(self, window, silence_qmessagebox):
        window._bundles_view.selectionModel().clear()
        window._slot_dissolve_at_row = MagicMock()
        window._slot_dissolve_selected()
        silence_qmessagebox["information"].assert_called_once()
        window._slot_dissolve_at_row.assert_not_called()

    def test_dissolve_selected_with_selection(self, window):
        window.runtime.bundle_repo.list_all.return_value = [_make_bundle()]
        window._bundles_model.refresh()
        window._bundles_view.selectRow(0)
        window._slot_dissolve_at_row = MagicMock()
        window._slot_dissolve_selected()
        window._slot_dissolve_at_row.assert_called_once_with(0)

    def test_bundle_edit_selected_no_selection(self, window, silence_qmessagebox):
        window._bundles_view.selectionModel().clear()
        window._slot_bundle_edit_at_row = MagicMock()
        window._slot_bundle_edit_selected()
        silence_qmessagebox["information"].assert_called_once()

    def test_bundle_edit_selected_with_selection(self, window):
        window.runtime.bundle_repo.list_all.return_value = [_make_bundle()]
        window._bundles_model.refresh()
        window._bundles_view.selectRow(0)
        window._slot_bundle_edit_at_row = MagicMock()
        window._slot_bundle_edit_selected()
        window._slot_bundle_edit_at_row.assert_called_once_with(0)


# ===========================================================================
# Inspect slot + open inspect dialog
# ===========================================================================


class TestSlotInspectAtIndex:
    def test_invalid_index_returns(self, window):
        from PySide6.QtCore import QModelIndex
        window._open_inspect_dialog = MagicMock()
        window._slot_inspect_at_index(QModelIndex())
        window._open_inspect_dialog.assert_not_called()

    def test_file_at_returns_none(self, window):
        window.runtime.file_repo.query.return_value = [_make_file()]
        window._files_model.refresh()
        idx = window._files_model.index(0, 0)
        window._files_model.file_at = MagicMock(return_value=None)
        window._open_inspect_dialog = MagicMock()
        window._slot_inspect_at_index(idx)
        window._open_inspect_dialog.assert_not_called()

    def test_valid_index_opens_dialog(self, window):
        f = _make_file()
        window.runtime.file_repo.query.return_value = [f]
        window._files_model.refresh()
        idx = window._files_model.index(0, 0)
        window._open_inspect_dialog = MagicMock()
        window._slot_inspect_at_index(idx)
        window._open_inspect_dialog.assert_called_once_with(f)


class TestOpenInspectDialog:
    def test_constructs_and_execs(self, window, monkeypatch):
        import curator.gui.dialogs as dialogs

        class _StubDialog:
            instances = []

            def __init__(self, *a, **kw):
                _StubDialog.instances.append(self)
                self.exec_called = False

            def exec(self):
                self.exec_called = True
                return 1

        monkeypatch.setattr(dialogs, "FileInspectDialog", _StubDialog,
                            raising=False)
        f = _make_file()
        window._open_inspect_dialog(f)
        assert len(_StubDialog.instances) == 1
        assert _StubDialog.instances[0].exec_called


# ===========================================================================
# Trash / restore / dissolve at row
# ===========================================================================


class TestSlotTrashAtRow:
    def test_file_at_none_returns(self, window, silence_qmessagebox):
        window._files_model.file_at = MagicMock(return_value=None)
        window._slot_trash_at_row(0)
        silence_qmessagebox["question"].assert_not_called()

    def test_user_cancels(self, window, silence_qmessagebox):
        from PySide6.QtWidgets import QMessageBox
        window._files_model.file_at = MagicMock(return_value=_make_file())
        silence_qmessagebox["question"].return_value = QMessageBox.StandardButton.Cancel
        window._perform_trash = MagicMock()
        window.refresh_all = MagicMock()
        window._slot_trash_at_row(0)
        window._perform_trash.assert_not_called()
        window.refresh_all.assert_not_called()

    def test_user_confirms(self, window, silence_qmessagebox):
        f = _make_file()
        window._files_model.file_at = MagicMock(return_value=f)
        window._perform_trash = MagicMock(return_value=(True, "trashed"))
        window._show_result_dialog = MagicMock()
        window.refresh_all = MagicMock()
        window._slot_trash_at_row(0)
        window._perform_trash.assert_called_once()
        window._show_result_dialog.assert_called_once()
        window.refresh_all.assert_called_once()


class TestSlotRestoreAtRow:
    def test_record_none_returns(self, window, silence_qmessagebox):
        window._trash_model.trash_at = MagicMock(return_value=None)
        window._slot_restore_at_row(0)
        silence_qmessagebox["question"].assert_not_called()

    def test_user_cancels(self, window, silence_qmessagebox):
        from PySide6.QtWidgets import QMessageBox
        window._trash_model.trash_at = MagicMock(return_value=_make_trash_record())
        silence_qmessagebox["question"].return_value = QMessageBox.StandardButton.Cancel
        window._perform_restore = MagicMock()
        window.refresh_all = MagicMock()
        window._slot_restore_at_row(0)
        window._perform_restore.assert_not_called()

    def test_user_confirms(self, window, silence_qmessagebox):
        r = _make_trash_record()
        window._trash_model.trash_at = MagicMock(return_value=r)
        window._perform_restore = MagicMock(return_value=(True, "restored"))
        window._show_result_dialog = MagicMock()
        window.refresh_all = MagicMock()
        window._slot_restore_at_row(0)
        window._perform_restore.assert_called_once_with(r.curator_id)


class TestSlotDissolveAtRow:
    def test_bundle_none_returns(self, window, silence_qmessagebox):
        window._bundles_model.bundle_at = MagicMock(return_value=None)
        window._slot_dissolve_at_row(0)
        silence_qmessagebox["question"].assert_not_called()

    def test_user_cancels(self, window, silence_qmessagebox):
        from PySide6.QtWidgets import QMessageBox
        window._bundles_model.bundle_at = MagicMock(return_value=_make_bundle())
        silence_qmessagebox["question"].return_value = QMessageBox.StandardButton.Cancel
        window._perform_dissolve = MagicMock()
        window._slot_dissolve_at_row(0)
        window._perform_dissolve.assert_not_called()

    def test_user_confirms(self, window, silence_qmessagebox):
        b = _make_bundle()
        window._bundles_model.bundle_at = MagicMock(return_value=b)
        window._perform_dissolve = MagicMock(return_value=(True, "dissolved"))
        window._show_result_dialog = MagicMock()
        window.refresh_all = MagicMock()
        window._slot_dissolve_at_row(0)
        window._perform_dissolve.assert_called_once_with(b.bundle_id)

    def test_unnamed_bundle_displays_unnamed(self, window, silence_qmessagebox):
        """The confirm dialog uses '(unnamed)' for name=None."""
        b = _make_bundle(name=None)
        window._bundles_model.bundle_at = MagicMock(return_value=b)
        window._perform_dissolve = MagicMock(return_value=(True, "ok"))
        window._show_result_dialog = MagicMock()
        window.refresh_all = MagicMock()
        window._slot_dissolve_at_row(0)
        # The question dialog body includes '(unnamed)'
        args, _ = silence_qmessagebox["question"].call_args
        assert "(unnamed)" in args[2]


# ===========================================================================
# _perform_trash / _perform_restore / _perform_dissolve
# ===========================================================================


class TestPerformTrash:
    def test_happy_path(self, window):
        record = MagicMock()
        record.original_path = "/p/old.txt"
        record.curator_id = uuid4()
        window.runtime.trash.send_to_trash.return_value = record
        cid = uuid4()
        success, msg = window._perform_trash(cid, reason="test")
        assert success
        assert "OS Recycle Bin" in msg
        assert "/p/old.txt" in msg

    def test_exception_returns_failure(self, window):
        window.runtime.trash.send_to_trash.side_effect = RuntimeError("kaboom")
        success, msg = window._perform_trash(uuid4(), reason="test")
        assert not success
        assert "Failed to send to Trash" in msg


class TestPerformRestore:
    def test_happy_path(self, window):
        f = MagicMock()
        f.source_path = "/p/restored.txt"
        window.runtime.trash.restore.return_value = f
        success, msg = window._perform_restore(uuid4())
        assert success
        assert "/p/restored.txt" in msg

    def test_generic_exception_returns_failure(self, window):
        window.runtime.trash.restore.side_effect = RuntimeError("generic error")
        success, msg = window._perform_restore(uuid4())
        assert not success
        assert "Failed to restore" in msg

    def test_restore_impossible_friendly_message(self, window):
        """RestoreImpossibleError gets a friendlier explanation."""
        class RestoreImpossibleError(Exception):
            pass
        window.runtime.trash.restore.side_effect = RestoreImpossibleError("no path")
        success, msg = window._perform_restore(uuid4())
        assert not success
        assert "Recycle Bin" in msg
        assert "manually" in msg


class TestPerformDissolve:
    def test_happy_path(self, window):
        window.runtime.bundle.dissolve = MagicMock()
        success, msg = window._perform_dissolve(uuid4())
        assert success
        assert "dissolved" in msg.lower()
        assert "Member files were preserved" in msg

    def test_exception_returns_failure(self, window):
        window.runtime.bundle.dissolve = MagicMock(
            side_effect=RuntimeError("dissolve fail")
        )
        success, msg = window._perform_dissolve(uuid4())
        assert not success
        assert "Failed to dissolve" in msg


# ===========================================================================
# Bundle new / edit slots
# ===========================================================================


class _StubBundleEditorResult:
    def __init__(self, *, name="B", description="d",
                 member_ids=None, primary_id=None, initial_member_ids=None):
        self.name = name
        self.description = description
        self.member_ids = member_ids or []
        self.primary_id = primary_id
        self.initial_member_ids = initial_member_ids or []


class TestSlotBundleNew:
    def test_user_cancels(self, window):
        """If _open_bundle_editor returns None (cancel), return early."""
        window._open_bundle_editor = MagicMock(return_value=None)
        window._perform_bundle_create = MagicMock()
        window._slot_bundle_new()
        window._perform_bundle_create.assert_not_called()

    def test_user_accepts(self, window):
        result = _StubBundleEditorResult(
            name="MyBundle", member_ids=[uuid4(), uuid4()],
            primary_id=uuid4(),
        )
        window._open_bundle_editor = MagicMock(return_value=result)
        window._perform_bundle_create = MagicMock(
            return_value=(True, "created"),
        )
        window._show_result_dialog = MagicMock()
        window.refresh_all = MagicMock()
        window._slot_bundle_new()
        window._perform_bundle_create.assert_called_once()
        window._show_result_dialog.assert_called_once_with(
            "New bundle", True, "created",
        )
        window.refresh_all.assert_called_once()


class TestSlotBundleEditAtRow:
    def test_bundle_none_returns(self, window):
        window._bundles_model.bundle_at = MagicMock(return_value=None)
        window._open_bundle_editor = MagicMock()
        window._slot_bundle_edit_at_row(0)
        window._open_bundle_editor.assert_not_called()

    def test_user_cancels_editor(self, window):
        b = _make_bundle()
        window._bundles_model.bundle_at = MagicMock(return_value=b)
        window._open_bundle_editor = MagicMock(return_value=None)
        window._perform_bundle_apply_edits = MagicMock()
        window._slot_bundle_edit_at_row(0)
        window._perform_bundle_apply_edits.assert_not_called()

    def test_user_accepts(self, window):
        b = _make_bundle()
        window._bundles_model.bundle_at = MagicMock(return_value=b)
        result = _StubBundleEditorResult(
            name="Edited", member_ids=[uuid4()],
        )
        window._open_bundle_editor = MagicMock(return_value=result)
        window._perform_bundle_apply_edits = MagicMock(
            return_value=(True, "updated"),
        )
        window._show_result_dialog = MagicMock()
        window.refresh_all = MagicMock()
        window._slot_bundle_edit_at_row(0)
        window._perform_bundle_apply_edits.assert_called_once()
        window._show_result_dialog.assert_called_once_with(
            "Edit bundle", True, "updated",
        )


class TestOpenBundleEditor:
    def test_accepted_returns_result(self, window, monkeypatch):
        import curator.gui.dialogs as dialogs

        class _StubDialog:
            def __init__(self, *a, **kw): ...
            def exec(self): return 1
            def get_result(self): return _StubBundleEditorResult()

        monkeypatch.setattr(dialogs, "BundleEditorDialog", _StubDialog,
                            raising=False)
        result = window._open_bundle_editor(existing_bundle=None)
        assert result is not None

    def test_cancelled_returns_none(self, window, monkeypatch):
        import curator.gui.dialogs as dialogs

        class _StubDialog:
            def __init__(self, *a, **kw): ...
            def exec(self): return 0
            def get_result(self): return None

        monkeypatch.setattr(dialogs, "BundleEditorDialog", _StubDialog,
                            raising=False)
        result = window._open_bundle_editor(existing_bundle=None)
        assert result is None


class TestPerformBundleCreate:
    def test_happy_path(self, window):
        bundle = MagicMock()
        bundle.name = "B"
        bundle.bundle_id = uuid4()
        window.runtime.bundle.create_manual.return_value = bundle
        success, msg = window._perform_bundle_create(
            name="B", description="d",
            member_ids=[uuid4(), uuid4()],
            primary_id=uuid4(),
        )
        assert success
        assert "Created bundle: B" in msg
        assert "Members: 2" in msg

    def test_exception(self, window):
        window.runtime.bundle.create_manual.side_effect = RuntimeError("nope")
        success, msg = window._perform_bundle_create(
            name="B", description="d",
            member_ids=[], primary_id=None,
        )
        assert not success
        assert "Failed to create bundle" in msg


class TestPerformBundleApplyEdits:
    def test_bundle_no_longer_exists(self, window):
        window.runtime.bundle.get.return_value = None
        success, msg = window._perform_bundle_apply_edits(
            bundle_id=uuid4(), name="X", description="",
            target_member_ids=[], primary_id=None,
            initial_member_ids=[],
        )
        assert not success
        assert "no longer exists" in msg

    def test_name_changed_updates_metadata(self, window):
        existing = _make_bundle(name="OLD")
        existing.description = "old"
        window.runtime.bundle.get.return_value = existing
        success, _ = window._perform_bundle_apply_edits(
            bundle_id=existing.bundle_id, name="NEW", description="new",
            target_member_ids=[], primary_id=None,
            initial_member_ids=[],
        )
        assert success
        # update was called on bundle_repo
        window.runtime.bundle_repo.update.assert_called_once()

    def test_metadata_unchanged_no_update_call(self, window):
        """When name + description match existing, don't call repo.update."""
        existing = _make_bundle(name="X")
        existing.description = "d"
        window.runtime.bundle.get.return_value = existing
        success, _ = window._perform_bundle_apply_edits(
            bundle_id=existing.bundle_id, name="X", description="d",
            target_member_ids=[], primary_id=None,
            initial_member_ids=[],
        )
        assert success
        # No update call
        window.runtime.bundle_repo.update.assert_not_called()

    def test_diff_adds_and_removes(self, window):
        existing = _make_bundle()
        window.runtime.bundle.get.return_value = existing
        a, b, c = uuid4(), uuid4(), uuid4()
        success, msg = window._perform_bundle_apply_edits(
            bundle_id=existing.bundle_id,
            name=existing.name, description=existing.description,
            target_member_ids=[b, c],  # Final: {b, c}
            primary_id=None,
            initial_member_ids=[a, b],  # Initial: {a, b} → add {c}, remove {a}
        )
        assert success
        # add_member called once (for c) and remove_member once (for a)
        assert window.runtime.bundle.add_member.call_count == 1
        assert window.runtime.bundle.remove_member.call_count == 1
        # Message shows the counts
        assert "Added: 1" in msg
        assert "Removed: 1" in msg

    def test_exception_returns_failure(self, window):
        window.runtime.bundle.get.side_effect = RuntimeError("get fail")
        success, msg = window._perform_bundle_apply_edits(
            bundle_id=uuid4(), name="X", description="",
            target_member_ids=[], primary_id=None,
            initial_member_ids=[],
        )
        assert not success
        assert "Failed to apply bundle edits" in msg


class TestResetPrimary:
    def test_primary_id_none_returns(self, window):
        window._reset_primary(uuid4(), None, [uuid4()])
        window.runtime.bundle_repo.add_membership.assert_not_called()

    def test_primary_not_in_members_returns(self, window):
        primary = uuid4()
        other = uuid4()
        window._reset_primary(uuid4(), primary, [other])  # primary not in list
        window.runtime.bundle_repo.add_membership.assert_not_called()

    def test_happy_path_re_adds_all_members(self, window):
        bundle_id = uuid4()
        primary = uuid4()
        secondary = uuid4()
        window._reset_primary(
            bundle_id, primary, [primary, secondary],
        )
        # add_membership called twice
        assert window.runtime.bundle_repo.add_membership.call_count == 2


# ===========================================================================
# _show_result_dialog
# ===========================================================================


class TestShowResultDialog:
    def test_success_shows_information(self, window, silence_qmessagebox):
        window._show_result_dialog("Title", True, "msg")
        silence_qmessagebox["information"].assert_called_once()
        silence_qmessagebox["warning"].assert_not_called()

    def test_failure_shows_warning(self, window, silence_qmessagebox):
        window._show_result_dialog("Title", False, "err")
        silence_qmessagebox["warning"].assert_called_once()
        silence_qmessagebox["information"].assert_not_called()


# ===========================================================================
# Context menus (defensive: invalid index returns early)
# ===========================================================================


class TestContextMenus:
    def test_browser_context_menu_invalid_index(self, window):
        """When indexAt returns invalid index, the menu doesn't show."""
        from PySide6.QtCore import QPoint
        window._slot_inspect_at_index = MagicMock()
        window._slot_trash_at_row = MagicMock()
        # No rows → any position is invalid
        window._show_browser_context_menu(QPoint(0, 0))
        window._slot_inspect_at_index.assert_not_called()
        window._slot_trash_at_row.assert_not_called()

    def test_trash_context_menu_invalid_index(self, window):
        from PySide6.QtCore import QPoint
        window._slot_restore_at_row = MagicMock()
        window._show_trash_context_menu(QPoint(0, 0))
        window._slot_restore_at_row.assert_not_called()

    def test_bundles_context_menu_invalid_index_only_new_action(
        self, window, monkeypatch,
    ):
        """Right-click on empty bundles table → only the 'New bundle' action.
        Since menu.exec() blocks, patch QMenu to record actions then return."""
        from PySide6.QtCore import QPoint
        captured = {"actions": []}

        class _StubMenu:
            def __init__(self, *a, **kw): ...
            def addAction(self, text):
                action = MagicMock()
                action.text_ = text
                captured["actions"].append(action)
                return action
            def addSeparator(self): ...
            def exec(self, *args, **kw):
                return None  # User dismissed without picking anything

        monkeypatch.setattr("curator.gui.main_window.QMenu", _StubMenu)
        window._show_bundles_context_menu(QPoint(0, 0))
        # Only "New bundle..." action was added (no bundle row)
        assert len(captured["actions"]) == 1


# ===========================================================================
# Context menu dispatches — QMenu stub returns a specific action
# ===========================================================================


def _stub_menu_picking(monkeypatch, picked_idx: int | None):
    """Patch QMenu to record actions added and make exec() return the action
    at index `picked_idx` (or None for 'user dismissed')."""
    captured = {"actions": []}

    class _StubMenu:
        def __init__(self, *a, **kw):
            self.actions = []

        def addAction(self, text):
            action = MagicMock()
            action.text_ = text
            self.actions.append(action)
            captured["actions"].append(action)
            return action

        def addSeparator(self): ...

        def exec(self, *args, **kw):
            if picked_idx is None:
                return None
            return captured["actions"][picked_idx]

    monkeypatch.setattr("curator.gui.main_window.QMenu", _StubMenu)
    return captured


class TestBrowserContextMenuDispatch:
    def test_inspect_chosen(self, window, monkeypatch):
        """Picking 'Inspect...' (action 0) calls _slot_inspect_at_index."""
        from PySide6.QtCore import QPoint
        f = _make_file()
        window.runtime.file_repo.query.return_value = [f]
        window._files_model.refresh()
        # Force indexAt to return a valid index
        valid_idx = window._files_model.index(0, 0)
        window._files_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_inspect_at_index = MagicMock()
        window._slot_trash_at_row = MagicMock()
        _stub_menu_picking(monkeypatch, 0)  # Pick "Inspect..." (action[0])
        window._show_browser_context_menu(QPoint(0, 0))
        window._slot_inspect_at_index.assert_called_once_with(valid_idx)
        window._slot_trash_at_row.assert_not_called()

    def test_trash_chosen(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        f = _make_file()
        window.runtime.file_repo.query.return_value = [f]
        window._files_model.refresh()
        valid_idx = window._files_model.index(0, 0)
        window._files_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_inspect_at_index = MagicMock()
        window._slot_trash_at_row = MagicMock()
        # Browser menu order: 0=Inspect, 1=(separator implicit), 1=Trash
        # _stub_menu_picking tracks actions list (separators aren't actions)
        # Action indexes: 0=Inspect, 1=Trash
        _stub_menu_picking(monkeypatch, 1)
        window._show_browser_context_menu(QPoint(0, 0))
        window._slot_inspect_at_index.assert_not_called()
        window._slot_trash_at_row.assert_called_once_with(0)


class TestTrashContextMenuDispatch:
    def test_restore_chosen(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        r = _make_trash_record()
        window.runtime.trash_repo.list.return_value = [r]
        window._trash_model.refresh()
        valid_idx = window._trash_model.index(0, 0)
        window._trash_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_restore_at_row = MagicMock()
        _stub_menu_picking(monkeypatch, 0)
        window._show_trash_context_menu(QPoint(0, 0))
        window._slot_restore_at_row.assert_called_once_with(0)


class TestBundlesContextMenuDispatch:
    def test_new_chosen_no_row(self, window, monkeypatch):
        """Right-click on empty bundles → only 'New bundle' available; pick it."""
        from PySide6.QtCore import QPoint
        window._slot_bundle_new = MagicMock()
        _stub_menu_picking(monkeypatch, 0)
        window._show_bundles_context_menu(QPoint(0, 0))
        window._slot_bundle_new.assert_called_once()

    def test_edit_chosen_on_valid_row(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        b = _make_bundle()
        window.runtime.bundle_repo.list_all.return_value = [b]
        window._bundles_model.refresh()
        valid_idx = window._bundles_model.index(0, 0)
        window._bundles_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_bundle_edit_at_row = MagicMock()
        # Actions: 0=New, 1=Edit, 2=Dissolve (separators don't add actions)
        _stub_menu_picking(monkeypatch, 1)
        window._show_bundles_context_menu(QPoint(0, 0))
        window._slot_bundle_edit_at_row.assert_called_once_with(0)

    def test_dissolve_chosen_on_valid_row(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        b = _make_bundle()
        window.runtime.bundle_repo.list_all.return_value = [b]
        window._bundles_model.refresh()
        valid_idx = window._bundles_model.index(0, 0)
        window._bundles_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_dissolve_at_row = MagicMock()
        _stub_menu_picking(monkeypatch, 2)
        window._show_bundles_context_menu(QPoint(0, 0))
        window._slot_dissolve_at_row.assert_called_once_with(0)

    def test_dismissed_no_action(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        b = _make_bundle()
        window.runtime.bundle_repo.list_all.return_value = [b]
        window._bundles_model.refresh()
        valid_idx = window._bundles_model.index(0, 0)
        window._bundles_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_bundle_new = MagicMock()
        window._slot_bundle_edit_at_row = MagicMock()
        window._slot_dissolve_at_row = MagicMock()
        _stub_menu_picking(monkeypatch, None)  # User dismissed
        window._show_bundles_context_menu(QPoint(0, 0))
        window._slot_bundle_new.assert_not_called()
        window._slot_bundle_edit_at_row.assert_not_called()
        window._slot_dissolve_at_row.assert_not_called()


class TestMigrateContextMenuDispatch:
    def _seed_running_job(self, window):
        job = _make_bundle()  # reuse bundle MagicMock as a generic shape
        job.job_id = uuid4()
        job.status = "running"
        job.files_total = 10
        window.runtime.migration_job_repo.list_jobs.return_value = [job]
        window._migrate_jobs_model.refresh()
        return job

    def test_abort_chosen_for_running_job(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        job = self._seed_running_job(window)
        valid_idx = window._migrate_jobs_model.index(0, 0)
        window._migrate_jobs_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_migrate_abort_at_row = MagicMock()
        window._slot_migrate_resume_at_row = MagicMock()
        _stub_menu_picking(monkeypatch, 0)  # action 0 = Abort
        window._show_migrate_context_menu(QPoint(0, 0))
        window._slot_migrate_abort_at_row.assert_called_once_with(0)
        window._slot_migrate_resume_at_row.assert_not_called()

    def test_resume_chosen_for_cancelled_job(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        job = self._seed_running_job(window)
        job.status = "cancelled"  # Make it resumable
        window._migrate_jobs_model.refresh()
        valid_idx = window._migrate_jobs_model.index(0, 0)
        window._migrate_jobs_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_migrate_resume_at_row = MagicMock()
        _stub_menu_picking(monkeypatch, 1)  # action 1 = Resume
        window._show_migrate_context_menu(QPoint(0, 0))
        window._slot_migrate_resume_at_row.assert_called_once_with(0)


class TestSourceContextMenuDispatch:
    def _seed_one_source(self, window):
        from PySide6.QtWidgets import QTableWidgetItem
        from PySide6.QtCore import Qt
        window._tbl_sources.setRowCount(1)
        # Column 0 = source_id
        item_sid = QTableWidgetItem("local")
        item_sid.setData(Qt.ItemDataRole.UserRole, "local")
        window._tbl_sources.setItem(0, 0, item_sid)
        # Column 3 = Enabled "yes"
        window._tbl_sources.setItem(0, 3, QTableWidgetItem("yes"))
        return item_sid

    def test_no_item_at_position_returns(self, window, monkeypatch):
        """itemAt returns None → return early."""
        from PySide6.QtCore import QPoint
        window._tbl_sources.itemAt = MagicMock(return_value=None)
        # We don't even need to stub QMenu — should return before menu construction
        window._tbl_sources.setRowCount(0)
        window._slot_source_context_menu(QPoint(0, 0))

    def test_no_sid_item_returns(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QTableWidgetItem
        # Add a row with no SID item in column 0
        window._tbl_sources.setRowCount(1)
        col1 = QTableWidgetItem("type")
        window._tbl_sources.setItem(0, 1, col1)
        # itemAt returns the column-1 item
        window._tbl_sources.itemAt = MagicMock(return_value=col1)
        # Column 0 item is None → early return
        window._slot_source_edit_properties = MagicMock()
        window._slot_source_context_menu(QPoint(0, 0))
        window._slot_source_edit_properties.assert_not_called()

    def test_migrate_context_menu_valid_index_but_no_job(
        self, window, monkeypatch,
    ):
        """Line 522-523: indexAt returns valid index but job_at returns None
        → early return, no menu constructed."""
        from PySide6.QtCore import QPoint
        # Seed a job so index is valid, but make job_at return None
        job = MagicMock()
        job.job_id = uuid4()
        job.status = "running"
        job.files_total = 0
        window.runtime.migration_job_repo.list_jobs.return_value = [job]
        window._migrate_jobs_model.refresh()
        valid_idx = window._migrate_jobs_model.index(0, 0)
        window._migrate_jobs_view.indexAt = MagicMock(return_value=valid_idx)
        # job_at returns None
        window._migrate_jobs_model.job_at = MagicMock(return_value=None)
        # If menu construction were attempted, it would hit QMenu — but we
        # don't patch QMenu here, so a successful early-return means no
        # exception was raised AND no slot fired.
        window._slot_migrate_abort_at_row = MagicMock()
        window._slot_migrate_resume_at_row = MagicMock()
        window._show_migrate_context_menu(QPoint(0, 0))
        window._slot_migrate_abort_at_row.assert_not_called()
        window._slot_migrate_resume_at_row.assert_not_called()

    def test_open_sources_tab_missing_warning(
        self, window, silence_qmessagebox,
    ):
        """Line 1320-1324: if the Sources tab is missing from the tab bar
        (defensive boundary), surface a warning."""
        # Remove the Sources tab from the tab bar
        for i in range(window._tabs.count()):
            if window._tabs.tabText(i) == "Sources":
                window._tabs.removeTab(i)
                break
        # Now _slot_open_sources_tab will iterate without finding it
        window._slot_open_sources_tab()
        silence_qmessagebox["warning"].assert_called_once()
        args, _ = silence_qmessagebox["warning"].call_args
        assert "Sources tab not found" in args[1]

    def test_menu_constructed_and_dismissed(self, window, monkeypatch):
        """Right-click on a source row, dismiss menu → no slot fires."""
        from PySide6.QtCore import QPoint
        item_sid = self._seed_one_source(window)
        window._tbl_sources.itemAt = MagicMock(return_value=item_sid)
        # menu.exec returns None (dismissed); the slot itself just constructs
        # menus + calls exec. Verify no exception by running.

        class _StubMenu:
            def __init__(self, *a, **kw):
                self.actions = []
            def addAction(self, text):
                a = MagicMock(text_=text)
                self.actions.append(a)
                return a
            def addSeparator(self): ...
            def exec(self, *args, **kw):
                return None

        monkeypatch.setattr("curator.gui.main_window.QMenu", _StubMenu)
        # Should not raise
        window._slot_source_context_menu(QPoint(0, 0))
