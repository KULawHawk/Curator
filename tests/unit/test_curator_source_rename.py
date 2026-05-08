"""Tracer Phase 4 P1 tests: ``curator_source_rename`` hookspec + impls.

Covers the new ``curator_source_rename(source_id, file_id, new_name, *,
overwrite=False)`` hook on both the local FS source plugin and the
Drive source plugin. Per design v0.2 RATIFIED \u00a73 DM-2: Optional[FileInfo]
return; ``overwrite`` kwarg matching ``curator_source_write``'s pattern;
``FileExistsError`` raised on sibling collision when ``overwrite=False``;
``None`` return when the plugin doesn't own the source_id.

Test layout:

* ``TestLocalRename``  -- 5 tests against the local FS impl
* ``TestGdriveRename`` -- 5 tests against the Drive impl with mocked client
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from curator.plugins.core.local_source import (
    Plugin as LocalPlugin,
    SOURCE_TYPE as LOCAL_SOURCE_TYPE,
)
from curator.plugins.core.gdrive_source import (
    Plugin as GDrivePlugin,
    SOURCE_TYPE as GDRIVE_SOURCE_TYPE,
)


# ===========================================================================
# Local FS impl
# ===========================================================================


class TestLocalRename:
    """``LocalPlugin.curator_source_rename`` end-to-end."""

    def test_rename_to_new_name_same_parent(self, tmp_path: Path):
        """Basic rename: same parent dir, new basename."""
        original = tmp_path / "original.mp3"
        original.write_bytes(b"some audio")
        plugin = LocalPlugin()

        info = plugin.curator_source_rename(
            "local", str(original), "renamed.mp3",
        )

        assert info is not None
        assert info.file_id == str(tmp_path / "renamed.mp3")
        assert info.path == str(tmp_path / "renamed.mp3")
        assert info.size == len(b"some audio")
        assert not original.exists()
        assert (tmp_path / "renamed.mp3").exists()
        assert (tmp_path / "renamed.mp3").read_bytes() == b"some audio"

    def test_rename_returns_file_info_with_inode(self, tmp_path: Path):
        """Returned FileInfo carries the new file's stat (inode preserved on Unix; >0 on Windows)."""
        original = tmp_path / "x.txt"
        original.write_bytes(b"hi")
        plugin = LocalPlugin()

        info = plugin.curator_source_rename("local", str(original), "y.txt")

        assert info is not None
        assert "inode" in info.extras
        assert info.is_directory is False
        assert isinstance(info.mtime, datetime)
        assert isinstance(info.ctime, datetime)

    def test_rename_into_existing_without_overwrite_raises_FileExistsError(
        self, tmp_path: Path,
    ):
        """Default overwrite=False MUST raise FileExistsError on sibling collision."""
        original = tmp_path / "src.mp3"
        original.write_bytes(b"src content")
        existing = tmp_path / "collision.mp3"
        existing.write_bytes(b"existing content")
        plugin = LocalPlugin()

        with pytest.raises(FileExistsError, match="already exists"):
            plugin.curator_source_rename(
                "local", str(original), "collision.mp3",
            )
        # Source untouched on raise
        assert original.exists()
        assert original.read_bytes() == b"src content"
        # Collision target untouched
        assert existing.read_bytes() == b"existing content"

    def test_rename_with_overwrite_true_replaces(self, tmp_path: Path):
        """overwrite=True replaces an existing sibling atomically."""
        original = tmp_path / "src.mp3"
        original.write_bytes(b"new content")
        existing = tmp_path / "collision.mp3"
        existing.write_bytes(b"old content")
        plugin = LocalPlugin()

        info = plugin.curator_source_rename(
            "local", str(original), "collision.mp3", overwrite=True,
        )

        assert info is not None
        assert info.file_id == str(existing)
        assert not original.exists()
        # Collision target now has the source's content
        assert existing.read_bytes() == b"new content"

    def test_rename_returns_none_for_non_local_source_id(self, tmp_path: Path):
        """Plugin returns None when source_id doesn't match its SOURCE_TYPE."""
        original = tmp_path / "x.txt"
        original.write_bytes(b"hi")
        plugin = LocalPlugin()

        # gdrive, onedrive, anything-not-local-prefix should return None
        assert plugin.curator_source_rename(
            "gdrive", str(original), "y.txt",
        ) is None
        assert plugin.curator_source_rename(
            "onedrive:foo", str(original), "y.txt",
        ) is None
        # Original file untouched
        assert original.exists()
        assert original.read_bytes() == b"hi"


# ===========================================================================
# Drive impl
# ===========================================================================


def _make_fake_drive_file(file_id: str, title: str, parent_id: str = "parent_a"):
    """Build a SimpleNamespace mimicking PyDrive2's GoogleDriveFile metadata.

    Supports dict-style get / [] access used by the gdrive plugin's rename
    impl. Tracks calls to FetchMetadata + Upload + Trash for assertions.
    """
    state = {
        "id": file_id,
        "title": title,
        "mimeType": "application/pdf",
        "fileSize": "100",
        "modifiedDate": "2026-05-08T12:00:00.000Z",
        "createdDate": "2026-05-08T11:00:00.000Z",
        "parents": [{"id": parent_id}],
    }
    fetch_calls = {"count": 0}
    upload_calls = {"count": 0}
    trash_calls = {"count": 0}

    class _FakeDriveFile(dict):
        def __init__(self):
            super().__init__(state)

        def FetchMetadata(self):
            fetch_calls["count"] += 1

        def Upload(self):
            upload_calls["count"] += 1

        def Trash(self):
            trash_calls["count"] += 1

    f = _FakeDriveFile()
    return f, fetch_calls, upload_calls, trash_calls


