"""Tests for the v0.42 gdrive auth helper service.

Covers paths_for_alias / auth_status / ensure_alias_dir /
source_config_for_alias / run_interactive_auth via mocked PyDrive2.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from curator.services.gdrive_auth import (
    CLIENT_SECRETS_NAME,
    CREDENTIALS_NAME,
    DEFAULT_ALIAS,
    AuthPaths,
    AuthStatus,
    ClientSecretsMissing,
    PyDrive2NotInstalled,
    auth_status,
    default_gdrive_dir,
    ensure_alias_dir,
    paths_for_alias,
    run_interactive_auth,
    source_config_for_alias,
)


# ---------------------------------------------------------------------------
# default_gdrive_dir
# ---------------------------------------------------------------------------

class TestDefaultGdriveDir:
    def test_default_is_under_home_dot_curator(self) -> None:
        result = default_gdrive_dir()
        assert result.name == "gdrive"
        assert result.parent.name == ".curator"

    def test_curator_home_env_overrides(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        result = default_gdrive_dir()
        assert result == tmp_path / "gdrive"


# ---------------------------------------------------------------------------
# paths_for_alias
# ---------------------------------------------------------------------------

class TestPathsForAlias:
    def test_default_alias_constant(self) -> None:
        assert DEFAULT_ALIAS == "default"

    def test_basic_layout(self, tmp_path: Path) -> None:
        paths = paths_for_alias("personal", base=tmp_path)
        assert paths.alias == "personal"
        assert paths.dir == tmp_path / "personal"
        assert paths.client_secrets == tmp_path / "personal" / CLIENT_SECRETS_NAME
        assert paths.credentials == tmp_path / "personal" / CREDENTIALS_NAME

    def test_alias_with_special_chars_allowed(self, tmp_path: Path) -> None:
        # User-friendly aliases like 'jake@personal' are fine on filesystems
        paths = paths_for_alias("jake@personal", base=tmp_path)
        assert paths.dir.name == "jake@personal"

    def test_empty_alias_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="empty"):
            paths_for_alias("", base=tmp_path)

    def test_alias_with_forward_slash_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="path separators"):
            paths_for_alias("foo/bar", base=tmp_path)

    def test_alias_with_backslash_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="path separators"):
            paths_for_alias("foo\\bar", base=tmp_path)

    def test_dot_alias_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            paths_for_alias(".", base=tmp_path)

    def test_dotdot_alias_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            paths_for_alias("..", base=tmp_path)

    def test_uses_default_base_when_none(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        paths = paths_for_alias("foo")
        assert paths.dir == tmp_path / "gdrive" / "foo"


# ---------------------------------------------------------------------------
# auth_status
# ---------------------------------------------------------------------------

class TestAuthStatus:
    def test_no_client_secrets_when_dir_empty(self, tmp_path: Path) -> None:
        status = auth_status("personal", base=tmp_path)
        assert status.state == "no_client_secrets"
        assert status.has_client_secrets is False
        assert status.has_credentials is False
        assert "Place client_secrets.json" in status.detail

    def test_no_credentials_when_only_client_secrets(self, tmp_path: Path) -> None:
        paths = paths_for_alias("personal", base=tmp_path)
        paths.dir.mkdir(parents=True)
        paths.client_secrets.write_text("{}")
        status = auth_status("personal", base=tmp_path)
        assert status.state == "no_credentials"
        assert status.has_client_secrets is True
        assert status.has_credentials is False
        assert "curator gdrive auth" in status.detail

    def test_credentials_present_when_both_exist(self, tmp_path: Path) -> None:
        paths = paths_for_alias("personal", base=tmp_path)
        paths.dir.mkdir(parents=True)
        paths.client_secrets.write_text("{}")
        paths.credentials.write_text("{}")
        status = auth_status("personal", base=tmp_path)
        assert status.state == "credentials_present"
        assert status.has_client_secrets is True
        assert status.has_credentials is True
        assert "PyDrive2 will refresh" in status.detail

    def test_to_dict_includes_all_fields(self, tmp_path: Path) -> None:
        status = auth_status("personal", base=tmp_path)
        d = status.to_dict()
        assert d["alias"] == "personal"
        assert d["state"] == "no_client_secrets"
        assert d["has_client_secrets"] is False
        assert d["has_credentials"] is False
        assert "client_secrets" in d
        assert "credentials" in d
        assert "dir" in d
        assert "detail" in d


# ---------------------------------------------------------------------------
# ensure_alias_dir
# ---------------------------------------------------------------------------

class TestEnsureAliasDir:
    def test_creates_dir_when_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "personal"
        assert not target.exists()
        paths = ensure_alias_dir("personal", base=tmp_path)
        assert paths.dir.is_dir()
        assert paths.dir == target

    def test_idempotent_when_dir_exists(self, tmp_path: Path) -> None:
        (tmp_path / "personal").mkdir()
        # Should not raise
        paths = ensure_alias_dir("personal", base=tmp_path)
        assert paths.dir.is_dir()


# ---------------------------------------------------------------------------
# source_config_for_alias
# ---------------------------------------------------------------------------

class TestSourceConfigForAlias:
    def test_returns_pydrive2_compatible_config(self, tmp_path: Path) -> None:
        cfg = source_config_for_alias("personal", base=tmp_path)
        assert cfg["root_folder_id"] == "root"
        assert cfg["client_secrets_path"].endswith(
            os.path.join("personal", "client_secrets.json")
        )
        assert cfg["credentials_path"].endswith(
            os.path.join("personal", "credentials.json")
        )


# ---------------------------------------------------------------------------
# run_interactive_auth (PyDrive2 mocked)
# ---------------------------------------------------------------------------

class TestRunInteractiveAuth:
    def test_raises_pydrive2_not_installed_when_missing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Simulate PyDrive2 being absent
        if "pydrive2" in sys.modules:
            monkeypatch.delitem(sys.modules, "pydrive2", raising=False)
            monkeypatch.delitem(sys.modules, "pydrive2.auth", raising=False)

        # Block the import
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def block_pydrive2(name, *args, **kwargs):
            if name.startswith("pydrive2"):
                raise ImportError(f"No module named {name!r}")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", block_pydrive2)
        paths = paths_for_alias("personal", base=tmp_path)
        paths.dir.mkdir(parents=True)
        paths.client_secrets.write_text("{}")
        with pytest.raises(PyDrive2NotInstalled, match="PyDrive2 is not installed"):
            run_interactive_auth(paths)

    def test_raises_client_secrets_missing(self, tmp_path: Path) -> None:
        paths = paths_for_alias("personal", base=tmp_path)
        # Don't create the directory; client_secrets won't exist
        # We need to mock the PyDrive2 import or the function
        # will fail on the import first. The check happens AFTER
        # the import, so create the dir but not the file.
        paths.dir.mkdir(parents=True)
        # PyDrive2 may or may not be installed; if installed,
        # we still hit ClientSecretsMissing first.
        try:
            import pydrive2  # noqa: F401
            with pytest.raises(ClientSecretsMissing, match="client_secrets.json not found"):
                run_interactive_auth(paths)
        except ImportError:
            pytest.skip("pydrive2 not installed; covered by other test")

    def test_command_line_flow_saves_credentials(self, tmp_path: Path) -> None:
        """With PyDrive2's auth mocked, success path saves credentials.json."""
        paths = paths_for_alias("personal", base=tmp_path)
        paths.dir.mkdir(parents=True)
        paths.client_secrets.write_text("{}")

        mock_gauth = MagicMock()
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            run_interactive_auth(paths, auth_method="command_line")

        mock_gauth.LoadClientConfigFile.assert_called_once_with(str(paths.client_secrets))
        mock_gauth.CommandLineAuth.assert_called_once()
        mock_gauth.SaveCredentialsFile.assert_called_once_with(str(paths.credentials))

    def test_local_webserver_flow(self, tmp_path: Path) -> None:
        paths = paths_for_alias("personal", base=tmp_path)
        paths.dir.mkdir(parents=True)
        paths.client_secrets.write_text("{}")

        mock_gauth = MagicMock()
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            run_interactive_auth(paths, auth_method="local_webserver")

        mock_gauth.LocalWebserverAuth.assert_called_once()
        mock_gauth.CommandLineAuth.assert_not_called()

    def test_unknown_auth_method_rejected(self, tmp_path: Path) -> None:
        paths = paths_for_alias("personal", base=tmp_path)
        paths.dir.mkdir(parents=True)
        paths.client_secrets.write_text("{}")

        mock_gauth = MagicMock()
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            with pytest.raises(RuntimeError, match="auth flow failed"):
                run_interactive_auth(paths, auth_method="bogus")

    def test_auth_failure_wrapped_as_runtime_error(self, tmp_path: Path) -> None:
        paths = paths_for_alias("personal", base=tmp_path)
        paths.dir.mkdir(parents=True)
        paths.client_secrets.write_text("{}")

        mock_gauth = MagicMock()
        mock_gauth.CommandLineAuth.side_effect = Exception("network down")
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            with pytest.raises(RuntimeError, match="auth flow failed.*network down"):
                run_interactive_auth(paths)

    def test_save_failure_wrapped_as_runtime_error(self, tmp_path: Path) -> None:
        paths = paths_for_alias("personal", base=tmp_path)
        paths.dir.mkdir(parents=True)
        paths.client_secrets.write_text("{}")

        mock_gauth = MagicMock()
        mock_gauth.SaveCredentialsFile.side_effect = OSError("disk full")
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            with pytest.raises(RuntimeError, match="Failed to save credentials"):
                run_interactive_auth(paths)
