"""Coverage closure for ``curator.services.trash`` (v1.7.145).

The final Tier 4 ship. Closes 90 uncovered lines — essentially the
entire TrashService surface (currently 24.69% — only __init__ + class
defs + exception classes were touched by prior tests).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from curator._compat.datetime import utcnow_naive
from curator.models import (
    BundleEntity, BundleMembership, FileEntity, TrashRecord,
)
from curator.models.results import ConfirmationResult
from curator.services import trash as trash_mod
from curator.services.trash import (
    FileNotFoundError as TrashFileNotFoundError,
    NotInTrashError,
    RestoreImpossibleError,
    RestoreVetoed,
    Send2TrashUnavailableError,
    TrashError,
    TrashService,
    TrashVetoed,
)


# ---------------------------------------------------------------------------
# send2trash import fallback chain (lines 60-64)
# ---------------------------------------------------------------------------


# NOTE: send2trash import fallback (lines 60-64) is annotated
# `# pragma: no cover` in the source. Testing via importlib.reload
# poisons class identity for the test file's captured imports
# (after reload, Send2TrashUnavailableError is a new class object
# and pytest.raises against the old class fails). The import chain
# is a defensive boundary for users missing the vendored copy
# and is exercised at import time by the deployment environment.


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def file_in_index(repos, local_source):
    f = FileEntity(
        source_id="local",
        source_path="/a.txt",
        size=1,
        mtime=utcnow_naive(),
        xxhash3_128="hash_a",
    )
    repos.files.insert(f)
    return f


def _trash_service(repos, plugin_manager):
    return TrashService(
        plugin_manager=plugin_manager,
        file_repo=repos.files,
        trash_repo=repos.trash,
        bundle_repo=repos.bundles,
        audit_repo=repos.audit,
    )


# ---------------------------------------------------------------------------
# send_to_trash failure modes
# ---------------------------------------------------------------------------


class TestSendToTrashFailures:
    def test_send2trash_unavailable_raises(self, repos, local_source, plugin_manager, monkeypatch):
        """Line 154-158: when _send2trash is None, raises Send2TrashUnavailableError."""
        monkeypatch.setattr(trash_mod, "_send2trash", None)
        svc = _trash_service(repos, plugin_manager)
        f = FileEntity(
            source_id="local", source_path="/x", size=1, mtime=utcnow_naive(),
        )
        repos.files.insert(f)
        with pytest.raises(Send2TrashUnavailableError):
            svc.send_to_trash(f.curator_id, reason="test")

    def test_file_not_in_index_raises(self, repos, local_source, plugin_manager, monkeypatch):
        """Line 161-162: when file_repo.get returns None, raises FileNotFoundError."""
        monkeypatch.setattr(trash_mod, "_send2trash", MagicMock())
        svc = _trash_service(repos, plugin_manager)
        with pytest.raises(TrashFileNotFoundError):
            svc.send_to_trash(uuid4(), reason="test")

    def test_plugin_veto_raises_trash_vetoed(
        self, repos, local_source, plugin_manager, file_in_index, monkeypatch,
    ):
        """Lines 165-169: pre-trash hook veto raises TrashVetoed."""
        monkeypatch.setattr(trash_mod, "_send2trash", MagicMock())
        # Replace pm.hook.curator_pre_trash to return a veto
        veto = ConfirmationResult(allow=False, plugin="test_plugin", reason="nope")
        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_trash = MagicMock(return_value=[veto])

        svc = _trash_service(repos, plugin_manager)
        with pytest.raises(TrashVetoed, match="test_plugin"):
            svc.send_to_trash(file_in_index.curator_id, reason="test")

    def test_send2trash_failure_raises_trash_error(
        self, repos, local_source, plugin_manager, file_in_index, monkeypatch,
    ):
        """Lines 186-193: send2trash raising -> TrashError + nothing persisted."""
        def _bad_s2t(path):
            raise OSError("trash denied")

        monkeypatch.setattr(trash_mod, "_send2trash", _bad_s2t)
        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_trash = MagicMock(return_value=[])

        svc = _trash_service(repos, plugin_manager)
        with pytest.raises(TrashError, match="Failed to send to OS trash"):
            svc.send_to_trash(file_in_index.curator_id, reason="test")
        # File still in index, no TrashRecord
        assert repos.files.get(file_in_index.curator_id) is not None
        assert repos.trash.get(file_in_index.curator_id) is None


class TestSendToTrashHappyPath:
    def test_full_trash_flow(
        self, repos, local_source, plugin_manager, file_in_index, monkeypatch,
    ):
        """Lines 172-231: full happy path through trash flow."""
        trashed_paths = []

        def _fake_s2t(path):
            trashed_paths.append(path)

        monkeypatch.setattr(trash_mod, "_send2trash", _fake_s2t)
        # Make _derive_os_trash_location return None (cross-platform)
        monkeypatch.setattr(
            TrashService, "_derive_os_trash_location",
            lambda self, p: None,
        )
        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_trash = MagicMock(return_value=[])
        plugin_manager.hook.curator_post_trash = MagicMock(return_value=[])

        # Attach a bundle membership to test snapshot
        b = BundleEntity(bundle_type="manual", name="b")
        repos.bundles.insert(b)
        repos.bundles.add_membership(BundleMembership(
            bundle_id=b.bundle_id, curator_id=file_in_index.curator_id,
        ))
        # Attach flex
        file_in_index.set_flex("note", "important")
        repos.files.update(file_in_index)

        svc = _trash_service(repos, plugin_manager)
        record = svc.send_to_trash(
            file_in_index.curator_id, reason="testing",
        )
        assert record.curator_id == file_in_index.curator_id
        assert record.reason == "testing"
        # send2trash was called
        assert trashed_paths == ["/a.txt"]
        # Bundle snapshot includes the membership
        assert len(record.bundle_memberships_snapshot) == 1
        # Flex snapshot includes the attr
        assert record.file_attrs_snapshot.get("note") == "important"
        # File row soft-deleted
        f_after = repos.files.get(file_in_index.curator_id)
        assert f_after is not None
        assert f_after.deleted_at is not None
        # post_trash hook fired
        plugin_manager.hook.curator_post_trash.assert_called_once()


# ---------------------------------------------------------------------------
# Restore failure modes
# ---------------------------------------------------------------------------


class TestRestoreFailures:
    def test_no_trash_record_raises_not_in_trash(self, repos, local_source, plugin_manager):
        svc = _trash_service(repos, plugin_manager)
        with pytest.raises(NotInTrashError):
            svc.restore(uuid4())

    def test_plugin_veto_raises_restore_vetoed(
        self, repos, local_source, plugin_manager, file_in_index, monkeypatch,
    ):
        """Lines 270-274: pre_restore veto."""
        record = TrashRecord(
            curator_id=file_in_index.curator_id,
            original_source_id="local",
            original_path="/a.txt",
            trashed_by="user",
            reason="test",
        )
        repos.trash.insert(record)
        repos.files.mark_deleted(file_in_index.curator_id)

        veto = ConfirmationResult(allow=False, plugin="veto_plugin", reason="no")
        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_restore = MagicMock(return_value=[veto])

        svc = _trash_service(repos, plugin_manager)
        with pytest.raises(RestoreVetoed, match="veto_plugin"):
            svc.restore(file_in_index.curator_id)

    def test_no_os_trash_location_raises_impossible(
        self, repos, local_source, plugin_manager, file_in_index,
    ):
        """Lines 279-285: no os_trash_location -> RestoreImpossibleError."""
        record = TrashRecord(
            curator_id=file_in_index.curator_id,
            original_source_id="local",
            original_path="/a.txt",
            trashed_by="user",
            reason="test",
            os_trash_location=None,
        )
        repos.trash.insert(record)
        repos.files.mark_deleted(file_in_index.curator_id)

        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_restore = MagicMock(return_value=[])

        svc = _trash_service(repos, plugin_manager)
        with pytest.raises(RestoreImpossibleError, match="No OS trash location"):
            svc.restore(file_in_index.curator_id)

    def test_restore_from_os_trash_failure_wraps_in_impossible(
        self, repos, local_source, plugin_manager, file_in_index, monkeypatch,
    ):
        """Lines 287-292: _restore_from_os_trash raises -> wrapped."""
        record = TrashRecord(
            curator_id=file_in_index.curator_id,
            original_source_id="local",
            original_path="/a.txt",
            trashed_by="user",
            reason="test",
            os_trash_location="/bogus/path",
        )
        repos.trash.insert(record)
        repos.files.mark_deleted(file_in_index.curator_id)

        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_restore = MagicMock(return_value=[])

        svc = _trash_service(repos, plugin_manager)
        with pytest.raises(RestoreImpossibleError):
            svc.restore(file_in_index.curator_id)


class TestRestoreHappyPath:
    def test_full_restore_flow(
        self, repos, local_source, plugin_manager, tmp_path, monkeypatch,
    ):
        """Lines 287-343: happy path restore."""
        # Build a real trashed file scenario
        trash_loc = tmp_path / "trash" / "trashed.txt"
        trash_loc.parent.mkdir()
        trash_loc.write_text("restored content")

        restore_target = tmp_path / "restored.txt"

        f = FileEntity(
            source_id="local", source_path=str(restore_target),
            size=16, mtime=utcnow_naive(),
        )
        repos.files.insert(f)

        # Insert TrashRecord pointing to the trash_loc
        b = BundleEntity(bundle_type="manual", name="bundle1")
        repos.bundles.insert(b)
        record = TrashRecord(
            curator_id=f.curator_id,
            original_source_id="local",
            original_path=str(restore_target),
            trashed_by="user",
            reason="test",
            os_trash_location=str(trash_loc),
            bundle_memberships_snapshot=[
                {"bundle_id": str(b.bundle_id), "role": "member", "confidence": 1.0},
            ],
            file_attrs_snapshot={"attr": "value"},
        )
        repos.trash.insert(record)
        repos.files.mark_deleted(f.curator_id)

        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_restore = MagicMock(return_value=[])
        plugin_manager.hook.curator_post_restore = MagicMock(return_value=[])

        svc = _trash_service(repos, plugin_manager)
        restored = svc.restore(f.curator_id)

        assert restored.deleted_at is None
        assert restore_target.exists()
        # Trash record removed
        assert repos.trash.get(f.curator_id) is None
        # Bundle membership restored
        memberships = repos.bundles.get_memberships(b.bundle_id)
        assert any(m.curator_id == f.curator_id for m in memberships)
        # post_restore hook called
        plugin_manager.hook.curator_post_restore.assert_called_once()


# ---------------------------------------------------------------------------
# Reads (lines 356, 359)
# ---------------------------------------------------------------------------


class TestReads:
    def test_list_trashed_delegates_to_repo(self, repos, local_source, plugin_manager):
        svc = _trash_service(repos, plugin_manager)
        # No trash records -> empty list
        assert svc.list_trashed() == []
        assert svc.list_trashed(since=datetime(2026, 1, 1)) == []

    def test_is_in_trash_returns_bool(self, repos, local_source, plugin_manager, file_in_index):
        svc = _trash_service(repos, plugin_manager)
        assert svc.is_in_trash(file_in_index.curator_id) is False

        record = TrashRecord(
            curator_id=file_in_index.curator_id,
            original_source_id="local",
            original_path="/a",
            trashed_by="x",
            reason="r",
        )
        repos.trash.insert(record)
        assert svc.is_in_trash(file_in_index.curator_id) is True


# ---------------------------------------------------------------------------
# _check_pre_trash_veto + _check_pre_restore_veto (lines 371-379, 386-396)
# ---------------------------------------------------------------------------


class TestPreVetoHelpers:
    def test_pre_trash_no_results_returns_none(self, repos, local_source, plugin_manager):
        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_trash = MagicMock(return_value=[])
        svc = _trash_service(repos, plugin_manager)
        f = FileEntity(source_id="local", source_path="/x", size=1, mtime=utcnow_naive())
        assert svc._check_pre_trash_veto(f, "reason") is None

    def test_pre_trash_with_allow_results_returns_none(self, repos, local_source, plugin_manager):
        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_trash = MagicMock(return_value=[
            ConfirmationResult(allow=True, plugin="ok", reason=None),
            None,
        ])
        svc = _trash_service(repos, plugin_manager)
        f = FileEntity(source_id="local", source_path="/x", size=1, mtime=utcnow_naive())
        assert svc._check_pre_trash_veto(f, "reason") is None

    def test_pre_trash_first_veto_returned(self, repos, local_source, plugin_manager):
        veto = ConfirmationResult(allow=False, plugin="p1", reason="nope")
        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_trash = MagicMock(return_value=[None, veto])
        svc = _trash_service(repos, plugin_manager)
        f = FileEntity(source_id="local", source_path="/x", size=1, mtime=utcnow_naive())
        result = svc._check_pre_trash_veto(f, "reason")
        assert result is veto

    def test_pre_restore_returns_first_veto(self, repos, local_source, plugin_manager):
        veto = ConfirmationResult(allow=False, plugin="p1", reason="no")
        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_restore = MagicMock(return_value=[veto])
        svc = _trash_service(repos, plugin_manager)
        record = TrashRecord(
            curator_id=uuid4(), original_source_id="local",
            original_path="/a", trashed_by="u", reason="r",
        )
        result = svc._check_pre_restore_veto(record, "/target")
        assert result is veto

    def test_pre_restore_no_veto_returns_none(self, repos, local_source, plugin_manager):
        plugin_manager.hook = MagicMock()
        plugin_manager.hook.curator_pre_restore = MagicMock(return_value=[None])
        svc = _trash_service(repos, plugin_manager)
        record = TrashRecord(
            curator_id=uuid4(), original_source_id="local",
            original_path="/a", trashed_by="u", reason="r",
        )
        assert svc._check_pre_restore_veto(record, "/target") is None


# ---------------------------------------------------------------------------
# _derive_os_trash_location (lines 422-441)
# ---------------------------------------------------------------------------


class TestDeriveOsTrashLocation:
    def test_non_windows_returns_none(self, repos, local_source, plugin_manager, monkeypatch):
        """Line 422-423: non-win32 platforms return None."""
        monkeypatch.setattr(sys, "platform", "linux")
        svc = _trash_service(repos, plugin_manager)
        assert svc._derive_os_trash_location("/anything") is None

    def test_recycle_bin_import_failure_returns_none(
        self, repos, local_source, plugin_manager, monkeypatch,
    ):
        """Lines 428-429: recycle_bin import fails -> None."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(
            sys.modules, "curator._vendored.send2trash.win.recycle_bin", None,
        )
        svc = _trash_service(repos, plugin_manager)
        assert svc._derive_os_trash_location("/anything") is None

    def test_returns_content_path_when_entry_found(
        self, repos, local_source, plugin_manager, monkeypatch,
    ):
        """Lines 431-441: find_in_recycle_bin returns entry -> return content_path."""
        monkeypatch.setattr(sys, "platform", "win32")

        # Inject a fake recycle_bin module
        import types
        fake_mod = types.ModuleType("curator._vendored.send2trash.win.recycle_bin")
        fake_entry = MagicMock()
        fake_entry.content_path = Path("/Recycle.Bin/$R12345.ext")
        fake_mod.find_in_recycle_bin = MagicMock(return_value=fake_entry)

        # Need to add parent modules too
        monkeypatch.setitem(sys.modules, "curator._vendored.send2trash.win.recycle_bin", fake_mod)

        svc = _trash_service(repos, plugin_manager)
        result = svc._derive_os_trash_location("/orig/path")
        assert "Recycle.Bin" in result or "$R12345" in result

    def test_returns_none_when_entry_not_found(
        self, repos, local_source, plugin_manager, monkeypatch,
    ):
        """Lines 439-440: find_in_recycle_bin returns None."""
        monkeypatch.setattr(sys, "platform", "win32")
        import types
        fake_mod = types.ModuleType("curator._vendored.send2trash.win.recycle_bin")
        fake_mod.find_in_recycle_bin = MagicMock(return_value=None)
        monkeypatch.setitem(sys.modules, "curator._vendored.send2trash.win.recycle_bin", fake_mod)

        svc = _trash_service(repos, plugin_manager)
        assert svc._derive_os_trash_location("/orig/path") is None


