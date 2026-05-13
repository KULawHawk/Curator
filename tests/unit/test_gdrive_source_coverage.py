"""Coverage closure for ``curator.plugins.core.gdrive_source`` (v1.7.125).

The pre-existing ``test_gdrive_source.py`` and
``test_gdrive_source_v151_config_resolution.py`` cover the happy paths
via :meth:`Plugin.set_drive_client` mock injection. The remaining
branches are:

* ``_pydrive2_available`` ImportError fallback (PyDrive2 IS installed in
  this env, so we must inject ``sys.modules['pydrive2'] = None`` to
  force the ImportError).
* ``_build_drive_client`` — full body. PyDrive2 is real but slow + does
  network/disk work; we shim it via ``sys.modules`` with fakes that
  expose ``GoogleAuth`` and ``GoogleDrive``.
* ``_drive_file_to_file_info`` / ``_drive_file_to_file_stat`` size
  parse error branches (TypeError / ValueError / native-mime).
* Every hookimpl's ``client is None`` short-circuit + its broad
  ``except Exception`` arm.
* ``curator_source_rename`` — covers all four branches: non-owned,
  client-None, FetchMetadata failure, overwrite=False + collider raises
  FileExistsError, overwrite=True + sibling-trash, Upload failure.
* ``curator_source_write`` existence-check failure + trash-existing
  failure.
* ``_owns`` DB-lookup arms (source matches / source mismatch / DB
  raises).
* ``_get_or_build_client`` build-exception branch.
* ``_resolve_config`` source_repo.get exception, disk-fallback
  exception, and the bottom ``return None`` when ``source_config_for_alias``
  returns config without ``client_secrets_path``.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from curator.plugins.core import gdrive_source as gs_mod
from curator.plugins.core.gdrive_source import (
    GOOGLE_FOLDER_MIME,
    GOOGLE_NATIVE_PREFIX,
    Plugin,
    _build_drive_client,
    _drive_file_to_file_info,
    _drive_file_to_file_stat,
    _pydrive2_available,
)


# ---------------------------------------------------------------------------
# Helpers: fake PyDrive2 module injection
# ---------------------------------------------------------------------------


class _FakeGoogleAuth:
    """Stand-in for ``pydrive2.auth.GoogleAuth``.

    Class-level toggles configure behavior per test:
        credentials_present: bool — if False, ``.credentials`` is None
            (triggers the "no credentials" RuntimeError branch).
        access_token_expired: bool — controls the Refresh vs Authorize
            branch.
    """

    credentials_present = True
    access_token_expired = False

    def __init__(self):
        # Mirror PyDrive2's interface.
        self.client_config_loaded: str | None = None
        self.credentials_loaded: str | None = None
        self.refreshed = False
        self.saved_creds_path: str | None = None
        self.authorized = False

    @property
    def credentials(self):
        return object() if type(self).credentials_present else None

    def LoadClientConfigFile(self, path):
        self.client_config_loaded = path

    def LoadCredentialsFile(self, path):
        self.credentials_loaded = path

    def Refresh(self):
        self.refreshed = True

    def SaveCredentialsFile(self, path):
        self.saved_creds_path = path

    def Authorize(self):
        self.authorized = True


class _FakeGoogleDrive:
    """Stand-in for ``pydrive2.drive.GoogleDrive(gauth)``."""

    def __init__(self, gauth):
        self.gauth = gauth


@pytest.fixture
def fake_pydrive2(monkeypatch):
    """Inject fake ``pydrive2.auth`` + ``pydrive2.drive`` modules into
    ``sys.modules`` so ``_build_drive_client``'s internal imports
    resolve to the fakes.

    Resets the class-level toggles before each use so tests don't leak
    state.
    """
    _FakeGoogleAuth.credentials_present = True
    _FakeGoogleAuth.access_token_expired = False

    pydrive2_mod = types.ModuleType("pydrive2")
    auth_mod = types.ModuleType("pydrive2.auth")
    auth_mod.GoogleAuth = _FakeGoogleAuth
    drive_mod = types.ModuleType("pydrive2.drive")
    drive_mod.GoogleDrive = _FakeGoogleDrive
    pydrive2_mod.auth = auth_mod
    pydrive2_mod.drive = drive_mod

    monkeypatch.setitem(sys.modules, "pydrive2", pydrive2_mod)
    monkeypatch.setitem(sys.modules, "pydrive2.auth", auth_mod)
    monkeypatch.setitem(sys.modules, "pydrive2.drive", drive_mod)
    return SimpleNamespace(
        GoogleAuth=_FakeGoogleAuth,
        GoogleDrive=_FakeGoogleDrive,
    )


# ---------------------------------------------------------------------------
# _pydrive2_available — ImportError branch
# ---------------------------------------------------------------------------


class TestPydrive2AvailableImportError:
    def test_returns_false_when_pydrive2_import_fails(self, monkeypatch):
        # ``sys.modules[name] = None`` triggers ImportError on `import name`
        # (Python's standard sentinel pattern).
        monkeypatch.setitem(sys.modules, "pydrive2", None)
        assert _pydrive2_available() is False


# ---------------------------------------------------------------------------
# _build_drive_client — full body via injected fakes
# ---------------------------------------------------------------------------


class TestBuildDriveClient:
    def test_missing_client_secrets_raises_runtime_error(self, fake_pydrive2):
        with pytest.raises(RuntimeError, match="requires both"):
            _build_drive_client({"credentials_path": "/x"})  # client_secrets_path missing

    def test_missing_credentials_raises_runtime_error(self, fake_pydrive2):
        with pytest.raises(RuntimeError, match="requires both"):
            _build_drive_client({"client_secrets_path": "/x"})  # credentials_path missing

    def test_none_credentials_raises_runtime_error(self, fake_pydrive2):
        fake_pydrive2.GoogleAuth.credentials_present = False
        with pytest.raises(RuntimeError, match="No credentials at"):
            _build_drive_client({
                "client_secrets_path": "/s.json",
                "credentials_path": "/c.json",
            })

    def test_expired_token_calls_refresh_and_save(self, fake_pydrive2):
        fake_pydrive2.GoogleAuth.access_token_expired = True
        drive = _build_drive_client({
            "client_secrets_path": "/s.json",
            "credentials_path": "/c.json",
        })
        assert isinstance(drive, fake_pydrive2.GoogleDrive)
        assert drive.gauth.refreshed is True
        assert drive.gauth.saved_creds_path == "/c.json"
        assert drive.gauth.authorized is False

    def test_fresh_token_calls_authorize(self, fake_pydrive2):
        fake_pydrive2.GoogleAuth.access_token_expired = False
        drive = _build_drive_client({
            "client_secrets_path": "/s.json",
            "credentials_path": "/c.json",
        })
        assert drive.gauth.authorized is True
        assert drive.gauth.refreshed is False


# ---------------------------------------------------------------------------
# _drive_file_to_file_info / _drive_file_to_file_stat — error branches
# ---------------------------------------------------------------------------


class TestDriveFileToFileInfoErrors:
    def test_unparseable_size_falls_back_to_zero(self):
        info = _drive_file_to_file_info({
            "id": "x",
            "title": "x.bin",
            "mimeType": "application/octet-stream",
            "fileSize": "not-a-number",  # raises ValueError on int()
            "modifiedDate": "2026-01-15T00:00:00Z",
        })
        assert info.size == 0

    def test_typeerror_size_falls_back_to_zero(self):
        info = _drive_file_to_file_info({
            "id": "x",
            "title": "x.bin",
            "mimeType": "application/octet-stream",
            "fileSize": object(),  # int(object()) raises TypeError
            "modifiedDate": "2026-01-15T00:00:00Z",
        })
        assert info.size == 0


class TestDriveFileToFileStatBranches:
    def test_native_mime_yields_zero_size(self):
        stat = _drive_file_to_file_stat({
            "id": "doc",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "fileSize": "9999",
            "modifiedDate": "2026-01-15T00:00:00Z",
        })
        assert stat.size == 0
        assert stat.extras["drive_native"] is True

    def test_unparseable_size_falls_back_to_zero(self):
        stat = _drive_file_to_file_stat({
            "id": "x",
            "mimeType": "application/pdf",
            "fileSize": "garbage",
            "modifiedDate": "2026-01-15T00:00:00Z",
        })
        assert stat.size == 0

    def test_typeerror_size_falls_back_to_zero(self):
        stat = _drive_file_to_file_stat({
            "id": "x",
            "mimeType": "application/pdf",
            "fileSize": object(),
            "modifiedDate": "2026-01-15T00:00:00Z",
        })
        assert stat.size == 0


# ---------------------------------------------------------------------------
# Test scaffolding: a tiny Plugin factory that mocks _resolve_config to
# return None (so _get_or_build_client returns None, exercising every
# hook's "client is None" short-circuit branch).
# ---------------------------------------------------------------------------


@pytest.fixture
def plugin_no_config(monkeypatch):
    """Plugin where _resolve_config always returns None, so every hook
    that calls _get_or_build_client gets ``client=None`` and hits its
    short-circuit return."""
    p = Plugin()
    monkeypatch.setattr(p, "_resolve_config", lambda sid, opts: None)
    return p


class TestHookShortCircuitsOnNoneClient:
    def test_enumerate_returns_none_when_client_is_none(self, plugin_no_config):
        result = plugin_no_config.curator_source_enumerate(
            source_id="gdrive:x", root="root", options={},
        )
        assert result is None

    def test_stat_returns_none_when_client_is_none(self, plugin_no_config):
        assert plugin_no_config.curator_source_stat("gdrive:x", "f") is None

    def test_read_bytes_returns_none_when_client_is_none(self, plugin_no_config):
        assert plugin_no_config.curator_source_read_bytes(
            "gdrive:x", "f", 0, 10,
        ) is None

    def test_delete_returns_none_when_client_is_none(self, plugin_no_config):
        assert plugin_no_config.curator_source_delete(
            "gdrive:x", "f", to_trash=True,
        ) is None

    def test_rename_returns_none_when_client_is_none(self, plugin_no_config):
        assert plugin_no_config.curator_source_rename(
            "gdrive:x", "f", "new",
        ) is None

    def test_write_returns_none_when_client_is_none(self, plugin_no_config):
        assert plugin_no_config.curator_source_write(
            "gdrive:x", "parent", "name.txt", b"data",
        ) is None


# ---------------------------------------------------------------------------
# _iter_folder — ListFile exception arm
# ---------------------------------------------------------------------------


class TestIterFolderListFailure:
    def test_listfile_exception_skips_folder_and_continues(self, monkeypatch):
        """If the Drive ListFile call raises for one folder, the BFS
        logs a warning and continues with the queue."""

        class _ExplodingDrive:
            def __init__(self):
                self.calls = 0

            def ListFile(self, query_args):
                self.calls += 1
                # First folder: raise. Subsequent folders: return [].
                if self.calls == 1:
                    raise RuntimeError("api down")
                return SimpleNamespace(GetList=lambda: [])

        drive = _ExplodingDrive()
        plugin = Plugin()
        # Pre-populate so _owns succeeds via the 'gdrive:' prefix
        plugin.set_drive_client("gdrive:iter_err", drive)
        result = list(plugin.curator_source_enumerate(
            source_id="gdrive:iter_err", root="folderA", options={},
        ))
        assert result == []
        assert drive.calls == 1  # didn't enqueue more folders


# ---------------------------------------------------------------------------
# stat / read_bytes / delete — broad exception arms
# ---------------------------------------------------------------------------


class _ExplodingDriveClient:
    """Drive client whose CreateFile returns a file-like that explodes
    on every method call."""

    def __init__(self):
        self._exploder = self._make_exploder()

    def _make_exploder(self):
        m = MagicMock()
        m.FetchMetadata.side_effect = RuntimeError("boom")
        m.GetContentString.side_effect = RuntimeError("boom")
        m.Trash.side_effect = RuntimeError("boom")
        m.Delete.side_effect = RuntimeError("boom")
        return m

    def CreateFile(self, ref):
        return self._exploder


class TestStatExceptions:
    def test_stat_returns_none_on_exception(self):
        plugin = Plugin()
        plugin.set_drive_client("gdrive:stat_err", _ExplodingDriveClient())
        assert plugin.curator_source_stat("gdrive:stat_err", "f1") is None


class TestReadBytesExceptions:
    def test_read_bytes_returns_none_on_exception(self):
        plugin = Plugin()
        plugin.set_drive_client("gdrive:read_err", _ExplodingDriveClient())
        result = plugin.curator_source_read_bytes(
            "gdrive:read_err", "f1", offset=0, length=10,
        )
        assert result is None


class TestDeleteBranches:
    def test_delete_returns_none_for_non_owned_source(self):
        plugin = Plugin()
        assert plugin.curator_source_delete(
            "local:x", "f1", to_trash=True,
        ) is None

    def test_delete_returns_false_on_exception(self):
        plugin = Plugin()
        plugin.set_drive_client("gdrive:del_err", _ExplodingDriveClient())
        assert plugin.curator_source_delete(
            "gdrive:del_err", "f1", to_trash=True,
        ) is False

    def test_permanent_delete_returns_false_on_exception(self):
        plugin = Plugin()
        plugin.set_drive_client("gdrive:del_err", _ExplodingDriveClient())
        assert plugin.curator_source_delete(
            "gdrive:del_err", "f1", to_trash=False,
        ) is False


# ---------------------------------------------------------------------------
# curator_source_rename — every branch
# ---------------------------------------------------------------------------


class _FakeRenameDriveFile(dict):
    """Dict-like Drive file with metadata + Upload + FetchMetadata."""

    def __init__(self, metadata, *, fetch_raises=False, upload_raises=False):
        super().__init__(metadata)
        self._fetch_raises = fetch_raises
        self._upload_raises = upload_raises
        self.uploaded = False
        self.trashed = False

    def FetchMetadata(self):
        if self._fetch_raises:
            raise RuntimeError("fetch failed")

    def Upload(self):
        if self._upload_raises:
            raise RuntimeError("upload failed")
        self.uploaded = True

    def Trash(self):
        self.trashed = True


class TestRenameBranches:
    def test_returns_none_for_non_owned_source(self):
        plugin = Plugin()
        result = plugin.curator_source_rename(
            "local:x", "f1", "newname",
        )
        assert result is None

    def test_fetch_metadata_exception_returns_none(self):
        target = _FakeRenameDriveFile({"id": "f1"}, fetch_raises=True)
        drive = MagicMock()
        drive.CreateFile.return_value = target

        plugin = Plugin()
        plugin.set_drive_client("gdrive:rename_err", drive)
        result = plugin.curator_source_rename(
            "gdrive:rename_err", "f1", "newname",
        )
        assert result is None

    def test_collider_raises_file_exists_error(self):
        """overwrite=False + a sibling collider exists -> FileExistsError."""
        target_md = {
            "id": "f1",
            "title": "old.txt",
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
            "parents": [{"id": "parent_folder"}],
        }
        target = _FakeRenameDriveFile(target_md)
        collider = {"id": "other", "title": "newname", "mimeType": "text/plain"}

        drive = MagicMock()
        drive.CreateFile.return_value = target
        drive.ListFile.return_value.GetList.return_value = [collider]

        plugin = Plugin()
        plugin.set_drive_client("gdrive:rename_clash", drive)
        with pytest.raises(FileExistsError, match="already exists"):
            plugin.curator_source_rename(
                "gdrive:rename_clash", "f1", "newname", overwrite=False,
            )

    def test_collider_query_exception_treated_as_no_colliders(self):
        """Sibling query failure -> treated as no colliders; rename proceeds."""
        target_md = {
            "id": "f1",
            "title": "old.txt",
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
            "parents": [{"id": "parent_folder"}],
        }
        target = _FakeRenameDriveFile(target_md)

        drive = MagicMock()
        drive.CreateFile.return_value = target
        drive.ListFile.side_effect = RuntimeError("list api down")

        plugin = Plugin()
        plugin.set_drive_client("gdrive:rename_listfail", drive)
        result = plugin.curator_source_rename(
            "gdrive:rename_listfail", "f1", "newname", overwrite=False,
        )
        # The query failed but was caught -> siblings=[] -> rename
        # proceeds via Upload().
        assert result is not None
        assert target.uploaded is True

    def test_self_id_excluded_from_collider_check(self):
        """If the only "sibling" with matching title IS the target file
        itself, that's not a collision -> rename proceeds."""
        target_md = {
            "id": "f1",
            "title": "newname",  # title already matches new_name
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
            "parents": [{"id": "parent_folder"}],
        }
        target = _FakeRenameDriveFile(target_md)
        drive = MagicMock()
        drive.CreateFile.return_value = target
        # Returned sibling list contains only self
        drive.ListFile.return_value.GetList.return_value = [{"id": "f1"}]

        plugin = Plugin()
        plugin.set_drive_client("gdrive:rename_self", drive)
        result = plugin.curator_source_rename(
            "gdrive:rename_self", "f1", "newname", overwrite=False,
        )
        assert result is not None
        assert target.uploaded is True

    def test_overwrite_true_trashes_colliders(self):
        target_md = {
            "id": "f1",
            "title": "old.txt",
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
            "parents": [{"id": "parent_folder"}],
        }
        target = _FakeRenameDriveFile(target_md)
        collider = MagicMock()
        collider.get.side_effect = lambda k, default=None: (
            "collider_id" if k == "id" else default
        )
        # Track Trash calls on the collider
        trashed = []
        collider.Trash.side_effect = lambda: trashed.append(True)

        drive = MagicMock()
        drive.CreateFile.return_value = target
        drive.ListFile.return_value.GetList.return_value = [collider]

        plugin = Plugin()
        plugin.set_drive_client("gdrive:rename_ow", drive)
        result = plugin.curator_source_rename(
            "gdrive:rename_ow", "f1", "newname", overwrite=True,
        )
        assert result is not None
        assert trashed == [True]
        assert target.uploaded is True

    def test_overwrite_true_skips_self_in_sibling_trash_loop(self):
        """When the sibling-with-matching-title is the target file
        itself, the trash loop's ``if s.get('id') != file_id`` is False
        and the iteration continues without calling Trash. Covers the
        545->544 partial branch."""
        target_md = {
            "id": "f1",
            "title": "newname",
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
            "parents": [{"id": "parent_folder"}],
        }
        target = _FakeRenameDriveFile(target_md)
        # The "sibling" matching the new title IS the target itself.
        # The loop must skip Trash() for it.
        self_sibling = MagicMock()
        self_sibling.get.side_effect = lambda k, default=None: (
            "f1" if k == "id" else default
        )
        trashed = []
        self_sibling.Trash.side_effect = lambda: trashed.append(True)

        drive = MagicMock()
        drive.CreateFile.return_value = target
        drive.ListFile.return_value.GetList.return_value = [self_sibling]

        plugin = Plugin()
        plugin.set_drive_client("gdrive:rename_self_ow", drive)
        result = plugin.curator_source_rename(
            "gdrive:rename_self_ow", "f1", "newname", overwrite=True,
        )
        assert result is not None
        # Self was NOT trashed (loop guard kept it)
        assert trashed == []
        assert target.uploaded is True

    def test_overwrite_true_collider_trash_failure_logged_and_continues(self):
        target_md = {
            "id": "f1",
            "title": "old.txt",
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
            "parents": [{"id": "parent_folder"}],
        }
        target = _FakeRenameDriveFile(target_md)
        collider = MagicMock()
        collider.get.side_effect = lambda k, default=None: (
            "collider_id" if k == "id" else default
        )
        collider.Trash.side_effect = RuntimeError("trash refused")

        drive = MagicMock()
        drive.CreateFile.return_value = target
        drive.ListFile.return_value.GetList.return_value = [collider]

        plugin = Plugin()
        plugin.set_drive_client("gdrive:rename_trashfail", drive)
        result = plugin.curator_source_rename(
            "gdrive:rename_trashfail", "f1", "newname", overwrite=True,
        )
        # Trash threw but was caught; rename still proceeds
        assert result is not None
        assert target.uploaded is True

    def test_overwrite_true_sibling_query_failure_logs_and_continues(self):
        target_md = {
            "id": "f1",
            "title": "old.txt",
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
            "parents": [{"id": "parent_folder"}],
        }
        target = _FakeRenameDriveFile(target_md)
        drive = MagicMock()
        drive.CreateFile.return_value = target
        drive.ListFile.side_effect = RuntimeError("list api down")

        plugin = Plugin()
        plugin.set_drive_client("gdrive:rename_ow_listfail", drive)
        result = plugin.curator_source_rename(
            "gdrive:rename_ow_listfail", "f1", "newname", overwrite=True,
        )
        # Sibling query failed, log+continue -> Upload runs
        assert result is not None
        assert target.uploaded is True

    def test_upload_failure_raises(self):
        target_md = {
            "id": "f1",
            "title": "old.txt",
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
            "parents": [],  # no parents -> skip sibling check
        }
        target = _FakeRenameDriveFile(target_md, upload_raises=True)
        drive = MagicMock()
        drive.CreateFile.return_value = target

        plugin = Plugin()
        plugin.set_drive_client("gdrive:rename_uplfail", drive)
        with pytest.raises(RuntimeError, match="upload failed"):
            plugin.curator_source_rename(
                "gdrive:rename_uplfail", "f1", "newname", overwrite=False,
            )


