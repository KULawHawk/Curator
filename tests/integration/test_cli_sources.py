"""Integration tests for the ``curator sources`` subcommands.

Exercises the full CLI path via Typer's ``CliRunner``: argument parsing,
runtime wiring, repository writes, audit logging, and exit codes.

This is also the first file that uses ``CliRunner`` in the test suite —
future CLI test files (Q-Phase Beta) can follow this pattern.

Q18 in BUILD_TRACKER is resolved by these commands + this test file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app


pytestmark = pytest.mark.integration


# ``runner`` and ``cli_db`` fixtures live in the top-level conftest.py.


def _invoke(runner: CliRunner, db_path: Path, *args: str):
    """Invoke ``curator --db <db_path> <args>`` and return the result."""
    return runner.invoke(app, ["--db", str(db_path), *args], catch_exceptions=False)


# ---------------------------------------------------------------------------
# sources list
# ---------------------------------------------------------------------------

class TestSourcesList:
    def test_empty_list_shows_hint(self, runner, cli_db):
        result = _invoke(runner, cli_db, "sources", "list")
        assert result.exit_code == 0
        assert "No sources registered" in result.stdout

    def test_json_output_when_empty(self, runner, cli_db):
        result = _invoke(runner, cli_db, "--json", "sources", "list")
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload == []

    def test_lists_after_adding(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        _invoke(runner, cli_db, "sources", "add", "personal", "--disabled")
        result = _invoke(runner, cli_db, "sources", "list")
        assert result.exit_code == 0
        assert "work" in result.stdout
        assert "personal" in result.stdout
        # Status indicators present
        assert "enabled" in result.stdout
        assert "disabled" in result.stdout

    def test_json_output_includes_all_fields(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work", "--name", "Work files")
        result = _invoke(runner, cli_db, "--json", "sources", "list")
        payload = json.loads(result.stdout)
        assert len(payload) == 1
        s = payload[0]
        assert s["source_id"] == "work"
        assert s["source_type"] == "local"
        assert s["display_name"] == "Work files"
        assert s["enabled"] is True
        assert s["files"] == 0


# ---------------------------------------------------------------------------
# sources add
# ---------------------------------------------------------------------------

class TestSourcesAdd:
    def test_add_with_defaults(self, runner, cli_db):
        result = _invoke(runner, cli_db, "sources", "add", "work")
        assert result.exit_code == 0

        # Verify the source actually exists
        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(show.stdout)
        assert payload["source_id"] == "work"
        assert payload["source_type"] == "local"
        assert payload["enabled"] is True
        assert payload["display_name"] is None

    def test_add_with_name_and_type(self, runner, cli_db):
        result = _invoke(
            runner, cli_db,
            "sources", "add", "gdrive_main",
            "--type", "gdrive",
            "--name", "Google Drive",
        )
        assert result.exit_code == 0

        show = _invoke(runner, cli_db, "--json", "sources", "show", "gdrive_main")
        payload = json.loads(show.stdout)
        assert payload["source_type"] == "gdrive"
        assert payload["display_name"] == "Google Drive"

    def test_add_disabled_flag(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "archive", "--disabled")
        show = _invoke(runner, cli_db, "--json", "sources", "show", "archive")
        payload = json.loads(show.stdout)
        assert payload["enabled"] is False

    def test_add_duplicate_id_fails(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(runner, cli_db, "sources", "add", "work")
        assert result.exit_code != 0
        assert "already exists" in result.stderr

    def test_add_writes_audit_entry(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work", "--type", "local")
        audit = _invoke(runner, cli_db, "--json", "audit", "--action", "source.add")
        entries = json.loads(audit.stdout)
        assert len(entries) == 1
        assert entries[0]["actor"] == "cli.sources"
        assert entries[0]["entity_id"] == "work"
        assert entries[0]["details"]["type"] == "local"
        assert entries[0]["details"]["enabled"] is True


# ---------------------------------------------------------------------------
# sources show
# ---------------------------------------------------------------------------

class TestSourcesShow:
    def test_show_unknown_source_errors(self, runner, cli_db):
        result = _invoke(runner, cli_db, "sources", "show", "nonexistent")
        assert result.exit_code != 0
        assert "No source" in result.stderr

    def test_show_displays_full_detail(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work",
                "--type", "local", "--name", "My work files")
        result = _invoke(runner, cli_db, "sources", "show", "work")
        assert result.exit_code == 0
        assert "work" in result.stdout
        assert "My work files" in result.stdout
        assert "enabled" in result.stdout

    def test_show_json_includes_created_at(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(result.stdout)
        assert "created_at" in payload
        assert payload["created_at"] is not None


# ---------------------------------------------------------------------------
# sources enable / disable
# ---------------------------------------------------------------------------

class TestSourcesEnableDisable:
    def test_enable_already_enabled_is_noop(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(runner, cli_db, "sources", "enable", "work")
        assert result.exit_code == 0
        assert "already enabled" in result.stdout

    def test_enable_a_disabled_source(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "archive", "--disabled")
        result = _invoke(runner, cli_db, "sources", "enable", "archive")
        assert result.exit_code == 0

        show = _invoke(runner, cli_db, "--json", "sources", "show", "archive")
        assert json.loads(show.stdout)["enabled"] is True

    def test_disable_an_enabled_source(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(runner, cli_db, "sources", "disable", "work")
        assert result.exit_code == 0

        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        assert json.loads(show.stdout)["enabled"] is False

    def test_disable_already_disabled_is_noop(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "archive", "--disabled")
        result = _invoke(runner, cli_db, "sources", "disable", "archive")
        assert result.exit_code == 0
        assert "already disabled" in result.stdout

    def test_enable_unknown_source_errors(self, runner, cli_db):
        result = _invoke(runner, cli_db, "sources", "enable", "ghost")
        assert result.exit_code != 0
        assert "No source" in result.stderr

    def test_enable_disable_write_audit_entries(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        _invoke(runner, cli_db, "sources", "disable", "work")
        _invoke(runner, cli_db, "sources", "enable", "work")

        audit = _invoke(
            runner, cli_db, "--json", "audit",
            "--action", "source.enable",
        )
        entries = json.loads(audit.stdout)
        assert len(entries) == 1

        audit2 = _invoke(
            runner, cli_db, "--json", "audit",
            "--action", "source.disable",
        )
        entries2 = json.loads(audit2.stdout)
        assert len(entries2) == 1


# ---------------------------------------------------------------------------
# sources remove
# ---------------------------------------------------------------------------

class TestSourcesRemove:
    def test_remove_unknown_errors(self, runner, cli_db):
        result = _invoke(runner, cli_db, "sources", "remove", "ghost")
        assert result.exit_code != 0
        assert "No source" in result.stderr

    def test_remove_without_apply_is_dry_run(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "throwaway")
        result = _invoke(runner, cli_db, "sources", "remove", "throwaway")
        assert result.exit_code == 0
        assert "would remove" in result.stdout

        # Source still exists
        show = _invoke(runner, cli_db, "--json", "sources", "show", "throwaway")
        assert show.exit_code == 0

    def test_remove_with_apply_actually_deletes(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "throwaway")
        result = _invoke(runner, cli_db, "sources", "remove", "throwaway", "--apply")
        assert result.exit_code == 0

        # Source no longer exists
        show = _invoke(runner, cli_db, "sources", "show", "throwaway")
        assert show.exit_code != 0

    def test_remove_writes_audit_entry(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "throwaway")
        _invoke(runner, cli_db, "sources", "remove", "throwaway", "--apply")

        audit = _invoke(
            runner, cli_db, "--json", "audit",
            "--action", "source.remove",
        )
        entries = json.loads(audit.stdout)
        assert len(entries) == 1
        assert entries[0]["entity_id"] == "throwaway"

    def test_remove_blocked_when_files_reference_source(
        self, runner, cli_db, tmp_path,
    ):
        """FK RESTRICT: can't delete a source while files still reference it.

        We have to scan a real tree to populate file rows. We use a sibling
        of cli_db so the scan doesn't pick up the SQLite DB itself.
        """
        # Set up a tiny tree to scan
        tree = tmp_path / "tree"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "a.txt").write_text("hello")

        # Scan auto-creates the 'local' source AND populates file rows
        scan_result = _invoke(runner, cli_db, "scan", "local", str(tree))
        assert scan_result.exit_code == 0

        # Dry-run should warn that files reference the source
        dry = _invoke(runner, cli_db, "sources", "remove", "local")
        assert dry.exit_code == 0
        assert "still reference" in dry.stdout

        # --apply should fail with a clear error
        applied = _invoke(runner, cli_db, "sources", "remove", "local", "--apply")
        assert applied.exit_code != 0
        assert "Cannot remove" in applied.stderr