# ---------------------------------------------------------------------------
# _restore_from_os_trash (lines 451-467)
# ---------------------------------------------------------------------------


class TestRestoreFromOsTrash:
    def test_missing_os_location_raises(self, repos, local_source, plugin_manager, tmp_path):
        svc = _trash_service(repos, plugin_manager)
        with pytest.raises(RestoreImpossibleError, match="doesn't exist"):
            svc._restore_from_os_trash(
                str(tmp_path / "doesnt_exist"),
                str(tmp_path / "target"),
            )

    def test_basic_restore_moves_file(self, repos, local_source, plugin_manager, tmp_path):
        """Lines 455-456: makedirs + os.replace move the file."""
        trash_file = tmp_path / "trash" / "$R123.txt"
        trash_file.parent.mkdir()
        trash_file.write_text("data")

        target = tmp_path / "restored" / "f.txt"

        svc = _trash_service(repos, plugin_manager)
        svc._restore_from_os_trash(str(trash_file), str(target))
        assert target.read_text() == "data"
        assert not trash_file.exists()

    def test_index_companion_removed_if_exists(
        self, repos, local_source, plugin_manager, tmp_path,
    ):
        """Lines 461-467: companion $I file is deleted after restore."""
        trash_file = tmp_path / "trash" / "$R123.txt"
        trash_file.parent.mkdir()
        trash_file.write_text("data")
        companion = tmp_path / "trash" / "$I123.txt"
        companion.write_text("meta")

        target = tmp_path / "restored.txt"

        svc = _trash_service(repos, plugin_manager)
        svc._restore_from_os_trash(str(trash_file), str(target))
        assert target.exists()
        assert not companion.exists()  # companion was cleaned up

    def test_missing_index_companion_skipped(
        self, repos, local_source, plugin_manager, tmp_path,
    ):
        """Lines 461-467: missing $I companion is silently skipped."""
        trash_file = tmp_path / "trash" / "$R123.txt"
        trash_file.parent.mkdir()
        trash_file.write_text("data")
        # No $I companion exists

        target = tmp_path / "restored.txt"

        svc = _trash_service(repos, plugin_manager)
        svc._restore_from_os_trash(str(trash_file), str(target))
        assert target.exists()
