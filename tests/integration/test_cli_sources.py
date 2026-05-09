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


# ---------------------------------------------------------------------------
# sources config (v1.6.0)
# ---------------------------------------------------------------------------

class TestSourcesConfig:
    """Per-source config mutation. Bridges the v1.5.0 CLI gap that
    setup_gdrive_source.py worked around."""

    def test_show_empty_config_when_no_flags(self, runner, cli_db):
        """With no mutation flags, prints config (empty for a fresh source)."""
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(runner, cli_db, "sources", "config", "work")
        assert result.exit_code == 0
        assert "work" in result.stdout
        assert "(empty)" in result.stdout

    def test_show_returns_json_with_no_flags(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(runner, cli_db, "--json", "sources", "config", "work")
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["source_id"] == "work"
        assert payload["config"] == {}

    def test_unknown_source_id_errors(self, runner, cli_db):
        result = _invoke(runner, cli_db, "sources", "config", "does_not_exist")
        assert result.exit_code != 0
        assert "No source with id" in result.stderr

    def test_set_single_string_value(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "client_secrets_path=/tmp/cs.json",
        )
        assert result.exit_code == 0
        assert "set" in result.stdout

        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(show.stdout)
        assert payload["config"] == {"client_secrets_path": "/tmp/cs.json"}

    def test_set_multiple_values_one_invocation(self, runner, cli_db):
        """--set is repeatable; all pairs apply in a single invocation."""
        _invoke(runner, cli_db, "sources", "add", "gdrive_main", "--type", "gdrive")
        result = _invoke(
            runner, cli_db, "sources", "config", "gdrive_main",
            "--set", "client_secrets_path=/p/cs.json",
            "--set", "credentials_path=/p/creds.json",
            "--set", "root_folder_id=1abc...",
        )
        assert result.exit_code == 0

        show = _invoke(runner, cli_db, "--json", "sources", "show", "gdrive_main")
        payload = json.loads(show.stdout)
        assert payload["config"] == {
            "client_secrets_path": "/p/cs.json",
            "credentials_path": "/p/creds.json",
            "root_folder_id": "1abc...",
        }

    def test_set_value_parsed_as_json_for_boolean(self, runner, cli_db):
        """Values are parsed as JSON first; 'true' becomes Python True."""
        _invoke(runner, cli_db, "sources", "add", "work")
        _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "include_shared=true",
        )
        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(show.stdout)
        assert payload["config"] == {"include_shared": True}  # Python bool, not string

    def test_set_value_parsed_as_json_for_int(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "max_workers=8",
        )
        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(show.stdout)
        assert payload["config"] == {"max_workers": 8}  # Python int

    def test_set_value_falls_back_to_string_when_json_invalid(
        self, runner, cli_db,
    ):
        """Values that aren't valid JSON are kept as literal strings."""
        _invoke(runner, cli_db, "sources", "add", "work")
        _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "client_secrets_path=/some/path/cs.json",
        )
        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(show.stdout)
        # Path string isn't valid JSON, so it's stored as string
        assert payload["config"] == {"client_secrets_path": "/some/path/cs.json"}

    def test_set_value_with_equals_in_value(self, runner, cli_db):
        """partition('=') splits on FIRST '=' so values can contain '='."""
        _invoke(runner, cli_db, "sources", "add", "work")
        _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "connstring=postgres://user:pass@host?db=foo",
        )
        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(show.stdout)
        assert payload["config"]["connstring"] == "postgres://user:pass@host?db=foo"

    def test_set_value_without_equals_errors(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "no_equals_sign",
        )
        assert result.exit_code != 0
        assert "KEY=VALUE" in result.stderr

    def test_set_with_empty_key_errors(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "=value_only",
        )
        assert result.exit_code != 0
        assert "empty key" in result.stderr

    def test_unset_removes_existing_key(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "a=1", "--set", "b=2",
        )
        result = _invoke(
            runner, cli_db, "sources", "config", "work",
            "--unset", "a",
        )
        assert result.exit_code == 0

        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(show.stdout)
        assert payload["config"] == {"b": 2}

    def test_unset_missing_key_silent_noop(self, runner, cli_db):
        """--unset on a non-existent key is silent and exits 0 with
        a 'no changes' message."""
        _invoke(runner, cli_db, "sources", "add", "work")
        result = _invoke(
            runner, cli_db, "sources", "config", "work",
            "--unset", "never_existed",
        )
        assert result.exit_code == 0
        assert "No changes" in result.stdout

    def test_clear_removes_all_keys(self, runner, cli_db):
        _invoke(runner, cli_db, "sources", "add", "work")
        _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "a=1", "--set", "b=2", "--set", "c=3",
        )
        result = _invoke(
            runner, cli_db, "sources", "config", "work", "--clear",
        )
        assert result.exit_code == 0

        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(show.stdout)
        assert payload["config"] == {}

    def test_clear_then_set_atomic_replace(self, runner, cli_db):
        """--clear + --set in one invocation = full replacement (clear
        applies first, then set). Useful for re-pointing a gdrive source."""
        _invoke(runner, cli_db, "sources", "add", "gdrive_main", "--type", "gdrive")
        _invoke(
            runner, cli_db, "sources", "config", "gdrive_main",
            "--set", "client_secrets_path=/old/cs.json",
            "--set", "credentials_path=/old/creds.json",
            "--set", "root_folder_id=OLD",
        )
        # Now: clear + set fresh values atomically
        result = _invoke(
            runner, cli_db, "sources", "config", "gdrive_main",
            "--clear",
            "--set", "client_secrets_path=/new/cs.json",
            "--set", "credentials_path=/new/creds.json",
            "--set", "root_folder_id=NEW",
        )
        assert result.exit_code == 0

        show = _invoke(runner, cli_db, "--json", "sources", "show", "gdrive_main")
        payload = json.loads(show.stdout)
        # Old values gone; new values in place
        assert payload["config"] == {
            "client_secrets_path": "/new/cs.json",
            "credentials_path": "/new/creds.json",
            "root_folder_id": "NEW",
        }

    def test_audit_event_emitted_on_mutation(self, runner, cli_db):
        """Mutations emit a 'source.config' audit event for traceability."""
        _invoke(runner, cli_db, "sources", "add", "work")
        _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "foo=bar",
        )
        audit = _invoke(
            runner, cli_db, "--json", "audit", "--limit", "5",
        )
        entries = json.loads(audit.stdout)
        config_events = [e for e in entries if e["action"] == "source.config"]
        assert len(config_events) == 1
        assert config_events[0]["entity_id"] == "work"
        # entity_type is stored separately and lets us reconstruct
        # the 'source:work' display string when rendering tables.
        assert config_events[0]["entity_type"] == "source"

    def test_no_audit_event_when_noop(self, runner, cli_db):
        """--unset on a missing key produces no audit event
        (nothing changed)."""
        _invoke(runner, cli_db, "sources", "add", "work")
        before = _invoke(runner, cli_db, "--json", "audit", "--limit", "50")
        before_entries = json.loads(before.stdout)
        before_count = sum(
            1 for e in before_entries if e["action"] == "source.config"
        )

        _invoke(
            runner, cli_db, "sources", "config", "work",
            "--unset", "never_set",
        )

        after = _invoke(runner, cli_db, "--json", "audit", "--limit", "50")
        after_entries = json.loads(after.stdout)
        after_count = sum(
            1 for e in after_entries if e["action"] == "source.config"
        )
        assert after_count == before_count  # no new event

    def test_other_source_fields_preserved_through_mutation(
        self, runner, cli_db,
    ):
        """Updating config doesn't reset display_name, enabled, etc."""
        _invoke(
            runner, cli_db, "sources", "add", "work",
            "--type", "gdrive", "--name", "Important Name",
        )
        _invoke(runner, cli_db, "sources", "disable", "work")

        _invoke(
            runner, cli_db, "sources", "config", "work",
            "--set", "foo=bar",
        )

        show = _invoke(runner, cli_db, "--json", "sources", "show", "work")
        payload = json.loads(show.stdout)
        assert payload["display_name"] == "Important Name"
        assert payload["source_type"] == "gdrive"
        assert payload["enabled"] is False  # disable preserved
        assert payload["config"] == {"foo": "bar"}

