"""Unit tests for v1.5.1 SourceConfig resolution + parent_id translation.

These tests exist specifically to lock in the v1.5.1 fix per the
CHANGELOG note:

    Test additions for the new resolution path are deferred to a
    follow-up commit (existing tests use ``set_drive_client()`` mock
    injection which bypasses ``_resolve_config()`` entirely; a
    dedicated integration test is the proper coverage).

This is that follow-up. The pre-existing ``test_gdrive_source.py``
covers the static contract, datetime parsing, and the per-hook code
paths via ``set_drive_client()`` mock injection. That mocking strategy
is the very reason both v1.5.1 bugs went undetected: it bypassed
``_get_or_build_client()`` entirely (so no SourceConfig resolution
ever ran in tests) and accepted any ``parent_id`` value (so the
``"/"`` -> ``root_folder_id`` translation was never exercised).

This file tests the production code path:

* :meth:`Plugin.set_source_repo` injection mirrors the
  ``audit_writer.set_audit_repo`` two-step pattern.
* :meth:`Plugin._resolve_config` walks the four-tier priority order
  documented in its docstring.
* :meth:`Plugin._resolve_parent_id` maps root sentinels (``/``,
  ``\\``, ``""``, ``.``, ``None``) to the configured
  ``root_folder_id`` and passes real Drive folder IDs through.
* The integration: ``curator_source_write`` uses the resolved
  ``parent_id`` and resolved client (verified by inspecting what
  gets passed to a mocked ``_build_drive_client``).

Future cross-source plugins (OneDrive, Dropbox) should mirror this
test pattern from the start.
"""

from __future__ import annotations

from unittest import mock
from unittest.mock import MagicMock

import pytest

from curator.models.source import SourceConfig
from curator.plugins.core.gdrive_source import Plugin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def real_config():
    """A realistic gdrive SourceConfig dict like setup_gdrive_source.py
    would produce."""
    return {
        "client_secrets_path": "/fake/path/to/client_secrets.json",
        "credentials_path": "/fake/path/to/credentials.json",
        "root_folder_id": "1hIavkBD1F18Y12HupJyrWg591pq1rPw9",
    }


@pytest.fixture
def stub_source_repo(real_config):
    """A MagicMock SourceRepository whose ``.get('gdrive:src_drive')``
    returns a real-looking SourceConfig with the given config dict.
    """
    repo = MagicMock()
    sc = SourceConfig(
        source_id="gdrive:src_drive",
        source_type="gdrive",
        display_name="Google Drive (src_drive)",
        config=real_config,
        enabled=True,
    )

    def _get(source_id):
        if source_id == "gdrive:src_drive":
            return sc
        return None

    repo.get.side_effect = _get
    return repo


@pytest.fixture
def plugin_with_repo(stub_source_repo):
    """Fresh Plugin with ``set_source_repo`` injected. Mirrors what
    ``build_runtime`` does in production."""
    p = Plugin()
    p.set_source_repo(stub_source_repo)
    return p


# ---------------------------------------------------------------------------
# set_source_repo injection (mirrors AuditWriterPlugin.set_audit_repo)
# ---------------------------------------------------------------------------


class TestSetSourceRepo:
    def test_default_state_no_repo(self):
        p = Plugin()
        assert p._source_repo is None

    def test_set_source_repo_stores_reference(self, stub_source_repo):
        p = Plugin()
        p.set_source_repo(stub_source_repo)
        assert p._source_repo is stub_source_repo

    def test_set_source_repo_can_be_called_multiple_times(self):
        p = Plugin()
        repo1 = MagicMock()
        repo2 = MagicMock()
        p.set_source_repo(repo1)
        p.set_source_repo(repo2)
        # Last write wins
        assert p._source_repo is repo2

    def test_default_state_has_empty_config_cache(self):
        p = Plugin()
        assert p._config_cache == {}


# ---------------------------------------------------------------------------
# _resolve_config: four-tier priority walk
# ---------------------------------------------------------------------------


