"""Integration tests for `curator safety` CLI subcommands (Phase Gamma F1).

Verifies the SafetyService is properly wired into the runtime and the
CLI commands produce sensible human-readable + JSON output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app


pytestmark = pytest.mark.integration


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "curator_safety_test.db"


# ---------------------------------------------------------------------------
# `curator safety paths`
# ---------------------------------------------------------------------------

class TestSafetyPaths:
    def test_human_readable_output(self, runner, db_path, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(db_path), "safety", "paths"],
            env={"HOME": str(tmp_path)},
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        assert "App-data paths" in result.stdout
        assert "OS-managed paths" in result.stdout

    def test_json_output(self, runner, db_path, tmp_path):
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "safety", "paths"],
            env={"HOME": str(tmp_path)},
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)
        assert "app_data" in payload
        assert "os_managed" in payload
        assert "platform" in payload
        assert isinstance(payload["app_data"], list)
        assert isinstance(payload["os_managed"], list)


# ---------------------------------------------------------------------------
# `curator safety check`
# ---------------------------------------------------------------------------

class TestSafetyCheck:
    def test_safe_file_human_output(self, runner, db_path, tmp_path):
        plain = tmp_path / "plain.txt"
        plain.write_text("nothing special")

        result = runner.invoke(
            app,
            ["--db", str(db_path), "safety", "check", str(plain)],
        )
        # We don't assert the verdict (could be CAUTION on machines where
        # the temp dir is under %LOCALAPPDATA%); just that the command
        # runs cleanly and produces the path + a verdict line.
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        assert str(plain) in result.stdout
        assert "verdict:" in result.stdout

    def test_caution_for_project_file(self, runner, db_path, tmp_path):
        proj = tmp_path / "myproj"
        proj.mkdir()
        (proj / ".git").mkdir()
        target = proj / "src" / "x.py"
        target.parent.mkdir()
        target.write_text("")

        result = runner.invoke(
            app,
            ["--db", str(db_path), "safety", "check", str(target)],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        assert "CAUTION" in result.stdout
        assert "project_file" in result.stdout

    def test_json_output_structure(self, runner, db_path, tmp_path):
        proj = tmp_path / "p2"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("[project]\nname = 'x'")
        target = proj / "x.py"
        target.write_text("")

        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "safety", "check", str(target)],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)
        assert payload["path"] == str(target)
        # Could be CAUTION (project_file) or REFUSE (if temp is under an
        # OS-managed path). Either way, NOT safe; a project_file concern
        # appears unless OS_MANAGED short-circuited.
        assert payload["level"] in ("caution", "refuse")
        assert payload["project_root"] == str(proj) or payload["level"] == "refuse"

    def test_nonexistent_path_errors(self, runner, db_path, tmp_path):
        ghost = tmp_path / "no_such_file.txt"
        result = runner.invoke(
            app,
            ["--db", str(db_path), "safety", "check", str(ghost)],
        )
        assert result.exit_code != 0
        # Error message is on stderr because the CLI uses _err_console.
        # Typer's CliRunner with mix_stderr=False keeps stderr separate.
        combined = (result.stdout or "") + (result.stderr or "")
        assert "does not exist" in combined.lower()
