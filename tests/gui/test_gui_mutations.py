"""Tests for v0.35 GUI mutations: Trash / Restore / Dissolve.

Strategy: build a fully-wired CuratorRuntime against a temp DB with
real files on disk, instantiate CuratorMainWindow, then call the
``_perform_*`` methods directly. The slot wrappers are tested
separately with a mocked QMessageBox so we never block on dialogs.

The headline assertions:
    * _perform_trash actually moves the file to the OS Recycle Bin AND
      records a TrashRecord AND marks the FileEntity deleted_at
    * _perform_restore handles RestoreImpossibleError gracefully on
      Windows (returns success=False with a friendly message)
    * _perform_dissolve removes the bundle row but preserves member
      FileEntity rows

All tests skip if PySide6 isn't available.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

pyside6 = pytest.importorskip("PySide6")  # noqa: F841

from PySide6.QtWidgets import QApplication, QMessageBox

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.gui.main_window import CuratorMainWindow
from curator.models.bundle import BundleEntity, BundleMembership
from curator.models.file import FileEntity
from curator.models.source import SourceConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def runtime_with_real_file(tmp_path):
    """Real CuratorRuntime + a real file on disk that can actually be trashed."""
    db_path = tmp_path / "mutations.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )

    try:
        rt.source_repo.insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
    except Exception:
        pass

    # Create a real file on disk so send2trash has something to operate on.
    real_file = tmp_path / "to_be_trashed.txt"
    real_file.write_text("trash me\n")

    entity = FileEntity(
        curator_id=uuid4(), source_id="local",
        source_path=str(real_file), size=real_file.stat().st_size,
        mtime=datetime.fromtimestamp(real_file.stat().st_mtime),
        extension=".txt",
    )
    rt.file_repo.upsert(entity)

    yield rt, entity, real_file


@pytest.fixture
def runtime_with_bundle(tmp_path):
    """Real runtime + a bundle with two member files."""
    db_path = tmp_path / "bundle_mutations.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    try:
        rt.source_repo.insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
    except Exception:
        pass

    file_a = FileEntity(
        curator_id=uuid4(), source_id="local",
        source_path="/tmp/member_a.txt", size=10,
        mtime=datetime(2024, 1, 1),
    )
    file_b = FileEntity(
        curator_id=uuid4(), source_id="local",
        source_path="/tmp/member_b.txt", size=20,
        mtime=datetime(2024, 1, 2),
    )
    rt.file_repo.upsert(file_a)
    rt.file_repo.upsert(file_b)

    bundle = BundleEntity(name="Test bundle", bundle_type="manual")
    rt.bundle_repo.insert(bundle)
    rt.bundle_repo.add_membership(BundleMembership(
        bundle_id=bundle.bundle_id, curator_id=file_a.curator_id, role="primary",
    ))
    rt.bundle_repo.add_membership(BundleMembership(
        bundle_id=bundle.bundle_id, curator_id=file_b.curator_id, role="member",
    ))

    yield rt, bundle, [file_a, file_b]


# ===========================================================================
# _perform_trash
# ===========================================================================


class TestPerformTrash:
    def test_trash_moves_file_and_marks_deleted(
        self, qapp, runtime_with_real_file
    ):
        rt, entity, real_file = runtime_with_real_file
        window = CuratorMainWindow(rt)
        try:
            success, message = window._perform_trash(
                entity.curator_id, reason="test trash",
            )
            assert success is True
            assert "Sent to OS Recycle Bin" in message
            # File row marked deleted.
            db_row = rt.file_repo.get(entity.curator_id)
            assert db_row.deleted_at is not None
            # Trash record created.
            trash_record = rt.trash_repo.get(entity.curator_id)
            assert trash_record is not None
            assert trash_record.reason == "test trash"
            assert trash_record.trashed_by == "user.gui"
        finally:
            window.deleteLater()

    def test_trash_nonexistent_returns_failure(
        self, qapp, runtime_with_real_file
    ):
        rt, _entity, _real_file = runtime_with_real_file
        window = CuratorMainWindow(rt)
        try:
            # A curator_id with no FileEntity row.
            fake_id = uuid4()
            success, message = window._perform_trash(fake_id, reason="bogus")
            assert success is False
            assert "Failed to send to Trash" in message
        finally:
            window.deleteLater()

    def test_trash_missing_disk_file_returns_failure(
        self, qapp, runtime_with_real_file
    ):
        """If the FileEntity exists but the file isn't on disk, fail gracefully."""
        rt, entity, real_file = runtime_with_real_file
        # Delete the file from disk before attempting trash.
        real_file.unlink()

        window = CuratorMainWindow(rt)
        try:
            success, message = window._perform_trash(
                entity.curator_id, reason="missing disk",
            )
            # send2trash raises when the file doesn't exist; method catches.
            assert success is False
            assert "Failed" in message
        finally:
            window.deleteLater()


# ===========================================================================
# _perform_restore
# ===========================================================================


