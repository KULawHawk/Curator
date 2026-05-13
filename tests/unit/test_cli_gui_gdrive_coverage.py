"""Coverage closure for cli/main.py `gui` + `gdrive_app` (v1.7.165).

Tier 3 sub-ship 11 of the CLI Coverage Arc.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from curator.cli.main import app


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_gui_gdrive.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# gui command
# ---------------------------------------------------------------------------


class TestGuiCmd:
    def test_pyside6_not_installed_exits_2(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 2755-2760: PySide6 unavailable -> exit 2."""
        import curator.gui.launcher as launcher_mod
        monkeypatch.setattr(launcher_mod, "is_pyside6_available",
                             lambda: False)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "gui"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "PySide6 is not installed" in combined

    def test_run_gui_success_exit_0(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Line 2762-2764: run_gui returns 0 -> success exit."""
        import curator.gui.launcher as launcher_mod
        monkeypatch.setattr(launcher_mod, "is_pyside6_available",
                             lambda: True)
        monkeypatch.setattr(launcher_mod, "run_gui", lambda rt: 0)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "gui"],
        )
        assert result.exit_code == 0

    def test_run_gui_nonzero_exits_with_code(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Line 2763-2764: nonzero run_gui exit code propagates."""
        import curator.gui.launcher as launcher_mod
        monkeypatch.setattr(launcher_mod, "is_pyside6_available",
                             lambda: True)
        monkeypatch.setattr(launcher_mod, "run_gui", lambda rt: 3)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "gui"],
        )
        assert result.exit_code == 3


# ---------------------------------------------------------------------------
# gdrive paths
# ---------------------------------------------------------------------------


class TestGdrivePaths:
    def test_human_output_default_alias(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Lines 2787-2802: human output with default alias."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "gdrive", "paths"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "default" in combined
        assert "client_secrets" in combined
        assert "credentials" in combined

    def test_human_output_explicit_alias(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "paths", "my_alias"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "my_alias" in combined

    def test_json_output(self, runner, isolated_cli_db, monkeypatch, tmp_path):
        """Lines 2789-2797: JSON output."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "gdrive", "paths", "json_alias"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"alias": "json_alias"' in combined
        assert '"client_secrets"' in combined


# ---------------------------------------------------------------------------
# gdrive status
# ---------------------------------------------------------------------------


class TestGdriveStatus:
    def test_no_client_secrets_state(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Lines 2820-2841: human output for no_client_secrets state."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "status", "fresh"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "no_client_secrets" in combined
        assert "missing" in combined.lower()

    def test_no_credentials_state(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """client_secrets exists, credentials missing."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        gdrive_dir = tmp_path / "gdrive" / "have_cs"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "status", "have_cs"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "no_credentials" in combined

    def test_credentials_present_state(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Both files exist -> credentials_present (green)."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        gdrive_dir = tmp_path / "gdrive" / "ready"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")
        (gdrive_dir / "credentials.json").write_text("{}")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "status", "ready"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "credentials_present" in combined

    def test_json_output(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "gdrive", "status", "json_test"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"state"' in combined
        assert '"has_client_secrets"' in combined


# ---------------------------------------------------------------------------
# gdrive auth
# ---------------------------------------------------------------------------


class TestGdriveAuth:
    def test_already_authed_human_skips(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Lines 2887-2903: credentials_present + no --force -> skip."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        gdrive_dir = tmp_path / "gdrive" / "alreadyauth"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")
        (gdrive_dir / "credentials.json").write_text("{}")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "auth", "alreadyauth"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Credentials already present" in combined

    def test_already_authed_json_skips(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """JSON skip output."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        gdrive_dir = tmp_path / "gdrive" / "alreadyauthjson"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")
        (gdrive_dir / "credentials.json").write_text("{}")
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "gdrive", "auth", "alreadyauthjson"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"action": "skipped"' in combined
        assert '"reason": "credentials_present"' in combined

    def test_pydrive2_not_installed_exits_2(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Lines 2907-2909: PyDrive2NotInstalled -> exit 2."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        import curator.services.gdrive_auth as gauth_mod

        def _raise(*args, **kwargs):
            raise gauth_mod.PyDrive2NotInstalled("PyDrive2 missing")

        monkeypatch.setattr(gauth_mod, "run_interactive_auth", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "auth", "needpydrive"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "PyDrive2 missing" in combined

    def test_client_secrets_missing_exits_1(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Lines 2910-2915: ClientSecretsMissing -> exit 1 + tip."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        import curator.services.gdrive_auth as gauth_mod

        def _raise(*args, **kwargs):
            raise gauth_mod.ClientSecretsMissing("no client_secrets.json")

        monkeypatch.setattr(gauth_mod, "run_interactive_auth", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "auth", "needcs"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "no client_secrets.json" in combined
        assert "gdrive paths" in combined

    def test_runtime_error_exits_1(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Lines 2916-2918: RuntimeError (e.g. OAuth flow failed) -> exit 1."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        # Need client_secrets present so we don't skip past the auth call
        gdrive_dir = tmp_path / "gdrive" / "oauthfail"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")
        import curator.services.gdrive_auth as gauth_mod

        def _raise(*args, **kwargs):
            raise RuntimeError("oauth callback timed out")

        monkeypatch.setattr(gauth_mod, "run_interactive_auth", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "auth", "oauthfail"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "oauth callback timed out" in combined

    def test_auth_success_human(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Lines 2920-2929+: success path human output."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        gdrive_dir = tmp_path / "gdrive" / "happy"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")
        import curator.services.gdrive_auth as gauth_mod

        def _stub_auth(paths, *, auth_method):
            # Simulate successful auth by creating the credentials file
            paths.credentials.write_text("{}")

        monkeypatch.setattr(gauth_mod, "run_interactive_auth", _stub_auth)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "auth", "happy"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Auth complete" in combined

    def test_auth_success_json(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        gdrive_dir = tmp_path / "gdrive" / "happyjson"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")
        import curator.services.gdrive_auth as gauth_mod

        def _stub_auth(paths, *, auth_method):
            paths.credentials.write_text("{}")

        monkeypatch.setattr(gauth_mod, "run_interactive_auth", _stub_auth)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "gdrive", "auth", "happyjson"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"action": "completed"' in combined
        assert '"state": "credentials_present"' in combined

    def test_force_reruns_even_when_authed(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """--force bypasses the early-skip even when credentials_present."""
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        gdrive_dir = tmp_path / "gdrive" / "force"
        gdrive_dir.mkdir(parents=True)
        (gdrive_dir / "client_secrets.json").write_text("{}")
        (gdrive_dir / "credentials.json").write_text("{old: true}")
        import curator.services.gdrive_auth as gauth_mod

        calls = []

        def _stub_auth(paths, *, auth_method):
            calls.append(paths.credentials)
            paths.credentials.write_text("{new: true}")

        monkeypatch.setattr(gauth_mod, "run_interactive_auth", _stub_auth)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "gdrive", "auth", "force", "--force"],
        )
        assert result.exit_code == 0
        # The auth was re-run despite credentials_present
        assert len(calls) == 1
