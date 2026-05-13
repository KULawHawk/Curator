"""Coverage closure for cli/main.py `sources_app` simple subcommands (v1.7.158a).

Tier 3 sub-ship 4a — sources_app split A (per Lesson #88). Covers the
6 simpler subcommands; v1.7.158b covers the complex `config` subcommand.

Targets (~283 lines):
- `sources list` (lines 845-927: human / JSON / CSV header/no-header/tsv)
- `sources show` (lines 929-965: no-match / JSON / human with display_name + config)
- `sources add` (lines 1201-1240: happy / disabled / duplicate-error / JSON)
- `sources enable` (lines 1242-1262: no-match / already-enabled / happy)
- `sources disable` (lines 1265-1285: no-match / already-disabled / happy)
- `sources remove` (lines 1288-1330: no-match / dry-run-with-files / dry-run-empty / --apply-with-files-blocks / --apply-empty-succeeds)
"""

from __future__ import annotations

from datetime import datetime

import pytest
from typer.testing import CliRunner

from curator._compat.datetime import utcnow_naive
from curator.cli.main import app
from curator.models import FileEntity, SourceConfig


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    from curator.storage.repositories import FileRepository, SourceRepository
    db_path = tmp_path / "cli_sources.db"
    db = CuratorDB(db_path)
    db.init()
    return {
        "db": db,
        "db_path": db_path,
        "files": FileRepository(db),
        "sources": SourceRepository(db),
    }


def _add_source(repos, **overrides) -> SourceConfig:
    base = dict(
        source_id="local", source_type="local",
        display_name="Local FS",
    )
    base.update(overrides)
    s = SourceConfig(**base)
    repos["sources"].insert(s)
    return s


def _add_file(repos, source_id: str, path: str) -> FileEntity:
    f = FileEntity(
        source_id=source_id, source_path=path,
        size=10, mtime=utcnow_naive(),
    )
    repos["files"].insert(f)
    return f


# ---------------------------------------------------------------------------
# sources list
# ---------------------------------------------------------------------------


class TestSourcesList:
    def test_empty_human_message(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "sources", "list"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No sources" in combined

    def test_human_with_sources(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="local")
        _add_source(isolated_cli_db, source_id="gdrive:x",
                    source_type="gdrive", enabled=False)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "sources", "list"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "local" in combined
        assert "gdrive:x" in combined
        assert "enabled" in combined
        assert "disabled" in combined

    def test_json_output(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="local",
                    config={"path": "/tmp"})
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "sources", "list"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"source_id": "local"' in combined
        assert '"enabled": true' in combined

    def test_csv_output_with_header(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="local",
                    config={"path": "/tmp"})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "list", "--csv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source_id,source_type" in combined
        assert "local" in combined

    def test_csv_output_no_header(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="local")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "list", "--csv", "--no-header"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source_id,source_type" not in combined
        assert "local" in combined

    def test_csv_output_tsv(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="local")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "list", "--csv", "--csv-dialect", "tsv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source_id\tsource_type" in combined


# ---------------------------------------------------------------------------
# sources show
# ---------------------------------------------------------------------------


class TestSourcesShow:
    def test_no_match_returns_error(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "show", "nonexistent"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "No source with id" in combined

    def test_json_output(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="local",
                    config={"path": "/x"})
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "sources", "show", "local"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"source_id": "local"' in combined
        assert '"path"' in combined

    def test_human_with_display_name_and_config(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="local",
                    display_name="My Local FS",
                    config={"path": "/home/user"})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "show", "local"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "My Local FS" in combined
        assert "config:" in combined
        assert "path" in combined
        assert "/home/user" in combined

    def test_human_without_display_name_no_config(
        self, runner, isolated_cli_db,
    ):
        """Branches: display_name is None (skip line 955), config is None
        (skip lines 961-964)."""
        _add_source(isolated_cli_db, source_id="local",
                    display_name=None, config={})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "show", "local"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "local" in combined
        # No "name:" line, no "config:" section
        assert "name:" not in combined or "  name:" not in combined


# ---------------------------------------------------------------------------
# sources add
# ---------------------------------------------------------------------------


class TestSourcesAdd:
    def test_happy_path_human(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "add", "work", "--type", "local",
             "--name", "Work"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Added source" in combined
        assert "work" in combined
        # Verify in DB
        assert isolated_cli_db["sources"].get("work") is not None

    def test_disabled_flag(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "add", "off", "--disabled"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "disabled" in combined
        src = isolated_cli_db["sources"].get("off")
        assert src.enabled is False

    def test_duplicate_errors(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="dup")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "add", "dup"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Source already exists" in combined

    def test_json_output(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "sources", "add", "json-src"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"source_id": "json-src"' in combined
        assert '"added": true' in combined


# ---------------------------------------------------------------------------
# sources enable / disable
# ---------------------------------------------------------------------------


class TestSourcesEnable:
    def test_no_match_errors(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "enable", "ghost"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "No source with id" in combined

    def test_already_enabled_message(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="on", enabled=True)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "enable", "on"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "already enabled" in combined

    def test_enables_disabled_source(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="off", enabled=False)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "enable", "off"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Enabled" in combined
        src = isolated_cli_db["sources"].get("off")
        assert src.enabled is True


class TestSourcesDisable:
    def test_no_match_errors(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "disable", "ghost"],
        )
        assert result.exit_code == 1

    def test_already_disabled_message(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="off", enabled=False)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "disable", "off"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "already disabled" in combined

    def test_disables_enabled_source(self, runner, isolated_cli_db):
        _add_source(isolated_cli_db, source_id="on", enabled=True)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "disable", "on"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Disabled" in combined
        src = isolated_cli_db["sources"].get("on")
        assert src.enabled is False


# ---------------------------------------------------------------------------
# sources remove
# ---------------------------------------------------------------------------


class TestSourcesRemove:
    def test_no_match_errors(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "remove", "ghost"],
        )
        assert result.exit_code == 1

    def test_dry_run_with_files_warns_cannot_remove(
        self, runner, isolated_cli_db,
    ):
        """Lines 1303-1308: dry-run + files referencing the source -> warning."""
        _add_source(isolated_cli_db, source_id="busy")
        _add_file(isolated_cli_db, "busy", "/file.txt")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "remove", "busy"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "cannot remove" in combined
        # Source NOT removed
        assert isolated_cli_db["sources"].get("busy") is not None

    def test_dry_run_empty_source_says_would_remove(
        self, runner, isolated_cli_db,
    ):
        """Lines 1310-1313: dry-run + no files -> 'would remove' message."""
        _add_source(isolated_cli_db, source_id="empty")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "remove", "empty"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "would remove" in combined
        # Still there
        assert isolated_cli_db["sources"].get("empty") is not None

    def test_apply_with_files_errors(self, runner, isolated_cli_db):
        """Lines 1316-1321: --apply + files -> err_exit."""
        _add_source(isolated_cli_db, source_id="busy")
        _add_file(isolated_cli_db, "busy", "/file.txt")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "remove", "busy", "--apply"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Cannot remove" in combined

    def test_apply_empty_removes(self, runner, isolated_cli_db):
        """Lines 1323-1330: --apply + no files -> delete + audit + message."""
        _add_source(isolated_cli_db, source_id="empty")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "remove", "empty", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Removed source" in combined
        assert isolated_cli_db["sources"].get("empty") is None
