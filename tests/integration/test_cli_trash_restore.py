"""Integration tests for ``curator trash`` and ``curator restore``.

Covers:
  * ``trash`` --apply gating, dry-run output, audit entry, soft-delete.
  * ``restore`` UUID-only argument validation, missing-record error,
    Phase Alpha ``RestoreImpossibleError`` (no os_trash_location yet).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from curator.cli.main import app


pytestmark = pytest.mark.integration


# ``runner`` and ``cli_db`` fixtures live in the top-level conftest.py.


def _invoke(runner: CliRunner, db_path: Path, *args: str):
    return runner.invoke(app, ["--db", str(db_path), *args], catch_exceptions=False)


def _seed_one_file(runner: CliRunner, cli_db: Path, tmp_path: Path,
                    name: str = "doomed.txt") -> tuple[str, str]:
    """Create + scan a single file, return (path, curator_id)."""
    tree = tmp_path / "tree_for_trash"
    tree.mkdir(parents=True, exist_ok=True)
    p = tree / name
    p.write_text(f"content of {name}")
    _invoke(runner, cli_db, "scan", "local", str(tree))

    # Pull the curator_id back out via inspect --json
    show = _invoke(runner, cli_db, "--json", "inspect", str(p))
    cid = json.loads(show.stdout)["curator_id"]
    return str(p), cid


# ---------------------------------------------------------------------------
# trash
# ---------------------------------------------------------------------------

class TestCliTrash:
    def test_trash_unknown_identifier(self, runner, cli_db):
        result = _invoke(runner, cli_db, "trash", "/nonexistent/path.txt")
        assert result.exit_code != 0
        assert "No file matches" in result.stderr

    def test_trash_without_apply_is_dry_run(self, runner, cli_db, tmp_path):
        path, _ = _seed_one_file(runner, cli_db, tmp_path)
        result = _invoke(runner, cli_db, "trash", path)
        assert result.exit_code == 0
        assert "would trash" in result.stdout

        # File still exists on disk
        assert Path(path).exists()

    def test_trash_with_apply_actually_trashes(self, runner, cli_db, tmp_path):
        path, cid = _seed_one_file(runner, cli_db, tmp_path)
        result = _invoke(runner, cli_db, "trash", path, "--apply")
        assert result.exit_code == 0

        # File is gone from disk
        assert not Path(path).exists()

        # And soft-deleted in the index
        show = _invoke(runner, cli_db, "--json", "inspect", cid)
        payload = json.loads(show.stdout)
        assert payload["deleted_at"] is not None

    def test_trash_with_reason_and_apply(self, runner, cli_db, tmp_path):
        path, _ = _seed_one_file(runner, cli_db, tmp_path)
        result = _invoke(
            runner, cli_db,
            "trash", path,
            "--reason", "spring cleaning",
            "--apply",
        )
        assert result.exit_code == 0

        # Reason flows into the audit log
        audit = _invoke(runner, cli_db, "--json", "audit", "--action", "trash")
        entries = json.loads(audit.stdout)
        assert len(entries) == 1
        assert entries[0]["actor"] == "cli.trash"

    def test_trash_by_curator_id(self, runner, cli_db, tmp_path):
        _, cid = _seed_one_file(runner, cli_db, tmp_path)
        result = _invoke(runner, cli_db, "trash", cid, "--apply")
        assert result.exit_code == 0

    def test_trash_writes_audit_entry(self, runner, cli_db, tmp_path):
        path, _ = _seed_one_file(runner, cli_db, tmp_path)
        _invoke(runner, cli_db, "trash", path, "--apply")

        audit = _invoke(runner, cli_db, "--json", "audit", "--action", "trash")
        entries = json.loads(audit.stdout)
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

class TestCliRestore:
    def test_restore_rejects_non_uuid_identifier(self, runner, cli_db):
        result = _invoke(runner, cli_db, "restore", "/some/path.txt")
        assert result.exit_code != 0
        assert "UUID" in result.stderr

    def test_restore_unknown_curator_id(self, runner, cli_db):
        bogus = str(uuid4())
        result = _invoke(runner, cli_db, "restore", bogus)
        assert result.exit_code != 0
        assert "No trash record" in result.stderr

    def test_restore_dry_run_shows_target_path(self, runner, cli_db, tmp_path):
        path, cid = _seed_one_file(runner, cli_db, tmp_path)
        _invoke(runner, cli_db, "trash", path, "--apply")

        result = _invoke(runner, cli_db, "restore", cid)
        assert result.exit_code == 0
        assert "would restore" in result.stdout
        # Mentions the target path that restore would attempt
        assert path in result.stdout or path.replace("\\", "/") in result.stdout

    def test_restore_apply_round_trip_on_windows(self, runner, cli_db, tmp_path):
        """Q14: on Windows, ``restore --apply`` puts the file back."""
        if sys.platform != "win32":
            pytest.skip("OS-trash restore is Windows-only in Phase Alpha")

        path, cid = _seed_one_file(runner, cli_db, tmp_path)
        _invoke(runner, cli_db, "trash", path, "--apply")
        assert not Path(path).exists()

        result = _invoke(runner, cli_db, "restore", cid, "--apply")
        assert result.exit_code == 0, (
            f"restore --apply should succeed on Windows after Q14; "
            f"got exit={result.exit_code}, stderr={result.stderr!r}"
        )
        assert Path(path).exists()

    def test_restore_apply_unsupported_on_non_windows(self, runner, cli_db, tmp_path):
        """On non-Windows the lookup returns None → RestoreImpossibleError, exit 2."""
        if sys.platform == "win32":
            pytest.skip("Windows path is covered by test_restore_apply_round_trip_on_windows")

        path, cid = _seed_one_file(runner, cli_db, tmp_path)
        _invoke(runner, cli_db, "trash", path, "--apply")

        result = _invoke(runner, cli_db, "restore", cid, "--apply")
        # Code 2 distinguishes "not user error" from "user error" (1).
        assert result.exit_code == 2