class TestResolveConfigPriority:
    """The documented priority order from _resolve_config's docstring:
    1. options['source_config']
    2. self._config_cache[source_id]
    3. self._source_repo.get(source_id).config
    4. source_config_for_alias(alias)
    """

    def test_tier_1_options_source_config_preferred(
        self, plugin_with_repo, real_config,
    ):
        """When options carries source_config, use it directly without
        consulting source_repo or disk."""
        # source_repo would return real_config; options carries a different one
        options_config = {
            "client_secrets_path": "/different/secrets.json",
            "credentials_path": "/different/creds.json",
            "root_folder_id": "OPTIONS_ROOT_ID",
        }
        result = plugin_with_repo._resolve_config(
            "gdrive:src_drive",
            options={"source_config": options_config},
        )
        assert result["root_folder_id"] == "OPTIONS_ROOT_ID"

    def test_tier_1_options_without_client_secrets_falls_through(
        self, plugin_with_repo, real_config,
    ):
        """An options['source_config'] that's missing client_secrets_path
        is treated as 'no useful options config' and falls through to
        the next tier."""
        result = plugin_with_repo._resolve_config(
            "gdrive:src_drive",
            options={"source_config": {"root_folder_id": "x"}},  # missing creds
        )
        # Falls through to source_repo (tier 3) which returns real_config
        assert result["root_folder_id"] == real_config["root_folder_id"]

    def test_tier_2_cache_used_after_first_resolution(
        self, plugin_with_repo,
    ):
        """Once resolved (any tier), subsequent calls hit the cache."""
        # First call: resolves via source_repo
        first = plugin_with_repo._resolve_config(
            "gdrive:src_drive", options={},
        )
        # Stub the source_repo to crash if called again
        plugin_with_repo._source_repo.get.side_effect = AssertionError(
            "should not be called; cache should hit"
        )
        # Second call: should hit cache, not source_repo
        second = plugin_with_repo._resolve_config(
            "gdrive:src_drive", options={},
        )
        assert second is first  # same dict object via cache

    def test_tier_3_source_repo_used_when_no_options(
        self, plugin_with_repo, real_config,
    ):
        """When options is empty, fall back to source_repo lookup."""
        result = plugin_with_repo._resolve_config(
            "gdrive:src_drive", options={},
        )
        assert result["client_secrets_path"] == real_config["client_secrets_path"]
        assert result["root_folder_id"] == real_config["root_folder_id"]

    def test_tier_3_source_repo_returns_none_falls_through_to_disk(
        self, real_config, monkeypatch, tmp_path,
    ):
        """When source_repo returns None for the source_id, fall through
        to the disk fallback (which uses source_config_for_alias)."""
        repo = MagicMock()
        repo.get.return_value = None
        p = Plugin()
        p.set_source_repo(repo)

        # Set up disk fallback: CURATOR_HOME points at tmp_path with a
        # gdrive/<alias>/ structure containing fake client_secrets and
        # credentials files (existence is enough; source_config_for_alias
        # doesn't read them).
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        gdrive_dir = tmp_path / "gdrive" / "src_drive"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")
        (gdrive_dir / "credentials.json").write_text("{}")

        result = p._resolve_config("gdrive:src_drive", options={})
        # Disk fallback yields paths under tmp_path with default root="root"
        assert result is not None
        assert "client_secrets.json" in result["client_secrets_path"]
        assert result["root_folder_id"] == "root"  # disk fallback default

    def test_tier_4_disk_fallback_when_no_source_repo(
        self, monkeypatch, tmp_path,
    ):
        """A plugin with no source_repo injected (set_source_repo never
        called) falls back to disk via source_config_for_alias."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        gdrive_dir = tmp_path / "gdrive" / "src_drive"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")

        p = Plugin()  # no set_source_repo call
        result = p._resolve_config("gdrive:src_drive", options={})
        assert result is not None
        assert "client_secrets.json" in result["client_secrets_path"]

    def test_returns_none_for_non_gdrive_source_id(self, plugin_with_repo):
        """source_ids that don't match the gdrive ownership pattern
        AND aren't in source_repo return None."""
        plugin_with_repo._source_repo.get.return_value = None
        result = plugin_with_repo._resolve_config(
            "local", options={},
        )
        assert result is None

    def test_returns_none_when_no_repo_no_options_no_disk(
        self, monkeypatch, tmp_path,
    ):
        """All four tiers exhausted -> return None."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        # tmp_path/gdrive/src_drive/ does not exist -> disk fallback fails
        p = Plugin()  # no source_repo
        result = p._resolve_config("gdrive:src_drive", options={})
        assert result is None

    def test_alias_extraction_for_bare_gdrive_source_id(
        self, monkeypatch, tmp_path,
    ):
        """source_id='gdrive' (no colon) extracts alias='default'."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        default_dir = tmp_path / "gdrive" / "default"
        default_dir.mkdir(parents=True)
        (default_dir / "client_secrets.json").write_text("{}")

        p = Plugin()
        result = p._resolve_config("gdrive", options={})
        assert result is not None
        assert "default" in result["client_secrets_path"]