def _make_fake_client(target_file, sibling_files=None):
    """Build a fake Drive client whose CreateFile returns ``target_file``
    and whose ListFile returns ``sibling_files`` (default empty)."""
    if sibling_files is None:
        sibling_files = []
    list_calls = {"queries": []}

    class _FakeListing:
        def __init__(self, results):
            self._results = results

        def GetList(self):
            return self._results

    class _FakeClient:
        def CreateFile(self, metadata):
            return target_file

        def ListFile(self, params):
            list_calls["queries"].append(params.get("q", ""))
            return _FakeListing(sibling_files)

    return _FakeClient(), list_calls


class TestGdriveRename:
    """``GDrivePlugin.curator_source_rename`` with mocked Drive client."""

    def test_rename_via_title_patch(self):
        """Successful rename: FetchMetadata called, title set, Upload called."""
        f, fetch_calls, upload_calls, _ = _make_fake_drive_file(
            "drive_id_1", "old.pdf",
        )
        client, _ = _make_fake_client(f, sibling_files=[])
        plugin = GDrivePlugin()
        plugin.set_drive_client("gdrive", client)

        info = plugin.curator_source_rename(
            "gdrive", "drive_id_1", "new.pdf",
        )

        assert info is not None
        assert info.file_id == "drive_id_1"
        assert info.path == "new.pdf"
        assert fetch_calls["count"] == 1
        assert upload_calls["count"] == 1
        # Title was patched in-place before Upload
        assert f["title"] == "new.pdf"

    def test_rename_collision_raises_FileExistsError(self):
        """Sibling with target title in same parent (and not self) raises."""
        f, fetch_calls, upload_calls, _ = _make_fake_drive_file(
            "drive_id_1", "old.pdf", parent_id="parent_a",
        )
        # ListFile returns a different file with the target title in
        # the same parent -> collision.
        sibling = {"id": "drive_id_2", "title": "new.pdf"}
        client, _ = _make_fake_client(f, sibling_files=[sibling])
        plugin = GDrivePlugin()
        plugin.set_drive_client("gdrive", client)

        with pytest.raises(FileExistsError, match="already exists"):
            plugin.curator_source_rename(
                "gdrive", "drive_id_1", "new.pdf",
            )
        # Title NOT patched, Upload NOT called
        assert f["title"] == "old.pdf"
        assert upload_calls["count"] == 0

    def test_rename_self_collision_is_ignored(self):
        """If ListFile returns the same file_id (race / Drive eventual
        consistency artifact), it's NOT counted as a collision."""
        f, _, upload_calls, _ = _make_fake_drive_file(
            "drive_id_1", "old.pdf", parent_id="parent_a",
        )
        # ListFile returns the file we're renaming -> not a collision
        self_in_results = {"id": "drive_id_1", "title": "new.pdf"}
        client, _ = _make_fake_client(f, sibling_files=[self_in_results])
        plugin = GDrivePlugin()
        plugin.set_drive_client("gdrive", client)

        info = plugin.curator_source_rename(
            "gdrive", "drive_id_1", "new.pdf",
        )

        assert info is not None
        assert upload_calls["count"] == 1
        assert f["title"] == "new.pdf"

    def test_rename_with_overwrite_trashes_collider_then_renames(self):
        """overwrite=True trashes any colliding sibling before the rename."""
        f, _, upload_calls, _ = _make_fake_drive_file(
            "drive_id_1", "old.pdf", parent_id="parent_a",
        )
        # Manually construct a collider that tracks Trash calls
        collider_trash = {"count": 0}

        class _Collider(dict):
            def __init__(self):
                super().__init__({"id": "drive_id_collider", "title": "new.pdf"})

            def Trash(self):
                collider_trash["count"] += 1

        collider = _Collider()
        client, _ = _make_fake_client(f, sibling_files=[collider])
        plugin = GDrivePlugin()
        plugin.set_drive_client("gdrive", client)

        info = plugin.curator_source_rename(
            "gdrive", "drive_id_1", "new.pdf", overwrite=True,
        )

        assert info is not None
        assert collider_trash["count"] == 1, "collider was not trashed"
        assert upload_calls["count"] == 1
        assert f["title"] == "new.pdf"

    def test_rename_returns_none_for_non_gdrive_source_id(self):
        """Plugin returns None when source_id doesn't match its SOURCE_TYPE."""
        plugin = GDrivePlugin()
        # No drive client injected; the _owns check short-circuits before
        # any client lookup.
        assert plugin.curator_source_rename(
            "local", "/some/path", "new.txt",
        ) is None
        assert plugin.curator_source_rename(
            "local:home", "/some/path", "new.txt",
        ) is None
