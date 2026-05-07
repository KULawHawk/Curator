"""Tests for the v0.40 ``curator_source_write`` hook (Phase β gate 5).

Covers both source plugins:
  * ``LocalFSSource`` — atomic write via tempfile + os.replace
  * ``Google Drive`` — PyDrive2 CreateFile + Upload via injected mock client

Plus contract-level tests:
  * ``SourcePluginInfo.supports_write`` defaults to False, accepts True
  * Each plugin's ``register`` advertises ``supports_write=True``
  * The hookspec is present and has the expected signature

The local plugin's tests use a real tmp_path so the atomicity guarantees
are exercised on the real filesystem. The gdrive plugin's tests inject
a fake client to avoid real Drive API calls (same pattern as
test_gdrive_source.py).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from curator.models.types import FileInfo, SourcePluginInfo
from curator.plugins.core.gdrive_source import (
    GOOGLE_FOLDER_MIME,
    Plugin as GDrivePlugin,
)
from curator.plugins.core.local_source import Plugin as LocalPlugin


# ===========================================================================
# Contract-level tests
# ===========================================================================


class TestSourcePluginInfoSupportsWrite:
    def test_default_is_false(self):
        info = SourcePluginInfo(
            source_type="x", display_name="X",
            requires_auth=False, supports_watch=False,
        )
        assert info.supports_write is False

    def test_can_be_set_true(self):
        info = SourcePluginInfo(
            source_type="x", display_name="X",
            requires_auth=False, supports_watch=False, supports_write=True,
        )
        assert info.supports_write is True


class TestLocalAdvertisesWriteSupport:
    def test_register_supports_write_true(self):
        info = LocalPlugin().curator_source_register()
        assert info.supports_write is True


class TestGDriveAdvertisesWriteSupport:
    def test_register_supports_write_true_when_pydrive2_available(self, monkeypatch):
        monkeypatch.setattr(
            "curator.plugins.core.gdrive_source._pydrive2_available",
            lambda: True,
        )
        info = GDrivePlugin().curator_source_register()
        assert info is not None
        assert info.supports_write is True


class TestHookspecPresent:
    def test_hook_is_callable_via_plugin_manager(self):
        # The hookspec exists in curator.plugins.hookspecs and is bound
        # via the curator.plugins.manager. This is mostly a smoke check
        # that the hook name resolves.
        from curator.plugins import hookspecs
        assert hasattr(hookspecs, "curator_source_write")


# ===========================================================================
# Local source: write hook
# ===========================================================================


class TestLocalWriteBasic:
    def test_writes_bytes_to_target(self, tmp_path):
        plugin = LocalPlugin()
        info = plugin.curator_source_write(
            source_id="local",
            parent_id=str(tmp_path),
            name="hello.txt",
            data=b"Hello, world!",
        )
        target = tmp_path / "hello.txt"
        assert target.read_bytes() == b"Hello, world!"
        assert info.file_id == str(target)
        assert info.path == str(target)
        assert info.size == 13

    def test_returns_file_info_with_extras_inode(self, tmp_path):
        plugin = LocalPlugin()
        info = plugin.curator_source_write(
            source_id="local",
            parent_id=str(tmp_path),
            name="file.bin",
            data=b"data",
        )
        # On Windows + most POSIX, inode is exposed.
        assert "inode" in info.extras

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        plugin = LocalPlugin()
        info = plugin.curator_source_write(
            source_id="local",
            parent_id=str(nested),
            name="deep.txt",
            data=b"x",
        )
        assert (nested / "deep.txt").exists()

    def test_returns_none_for_non_local_source(self, tmp_path):
        plugin = LocalPlugin()
        result = plugin.curator_source_write(
            source_id="gdrive",
            parent_id=str(tmp_path),
            name="x.txt",
            data=b"x",
        )
        assert result is None


class TestLocalWriteOverwrite:
    def test_existing_file_no_overwrite_raises(self, tmp_path):
        existing = tmp_path / "existing.txt"
        existing.write_bytes(b"original")

        plugin = LocalPlugin()
        with pytest.raises(FileExistsError):
            plugin.curator_source_write(
                source_id="local",
                parent_id=str(tmp_path),
                name="existing.txt",
                data=b"new",
            )
        # Original is untouched.
        assert existing.read_bytes() == b"original"

    def test_existing_file_with_overwrite_replaces(self, tmp_path):
        existing = tmp_path / "existing.txt"
        existing.write_bytes(b"original")

        plugin = LocalPlugin()
        info = plugin.curator_source_write(
            source_id="local",
            parent_id=str(tmp_path),
            name="existing.txt",
            data=b"new content",
            overwrite=True,
        )
        assert existing.read_bytes() == b"new content"
        assert info.size == len(b"new content")


class TestLocalWriteMtime:
    def test_mtime_is_set_when_provided(self, tmp_path):
        plugin = LocalPlugin()
        target_mtime = datetime(2023, 6, 15, 12, 30, 0)
        info = plugin.curator_source_write(
            source_id="local",
            parent_id=str(tmp_path),
            name="dated.txt",
            data=b"x",
            mtime=target_mtime,
        )
        # Read back actual mtime.
        actual = (tmp_path / "dated.txt").stat().st_mtime
        # Allow 1-second tolerance for filesystem precision.
        assert abs(actual - target_mtime.timestamp()) < 1.0

    def test_mtime_omitted_uses_now(self, tmp_path):
        plugin = LocalPlugin()
        before = datetime.now().timestamp()
        plugin.curator_source_write(
            source_id="local",
            parent_id=str(tmp_path),
            name="now.txt",
            data=b"x",
        )
        after = datetime.now().timestamp()
        actual = (tmp_path / "now.txt").stat().st_mtime
        # Should be between "before" and "after" (loose bounds for clock skew).
        assert before - 2 <= actual <= after + 2


class TestLocalWriteAtomicity:
    def test_no_temp_file_left_behind_on_success(self, tmp_path):
        plugin = LocalPlugin()
        plugin.curator_source_write(
            source_id="local",
            parent_id=str(tmp_path),
            name="ok.txt",
            data=b"x",
        )
        # Only the target should exist, no .tmp leftovers.
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "ok.txt"

    def test_temp_file_cleaned_up_on_exception(self, tmp_path):
        plugin = LocalPlugin()
        # Patch os.replace to raise — simulates a mid-operation failure
        # AFTER the tempfile is written but BEFORE rename.
        with mock.patch(
            "curator.plugins.core.local_source.os.replace",
            side_effect=OSError("simulated"),
        ):
            with pytest.raises(OSError, match="simulated"):
                plugin.curator_source_write(
                    source_id="local",
                    parent_id=str(tmp_path),
                    name="failed.txt",
                    data=b"x",
                )
        # No .tmp files left behind.
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".failed")]
        assert leftovers == []

    def test_target_unchanged_on_overwrite_failure(self, tmp_path):
        existing = tmp_path / "preserve.txt"
        existing.write_bytes(b"keep me safe")

        plugin = LocalPlugin()
        with mock.patch(
            "curator.plugins.core.local_source.os.replace",
            side_effect=OSError("simulated"),
        ):
            with pytest.raises(OSError):
                plugin.curator_source_write(
                    source_id="local",
                    parent_id=str(tmp_path),
                    name="preserve.txt",
                    data=b"BAD NEW CONTENT",
                    overwrite=True,
                )
        # Original survives intact.
        assert existing.read_bytes() == b"keep me safe"


class TestLocalWriteParentValidation:
    def test_parent_is_existing_file_raises(self, tmp_path):
        # parent_id points at a file, not a dir
        bad_parent = tmp_path / "imafile.txt"
        bad_parent.write_bytes(b"x")

        plugin = LocalPlugin()
        with pytest.raises(OSError, match="not a directory"):
            plugin.curator_source_write(
                source_id="local",
                parent_id=str(bad_parent),
                name="x.txt",
                data=b"x",
            )


# ===========================================================================
# Google Drive: write hook (with injected fake client)
# ===========================================================================


class _FakeDriveFile(dict):
    """Same shape as test_gdrive_source.py's _FakeDriveFile."""

    def __init__(self, metadata: dict):
        super().__init__(metadata)
        self._uploaded = False
        self._trashed = False
        self.content = None  # set by the plugin

    def FetchMetadata(self) -> None:
        return None

    def Upload(self) -> None:
        self._uploaded = True

    def Trash(self) -> None:
        self._trashed = True