# ---------------------------------------------------------------------------
# _resolve_parent_id: sentinel translation
# ---------------------------------------------------------------------------


class TestResolveParentId:
    """Per the design: root sentinels (/, \\, '', ., None) map to the
    configured root_folder_id; real Drive folder IDs pass through."""

    @pytest.mark.parametrize(
        "sentinel",
        ["/", "\\", "", ".", None],
        ids=["forward-slash", "back-slash", "empty", "dot", "None"],
    )
    def test_root_sentinel_maps_to_configured_root_folder_id(
        self, plugin_with_repo, real_config, sentinel,
    ):
        # Pre-warm the config cache (the resolution would normally happen
        # earlier in the call sequence)
        plugin_with_repo._config_cache["gdrive:src_drive"] = real_config

        result = plugin_with_repo._resolve_parent_id(
            "gdrive:src_drive", sentinel,
        )
        assert result == real_config["root_folder_id"]

    def test_real_drive_folder_id_passes_through(self, plugin_with_repo):
        """28-char alphanumeric strings are real Drive folder IDs and
        must NOT be modified."""
        real_id = "1hIavkBD1F18Y12HupJyrWg591pq1rPw9"
        result = plugin_with_repo._resolve_parent_id(
            "gdrive:src_drive", real_id,
        )
        assert result == real_id

    def test_root_literal_sentinel_passes_through(self, plugin_with_repo, real_config):
        """The string ``"root"`` is not in the sentinel set and is a
        valid Drive alias; it passes through."""
        plugin_with_repo._config_cache["gdrive:src_drive"] = real_config
        result = plugin_with_repo._resolve_parent_id(
            "gdrive:src_drive", "root",
        )
        assert result == "root"

    def test_no_cached_config_falls_back_to_root_string(self, plugin_with_repo):
        """If no config has been resolved/cached for this source_id, a
        sentinel parent_id falls back to the literal string 'root'
        (Drive's My Drive root)."""
        # NO config in cache for gdrive:src_drive
        result = plugin_with_repo._resolve_parent_id(
            "gdrive:src_drive", "/",
        )
        assert result == "root"

    def test_cached_config_without_root_folder_id_falls_back_to_root(
        self, plugin_with_repo,
    ):
        """If the cached config lacks root_folder_id, sentinel falls
        back to literal 'root'."""
        plugin_with_repo._config_cache["gdrive:src_drive"] = {
            "client_secrets_path": "/x",
            "credentials_path": "/y",
            # no root_folder_id key
        }
        result = plugin_with_repo._resolve_parent_id(
            "gdrive:src_drive", "/",
        )
        assert result == "root"


# ---------------------------------------------------------------------------
# _get_or_build_client: integrates with _resolve_config
# ---------------------------------------------------------------------------