# ---------------------------------------------------------------------------
# curator_source_write — existence-check + trash-existing failure arms
# ---------------------------------------------------------------------------


class TestWriteFailurePaths:
    def test_existence_check_failure_treated_as_no_existing(self):
        """If the pre-flight ListFile query fails, the plugin logs a
        warning and proceeds with the upload as if no collision exists."""
        new_md = {
            "id": "new_id",
            "title": "x.txt",
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
        }
        new_file = MagicMock()
        new_file.__getitem__.side_effect = new_md.__getitem__
        new_file.get.side_effect = lambda k, default=None: new_md.get(k, default)

        drive = MagicMock()
        drive.ListFile.side_effect = RuntimeError("list api down")
        drive.CreateFile.return_value = new_file

        plugin = Plugin()
        plugin.set_drive_client("gdrive:write_lf_fail", drive)
        # No collision check => upload proceeds
        result = plugin.curator_source_write(
            source_id="gdrive:write_lf_fail",
            parent_id="parent_folder_id",
            name="x.txt",
            data=b"hello",
            overwrite=False,
        )
        assert result is not None
        new_file.Upload.assert_called_once()

    def test_overwrite_true_trash_existing_failure_logs_and_continues(self):
        """When overwrite=True and trashing an existing collider fails,
        the warning is logged and the upload proceeds."""
        new_md = {
            "id": "new_id",
            "title": "x.txt",
            "mimeType": "text/plain",
            "fileSize": "5",
            "modifiedDate": "2026-01-15T00:00:00Z",
        }
        new_file = MagicMock()
        new_file.__getitem__.side_effect = new_md.__getitem__
        new_file.get.side_effect = lambda k, default=None: new_md.get(k, default)

        existing_collider = {"id": "old_id"}

        # CreateFile is called twice: once for the existing collider
        # (must support .Trash) and once for the new upload.
        existing_obj = MagicMock()
        existing_obj.Trash.side_effect = RuntimeError("trash denied")

        def _create_file(ref):
            if ref.get("id") == "old_id":
                return existing_obj
            return new_file

        drive = MagicMock()
        drive.ListFile.return_value.GetList.return_value = [existing_collider]
        drive.CreateFile.side_effect = _create_file

        plugin = Plugin()
        plugin.set_drive_client("gdrive:write_trash_fail", drive)
        result = plugin.curator_source_write(
            source_id="gdrive:write_trash_fail",
            parent_id="parent_folder_id",
            name="x.txt",
            data=b"hello",
            overwrite=True,
        )
        # Trash threw but was caught; new file uploaded
        assert result is not None
        new_file.Upload.assert_called_once()

    def test_overwrite_false_with_collider_raises_file_exists_error(self):
        drive = MagicMock()
        drive.ListFile.return_value.GetList.return_value = [{"id": "old"}]

        plugin = Plugin()
        plugin.set_drive_client("gdrive:write_clash", drive)
        with pytest.raises(FileExistsError, match="already exists"):
            plugin.curator_source_write(
                source_id="gdrive:write_clash",
                parent_id="parent_folder_id",
                name="x.txt",
                data=b"hello",
                overwrite=False,
            )

    def test_upload_failure_wraps_in_runtime_error(self):
        new_file = MagicMock()
        new_file.Upload.side_effect = RuntimeError("upload denied")

        drive = MagicMock()
        drive.ListFile.return_value.GetList.return_value = []
        drive.CreateFile.return_value = new_file

        plugin = Plugin()
        plugin.set_drive_client("gdrive:write_upl_fail", drive)
        with pytest.raises(RuntimeError, match="upload of"):
            plugin.curator_source_write(
                source_id="gdrive:write_upl_fail",
                parent_id="parent_folder_id",
                name="x.txt",
                data=b"hello",
                overwrite=False,
            )


