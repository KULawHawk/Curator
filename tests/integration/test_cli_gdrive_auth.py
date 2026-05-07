"""Tests for the v0.42 CLI ``curator gdrive`` subcommands.

Covers the three commands: paths / status / auth. The auth command
mocks PyDrive2 so we never hit the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from curator.cli.main import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def gdrive_home(monkeypatch, tmp_path: Path) -> Path:
    """Override the gdrive base dir to a fresh tmp tree."""
    monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
    return tmp_path / "gdrive"


# ---------------------------------------------------------------------------
# gdrive paths
# ---------------------------------------------------------------------------

class TestGdrivePathsCmd:
    def test_default_alias(self, runner: CliRunner, gdrive_home: Path) -> None:
        result = runner.invoke(app, ["gdrive", "paths"])
        assert result.exit_code == 0
        assert "default" in result.stdout
        assert "client_secrets.json" in result.stdout
        assert "credentials.json" in result.stdout

    def test_named_alias(self, runner: CliRunner, gdrive_home: Path) -> None:
        result = runner.invoke(app, ["gdrive", "paths", "personal"])
        assert result.exit_code == 0
        assert "personal" in result.stdout

    def test_json_output(self, runner: CliRunner, gdrive_home: Path) -> None:
        result = runner.invoke(app, ["--json", "gdrive", "paths", "personal"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["alias"] == "personal"
        assert payload["client_secrets"].endswith("client_secrets.json")
        assert payload["credentials"].endswith("credentials.json")
        assert "personal" in payload["dir"]

    def test_invalid_alias_raises(self, runner: CliRunner, gdrive_home: Path) -> None:
        result = runner.invoke(app, ["gdrive", "paths", "foo/bar"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# gdrive status
# ---------------------------------------------------------------------------

class TestGdriveStatusCmd:
    def test_no_client_secrets_state(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        result = runner.invoke(app, ["gdrive", "status", "personal"])
        assert result.exit_code == 0
        assert "no_client_secrets" in result.stdout
        assert "Place client_secrets.json" in result.stdout

    def test_no_credentials_state(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        alias_dir = gdrive_home / "personal"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")

        result = runner.invoke(app, ["gdrive", "status", "personal"])
        assert result.exit_code == 0
        assert "no_credentials" in result.stdout
        assert "curator gdrive auth" in result.stdout

    def test_credentials_present_state(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        alias_dir = gdrive_home / "personal"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")
        (alias_dir / "credentials.json").write_text("{}")

        result = runner.invoke(app, ["gdrive", "status", "personal"])
        assert result.exit_code == 0
        assert "credentials_present" in result.stdout

    def test_json_output_no_secrets(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        result = runner.invoke(app, ["--json", "gdrive", "status", "personal"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["alias"] == "personal"
        assert payload["state"] == "no_client_secrets"
        assert payload["has_client_secrets"] is False
        assert payload["has_credentials"] is False

    def test_json_output_credentials_present(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        alias_dir = gdrive_home / "personal"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")
        (alias_dir / "credentials.json").write_text("{}")

        result = runner.invoke(app, ["--json", "gdrive", "status", "personal"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["state"] == "credentials_present"
        assert payload["has_client_secrets"] is True
        assert payload["has_credentials"] is True


# ---------------------------------------------------------------------------
# gdrive auth
# ---------------------------------------------------------------------------

class TestGdriveAuthCmd:
    def test_skips_when_credentials_present_without_force(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        alias_dir = gdrive_home / "personal"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")
        (alias_dir / "credentials.json").write_text("{}")

        result = runner.invoke(app, ["gdrive", "auth", "personal"])
        assert result.exit_code == 0
        assert "already present" in result.stdout
        assert "--force" in result.stdout

    def test_skips_when_credentials_present_json_output(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        alias_dir = gdrive_home / "personal"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")
        (alias_dir / "credentials.json").write_text("{}")

        result = runner.invoke(app, ["--json", "gdrive", "auth", "personal"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["action"] == "skipped"
        assert payload["reason"] == "credentials_present"

    def test_missing_client_secrets_exits_1(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        # Don't create client_secrets.json
        # Mock PyDrive2's GoogleAuth to be importable (so we get past the
        # PyDrive2NotInstalled check) but the file check should fail.
        mock_gauth = MagicMock()
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            result = runner.invoke(
                app, ["gdrive", "auth", "personal"]
            )
        # Error message goes to stderr; check exit code (1) and that auth
        # flow was NOT actually invoked (we bailed before PyDrive2 calls).
        assert result.exit_code == 1
        mock_gauth.CommandLineAuth.assert_not_called()

    def test_completes_with_mocked_pydrive2(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        alias_dir = gdrive_home / "personal"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")

        mock_gauth = MagicMock()
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            result = runner.invoke(
                app, ["gdrive", "auth", "personal"]
            )
        assert result.exit_code == 0
        assert "Auth complete" in result.stdout
        assert "personal" in result.stdout
        mock_gauth.CommandLineAuth.assert_called_once()
        mock_gauth.SaveCredentialsFile.assert_called_once()

    def test_completes_with_json_output(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        alias_dir = gdrive_home / "personal"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")

        mock_gauth = MagicMock()
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            result = runner.invoke(
                app, ["--json", "gdrive", "auth", "personal"]
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["action"] == "completed"
        assert payload["alias"] == "personal"

    def test_force_re_runs_even_with_existing_credentials(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        alias_dir = gdrive_home / "personal"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")
        (alias_dir / "credentials.json").write_text("old")

        mock_gauth = MagicMock()
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            result = runner.invoke(
                app, ["gdrive", "auth", "personal", "--force"]
            )
        assert result.exit_code == 0
        assert "Auth complete" in result.stdout
        mock_gauth.CommandLineAuth.assert_called_once()

    def test_local_webserver_method(
        self, runner: CliRunner, gdrive_home: Path
    ) -> None:
        alias_dir = gdrive_home / "personal"
        alias_dir.mkdir(parents=True)
        (alias_dir / "client_secrets.json").write_text("{}")

        mock_gauth = MagicMock()
        mock_gauth_class = MagicMock(return_value=mock_gauth)

        with patch.dict(
            sys.modules,
            {
                "pydrive2": MagicMock(),
                "pydrive2.auth": MagicMock(GoogleAuth=mock_gauth_class),
            },
        ):
            result = runner.invoke(
                app, ["gdrive", "auth", "personal", "--method", "local_webserver"]
            )
        assert result.exit_code == 0
        mock_gauth.LocalWebserverAuth.assert_called_once()
        mock_gauth.CommandLineAuth.assert_not_called()