class _FakeDriveClient:
    """Fake GoogleDrive client supporting the calls write needs.

    Tracks creation history so tests can assert what happened.
    """

    def __init__(self, existing_files_in_folder: list[dict] | None = None):
        self.created: list[dict] = []        # CreateFile metadata records
        self.list_calls: list[str] = []      # queries
        # Pre-existing files (for overwrite testing).
        self.existing = existing_files_in_folder or []

    def CreateFile(self, ref: dict):
        # Track the create call.
        self.created.append(ref)
        # Build a fake response file with an assigned id.
        next_id = f"new_{len(self.created)}"
        meta = {
            "id": ref.get("id") or next_id,
            "title": ref.get("title", ""),
            "mimeType": "application/octet-stream",
            "fileSize": "0",
            "modifiedDate": "2026-05-08T10:00:00Z",
            "createdDate": "2026-05-08T10:00:00Z",
            "parents": ref.get("parents", []),
        }
        return _FakeDriveFile(meta)

    def ListFile(self, query_args: dict):
        q = query_args.get("q", "")
        self.list_calls.append(q)
        # Match against pre-existing files we want this query to return.
        # Simple matcher: if the query has "title = 'X'", return existing
        # entries whose title is X.
        result = []
        for f in self.existing:
            if f"title = '{f['title']}'" in q and f["folder"] in q:
                result.append({"id": f["id"], "title": f["title"]})
        return SimpleNamespace(GetList=lambda: result)