class TestPerformRestore:
    def test_restore_handles_impossible_gracefully(
        self, qapp, runtime_with_real_file
    ):
        """On Windows, restore raises RestoreImpossibleError because send2trash
        doesn't record os_trash_location. The GUI must report this without
        crashing."""
        rt, entity, _real_file = runtime_with_real_file
        window = CuratorMainWindow(rt)
        try:
            # First, trash the file.
            ok, _msg = window._perform_trash(entity.curator_id, reason="setup")
            assert ok is True
            # Now try to restore.
            success, message = window._perform_restore(entity.curator_id)
            # On Windows this fails with the friendly message;
            # on Linux freedesktop / macOS it might succeed -- both
            # outcomes are acceptable, just don't crash.
            if not success:
                assert "manually" in message.lower() or "Failed" in message
        finally:
            window.deleteLater()

    def test_restore_nonexistent_returns_failure(
        self, qapp, runtime_with_real_file
    ):
        rt, _entity, _real_file = runtime_with_real_file
        window = CuratorMainWindow(rt)
        try:
            fake_id = uuid4()
            success, message = window._perform_restore(fake_id)
            assert success is False
            assert "Failed" in message or "not in trash" in message.lower()
        finally:
            window.deleteLater()


# ===========================================================================
# _perform_dissolve
# ===========================================================================


class TestPerformDissolve:
    def test_dissolve_removes_bundle_preserves_files(
        self, qapp, runtime_with_bundle
    ):
        rt, bundle, files = runtime_with_bundle
        window = CuratorMainWindow(rt)
        try:
            success, message = window._perform_dissolve(bundle.bundle_id)
            assert success is True
            assert "preserved" in message.lower()
            # Bundle row removed.
            assert rt.bundle_repo.get(bundle.bundle_id) is None
            # Member files preserved.
            for f in files:
                assert rt.file_repo.get(f.curator_id) is not None
        finally:
            window.deleteLater()

    def test_dissolve_nonexistent_handled_gracefully(
        self, qapp, runtime_with_bundle
    ):
        rt, _bundle, _files = runtime_with_bundle
        window = CuratorMainWindow(rt)
        try:
            fake_id = uuid4()
            # BundleService.dissolve might silently no-op on missing IDs;
            # either way the GUI must not crash.
            success, message = window._perform_dissolve(fake_id)
            # Either outcome is acceptable; just don't raise.
            assert isinstance(success, bool)
            assert isinstance(message, str)
        finally:
            window.deleteLater()


# ===========================================================================
# Slot wrappers (with mocked dialog) -- ensure cancellation paths work
# ===========================================================================


class TestSlotConfirmationCancellation:
    def test_trash_cancelled_does_not_perform(self, qapp, runtime_with_real_file):
        """If the user clicks Cancel in the confirmation, no trash happens."""
        rt, entity, real_file = runtime_with_real_file
        window = CuratorMainWindow(rt)
        try:
            # Patch QMessageBox.question to return Cancel.
            with patch.object(QMessageBox, "question",
                              return_value=QMessageBox.StandardButton.Cancel):
                # Need to make the row clickable -- set selection.
                idx = window._files_model.index(0, 0)
                window._files_view.setCurrentIndex(idx)
                window._slot_trash_at_row(0)
            # File still exists, no trash record, deleted_at still None.
            assert real_file.exists()
            assert rt.trash_repo.get(entity.curator_id) is None
            assert rt.file_repo.get(entity.curator_id).deleted_at is None
        finally:
            window.deleteLater()

    def test_dissolve_cancelled_does_not_perform(self, qapp, runtime_with_bundle):
        rt, bundle, _files = runtime_with_bundle
        window = CuratorMainWindow(rt)
        try:
            with patch.object(QMessageBox, "question",
                              return_value=QMessageBox.StandardButton.Cancel):
                idx = window._bundles_model.index(0, 0)
                window._bundles_view.setCurrentIndex(idx)
                window._slot_dissolve_at_row(0)
            # Bundle still exists.
            assert rt.bundle_repo.get(bundle.bundle_id) is not None
        finally:
            window.deleteLater()


# ===========================================================================
# Slot wrappers (no selection)
# ===========================================================================


class TestSlotNoSelection:
    def test_trash_with_no_selection_shows_info_dialog(
        self, qapp, runtime_with_real_file
    ):
        rt, _entity, _real_file = runtime_with_real_file
        window = CuratorMainWindow(rt)
        try:
            # Clear any selection.
            window._files_view.clearSelection()
            window._files_view.setCurrentIndex(window._files_model.index(-1, -1))
            with patch.object(QMessageBox, "information") as mock_info:
                window._slot_trash_selected()
            # The "no selection" info dialog was triggered.
            assert mock_info.called
        finally:
            window.deleteLater()

    def test_restore_with_no_selection_shows_info_dialog(
        self, qapp, runtime_with_real_file
    ):
        rt, _entity, _real_file = runtime_with_real_file
        window = CuratorMainWindow(rt)
        try:
            window._trash_view.clearSelection()
            window._trash_view.setCurrentIndex(window._trash_model.index(-1, -1))
            with patch.object(QMessageBox, "information") as mock_info:
                window._slot_restore_selected()
            assert mock_info.called
        finally:
            window.deleteLater()
