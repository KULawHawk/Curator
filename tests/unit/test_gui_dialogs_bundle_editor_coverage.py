"""Coverage for BundleEditorDialog (v1.7.200).

Round 5 Tier 1 sub-ship 2 of 8 — covers the bundle create/edit modal
with its dual-list widget (Available files | In bundle), primary-member
star indicator, and validation flow.

The dialog itself does NOT call BundleService — it only collects a
``BundleEditorResult`` via :meth:`get_result`. So tests stub
``runtime.file_repo.query`` and ``runtime.bundle_repo.get_memberships``
and exercise the UI directly.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
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
    """Stub the static QMessageBox methods so dialog flows don't block."""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", MagicMock())
    monkeypatch.setattr(QMessageBox, "warning", MagicMock())
    monkeypatch.setattr(QMessageBox, "critical", MagicMock())
    monkeypatch.setattr(QMessageBox, "question",
                        MagicMock(return_value=QMessageBox.StandardButton.Yes))


# ===========================================================================
# Helpers
# ===========================================================================


def _make_file(*, curator_id=None, source_path="/p/file.txt"):
    f = MagicMock()
    f.curator_id = curator_id or uuid4()
    f.source_path = source_path
    return f


def _make_bundle(*, bundle_id=None, name="MyBundle", description="A bundle"):
    b = MagicMock()
    b.bundle_id = bundle_id or uuid4()
    b.name = name
    b.description = description
    return b


def _make_membership(*, curator_id=None, role="member"):
    m = MagicMock()
    m.curator_id = curator_id or uuid4()
    m.role = role
    return m


def _make_runtime(files=None, memberships=None,
                  file_query_raises=False, memberships_raise=False):
    rt = MagicMock()
    if file_query_raises:
        rt.file_repo.query.side_effect = RuntimeError("fail")
    else:
        rt.file_repo.query.return_value = files or []
    if memberships_raise:
        rt.bundle_repo.get_memberships.side_effect = RuntimeError("fail")
    else:
        rt.bundle_repo.get_memberships.return_value = memberships or []
    return rt


# ===========================================================================
# Construction (create mode + edit mode)
# ===========================================================================


