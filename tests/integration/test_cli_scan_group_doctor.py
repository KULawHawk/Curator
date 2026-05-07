"""Integration tests for ``curator scan``, ``curator group``, ``curator doctor``.

These three commands together exercise:
  * ``scan`` argument parsing, ``--ignore`` repeated option, ``--json`` output
  * ``group`` --apply gating, primary-pick strategies, dedupe behavior
  * ``doctor`` health-check output format
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
    return runner.invoke(app, ["--db", str(db_path), *args], catch_exceptions=False)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

class TestCliScan:
    def test_scan_nonexistent_path_errors(self, runner, cli_db):
        result = _invoke(runner, cli_db, "scan", "local", "/nope/never/exists")
        assert result.exit_code != 0
        assert "Path does not exist" in result.stderr

    def test_scan_empty_dir_succeeds(self, runner, cli_db, tmp_path):
        tree = tmp_path / "empty_tree"
        tree.mkdir(parents=True, exist_ok=True)
        result = _invoke(runner, cli_db, "scan", "local", str(tree))
        assert result.exit_code == 0

    def test_scan_json_output_has_metrics(self, runner, cli_db, tmp_path):
        tree = tmp_path / "t"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "a.txt").write_text("a")
        (tree / "b.txt").write_text("b")
        result = _invoke(runner, cli_db, "--json", "scan", "local", str(tree))
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["files_seen"] == 2
        assert payload["files_hashed"] == 2
        assert payload["errors"] == 0
        assert "duration_seconds" in payload

    def test_scan_with_ignore_option(self, runner, cli_db, tmp_path):
        tree = tmp_path / "t"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "keep.txt").write_text("k")
        skip_dir = tree / "skip"
        skip_dir.mkdir()
        (skip_dir / "ignored.txt").write_text("i")

        result = _invoke(
            runner, cli_db, "--json",
            "scan", "local", str(tree),
            "--ignore", "skip",
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["files_seen"] == 1


# ---------------------------------------------------------------------------
# group
# ---------------------------------------------------------------------------

class TestCliGroup:
    def test_group_no_duplicates_returns_message(self, runner, cli_db, tmp_path):
        tree = tmp_path / "t"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "unique_a.txt").write_text("alpha")
        (tree / "unique_b.txt").write_text("beta")
        _invoke(runner, cli_db, "scan", "local", str(tree))

        result = _invoke(runner, cli_db, "group")
        assert result.exit_code == 0
        assert "No duplicate" in result.stdout

    def test_group_dry_run_shows_would_trash(self, runner, cli_db, tmp_path):
        tree = tmp_path / "dups"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "a.txt").write_text("same")
        (tree / "b.txt").write_text("same")
        _invoke(runner, cli_db, "scan", "local", str(tree))

        result = _invoke(runner, cli_db, "group")
        assert result.exit_code == 0
        # Without --apply, output describes what *would* happen.
        assert "would" in result.stdout.lower() or "would_trash" in result.stdout

    def test_group_apply_actually_trashes_extras(self, runner, cli_db, tmp_path):
        tree = tmp_path / "dups2"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "primary.txt").write_text("identical")
        (tree / "duplicate.txt").write_text("identical")
        _invoke(runner, cli_db, "scan", "local", str(tree))

        result = _invoke(runner, cli_db, "group", "--apply")
        assert result.exit_code == 0

        # Verify exactly one file remains on disk
        remaining = list(tree.iterdir())
        assert len(remaining) == 1

    def test_group_keep_strategy_shortest_path(self, runner, cli_db, tmp_path):
        tree = tmp_path / "strat"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "a.txt").write_text("same")
        deep = tree / "very_long_dir" / "nested"
        deep.mkdir(parents=True)
        (deep / "duplicate.txt").write_text("same")
        _invoke(runner, cli_db, "scan", "local", str(tree))

        result = _invoke(
            runner, cli_db, "--json",
            "group", "--apply", "--keep", "shortest_path",
        )
        assert result.exit_code == 0
        # The shorter path (a.txt at the root) should remain
        assert (tree / "a.txt").exists()
        assert not (deep / "duplicate.txt").exists()

    def test_group_unknown_keep_strategy_errors(self, runner, cli_db, tmp_path):
        tree = tmp_path / "x"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "a.txt").write_text("same")
        (tree / "b.txt").write_text("same")
        _invoke(runner, cli_db, "scan", "local", str(tree))

        # Catch the BadParameter exception manually so the runner returns
        # a non-zero exit code instead of bubbling up.
        result = runner.invoke(
            app,
            ["--db", str(cli_db), "group", "--apply", "--keep", "no_such_strategy"],
            catch_exceptions=True,
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

class TestCliDoctor:
    def test_doctor_empty_db_succeeds(self, runner, cli_db):
        result = _invoke(runner, cli_db, "doctor")
        assert result.exit_code == 0
        # Standard sections present
        assert "Curator doctor" in result.stdout
        assert "plugins:" in result.stdout
        assert "Index stats" in result.stdout

    def test_doctor_reports_vendored_optional_deps(self, runner, cli_db):
        result = _invoke(runner, cli_db, "doctor")
        assert result.exit_code == 0
        # ppdeep + send2trash both vendored after Step 8
        assert "ppdeep" in result.stdout
        assert "send2trash" in result.stdout

    def test_doctor_lists_sources_after_scan(self, runner, cli_db, tmp_path):
        tree = tmp_path / "t"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "a.txt").write_text("a")
        _invoke(runner, cli_db, "scan", "local", str(tree))

        result = _invoke(runner, cli_db, "doctor")
        assert result.exit_code == 0
        assert "Sources" in result.stdout
        assert "local" in result.stdout
