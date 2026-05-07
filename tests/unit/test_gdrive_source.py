"""Unit tests for the Google Drive source plugin scaffolding.

We can't (and shouldn't) make real Drive API calls in CI, so these
tests:

  * Verify the plugin's static contract (SOURCE_TYPE, _owns logic,
    register signature, mime-type handling, datetime parsing).
  * Use ``Plugin.set_drive_client(...)`` to inject a fake client and
    exercise the enumerate/stat/read_bytes/delete code paths.

The PyDrive2 dep is optional (lives in ``[cloud]`` extras). The
plugin's ``register`` returns None when PyDrive2 isn't installed; we
verify that behavior too.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from curator.plugins.core.gdrive_source import (
    GOOGLE_FOLDER_MIME,
    GOOGLE_NATIVE_PREFIX,
    SOURCE_TYPE,
    Plugin,
    _drive_file_to_file_info,
    _drive_file_to_file_stat,
    _parse_drive_datetime,
    _pydrive2_available,
)


# ---------------------------------------------------------------------------
# Static contract
# ---------------------------------------------------------------------------

class TestStaticContract:
    def test_source_type_is_gdrive(self):
        assert SOURCE_TYPE == "gdrive"

    def test_native_prefix_constant(self):
        assert GOOGLE_NATIVE_PREFIX == "application/vnd.google-apps."

    def test_folder_mime_constant(self):
        assert GOOGLE_FOLDER_MIME == "application/vnd.google-apps.folder"

    def test_owns_bare_gdrive(self):
        assert Plugin()._owns("gdrive") is True

    def test_owns_gdrive_with_alias(self):
        assert Plugin()._owns("gdrive:jake@personal") is True

    def test_does_not_own_other_sources(self):
        p = Plugin()
        assert p._owns("local") is False
        assert p._owns("local:home") is False
        assert p._owns("onedrive") is False
        # Tricky: source IDs that happen to start with the letters g-d-r-i-v-e
        # but aren't gdrive: should NOT be claimed.
        assert p._owns("gdriveXYZ") is False


# ---------------------------------------------------------------------------
# Datetime parsing
# ---------------------------------------------------------------------------

class TestParseDriveDatetime:
    def test_parses_iso_with_z(self):
        result = _parse_drive_datetime("2026-01-15T12:34:56.789Z")
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 12

    def test_parses_iso_without_z(self):
        result = _parse_drive_datetime("2026-01-15T12:34:56")
        assert result == datetime(2026, 1, 15, 12, 34, 56)

    def test_none_returns_now_ish(self):
        result = _parse_drive_datetime(None)
        # Just verify it returns SOME datetime; "now" is hard to assert.
        assert isinstance(result, datetime)

    def test_malformed_falls_back_to_now(self):
        result = _parse_drive_datetime("not a date")
        assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# Drive metadata -> Curator types
# ---------------------------------------------------------------------------

class TestDriveFileToFileInfo:
    def test_regular_file(self):
        md = {
            "id": "abc123",
            "title": "report.pdf",
            "mimeType": "application/pdf",
            "fileSize": "12345",
            "modifiedDate": "2026-01-15T12:34:56.789Z",
            "createdDate": "2026-01-10T08:00:00.000Z",
            "parents": [{"id": "parent1"}],
        }
        info = _drive_file_to_file_info(md)
        assert info.file_id == "abc123"
        assert info.path == "report.pdf"
        assert info.size == 12345
        assert info.is_directory is False
        assert info.extras["mime_type"] == "application/pdf"
        assert info.extras["drive_native"] is False
        assert info.extras["drive_parents"] == ["parent1"]

    def test_google_native_file_has_zero_size(self):
        md = {
            "id": "doc456",
            "title": "My Notes",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedDate": "2026-01-15T12:34:56.789Z",
        }
        info = _drive_file_to_file_info(md)
        assert info.size == 0
        assert info.extras["drive_native"] is True

    def test_handles_missing_size(self):
        md = {
            "id": "x",
            "title": "x.bin",
            "mimeType": "application/octet-stream",
            "modifiedDate": "2026-01-15T12:34:56Z",
        }
        info = _drive_file_to_file_info(md)
        assert info.size == 0  # missing fileSize -> 0

    def test_uses_v3_field_names_too(self):
        # Drive API v3 uses 'name' instead of 'title' and 'modifiedTime'
        # instead of 'modifiedDate'. Our parser handles both.
        md = {
            "id": "v3",
            "name": "v3-style.txt",
            "mimeType": "text/plain",
            "size": "100",
            "modifiedTime": "2026-01-15T12:34:56Z",
            "createdTime": "2026-01-10T00:00:00Z",
        }
        info = _drive_file_to_file_info(md)
        assert info.path == "v3-style.txt"
        assert info.size == 100


class TestDriveFileToFileStat:
    def test_basic_stat(self):
        md = {
            "id": "x",
            "mimeType": "text/plain",
            "fileSize": "500",
            "modifiedDate": "2026-01-15T12:00:00Z",
            "createdDate": "2026-01-10T00:00:00Z",
        }
        stat = _drive_file_to_file_stat(md)
        assert stat.file_id == "x"
        assert stat.size == 500
        assert stat.inode is None  # Drive has no inode concept
        assert stat.extras["mime_type"] == "text/plain"


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_returns_none_when_pydrive2_unavailable(self, monkeypatch):
        # Force the availability check to return False.
        monkeypatch.setattr(
            "curator.plugins.core.gdrive_source._pydrive2_available",
            lambda: False,
        )
        info = Plugin().curator_source_register()
        assert info is None

    def test_returns_info_when_pydrive2_available(self, monkeypatch):
        monkeypatch.setattr(
            "curator.plugins.core.gdrive_source._pydrive2_available",
            lambda: True,
        )
        info = Plugin().curator_source_register()
        assert info is not None
        assert info.source_type == "gdrive"
        assert info.requires_auth is True
        assert info.supports_watch is False
        assert "credentials_path" in info.config_schema["required"]
        assert "client_secrets_path" in info.config_schema["required"]


# ---------------------------------------------------------------------------
# Plugin operations with injected mock client
# ---------------------------------------------------------------------------

def _fake_drive_file(metadata: dict) -> "_FakeDriveFile":
    """Behave just enough like a PyDrive2 GoogleDriveFile for our tests.

    Real GoogleDriveFile is a dict subclass with extra methods
    (.FetchMetadata, .Trash, .GetContentString, .Delete). We mimic that
    shape exactly so subscript access (md["id"]) and .get() both work.
    """
    return _FakeDriveFile(metadata)


class _FakeDriveFile(dict):
    """Dict subclass with the GoogleDriveFile-shaped methods used by Plugin."""

    def __init__(self, metadata: dict):
        super().__init__(metadata)
        self._trashed = False
        self._deleted = False

    def FetchMetadata(self) -> None:
        return None

    def GetContentString(self) -> str:
        return self.get("_content", "")

    def Trash(self) -> None:
        self._trashed = True

    def Delete(self) -> None:
        self._deleted = True


class _FakeDriveClient:
    """Mock GoogleDrive client that maps id -> metadata dict."""

    def __init__(self, files_by_id: dict[str, dict], folder_children: dict[str, list[str]]):
        self.files_by_id = files_by_id
        self.folder_children = folder_children
        self.last_query: str | None = None

    def CreateFile(self, ref: dict):
        return _fake_drive_file(self.files_by_id[ref["id"]])

    def ListFile(self, query_args: dict):
        self.last_query = query_args.get("q", "")
        return SimpleNamespace(GetList=lambda: self._list_for_query(query_args["q"]))

    def _list_for_query(self, q: str):
        # Parse the parent ID from the canned query format the plugin uses:
        # "'<id>' in parents and trashed = false"
        if " in parents" not in q:
            return []
        parent = q.split("'")[1]
        child_ids = self.folder_children.get(parent, [])
        return [self.files_by_id[cid] for cid in child_ids]


class TestEnumerate:
    def test_yields_files_skipping_folders(self):
        files = {
            "root":   {"id": "root",   "title": "root",   "mimeType": GOOGLE_FOLDER_MIME},
            "folder1":{"id": "folder1","title": "subdir", "mimeType": GOOGLE_FOLDER_MIME},
            "doc1":   {"id": "doc1",   "title": "a.txt",  "mimeType": "text/plain", "fileSize": "10",
                       "modifiedDate": "2026-01-15T00:00:00Z"},
            "doc2":   {"id": "doc2",   "title": "b.txt",  "mimeType": "text/plain", "fileSize": "20",
                       "modifiedDate": "2026-01-15T00:00:00Z"},
            "doc3":   {"id": "doc3",   "title": "c.txt",  "mimeType": "text/plain", "fileSize": "30",
                       "modifiedDate": "2026-01-15T00:00:00Z"},
        }
        children = {
            "root":    ["folder1", "doc1"],
            "folder1": ["doc2", "doc3"],
        }
        client = _FakeDriveClient(files, children)
        plugin = Plugin()
        plugin.set_drive_client("gdrive:test", client)

        result = list(plugin.curator_source_enumerate(
            source_id="gdrive:test",
            root="root",
            options={},
        ))
        names = sorted(r.path for r in result)
        # Three files, two folders excluded.
        assert names == ["a.txt", "b.txt", "c.txt"]

    def test_returns_none_for_non_gdrive_source(self):
        plugin = Plugin()
        result = plugin.curator_source_enumerate(
            source_id="local",
            root="/tmp",
            options={},
        )
        assert result is None

    def test_no_infinite_loop_on_self_referencing_folder(self):
        files = {
            "loop": {"id": "loop", "title": "loop", "mimeType": GOOGLE_FOLDER_MIME},
            "doc": {"id": "doc", "title": "doc.txt", "mimeType": "text/plain",
                    "fileSize": "1", "modifiedDate": "2026-01-15T00:00:00Z"},
        }
        children = {
            "loop": ["loop", "doc"],  # ← parent of itself; would loop without visited set
        }
        client = _FakeDriveClient(files, children)
        plugin = Plugin()
        plugin.set_drive_client("gdrive:loop_test", client)

        result = list(plugin.curator_source_enumerate(
            source_id="gdrive:loop_test", root="loop", options={},
        ))
        assert len(result) == 1


class TestStat:
    def test_returns_filestat_for_known_file(self):
        files = {
            "f1": {"id": "f1", "title": "x.pdf", "mimeType": "application/pdf",
                   "fileSize": "999", "modifiedDate": "2026-01-15T00:00:00Z"},
        }
        client = _FakeDriveClient(files, {})
        plugin = Plugin()
        plugin.set_drive_client("gdrive:stat_test", client)

        stat = plugin.curator_source_stat("gdrive:stat_test", "f1")
        assert stat is not None
        assert stat.size == 999

    def test_returns_none_for_non_owned_source(self):
        plugin = Plugin()
        assert plugin.curator_source_stat("local", "anything") is None


class TestDelete:
    def test_to_trash_calls_trash(self):
        files = {
            "f1": {"id": "f1", "title": "x.txt", "mimeType": "text/plain"},
        }
        client = _FakeDriveClient(files, {})
        plugin = Plugin()
        plugin.set_drive_client("gdrive:del_test", client)

        # Patch CreateFile to return a tracked obj.
        tracked = _fake_drive_file(files["f1"])
        client.CreateFile = lambda ref: tracked

        result = plugin.curator_source_delete(
            source_id="gdrive:del_test", file_id="f1", to_trash=True,
        )
        assert result is True
        assert tracked._trashed is True
        assert tracked._deleted is False

    def test_permanent_delete_calls_delete(self):
        client = _FakeDriveClient({}, {})
        tracked = _fake_drive_file({"id": "f1", "title": "x", "mimeType": "text/plain"})
        client.CreateFile = lambda ref: tracked

        plugin = Plugin()
        plugin.set_drive_client("gdrive:permadel", client)

        result = plugin.curator_source_delete(
            source_id="gdrive:permadel", file_id="f1", to_trash=False,
        )
        assert result is True
        assert tracked._deleted is True
        assert tracked._trashed is False


class TestReadBytes:
    def test_returns_offset_window(self):
        client = _FakeDriveClient({}, {})
        tracked = _fake_drive_file({
            "id": "f1", "title": "x.txt", "mimeType": "text/plain",
            "_content": "Hello, this is a test file.",
        })
        client.CreateFile = lambda ref: tracked

        plugin = Plugin()
        plugin.set_drive_client("gdrive:read_test", client)

        # Read bytes 7..11 (== "this ")
        result = plugin.curator_source_read_bytes(
            source_id="gdrive:read_test", file_id="f1", offset=7, length=5,
        )
        assert result == b"this "


class TestMove:
    def test_raises_not_implemented_for_owned_source(self):
        plugin = Plugin()
        plugin.set_drive_client("gdrive:move_test", _FakeDriveClient({}, {}))
        with pytest.raises(NotImplementedError, match="Phase Gamma"):
            plugin.curator_source_move(
                source_id="gdrive:move_test",
                file_id="f1",
                new_path="anywhere",
            )

    def test_returns_none_for_non_owned_source(self):
        # No NotImplementedError because we delegate to other plugins
        # before even thinking about the operation.
        plugin = Plugin()
        result = plugin.curator_source_move(
            source_id="local", file_id="x", new_path="/y",
        )
        assert result is None