# ---------------------------------------------------------------------------
# _owns — DB-lookup arms (source_repo injection)
# ---------------------------------------------------------------------------


class TestOwnsDBLookup:
    """The legacy convention (`gdrive` / `gdrive:...`) is tested in
    test_gdrive_source.py. These tests target the DB-lookup fallback
    branches added by the v1.6.5 fix."""

    def test_db_match_returns_true(self):
        repo = MagicMock()
        src = SimpleNamespace(source_type="gdrive")
        repo.get.return_value = src

        p = Plugin()
        p.set_source_repo(repo)
        # source_id doesn't match the legacy convention; relies on DB lookup
        assert p._owns("my_custom_drive_alias") is True

    def test_db_returns_none_returns_false(self):
        repo = MagicMock()
        repo.get.return_value = None

        p = Plugin()
        p.set_source_repo(repo)
        assert p._owns("unknown_id") is False

    def test_db_returns_wrong_source_type_returns_false(self):
        repo = MagicMock()
        repo.get.return_value = SimpleNamespace(source_type="local")

        p = Plugin()
        p.set_source_repo(repo)
        assert p._owns("local_disguised") is False

    def test_db_lookup_exception_returns_false(self):
        repo = MagicMock()
        repo.get.side_effect = RuntimeError("db blew up")

        p = Plugin()
        p.set_source_repo(repo)
        assert p._owns("transient_db_err") is False


