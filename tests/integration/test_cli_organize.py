"""Integration tests for `curator organize` CLI command (Phase Gamma F1).

Verifies the organize command runs end-to-end: scan a tmp directory,
then run ``curator organize <source>`` and check the output.
"""

from __future__ import annotations

import json
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
    return tmp_path / "curator_organize_test.db"


# ---------------------------------------------------------------------------
# `curator organize` (plan mode)
# ---------------------------------------------------------------------------


class TestOrganizeCli:
    def test_unknown_source_prints_empty_message(
        self, runner, db_path
    ):
        # No scan has been done; organize on a non-existent source should
        # print the "no files indexed" message and return cleanly.
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "ghost_source"],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        assert "No files indexed" in result.stdout

    def test_organize_after_scan_shows_buckets(
        self, runner, db_path, tmp_path
    ):
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "ordinary.txt").write_text("hi")
        # Add a file that'll trigger CAUTION (project_file).
        proj = tree / "myproj"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "main.py").write_text("print()")

        # Scan first.
        scan_result = runner.invoke(
            app,
            ["--db", str(db_path), "scan", "local", str(tree)],
        )
        assert scan_result.exit_code == 0, scan_result.stdout

        # Now plan.
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local"],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        assert "Organize plan" in result.stdout
        assert "Scanned:" in result.stdout

    def test_json_output_structure(
        self, runner, db_path, tmp_path
    ):
        tree = tmp_path / "json_tree"
        tree.mkdir()
        (tree / "a.txt").write_text("x")
        (tree / "b.txt").write_text("y")

        scan_result = runner.invoke(
            app,
            ["--db", str(db_path), "scan", "local", str(tree)],
        )
        assert scan_result.exit_code == 0

        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local"],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)
        assert payload["source_id"] == "local"
        assert "total_files" in payload
        assert "safe" in payload
        assert "caution" in payload
        assert "refuse" in payload
        # Each bucket has count, total_size, by_concern.
        for bucket_name in ("safe", "caution", "refuse"):
            b = payload[bucket_name]
            assert "count" in b
            assert "total_size" in b
            assert "by_concern" in b

    def test_show_files_flag_includes_paths(
        self, runner, db_path, tmp_path
    ):
        tree = tmp_path / "showfiles"
        tree.mkdir()
        target = tree / "named_file.txt"
        target.write_text("findable")

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(tree)])
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local", "--show-files"],
        )
        assert result.exit_code == 0
        # The path of named_file.txt should appear in the output when
        # --show-files is set. (It might appear in any bucket depending
        # on the test machine's app-data paths; we just check presence.)
        assert "named_file.txt" in result.stdout

    def test_root_prefix_filters_files(
        self, runner, db_path, tmp_path
    ):
        tree = tmp_path / "prefix_tree"
        tree.mkdir()
        (tree / "outside" / "a.txt").parent.mkdir()
        (tree / "outside" / "a.txt").write_text("x")
        (tree / "inside" / "b.txt").parent.mkdir()
        (tree / "inside" / "b.txt").write_text("y")

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(tree)])

        # Plan only the "inside" subtree.
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--root", str(tree / "inside")],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["total_files"] == 1
        assert payload["root_prefix"] == str(tree / "inside")
