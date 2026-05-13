"""Coverage closure for cli/main.py `audit-export` command (v1.7.174).

Tier 3 sub-ship 20 of the CLI Coverage Arc — final command ship before
the cleanup/close ship.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator._compat.datetime import utcnow_naive
from curator.cli.main import app


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    from curator.storage.repositories import AuditRepository
    db_path = tmp_path / "cli_audit_export.db"
    db = CuratorDB(db_path)
    db.init()
    return {
        "db_path": db_path, "tmp_path": tmp_path,
        "audit": AuditRepository(db),
    }


def _seed(repos, count=3, *, days_ago_start=10):
    base = utcnow_naive() - timedelta(days=days_ago_start)
    for i in range(count):
        repos["audit"].log(
            actor=f"actor_{i}", action=f"action_{i}",
            entity_type="file", entity_id=f"ent_{i}",
            details={"i": i, "x": "y"},
            when=base + timedelta(minutes=i),
        )


class TestAuditExport:
    def test_invalid_format_exits_1(self, runner, isolated_cli_db, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(tmp_path / "out"),
             "--format", "bogus"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "--format must be" in combined

    def test_older_than_and_before_mutually_exclusive(
        self, runner, isolated_cli_db, tmp_path,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(tmp_path / "out"),
             "--older-than", "30", "--before", "2026-01-01"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "mutually exclusive" in combined

    def test_negative_older_than_exits_1(
        self, runner, isolated_cli_db, tmp_path,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(tmp_path / "out"),
             "--older-than", "-5"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "must be >= 0" in combined

    def test_bad_before_iso_exits_1(
        self, runner, isolated_cli_db, tmp_path,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(tmp_path / "out"),
             "--before", "garbage"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "not valid ISO datetime" in combined

    def test_bad_since_iso_exits_1(
        self, runner, isolated_cli_db, tmp_path,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(tmp_path / "out"),
             "--since", "not-a-date"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "not valid ISO datetime" in combined

    def test_since_after_before_exits_1(
        self, runner, isolated_cli_db, tmp_path,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(tmp_path / "out"),
             "--since", "2026-06-01",
             "--before", "2026-01-01"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "must be earlier than" in combined

    def test_refuses_db_extension(
        self, runner, isolated_cli_db, tmp_path,
    ):
        """Defensive: refuse to write to a .db file."""
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(tmp_path / "danger.db")],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "refusing to write" in combined

    def test_jsonl_export_to_file(
        self, runner, isolated_cli_db, tmp_path,
    ):
        _seed(isolated_cli_db, count=3)
        out = tmp_path / "out.jsonl"
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(out)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Exported" in combined
        assert out.exists()
        # Parse jsonl
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        # 3 + 1 meta-audit = at least 3 (the meta-audit happens AFTER, so
        # it's NOT in the export — only events that existed before the
        # query are exported)
        assert len(lines) == 3
        rec = json.loads(lines[0])
        assert "actor" in rec
        assert "details" in rec

    def test_csv_export_to_file(
        self, runner, isolated_cli_db, tmp_path,
    ):
        _seed(isolated_cli_db, count=2)
        out = tmp_path / "out.csv"
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(out), "--format", "csv"],
        )
        assert result.exit_code == 0
        content = out.read_text(encoding="utf-8")
        assert "audit_id,occurred_at,actor" in content

    def test_tsv_export_to_file(
        self, runner, isolated_cli_db, tmp_path,
    ):
        _seed(isolated_cli_db, count=2)
        out = tmp_path / "out.tsv"
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(out), "--format", "tsv"],
        )
        assert result.exit_code == 0
        content = out.read_text(encoding="utf-8")
        assert "audit_id\toccurred_at" in content

    def test_export_to_stdout(self, runner, isolated_cli_db):
        _seed(isolated_cli_db, count=2)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", "-"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # JSONL lines in stdout, no summary message
        assert "actor_0" in combined or "actor_1" in combined
        # No "Exported N entries" line (stdout mode suppresses summary)
        assert "Exported" not in combined

    def test_older_than_filter(
        self, runner, isolated_cli_db, tmp_path,
    ):
        """--older-than 5 days filters out recent events."""
        # Old events (10 days ago)
        _seed(isolated_cli_db, count=2, days_ago_start=10)
        # Recent events (1 day ago)
        recent_base = utcnow_naive() - timedelta(days=1)
        for i in range(2):
            isolated_cli_db["audit"].log(
                actor=f"recent_{i}", action="event",
                when=recent_base + timedelta(minutes=i),
            )
        out = tmp_path / "old.jsonl"
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(out), "--older-than", "5"],
        )
        assert result.exit_code == 0
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        # Only old events exported
        assert len(lines) == 2
        for ln in lines:
            rec = json.loads(ln)
            assert "recent" not in rec["actor"]

    def test_filters_by_actor_action_entity_type(
        self, runner, isolated_cli_db, tmp_path,
    ):
        # Seed with mixed actors/actions
        base = utcnow_naive() - timedelta(hours=1)
        isolated_cli_db["audit"].log(
            actor="match_actor", action="match_action",
            entity_type="match_type", entity_id="x",
            when=base,
        )
        isolated_cli_db["audit"].log(
            actor="other", action="other_action",
            entity_type="other_type", entity_id="y",
            when=base,
        )
        out = tmp_path / "filtered.jsonl"
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(out),
             "--actor", "match_actor",
             "--action", "match_action",
             "--entity-type", "match_type"],
        )
        assert result.exit_code == 0
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        # Only matching entry
        assert len(lines) == 1

    def test_singular_count_when_one_row(
        self, runner, isolated_cli_db, tmp_path,
    ):
        _seed(isolated_cli_db, count=1)
        out = tmp_path / "one.jsonl"
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(out)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "1 audit entry" in combined

    def test_json_output_summary(
        self, runner, isolated_cli_db, tmp_path,
    ):
        _seed(isolated_cli_db, count=2)
        out = tmp_path / "j.jsonl"
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(out)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"rows_exported": 2' in combined
        assert '"format": "jsonl"' in combined

    def test_meta_audit_event_recorded(
        self, runner, isolated_cli_db, tmp_path,
    ):
        """The export itself creates an 'audit.exported' event."""
        _seed(isolated_cli_db, count=2)
        out = tmp_path / "meta.jsonl"
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-export", "--to", str(out)],
        )
        assert result.exit_code == 0
        # Re-query the audit log to find the meta event
        all_entries = isolated_cli_db["audit"].query(actor="cli.audit", limit=10)
        assert any(e.action == "audit.exported" for e in all_entries)
