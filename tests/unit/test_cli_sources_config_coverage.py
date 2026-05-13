"""Coverage closure for cli/main.py `sources config` subcommand (v1.7.158b).

Tier 3 sub-ship 4b — the second half of the v1.7.158 split per Lesson #88.
Targets the 203-line `sources config` command + `_parse_set_value` helper
(lines 983-1200).

Test surface:
- `_parse_set_value` directly: JSON-parseable + literal-string fallback
- Read-only path: no flags → print current config (human + JSON, empty + populated)
- --share-visibility validation
- --share-visibility update path
- --set with JSON-parsed values (bool, int, list, string fallback)
- --set malformed (no '=') / empty key errors
- --unset (existing + non-existing key)
- --clear (with content + already empty)
- Combined --clear + --set (atomic reset+rewrite)
- No-op output (when --unset hits no keys)
- Source not found error
- JSON output for mutation path
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from curator.cli.main import _parse_set_value, app
from curator.models import SourceConfig


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    from curator.storage.repositories import SourceRepository
    db_path = tmp_path / "cli_sources_config.db"
    db = CuratorDB(db_path)
    db.init()
    return {
        "db": db,
        "db_path": db_path,
        "sources": SourceRepository(db),
    }


def _add(repos, **overrides) -> SourceConfig:
    base = dict(
        source_id="gdrive:x", source_type="gdrive",
        display_name="GDrive X", config={},
    )
    base.update(overrides)
    s = SourceConfig(**base)
    repos["sources"].insert(s)
    return s


# ---------------------------------------------------------------------------
# _parse_set_value
# ---------------------------------------------------------------------------


class TestParseSetValue:
    def test_parses_bool_true(self):
        assert _parse_set_value("true") is True

    def test_parses_bool_false(self):
        assert _parse_set_value("false") is False

    def test_parses_int(self):
        assert _parse_set_value("42") == 42

    def test_parses_float(self):
        assert _parse_set_value("3.14") == 3.14

    def test_parses_list(self):
        assert _parse_set_value("[1, 2, 3]") == [1, 2, 3]

    def test_parses_null(self):
        assert _parse_set_value("null") is None

    def test_parses_quoted_string(self):
        assert _parse_set_value('"hello"') == "hello"

    def test_string_fallback(self):
        # Unquoted bare string is not valid JSON -> falls back to literal
        assert _parse_set_value("hello") == "hello"

    def test_path_string_fallback(self):
        # Paths with slashes/colons aren't valid JSON
        assert _parse_set_value("/home/user/secret.json") == "/home/user/secret.json"


# ---------------------------------------------------------------------------
# Source not found / share-visibility validation
# ---------------------------------------------------------------------------


class TestSourceNotFoundAndValidation:
    def test_no_source_errors(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "ghost"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "No source with id" in combined

    def test_invalid_share_visibility_errors(self, runner, isolated_cli_db):
        _add(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x",
             "--share-visibility", "bogus"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "must be one of" in combined


# ---------------------------------------------------------------------------
# Read-only path (no flags)
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_human_empty_config(self, runner, isolated_cli_db):
        _add(isolated_cli_db, config={})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "(empty)" in combined
        assert "share_visibility" in combined

    def test_human_populated_config(self, runner, isolated_cli_db):
        _add(isolated_cli_db, config={"path": "/x", "include_shared": True})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "path" in combined
        assert "/x" in combined
        assert "include_shared" in combined

    def test_json_output(self, runner, isolated_cli_db):
        _add(isolated_cli_db, config={"path": "/y"})
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"path": "/y"' in combined
        assert '"share_visibility"' in combined

    def test_share_visibility_team_color(self, runner, isolated_cli_db):
        """Cover share_visibility='team' branch (yellow color path)."""
        _add(isolated_cli_db, share_visibility="team")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x"],
        )
        assert result.exit_code == 0

    def test_share_visibility_public_color(self, runner, isolated_cli_db):
        _add(isolated_cli_db, share_visibility="public")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x"],
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# --set
# ---------------------------------------------------------------------------


class TestSet:
    def test_set_string_value(self, runner, isolated_cli_db):
        _add(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--set", "path=/abc"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "set" in combined and "path" in combined
        src = isolated_cli_db["sources"].get("gdrive:x")
        assert src.config["path"] == "/abc"

    def test_set_bool_value(self, runner, isolated_cli_db):
        _add(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x",
             "--set", "include_shared=true"],
        )
        assert result.exit_code == 0
        src = isolated_cli_db["sources"].get("gdrive:x")
        assert src.config["include_shared"] is True

    def test_set_multiple_pairs(self, runner, isolated_cli_db):
        _add(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x",
             "--set", "a=1", "--set", "b=two", "--set", "c=true"],
        )
        assert result.exit_code == 0
        src = isolated_cli_db["sources"].get("gdrive:x")
        assert src.config["a"] == 1
        assert src.config["b"] == "two"
        assert src.config["c"] is True

    def test_set_malformed_no_equals_errors(self, runner, isolated_cli_db):
        _add(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--set", "no_equals_here"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "KEY=VALUE" in combined

    def test_set_empty_key_errors(self, runner, isolated_cli_db):
        _add(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--set", "=value"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "empty key" in combined

    def test_set_json_output(self, runner, isolated_cli_db):
        _add(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--set", "k=v"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"action": "updated"' in combined
        assert '"op": "set"' in combined


# ---------------------------------------------------------------------------
# --unset
# ---------------------------------------------------------------------------


class TestUnset:
    def test_unset_existing_key(self, runner, isolated_cli_db):
        _add(isolated_cli_db, config={"to_remove": "x", "keeper": "y"})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--unset", "to_remove"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "unset" in combined
        src = isolated_cli_db["sources"].get("gdrive:x")
        assert "to_remove" not in src.config
        assert src.config["keeper"] == "y"

    def test_unset_missing_key_is_noop(self, runner, isolated_cli_db):
        _add(isolated_cli_db, config={"only_key": "x"})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--unset", "ghost_key"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # No changes applied
        assert "No changes to apply" in combined
        src = isolated_cli_db["sources"].get("gdrive:x")
        assert src.config == {"only_key": "x"}

    def test_unset_noop_json_output(self, runner, isolated_cli_db):
        _add(isolated_cli_db, config={"x": 1})
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--unset", "ghost"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"action": "no-op"' in combined


# ---------------------------------------------------------------------------
# --clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_empties_config(self, runner, isolated_cli_db):
        _add(isolated_cli_db, config={"a": 1, "b": 2})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--clear"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "clear" in combined
        src = isolated_cli_db["sources"].get("gdrive:x")
        assert src.config == {}

    def test_clear_already_empty_is_noop(self, runner, isolated_cli_db):
        _add(isolated_cli_db, config={})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--clear"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Empty config -> --clear has nothing to track -> no changes
        assert "No changes to apply" in combined


class TestAtomicResetAndRewrite:
    def test_clear_then_set_atomic(self, runner, isolated_cli_db):
        _add(isolated_cli_db, config={"old": "x", "stale": "y"})
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x", "--clear",
             "--set", "fresh=value", "--set", "count=42"],
        )
        assert result.exit_code == 0
        src = isolated_cli_db["sources"].get("gdrive:x")
        assert src.config == {"fresh": "value", "count": 42}


# ---------------------------------------------------------------------------
# --share-visibility update path
# ---------------------------------------------------------------------------


class TestShareVisibilityUpdate:
    def test_change_share_visibility(self, runner, isolated_cli_db):
        _add(isolated_cli_db, share_visibility="private")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x",
             "--share-visibility", "public"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "visibility" in combined.lower() or "public" in combined
        src = isolated_cli_db["sources"].get("gdrive:x")
        assert src.share_visibility == "public"

    def test_same_share_visibility_is_noop(self, runner, isolated_cli_db):
        """If --share-visibility matches current value, no change tracked."""
        _add(isolated_cli_db, share_visibility="team")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "sources", "config", "gdrive:x",
             "--share-visibility", "team"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No changes to apply" in combined
