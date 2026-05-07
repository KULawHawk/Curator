"""Integration tests for the ``curator bundles`` subcommands.

Covers ``bundles list``, ``bundles show``, ``bundles create``, and
``bundles dissolve``. Mutating commands (``create``, ``dissolve --apply``)
are verified to write audit log entries.
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


def _seed_files(runner: CliRunner, cli_db: Path, tmp_path: Path, *names: str) -> list[str]:
    """Create files in a temp tree, scan them, return their absolute paths.

    Used as setup for bundle-creation tests that need real curator_ids.
    """
    tree = tmp_path / "tree_for_bundles"
    tree.mkdir(parents=True, exist_ok=True)
    paths = []
    for n in names:
        p = tree / n
        p.write_text(f"content of {n}")
        paths.append(str(p))
    _invoke(runner, cli_db, "scan", "local", str(tree))
    return paths


# ---------------------------------------------------------------------------
# bundles list
# ---------------------------------------------------------------------------

class TestBundlesList:
    def test_empty_list_text_output(self, runner, cli_db):
        result = _invoke(runner, cli_db, "bundles", "list")
        assert result.exit_code == 0
        assert "No bundles" in result.stdout

    def test_empty_list_json_output(self, runner, cli_db):
        result = _invoke(runner, cli_db, "--json", "bundles", "list")
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []

    def test_lists_after_creating(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        _invoke(runner, cli_db, "bundles", "create", "my-bundle", *paths)

        result = _invoke(runner, cli_db, "bundles", "list")
        assert result.exit_code == 0
        assert "my-bundle" in result.stdout

    def test_list_json_returns_member_count(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt", "c.txt")
        _invoke(runner, cli_db, "bundles", "create", "trio", *paths)

        result = _invoke(runner, cli_db, "--json", "bundles", "list")
        payload = json.loads(result.stdout)
        assert len(payload) == 1
        assert payload[0]["name"] == "trio"
        assert payload[0]["members"] == 3


# ---------------------------------------------------------------------------
# bundles create
# ---------------------------------------------------------------------------

class TestBundlesCreate:
    def test_create_with_two_files(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        result = _invoke(runner, cli_db, "bundles", "create", "pair", *paths)
        assert result.exit_code == 0

        # Verify it actually exists
        list_result = _invoke(runner, cli_db, "--json", "bundles", "list")
        payload = json.loads(list_result.stdout)
        names = [b["name"] for b in payload]
        assert "pair" in names

    def test_create_with_description(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        result = _invoke(
            runner, cli_db,
            "bundles", "create", "labeled", *paths,
            "--description", "An important pair",
        )
        assert result.exit_code == 0

        # Find it and confirm description
        list_result = _invoke(runner, cli_db, "--json", "bundles", "list")
        bid = next(b["bundle_id"] for b in json.loads(list_result.stdout) if b["name"] == "labeled")
        show_result = _invoke(runner, cli_db, "--json", "bundles", "show", bid)
        assert json.loads(show_result.stdout)["description"] == "An important pair"

    def test_create_with_primary(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        result = _invoke(
            runner, cli_db,
            "bundles", "create", "with-pri", *paths,
            "--primary", paths[0],
        )
        assert result.exit_code == 0

        # Find the bundle, verify the primary role
        list_result = _invoke(runner, cli_db, "--json", "bundles", "list")
        bid = next(b["bundle_id"] for b in json.loads(list_result.stdout) if b["name"] == "with-pri")
        show_result = _invoke(runner, cli_db, "--json", "bundles", "show", bid)
        members = json.loads(show_result.stdout)["members"]
        primaries = [m for m in members if m["role"] == "primary"]
        assert len(primaries) == 1
        assert primaries[0]["path"] == paths[0]

    def test_create_with_unresolvable_member_errors(self, runner, cli_db, tmp_path):
        # Seed one real file so the runtime has a source, then try to create
        # with a nonsense identifier.
        _seed_files(runner, cli_db, tmp_path, "a.txt")
        result = _invoke(
            runner, cli_db,
            "bundles", "create", "broken",
            "/no/such/path.txt",
        )
        assert result.exit_code != 0
        assert "Couldn't resolve" in result.stderr

    def test_create_writes_audit_entry(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        _invoke(runner, cli_db, "bundles", "create", "audited", *paths)

        audit = _invoke(
            runner, cli_db, "--json", "audit",
            "--action", "create_manual",
        )
        entries = json.loads(audit.stdout)
        assert len(entries) == 1
        assert entries[0]["actor"] == "cli.bundles"
        assert entries[0]["details"]["name"] == "audited"
        assert entries[0]["details"]["members"] == 2


# ---------------------------------------------------------------------------
# bundles show
# ---------------------------------------------------------------------------

class TestBundlesShow:
    def test_show_unknown_bundle_id(self, runner, cli_db):
        result = _invoke(runner, cli_db, "bundles", "show", "12345678")
        assert result.exit_code != 0
        assert "No bundle matches" in result.stderr

    def test_show_by_full_uuid(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        _invoke(runner, cli_db, "bundles", "create", "lookup-test", *paths)

        list_result = _invoke(runner, cli_db, "--json", "bundles", "list")
        bid = json.loads(list_result.stdout)[0]["bundle_id"]

        result = _invoke(runner, cli_db, "bundles", "show", bid)
        assert result.exit_code == 0
        assert "lookup-test" in result.stdout

    def test_show_by_8char_prefix(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        _invoke(runner, cli_db, "bundles", "create", "prefix-test", *paths)

        list_result = _invoke(runner, cli_db, "--json", "bundles", "list")
        bid = json.loads(list_result.stdout)[0]["bundle_id"]
        prefix = bid[:8]

        result = _invoke(runner, cli_db, "bundles", "show", prefix)
        assert result.exit_code == 0
        assert "prefix-test" in result.stdout

    def test_show_json_includes_members_and_paths(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        _invoke(runner, cli_db, "bundles", "create", "json-test", *paths)

        list_result = _invoke(runner, cli_db, "--json", "bundles", "list")
        bid = json.loads(list_result.stdout)[0]["bundle_id"]

        result = _invoke(runner, cli_db, "--json", "bundles", "show", bid)
        payload = json.loads(result.stdout)
        assert payload["name"] == "json-test"
        assert len(payload["members"]) == 2
        # Each member has the path field populated (Q17 regression test)
        for m in payload["members"]:
            assert m["path"] is not None


# ---------------------------------------------------------------------------
# bundles dissolve
# ---------------------------------------------------------------------------

class TestBundlesDissolve:
    def test_dissolve_unknown_bundle(self, runner, cli_db):
        result = _invoke(runner, cli_db, "bundles", "dissolve", "12345678")
        assert result.exit_code != 0
        assert "No bundle matches" in result.stderr

    def test_dissolve_without_apply_is_dry_run(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        _invoke(runner, cli_db, "bundles", "create", "doomed", *paths)

        list_result = _invoke(runner, cli_db, "--json", "bundles", "list")
        bid = json.loads(list_result.stdout)[0]["bundle_id"]

        result = _invoke(runner, cli_db, "bundles", "dissolve", bid)
        assert result.exit_code == 0
        assert "would dissolve" in result.stdout

        # Bundle still exists
        list_after = _invoke(runner, cli_db, "--json", "bundles", "list")
        assert len(json.loads(list_after.stdout)) == 1

    def test_dissolve_with_apply_actually_deletes(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        _invoke(runner, cli_db, "bundles", "create", "doomed", *paths)

        list_result = _invoke(runner, cli_db, "--json", "bundles", "list")
        bid = json.loads(list_result.stdout)[0]["bundle_id"]

        result = _invoke(runner, cli_db, "bundles", "dissolve", bid, "--apply")
        assert result.exit_code == 0

        # Bundle gone
        list_after = _invoke(runner, cli_db, "--json", "bundles", "list")
        assert json.loads(list_after.stdout) == []

    def test_dissolve_writes_audit_entry(self, runner, cli_db, tmp_path):
        paths = _seed_files(runner, cli_db, tmp_path, "a.txt", "b.txt")
        _invoke(runner, cli_db, "bundles", "create", "audited", *paths)

        list_result = _invoke(runner, cli_db, "--json", "bundles", "list")
        bid = json.loads(list_result.stdout)[0]["bundle_id"]

        _invoke(runner, cli_db, "bundles", "dissolve", bid, "--apply")

        audit = _invoke(runner, cli_db, "--json", "audit", "--action", "dissolve")
        entries = json.loads(audit.stdout)
        assert len(entries) == 1
        assert entries[0]["actor"] == "cli.bundles"
