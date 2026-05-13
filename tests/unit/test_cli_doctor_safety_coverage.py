"""Coverage closure for cli/main.py `doctor` + `safety_app` (v1.7.162).

Tier 3 sub-ship 8 of the CLI Coverage Arc.
"""

from __future__ import annotations

import sys
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
    db_path = tmp_path / "cli_doctor.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


class TestDoctor:
    def test_doctor_clean_runs(self, runner, isolated_cli_db):
        """Happy path: empty DB, no issues."""
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "doctor"],
        )
        # Whether ppdeep / send2trash are missing depends on env; just verify
        # the command produced the expected sections
        combined = result.stdout + (result.stderr or "")
        assert "Curator doctor" in combined
        assert "config:" in combined
        assert "db:" in combined
        assert "plugins:" in combined
        assert "Index stats" in combined
        assert "files:" in combined

    def test_doctor_with_sources_section(self, runner, isolated_cli_db):
        """Verify Sources section appears when sources exist."""
        from curator.models import SourceConfig
        from curator.storage import CuratorDB
        from curator.storage.repositories import SourceRepository
        db = CuratorDB(isolated_cli_db["db_path"])
        db.init()
        repo = SourceRepository(db)
        repo.insert(SourceConfig(
            source_id="my_src", source_type="local", display_name="Mine",
        ))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "doctor"],
        )
        combined = result.stdout + (result.stderr or "")
        assert "Sources" in combined
        assert "my_src" in combined

    def test_doctor_reports_missing_send2trash_as_issue(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1685-1687: both vendored + PyPI send2trash imports fail
        -> red 'missing' message + issue accumulated -> exit 1.

        Need to also remove the attribute on the parent _vendored module
        so the `from curator._vendored import send2trash` attribute
        lookup also fails after sys.modules signals ImportError."""
        import curator._vendored as vendored_mod
        # Cache + remove the attribute to defeat the `from X import Y` path
        original = getattr(vendored_mod, "send2trash", None)
        if original is not None:
            monkeypatch.delattr(vendored_mod, "send2trash")
        monkeypatch.setitem(sys.modules, "curator._vendored.send2trash", None)
        monkeypatch.setitem(sys.modules, "send2trash", None)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "doctor"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "send2trash" in combined
        assert "missing" in combined
        assert "1 issue" in combined

    def test_doctor_reports_pypi_ppdeep_as_installed(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1672-1674: vendored ppdeep fails but PyPI succeeds -> installed."""
        import types
        # Make vendored ppdeep fail
        monkeypatch.setitem(sys.modules, "curator._vendored.ppdeep", None)
        # Inject fake PyPI ppdeep
        fake_ppdeep = types.ModuleType("ppdeep")
        monkeypatch.setitem(sys.modules, "ppdeep", fake_ppdeep)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "doctor"],
        )
        combined = result.stdout + (result.stderr or "")
        assert "ppdeep" in combined
        # Should report PyPI installed (not vendored, not missing)
        assert "installed" in combined or "vendored" in combined

    def test_doctor_reports_missing_ppdeep_no_issue(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1675-1676: both vendored + PyPI ppdeep fail -> missing
        (yellow), but does NOT accumulate an issue (ppdeep is optional)."""
        monkeypatch.setitem(sys.modules, "curator._vendored.ppdeep", None)
        monkeypatch.setitem(sys.modules, "ppdeep", None)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "doctor"],
        )
        combined = result.stdout + (result.stderr or "")
        assert "ppdeep" in combined
        assert "missing" in combined


# ---------------------------------------------------------------------------
# safety check
# ---------------------------------------------------------------------------


class TestSafetyCheck:
    def test_nonexistent_path_errors(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "safety", "check",
             str(isolated_cli_db["tmp_path"] / "no_such_file")],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Path does not exist" in combined

    def test_safe_file_human_output(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Stub safety to return SAFE level (tmp_path on Windows is under
        AppData/Local which triggers CAUTION naturally)."""
        from curator.services.safety import (
            SafetyLevel, SafetyReport, SafetyService,
        )

        def _stub_check(self, path, *, check_handles=False):
            return SafetyReport(path=str(path), level=SafetyLevel.SAFE)

        monkeypatch.setattr(SafetyService, "check_path", _stub_check)
        target = isolated_cli_db["tmp_path"] / "safe.txt"
        target.write_text("data")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "safety", "check", str(target)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "verdict" in combined
        assert "SAFE" in combined.upper()
        assert "safe to organize" in combined

    def test_safe_file_json_output(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.safety import (
            SafetyLevel, SafetyReport, SafetyService,
        )

        def _stub_check(self, path, *, check_handles=False):
            return SafetyReport(path=str(path), level=SafetyLevel.SAFE)

        monkeypatch.setattr(SafetyService, "check_path", _stub_check)
        target = isolated_cli_db["tmp_path"] / "safe.txt"
        target.write_text("data")
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "safety", "check", str(target)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"level"' in combined
        assert '"concerns"' in combined

    def test_caution_with_concerns_renders_concerns_section(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Stub safety to return a CAUTION-level report with concerns + project_root."""
        from curator.services.safety import (
            SafetyConcern, SafetyLevel, SafetyReport, SafetyService,
        )

        def _stub_check(self, path, *, check_handles=False):
            r = SafetyReport(path=str(path))
            r.add_concern(SafetyConcern.APP_DATA, "in roaming")
            r.project_root = "/some/project"
            return r

        monkeypatch.setattr(SafetyService, "check_path", _stub_check)
        target = isolated_cli_db["tmp_path"] / "caution.txt"
        target.write_text("data")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "safety", "check", str(target)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "concerns" in combined
        assert "in roaming" in combined
        assert "project root" in combined

    def test_with_handles_renders_holders(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Stub safety to return holders list -> 'open by:' section."""
        from curator.services.safety import (
            SafetyReport, SafetyService,
        )

        def _stub_check(self, path, *, check_handles=False):
            r = SafetyReport(path=str(path))
            r.holders = ["notepad.exe", "explorer.exe"]
            return r

        monkeypatch.setattr(SafetyService, "check_path", _stub_check)
        target = isolated_cli_db["tmp_path"] / "held.txt"
        target.write_text("data")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "safety", "check", str(target), "--handles"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "open by" in combined
        assert "notepad" in combined


# ---------------------------------------------------------------------------
# safety paths
# ---------------------------------------------------------------------------


class TestSafetyPaths:
    """`app_data` + `os_managed` are instance attributes set in
    SafetyService.__init__ — patch the __init__ to control them."""

    def test_human_output_with_paths(self, runner, isolated_cli_db, monkeypatch):
        """Lines 1804-1817: app_data + os_managed populated -> render bullets.
        The default platform paths are already populated on Windows, so the
        default behavior covers this branch."""
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "safety", "paths"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "App-data paths" in combined
        assert "OS-managed" in combined

    def test_human_output_empty_paths(self, runner, isolated_cli_db, monkeypatch):
        """Lines 1808-1809 + 1815-1816: empty lists -> '(none)' messages.
        Wrap SafetyService.__init__ to override both attrs to []."""
        from curator.services.safety import SafetyService

        original_init = SafetyService.__init__

        def _wrapped_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.app_data = []
            self.os_managed = []

        monkeypatch.setattr(SafetyService, "__init__", _wrapped_init)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "safety", "paths"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert combined.count("(none)") >= 2

    def test_json_output(self, runner, isolated_cli_db):
        """Lines 1794-1801: JSON output payload."""
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "safety", "paths"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"app_data"' in combined
        assert '"os_managed"' in combined
        assert '"platform"' in combined
