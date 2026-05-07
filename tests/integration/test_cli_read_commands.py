"""Integration tests for the read-only CLI commands.

Covers ``inspect``, ``lineage``, and ``audit`` — none of which mutate state
(except indirectly through their setup scans). These commands all support
both human-readable and ``--json`` output paths; the tests exercise both.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from curator.cli.main import app


pytestmark = pytest.mark.integration


# ``runner`` and ``cli_db`` fixtures live in the top-level conftest.py.


def _invoke(runner: CliRunner, db_path: Path, *args: str):
    return runner.invoke(app, ["--db", str(db_path), *args], catch_exceptions=False)


def _seed_files(runner: CliRunner, cli_db: Path, tmp_path: Path,
                *names: str) -> Path:
    """Create + scan a small tree, return the tree root."""
    tree = tmp_path / "tree_for_read_cmds"
    tree.mkdir(parents=True, exist_ok=True)
    for n in names:
        (tree / n).write_text(f"content of {n}")
    _invoke(runner, cli_db, "scan", "local", str(tree))
    return tree


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------

class TestCliInspect:
    def test_inspect_unknown_path(self, runner, cli_db):
        result = _invoke(runner, cli_db, "inspect", "/nope/never.txt")
        assert result.exit_code != 0
        assert "No file matches" in result.stderr

    def test_inspect_by_path(self, runner, cli_db, tmp_path):
        tree = _seed_files(runner, cli_db, tmp_path, "report.md")
        path = str(tree / "report.md")
        result = _invoke(runner, cli_db, "inspect", path)
        assert result.exit_code == 0
        assert "report.md" in result.stdout

    def test_inspect_by_curator_id(self, runner, cli_db, tmp_path):
        tree = _seed_files(runner, cli_db, tmp_path, "report.md")
        path = str(tree / "report.md")
        # First fetch the id via JSON inspect
        first = _invoke(runner, cli_db, "--json", "inspect", path)
        cid = json.loads(first.stdout)["curator_id"]
        # Now look it up by id
        result = _invoke(runner, cli_db, "inspect", cid)
        assert result.exit_code == 0

    def test_inspect_json_includes_hash_and_classification(self, runner, cli_db, tmp_path):
        tree = _seed_files(runner, cli_db, tmp_path, "report.md")
        result = _invoke(
            runner, cli_db, "--json", "inspect", str(tree / "report.md"),
        )
        payload = json.loads(result.stdout)
        # Hashes computed
        assert payload["xxhash3_128"] is not None
        assert payload["md5"] is not None
        # Classification assigned
        assert payload["file_type"] is not None
        # Lineage list is present (possibly empty)
        assert isinstance(payload["lineage"], list)


# ---------------------------------------------------------------------------
# lineage
# ---------------------------------------------------------------------------

class TestCliLineage:
    def test_lineage_unknown_path(self, runner, cli_db):
        result = _invoke(runner, cli_db, "lineage", "/nowhere/x.txt")
        assert result.exit_code != 0
        assert "No file matches" in result.stderr

    def test_lineage_no_edges_returns_message(self, runner, cli_db, tmp_path):
        tree = _seed_files(runner, cli_db, tmp_path, "lonely.txt")
        result = _invoke(runner, cli_db, "lineage", str(tree / "lonely.txt"))
        assert result.exit_code == 0
        assert "No lineage edges" in result.stdout

    def test_lineage_finds_duplicate_edge(self, runner, cli_db, tmp_path):
        # Two files with identical content → DUPLICATE edge
        tree = tmp_path / "dup_tree"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "a.txt").write_text("identical content")
        (tree / "b.txt").write_text("identical content")
        _invoke(runner, cli_db, "scan", "local", str(tree))

        result = _invoke(runner, cli_db, "lineage", str(tree / "a.txt"))
        assert result.exit_code == 0
        assert "duplicate" in result.stdout.lower() or "DUPLICATE" in result.stdout

    def test_lineage_json_returns_edge_list(self, runner, cli_db, tmp_path):
        tree = tmp_path / "dup_tree"
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "a.txt").write_text("identical content")
        (tree / "b.txt").write_text("identical content")
        _invoke(runner, cli_db, "scan", "local", str(tree))

        result = _invoke(
            runner, cli_db, "--json", "lineage", str(tree / "a.txt"),
        )
        payload = json.loads(result.stdout)
        assert "edges" in payload
        assert len(payload["edges"]) >= 1
        assert payload["edges"][0]["kind"] == "duplicate"


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

class TestCliAudit:
    def test_audit_empty_db_returns_message(self, runner, cli_db):
        result = _invoke(runner, cli_db, "audit")
        assert result.exit_code == 0
        assert "No matching" in result.stdout

    def test_audit_after_a_scan_has_entries(self, runner, cli_db, tmp_path):
        _seed_files(runner, cli_db, tmp_path, "x.txt")
        result = _invoke(runner, cli_db, "audit")
        assert result.exit_code == 0
        assert "scan.start" in result.stdout
        assert "scan.complete" in result.stdout

    def test_audit_filter_by_actor(self, runner, cli_db, tmp_path):
        _seed_files(runner, cli_db, tmp_path, "x.txt")
        result = _invoke(
            runner, cli_db, "--json", "audit", "--actor", "curator.scan",
        )
        entries = json.loads(result.stdout)
        assert all(e["actor"] == "curator.scan" for e in entries)
        assert len(entries) >= 2  # start + complete

    def test_audit_filter_by_action(self, runner, cli_db, tmp_path):
        _seed_files(runner, cli_db, tmp_path, "x.txt")
        result = _invoke(
            runner, cli_db, "--json", "audit", "--action", "scan.start",
        )
        entries = json.loads(result.stdout)
        assert all(e["action"] == "scan.start" for e in entries)

    def test_audit_limit_caps_results(self, runner, cli_db, tmp_path):
        _seed_files(runner, cli_db, tmp_path, "x.txt")
        # Run several more scans to generate entries
        tree = tmp_path / "tree_for_read_cmds"
        for _ in range(3):
            _invoke(runner, cli_db, "scan", "local", str(tree))

        result = _invoke(runner, cli_db, "--json", "audit", "-n", "2")
        entries = json.loads(result.stdout)
        assert len(entries) <= 2
