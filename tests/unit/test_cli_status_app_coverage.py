"""Coverage closure for cli/main.py `status_app` (set/get/report) (v1.7.169).

Tier 3 sub-ship 15 of the CLI Coverage Arc.

Also covers the live ``_resolve_file`` helper (the duplicate that used
to live at line 187 was deleted in v1.7.180 after Jake's decision to
merge its prefix-match feature into this definition).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from curator._compat.datetime import utcnow_naive
from curator.cli.main import _resolve_file, app
from curator.models import FileEntity, SourceConfig


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    from curator.storage.repositories import (
        FileRepository, SourceRepository,
    )
    db_path = tmp_path / "cli_status.db"
    db = CuratorDB(db_path)
    db.init()
    return {
        "db": db, "db_path": db_path,
        "files": FileRepository(db),
        "sources": SourceRepository(db),
    }


def _setup(repos, path: str = "/file.txt",
            status: str = "active") -> FileEntity:
    # Idempotent: source may already exist from a prior _setup call
    if repos["sources"].get("local") is None:
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
    f = FileEntity(
        source_id="local", source_path=path,
        size=10, mtime=utcnow_naive(),
        status=status,
    )
    repos["files"].insert(f)
    return f


# ---------------------------------------------------------------------------
# _resolve_file (UUID + exact path + prefix-match per v1.7.180)
# ---------------------------------------------------------------------------


class TestResolveFile:
    def test_uuid_match(self, isolated_cli_db):
        f = _setup(isolated_cli_db, "/r1.txt")
        from types import SimpleNamespace
        rt = SimpleNamespace(
            file_repo=isolated_cli_db["files"],
            source_repo=isolated_cli_db["sources"],
        )
        result = _resolve_file(rt, str(f.curator_id))
        assert result is not None
        assert result.curator_id == f.curator_id

    def test_path_match(self, isolated_cli_db):
        f = _setup(isolated_cli_db, "/r2.txt")
        from types import SimpleNamespace
        rt = SimpleNamespace(
            file_repo=isolated_cli_db["files"],
            source_repo=isolated_cli_db["sources"],
        )
        result = _resolve_file(rt, "/r2.txt")
        assert result is not None
        assert result.curator_id == f.curator_id

    def test_no_match_returns_none(self, isolated_cli_db):
        from types import SimpleNamespace
        rt = SimpleNamespace(
            file_repo=isolated_cli_db["files"],
            source_repo=isolated_cli_db["sources"],
        )
        # Pre-create a source so list_all isn't empty
        isolated_cli_db["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="L",
        ))
        assert _resolve_file(rt, "/nonexistent.txt") is None

    def test_prefix_match_unambiguous(self, isolated_cli_db):
        """v1.7.180: when exactly one indexed file starts with the
        given prefix, return it (the path-prefix feature restored
        from the v1.0.0rc1 dead duplicate)."""
        f = _setup(isolated_cli_db, "/very/long/path/uniquely_named_file.pdf")
        from types import SimpleNamespace
        rt = SimpleNamespace(
            file_repo=isolated_cli_db["files"],
            source_repo=isolated_cli_db["sources"],
        )
        # Prefix doesn't match exact path; falls through to LIKE 'prefix%'
        result = _resolve_file(rt, "/very/long/path/uniquely")
        assert result is not None
        assert result.curator_id == f.curator_id

    def test_prefix_match_ambiguous_returns_none(self, isolated_cli_db):
        """v1.7.180: ambiguous prefix (>= 2 matches) returns None so the
        caller can surface 'No file matches' rather than auto-picking.
        limit=2 in the query ensures we short-circuit at 2 matches."""
        _setup(isolated_cli_db, "/dup/path/file_a.txt")
        _setup(isolated_cli_db, "/dup/path/file_b.txt")
        from types import SimpleNamespace
        rt = SimpleNamespace(
            file_repo=isolated_cli_db["files"],
            source_repo=isolated_cli_db["sources"],
        )
        # Both files share this prefix.
        assert _resolve_file(rt, "/dup/path/") is None


# ---------------------------------------------------------------------------
# status set
# ---------------------------------------------------------------------------


class TestStatusSet:
    def test_file_not_found_exits_1(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "set", "/nonexistent.txt", "vital"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "File not found" in combined

    def test_invalid_status_value_exits_1(
        self, runner, isolated_cli_db,
    ):
        f = _setup(isolated_cli_db, "/inv.txt")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "set", str(f.curator_id), "bogus_status"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Invalid status" in combined or "bogus" in combined

    def test_happy_path_human(self, runner, isolated_cli_db):
        f = _setup(isolated_cli_db, "/h.txt", status="active")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "set", str(f.curator_id), "vital"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Updated" in combined
        # Verify in DB
        updated = isolated_cli_db["files"].get(f.curator_id)
        assert updated.status == "vital"

    def test_with_expires_in_days(self, runner, isolated_cli_db):
        f = _setup(isolated_cli_db, "/exp.txt")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "set", str(f.curator_id), "provisional",
             "--expires-in-days", "30"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "expires_at" in combined
        updated = isolated_cli_db["files"].get(f.curator_id)
        assert updated.expires_at is not None

    def test_clear_expires_flag(self, runner, isolated_cli_db):
        f = _setup(isolated_cli_db, "/clear.txt")
        # First set expires
        isolated_cli_db["files"].update_status(
            f.curator_id, "provisional",
            expires_at=datetime(2026, 12, 31),
        )
        # Now clear it via CLI
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "set", str(f.curator_id), "active",
             "--clear-expires"],
        )
        assert result.exit_code == 0
        updated = isolated_cli_db["files"].get(f.curator_id)
        assert updated.expires_at is None

    def test_json_output(self, runner, isolated_cli_db):
        f = _setup(isolated_cli_db, "/json.txt")
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "status", "set", str(f.curator_id), "junk",
             "--expires-in-days", "7"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"to": "junk"' in combined
        assert '"expires_at"' in combined


# ---------------------------------------------------------------------------
# status get
# ---------------------------------------------------------------------------


class TestStatusGet:
    def test_file_not_found_exits_1(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "get", "/ghost.txt"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "File not found" in combined

    def test_human_output_all_buckets(self, runner, isolated_cli_db):
        """Verify all 4 status color branches: vital/active/provisional/junk."""
        for status in ("vital", "active", "provisional", "junk"):
            f = _setup(isolated_cli_db, f"/{status}.txt", status=status)
            result = runner.invoke(
                app,
                ["--db", str(isolated_cli_db["db_path"]),
                 "status", "get", str(f.curator_id)],
            )
            assert result.exit_code == 0
            combined = result.stdout + (result.stderr or "")
            assert status in combined

    def test_with_supersedes_and_expires(self, runner, isolated_cli_db):
        """Lines 3844-3847: supersedes_id + expires_at rendered."""
        f = _setup(isolated_cli_db, "/sup.txt")
        # Add another file to supersede
        other = _setup(isolated_cli_db, "/older.txt")
        isolated_cli_db["files"].update_status(
            f.curator_id, "vital",
            supersedes_id=other.curator_id,
            expires_at=datetime(2026, 12, 31),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "get", str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "supersedes" in combined
        assert "expires_at" in combined

    def test_json_output(self, runner, isolated_cli_db):
        f = _setup(isolated_cli_db, "/json_get.txt")
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "status", "get", str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"status"' in combined
        assert '"source_path"' in combined


# ---------------------------------------------------------------------------
# status report
# ---------------------------------------------------------------------------


class TestStatusReport:
    def test_human_empty(self, runner, isolated_cli_db):
        """Total == 0 -> early return after title."""
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "report"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Status report" in combined
        assert "Total files: 0" in combined

    def test_human_populated_all_buckets(self, runner, isolated_cli_db):
        # Create files in each status
        for status in ("vital", "active", "provisional", "junk"):
            for i in range(2):
                f = _setup(isolated_cli_db, f"/{status}_{i}.txt", status="active")
                isolated_cli_db["files"].update_status(
                    f.curator_id, status,
                )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "report"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Total files: 8" in combined
        # All 4 buckets shown
        for bucket in ("vital", "active", "provisional", "junk"):
            assert bucket in combined

    def test_human_with_source_filter(self, runner, isolated_cli_db):
        """--source filters to a specific source."""
        repos = isolated_cli_db
        # Add a second source
        repos["sources"].insert(SourceConfig(
            source_id="other", source_type="local", display_name="Other",
        ))
        # Files in different sources
        f1 = FileEntity(source_id="local", source_path="/l.txt",
                        size=1, mtime=utcnow_naive())
        f2 = FileEntity(source_id="other", source_path="/o.txt",
                        size=1, mtime=utcnow_naive())
        # First setup creates "local" source
        _setup(repos, "/setup.txt")
        repos["files"].insert(f1)
        repos["files"].insert(f2)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "status", "report", "--source", "other"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "other" in combined
        assert "Total files: 1" in combined

    def test_json_output(self, runner, isolated_cli_db):
        _setup(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "status", "report"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"total"' in combined
        assert '"counts"' in combined