class TestGetOrBuildClientUsesResolveConfig:
    def test_build_called_with_repo_resolved_config(
        self, plugin_with_repo, real_config,
    ):
        """_get_or_build_client passes the source_repo-resolved config
        (NOT empty options) to _build_drive_client. This is the v1.5.1
        bug 1 regression test."""
        with mock.patch(
            "curator.plugins.core.gdrive_source._build_drive_client"
        ) as mock_build:
            fake_client = MagicMock()
            mock_build.return_value = fake_client

            client = plugin_with_repo._get_or_build_client(
                "gdrive:src_drive", options={},
            )

            assert client is fake_client
            mock_build.assert_called_once()
            # Critical assertion: the config passed in must be the
            # repo-resolved real_config, NOT options={}.
            (passed_config,) = mock_build.call_args.args
            assert passed_config["client_secrets_path"] == \
                real_config["client_secrets_path"]
            assert passed_config["credentials_path"] == \
                real_config["credentials_path"]

    def test_returns_none_when_no_config_resolves(
        self, monkeypatch, tmp_path,
    ):
        """_get_or_build_client returns None (and logs) when no config
        can be resolved from any tier. Caller propagates None to
        produce a clean MigrationOutcome.FAILED instead of crashing."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        p = Plugin()  # no source_repo injection, no disk fallback files

        with mock.patch(
            "curator.plugins.core.gdrive_source._build_drive_client"
        ) as mock_build:
            client = p._get_or_build_client(
                "gdrive:src_drive", options={},
            )

            assert client is None
            # _build_drive_client should NOT have been called -- we
            # short-circuit when config resolution returns None.
            mock_build.assert_not_called()

    def test_client_cache_works(self, plugin_with_repo):
        """A second call for the same source_id returns the cached
        client instance without rebuilding."""
        with mock.patch(
            "curator.plugins.core.gdrive_source._build_drive_client"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            client1 = plugin_with_repo._get_or_build_client(
                "gdrive:src_drive", options={},
            )
            client2 = plugin_with_repo._get_or_build_client(
                "gdrive:src_drive", options={},
            )

            assert client1 is client2
            assert mock_build.call_count == 1  # built once, cached for second


# ---------------------------------------------------------------------------
# curator_source_write: uses resolved parent_id (v1.5.1 bug 2 regression)
# ---------------------------------------------------------------------------


class TestCuratorSourceWriteUsesResolvedParent:
    """The v1.5.1 bug 2 regression test: curator_source_write must
    translate "/" parent_id to the configured root_folder_id before
    sending to Drive's CreateFile."""

    def test_slash_parent_translates_to_configured_folder_id(
        self, plugin_with_repo, real_config,
    ):
        """When called with parent_id='/', the Drive CreateFile call
        receives the configured root_folder_id, not '/'."""
        # Inject a fake Drive client (matches existing test pattern)
        fake_drive = MagicMock()
        fake_drive.ListFile.return_value.GetList.return_value = []  # no collisions
        fake_new_file = MagicMock()
        # Drive file objects support both __getitem__ (md["id"]) and
        # .get() in _drive_file_to_file_info -- mock both.
        _file_md = {
            "id": "new_file_id_xyz",
            "title": "test.txt",
            "mimeType": "text/plain",
            "fileSize": "11",
        }
        fake_new_file.__getitem__.side_effect = _file_md.__getitem__
        fake_new_file.get.side_effect = lambda k, default=None: _file_md.get(k, default)
        fake_drive.CreateFile.return_value = fake_new_file
        plugin_with_repo.set_drive_client("gdrive:src_drive", fake_drive)

        # Pre-warm the config cache so _resolve_parent_id has the
        # root_folder_id available (in production, _get_or_build_client
        # would have triggered _resolve_config first; here we set it
        # directly because we're not exercising the client-build path).
        plugin_with_repo._config_cache["gdrive:src_drive"] = real_config

        # Call the write hook with parent_id="/" (the migration's
        # output for top-level destinations)
        plugin_with_repo.curator_source_write(
            source_id="gdrive:src_drive",
            parent_id="/",
            name="test.txt",
            data=b"hello world",
            overwrite=False,
        )

        # CreateFile was called with the resolved root_folder_id, not "/"
        fake_drive.CreateFile.assert_called_once()
        create_args = fake_drive.CreateFile.call_args.args[0]
        assert create_args["title"] == "test.txt"
        assert create_args["parents"] == [
            {"id": real_config["root_folder_id"]}
        ]

    def test_real_folder_id_parent_passes_through(
        self, plugin_with_repo, real_config,
    ):
        """When called with parent_id=real_folder_id (e.g., a sub-folder),
        the Drive CreateFile call uses that ID directly without
        translation."""
        fake_drive = MagicMock()
        fake_drive.ListFile.return_value.GetList.return_value = []
        fake_new_file = MagicMock()
        # Drive file objects support both __getitem__ (md["id"]) and
        # .get() in _drive_file_to_file_info -- mock both.
        _file_md = {
            "id": "new_file_id",
            "title": "x",
            "mimeType": "text/plain",
            "fileSize": "1",
        }
        fake_new_file.__getitem__.side_effect = _file_md.__getitem__
        fake_new_file.get.side_effect = lambda k, default=None: _file_md.get(k, default)
        fake_drive.CreateFile.return_value = fake_new_file
        plugin_with_repo.set_drive_client("gdrive:src_drive", fake_drive)
        plugin_with_repo._config_cache["gdrive:src_drive"] = real_config

        sub_folder_id = "1subFolderXYZ123abc"
        plugin_with_repo.curator_source_write(
            source_id="gdrive:src_drive",
            parent_id=sub_folder_id,
            name="x",
            data=b"x",
            overwrite=False,
        )

        create_args = fake_drive.CreateFile.call_args.args[0]
        assert create_args["parents"] == [{"id": sub_folder_id}]

    def test_does_not_own_returns_none(self, plugin_with_repo):
        """Sources the plugin doesn't own (no gdrive: prefix) return
        None from the hook, letting other plugins claim them."""
        result = plugin_with_repo.curator_source_write(
            source_id="local",
            parent_id="/whatever",
            name="x",
            data=b"x",
        )
        assert result is None