# ---------------------------------------------------------------------------
# _get_or_build_client — exception branch in _build_drive_client
# ---------------------------------------------------------------------------


class TestGetOrBuildClientBuildFailure:
    def test_build_drive_client_runtime_error_returns_none(self, monkeypatch):
        """If _build_drive_client raises one of the expected exceptions,
        _get_or_build_client logs a warning and returns None."""
        repo = MagicMock()
        from curator.models.source import SourceConfig
        sc = SourceConfig(
            source_id="gdrive:build_err",
            source_type="gdrive",
            display_name="x",
            config={
                "client_secrets_path": "/x",
                "credentials_path": "/y",
            },
            enabled=True,
        )
        repo.get.return_value = sc

        def _raise_runtime(_cfg):
            raise RuntimeError("auth expired")

        monkeypatch.setattr(gs_mod, "_build_drive_client", _raise_runtime)

        p = Plugin()
        p.set_source_repo(repo)
        client = p._get_or_build_client("gdrive:build_err", options={})
        assert client is None

    def test_build_drive_client_import_error_returns_none(self, monkeypatch):
        repo = MagicMock()
        from curator.models.source import SourceConfig
        sc = SourceConfig(
            source_id="gdrive:ie",
            source_type="gdrive",
            display_name="x",
            config={
                "client_secrets_path": "/x",
                "credentials_path": "/y",
            },
            enabled=True,
        )
        repo.get.return_value = sc

        def _raise_ie(_cfg):
            raise ImportError("pydrive2 gone")

        monkeypatch.setattr(gs_mod, "_build_drive_client", _raise_ie)

        p = Plugin()
        p.set_source_repo(repo)
        assert p._get_or_build_client("gdrive:ie", options={}) is None

    def test_build_drive_client_file_not_found_returns_none(self, monkeypatch):
        repo = MagicMock()
        from curator.models.source import SourceConfig
        sc = SourceConfig(
            source_id="gdrive:fnf",
            source_type="gdrive",
            display_name="x",
            config={
                "client_secrets_path": "/x",
                "credentials_path": "/y",
            },
            enabled=True,
        )
        repo.get.return_value = sc

        def _raise_fnf(_cfg):
            raise FileNotFoundError("secrets missing")

        monkeypatch.setattr(gs_mod, "_build_drive_client", _raise_fnf)

        p = Plugin()
        p.set_source_repo(repo)
        assert p._get_or_build_client("gdrive:fnf", options={}) is None