class TestGDriveWriteBasic:
    def test_creates_new_file(self):
        client = _FakeDriveClient()
        plugin = GDrivePlugin()
        plugin.set_drive_client("gdrive:test", client)

        info = plugin.curator_source_write(
            source_id="gdrive:test",
            parent_id="root",
            name="upload.txt",
            data=b"hello drive",
        )
        assert info is not None
        assert info.path == "upload.txt"
        # CreateFile was called once with the new file's metadata
        assert len(client.created) == 1
        assert client.created[0]["title"] == "upload.txt"
        assert client.created[0]["parents"] == [{"id": "root"}]

    def test_returns_none_for_non_owned_source(self):
        plugin = GDrivePlugin()
        result = plugin.curator_source_write(
            source_id="local",
            parent_id="root",
            name="x.txt",
            data=b"x",
        )
        assert result is None

    def test_returns_none_when_client_unavailable(self, monkeypatch):
        # Don't inject a client; the plugin will try to build one and
        # fail (no credentials).
        monkeypatch.setattr(
            "curator.plugins.core.gdrive_source._build_drive_client",
            lambda config: (_ for _ in ()).throw(RuntimeError("no creds")),
        )
        plugin = GDrivePlugin()
        result = plugin.curator_source_write(
            source_id="gdrive:fail",
            parent_id="root",
            name="x.txt",
            data=b"x",
        )
        assert result is None


class TestGDriveWriteOverwrite:
    def test_existing_no_overwrite_raises(self):
        client = _FakeDriveClient(
            existing_files_in_folder=[
                {"id": "old1", "title": "report.pdf", "folder": "folderA"},
            ],
        )
        plugin = GDrivePlugin()
        plugin.set_drive_client("gdrive:test", client)

        with pytest.raises(FileExistsError):
            plugin.curator_source_write(
                source_id="gdrive:test",
                parent_id="folderA",
                name="report.pdf",
                data=b"new",
            )
        # No file was created.
        # (CreateFile is also called for the existence-check path? No;
        # only for the actual upload, which we never reached.)
        assert client.created == []

    def test_existing_with_overwrite_trashes_old_and_creates_new(self):
        client = _FakeDriveClient(
            existing_files_in_folder=[
                {"id": "old1", "title": "report.pdf", "folder": "folderA"},
            ],
        )
        plugin = GDrivePlugin()
        plugin.set_drive_client("gdrive:test", client)

        info = plugin.curator_source_write(
            source_id="gdrive:test",
            parent_id="folderA",
            name="report.pdf",
            data=b"replacement",
            overwrite=True,
        )
        assert info is not None
        # Two CreateFile calls: one to fetch the old (for trashing),
        # one to create the new.
        assert len(client.created) == 2
        # First one was the trash target; second was the new file.
        assert client.created[0].get("id") == "old1"
        assert client.created[1]["title"] == "report.pdf"


class TestGDriveWriteFailure:
    def test_upload_failure_raises_runtime_error(self):
        client = _FakeDriveClient()
        # Patch CreateFile to return an object whose Upload raises.
        original_create = client.CreateFile

        def failing_create(ref):
            f = original_create(ref)
            f.Upload = lambda: (_ for _ in ()).throw(
                RuntimeError("simulated upload failure"),
            )
            return f

        client.CreateFile = failing_create

        plugin = GDrivePlugin()
        plugin.set_drive_client("gdrive:test", client)

        with pytest.raises(RuntimeError, match="upload of"):
            plugin.curator_source_write(
                source_id="gdrive:test",
                parent_id="root",
                name="bad.txt",
                data=b"x",
            )


class TestGDriveWriteContent:
    def test_content_is_set_to_bytes_io(self):
        client = _FakeDriveClient()
        captured: list = []

        original_create = client.CreateFile

        def capturing_create(ref):
            f = original_create(ref)
            # Wrap Upload to record what content was set before upload.
            orig_upload = f.Upload

            def upload_recording():
                captured.append(f.content)
                orig_upload()
            f.Upload = upload_recording
            return f

        client.CreateFile = capturing_create

        plugin = GDrivePlugin()
        plugin.set_drive_client("gdrive:test", client)

        plugin.curator_source_write(
            source_id="gdrive:test",
            parent_id="root",
            name="content.bin",
            data=b"these are real bytes",
        )
        # Content should have been set to a BytesIO containing our data.
        assert len(captured) == 1
        captured[0].seek(0)
        assert captured[0].read() == b"these are real bytes"