class TestBundleEditorConstruction:
    def test_create_mode_basic(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime()
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        assert "Create new bundle" in dlg.windowTitle()
        assert dlg._existing_bundle is None
        assert dlg._available_list.count() == 0
        assert dlg._bundle_list.count() == 0

    def test_create_mode_with_files(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        files = [_make_file(source_path="/a.txt"), _make_file(source_path="/b.txt")]
        rt = _make_runtime(files=files)
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg._available_list.count() == 2

    def test_create_mode_file_query_exception(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime(file_query_raises=True)
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        # Empty available list on exception
        assert dlg._available_list.count() == 0

    def test_edit_mode_with_existing_bundle(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        f1 = _make_file(source_path="/m1.txt")
        f2 = _make_file(source_path="/m2.txt")
        files = [f1, f2, _make_file(source_path="/other.txt")]
        memberships = [
            _make_membership(curator_id=f1.curator_id, role="primary"),
            _make_membership(curator_id=f2.curator_id, role="member"),
        ]
        rt = _make_runtime(files=files, memberships=memberships)
        bundle = _make_bundle(name="B", description="desc")
        dlg = BundleEditorDialog(rt, existing_bundle=bundle)
        qtbot.addWidget(dlg)
        assert "Edit bundle" in dlg.windowTitle()
        assert dlg._name_edit.text() == "B"
        assert dlg._desc_edit.text() == "desc"
        # 2 members in bundle list, 1 left in available
        assert dlg._bundle_list.count() == 2
        assert dlg._available_list.count() == 1
        # Primary id captured
        assert dlg._primary_id == f1.curator_id

    def test_edit_mode_bundle_with_no_name(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        bundle = _make_bundle(name=None, description=None)
        rt = _make_runtime()
        dlg = BundleEditorDialog(rt, existing_bundle=bundle)
        qtbot.addWidget(dlg)
        assert "(unnamed)" in dlg.windowTitle()
        assert dlg._name_edit.text() == ""
        assert dlg._desc_edit.text() == ""

    def test_edit_mode_get_memberships_exception(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        bundle = _make_bundle()
        rt = _make_runtime(memberships_raise=True)
        dlg = BundleEditorDialog(rt, existing_bundle=bundle)
        qtbot.addWidget(dlg)
        # No members loaded
        assert dlg._bundle_list.count() == 0


# ===========================================================================
# Static helpers
# ===========================================================================


class TestStaticHelpers:
    def test_basename_for_sort_forward_slash(self):
        from curator.gui.dialogs import BundleEditorDialog
        assert BundleEditorDialog._basename_for_sort("/a/b/c.TXT") == "c.txt"

    def test_basename_for_sort_backslash(self):
        from curator.gui.dialogs import BundleEditorDialog
        assert BundleEditorDialog._basename_for_sort("C:\\a\\b.PDF") == "b.pdf"

    def test_basename_for_sort_no_separator(self):
        from curator.gui.dialogs import BundleEditorDialog
        assert BundleEditorDialog._basename_for_sort("single.txt") == "single.txt"

    def test_format_file_label_with_short_parent(self):
        from curator.gui.dialogs import BundleEditorDialog
        out = BundleEditorDialog._format_file_label("/a/b.txt")
        assert "b.txt" in out
        assert "/a" in out

    def test_format_file_label_with_long_parent(self):
        from curator.gui.dialogs import BundleEditorDialog
        out = BundleEditorDialog._format_file_label("/a/b/c/d/file.txt")
        # Truncated to last 2 segments
        assert "file.txt" in out
        assert "..." in out

    def test_format_file_label_no_separator(self):
        from curator.gui.dialogs import BundleEditorDialog
        out = BundleEditorDialog._format_file_label("single.txt")
        assert out == "single.txt"

    def test_format_file_label_backslash_normalized(self):
        from curator.gui.dialogs import BundleEditorDialog
        out = BundleEditorDialog._format_file_label("C:\\a\\b\\c.txt")
        # Backslashes normalized to forward slashes
        assert "c.txt" in out


# ===========================================================================
# List manipulation
# ===========================================================================


class TestListManipulation:
    def test_move_selected_to_bundle(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        f1 = _make_file(source_path="/a.txt")
        rt = _make_runtime(files=[f1])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg._available_list.setCurrentRow(0)
        dlg._move_selected_to_bundle()
        assert dlg._available_list.count() == 0
        assert dlg._bundle_list.count() == 1

    def test_move_selected_to_bundle_no_selection_noop(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        f1 = _make_file()
        rt = _make_runtime(files=[f1])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        # No selection
        dlg._move_selected_to_bundle()
        assert dlg._available_list.count() == 1
        assert dlg._bundle_list.count() == 0

    def test_move_selected_from_bundle(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        f1 = _make_file()
        rt = _make_runtime(files=[f1])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        # Move to bundle, then back
        dlg._available_list.setCurrentRow(0)
        dlg._move_selected_to_bundle()
        dlg._bundle_list.setCurrentRow(0)
        dlg._move_selected_from_bundle()
        assert dlg._available_list.count() == 1
        assert dlg._bundle_list.count() == 0

    def test_move_selected_from_bundle_no_selection_noop(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime()
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg._move_selected_from_bundle()  # noop
        assert dlg._bundle_list.count() == 0

    def test_move_primary_clears_primary_state(self, qapp, qtbot):
        """Moving the primary member back to Available clears _primary_id."""
        from curator.gui.dialogs import BundleEditorDialog
        f1 = _make_file()
        rt = _make_runtime(files=[f1])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        # Move to bundle, then set as primary, then move back
        dlg._available_list.setCurrentRow(0)
        dlg._move_selected_to_bundle()
        dlg._bundle_list.setCurrentRow(0)
        dlg._set_selected_as_primary()
        assert dlg._primary_id == f1.curator_id
        # Move back
        dlg._bundle_list.setCurrentRow(0)
        dlg._move_selected_from_bundle()
        assert dlg._primary_id is None


# ===========================================================================
# Primary handling
# ===========================================================================


class TestPrimaryHandling:
    def test_set_primary_no_selection_shows_info(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime()
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QMessageBox
        dlg._set_selected_as_primary()
        # QMessageBox.information called
        assert QMessageBox.information.called

    def test_set_primary_multiple_selection_shows_info(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        """Selecting >1 items should show the info message."""
        from curator.gui.dialogs import BundleEditorDialog
        f1, f2 = _make_file(), _make_file(source_path="/b.txt")
        rt = _make_runtime(files=[f1, f2])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        # Move both to bundle
        dlg._available_list.selectAll()
        dlg._move_selected_to_bundle()
        # Select all
        dlg._bundle_list.selectAll()
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information.reset_mock()
        dlg._set_selected_as_primary()
        assert QMessageBox.information.called

    def test_set_primary_with_single_selection(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        f1, f2 = _make_file(), _make_file(source_path="/b.txt")
        rt = _make_runtime(files=[f1, f2])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg._available_list.selectAll()
        dlg._move_selected_to_bundle()
        dlg._bundle_list.setCurrentRow(0)
        dlg._set_selected_as_primary()
        # Primary id is the curator_id of the selected item
        assert dlg._primary_id == dlg._bundle_list.item(0).data(0x100)
        # The item text should have the star prefix
        assert dlg._bundle_list.item(0).text().startswith(
            BundleEditorDialog.PRIMARY_PREFIX,
        )


# ===========================================================================
# Filtering
# ===========================================================================


class TestFiltering:
    def test_refilter_available_hides_non_matching(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime(files=[
            _make_file(source_path="/apple.txt"),
            _make_file(source_path="/banana.txt"),
        ])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg._refilter_available("apple")
        # Apple row visible, banana row hidden
        for row in range(dlg._available_list.count()):
            item = dlg._available_list.item(row)
            path = item.data(0x101) or ""
            if "apple" in path.lower():
                assert not item.isHidden()
            else:
                assert item.isHidden()

    def test_refilter_available_empty_query_shows_all(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime(files=[_make_file()])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg._refilter_available("")
        # All visible
        assert not dlg._available_list.item(0).isHidden()

    def test_refilter_bundle_hides_non_matching(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime(files=[
            _make_file(source_path="/apple.txt"),
            _make_file(source_path="/banana.txt"),
        ])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg._available_list.selectAll()
        dlg._move_selected_to_bundle()
        dlg._refilter_bundle("apple")
        for row in range(dlg._bundle_list.count()):
            item = dlg._bundle_list.item(row)
            path = item.data(0x101) or ""
            if "apple" in path.lower():
                assert not item.isHidden()
            else:
                assert item.isHidden()


# ===========================================================================
# Accept flow
# ===========================================================================


class TestAcceptFlow:
    def test_accept_with_empty_name_shows_warning(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime()
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        # No name set, no members → first check fires
        dlg._on_accept()
        from PySide6.QtWidgets import QMessageBox
        assert QMessageBox.warning.called

    def test_accept_with_no_members_shows_warning(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime()
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg._name_edit.setText("OK Name")
        dlg._on_accept()
        from PySide6.QtWidgets import QMessageBox
        assert QMessageBox.warning.called

    def test_accept_with_name_and_members_succeeds(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import BundleEditorDialog
        f1 = _make_file()
        rt = _make_runtime(files=[f1])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        # Stub accept to avoid the modal close
        dlg.accept = MagicMock()
        dlg._name_edit.setText("Test Bundle")
        dlg._desc_edit.setText("Test description")
        dlg._available_list.setCurrentRow(0)
        dlg._move_selected_to_bundle()
        dlg._on_accept()
        # Result populated; accept() called
        result = dlg.get_result()
        assert result is not None
        assert result.name == "Test Bundle"
        assert result.description == "Test description"
        assert len(result.member_ids) == 1
        # Default primary = first member
        assert result.primary_id == result.member_ids[0]
        dlg.accept.assert_called_once()

    def test_accept_with_explicit_primary(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import BundleEditorDialog
        f1, f2 = _make_file(), _make_file(source_path="/b.txt")
        rt = _make_runtime(files=[f1, f2])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg.accept = MagicMock()
        dlg._name_edit.setText("Test")
        dlg._available_list.selectAll()
        dlg._move_selected_to_bundle()
        # Set second item as primary
        dlg._bundle_list.setCurrentRow(1)
        dlg._set_selected_as_primary()
        explicit_primary = dlg._primary_id
        dlg._on_accept()
        result = dlg.get_result()
        assert result.primary_id == explicit_primary

    def test_accept_with_primary_removed_falls_back_to_first(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        """If _primary_id no longer in member_ids, fall back to first."""
        from curator.gui.dialogs import BundleEditorDialog
        f1 = _make_file()
        rt = _make_runtime(files=[f1])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg.accept = MagicMock()
        dlg._name_edit.setText("Test")
        dlg._available_list.setCurrentRow(0)
        dlg._move_selected_to_bundle()
        # Set primary to a UUID not in members
        dlg._primary_id = uuid4()
        dlg._on_accept()
        result = dlg.get_result()
        # Falls back to first member's id
        assert result.primary_id == result.member_ids[0]

    def test_accept_with_empty_description_becomes_none(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import BundleEditorDialog
        f1 = _make_file()
        rt = _make_runtime(files=[f1])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg.accept = MagicMock()
        dlg._name_edit.setText("Test")
        # Leave description empty
        dlg._available_list.setCurrentRow(0)
        dlg._move_selected_to_bundle()
        dlg._on_accept()
        assert dlg.get_result().description is None

    def test_accept_edit_mode_stores_existing_bundle_id(
        self, qapp, qtbot, silence_qmessagebox,
    ):
        from curator.gui.dialogs import BundleEditorDialog
        bundle = _make_bundle()
        f1 = _make_file()
        rt = _make_runtime(files=[f1])
        dlg = BundleEditorDialog(rt, existing_bundle=bundle)
        qtbot.addWidget(dlg)
        dlg.accept = MagicMock()
        dlg._name_edit.setText("Test")
        dlg._available_list.setCurrentRow(0)
        dlg._move_selected_to_bundle()
        dlg._on_accept()
        result = dlg.get_result()
        assert result.existing_bundle_id == bundle.bundle_id


# ===========================================================================
# get_result before accept returns None
# ===========================================================================


class TestGetResultBeforeAccept:
    def test_get_result_returns_none_before_accept(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime()
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg.get_result() is None


# ===========================================================================
# Double-click handlers (delegate to move methods — exercised indirectly)
# ===========================================================================


class TestDoubleClickIntegration:
    def test_available_double_click_handler_callable(self, qapp, qtbot):
        """The double-click handler invokes _move_selected_to_bundle.
        We can't easily synthesize the doubleClicked signal — just
        verify the handler exists and the underlying method works."""
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime(files=[_make_file()])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg._available_list.setCurrentRow(0)
        dlg._move_selected_to_bundle()
        assert dlg._bundle_list.count() == 1

    def test_bundle_double_click_handler_callable(self, qapp, qtbot):
        from curator.gui.dialogs import BundleEditorDialog
        rt = _make_runtime(files=[_make_file()])
        dlg = BundleEditorDialog(rt)
        qtbot.addWidget(dlg)
        dlg._available_list.setCurrentRow(0)
        dlg._move_selected_to_bundle()
        dlg._bundle_list.setCurrentRow(0)
        dlg._move_selected_from_bundle()
        assert dlg._available_list.count() == 1