# ---------------------------------------------------------------------------
# _resolve_config — exception arms + bottom return None
# ---------------------------------------------------------------------------


class TestResolveConfigExceptionArms:
    def test_source_repo_get_exception_falls_through_to_disk_or_none(
        self, monkeypatch, tmp_path,
    ):
        """A source_repo.get() raise is caught; falls through to the
        disk fallback. With no disk files, returns None."""
        repo = MagicMock()
        repo.get.side_effect = RuntimeError("db transient")

        p = Plugin()
        p.set_source_repo(repo)

        # Empty disk fallback (no client_secrets at the expected path)
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        result = p._resolve_config("gdrive:db_err", options={})
        assert result is None

    def test_disk_fallback_exception_returns_none(self, monkeypatch, tmp_path):
        """When the disk fallback (paths_for_alias / source_config_for_alias)
        raises, the except logs a warning and returns None."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        alias_dir = tmp_path / "gdrive" / "disk_err"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")

        # Force source_config_for_alias to raise
        import curator.services.gdrive_auth as gauth_mod

        def _raise(*args, **kwargs):
            raise RuntimeError("disk read error")

        monkeypatch.setattr(gauth_mod, "source_config_for_alias", _raise)

        p = Plugin()  # no source_repo
        result = p._resolve_config("gdrive:disk_err", options={})
        assert result is None

    def test_resolve_returns_none_when_disk_config_missing_client_secrets_path(
        self, monkeypatch, tmp_path,
    ):
        """If source_config_for_alias returns a config that lacks
        client_secrets_path, the bottom-of-function ``return None``
        fires (line 816)."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        alias_dir = tmp_path / "gdrive" / "empty_cfg"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")

        import curator.services.gdrive_auth as gauth_mod
        # source_config_for_alias returns an incomplete config dict
        monkeypatch.setattr(
            gauth_mod, "source_config_for_alias",
            lambda alias, **kw: {"root_folder_id": "anything"},
        )

        p = Plugin()
        result = p._resolve_config("gdrive:empty_cfg", options={})
        assert result is None
