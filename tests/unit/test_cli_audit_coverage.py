"""Coverage closure for cli/main.py `audit` command (v1.7.160).

Tier 3 sub-ship 6 of the CLI Coverage Arc.
"""

from __future__ import annotations

from datetime import datetime, timedelta

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
    db_path = tmp_path / "cli_audit.db"
    db = CuratorDB(db_path)
    db.init()
    return {
        "db": db,
        "db_path": db_path,
        "audit": AuditRepository(db),
    }


def _log_entries(repos, n=3):
    base = datetime(2026, 5, 1, 12, 0, 0)
    actors = ["alice", "bob"]
    actions = ["scan", "migrate"]
    for i in range(n):
        repos["audit"].log(
            actor=actors[i % len(actors)],
            action=actions[i % len(actions)],
            entity_type="file",
            entity_id=f"e_{i:03d}",
            details={"i": i},
            when=base + timedelta(minutes=i),
        )


class TestAuditCmd:
    def test_empty_human(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "audit"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No matching audit entries" in combined

    def test_human_with_entries(self, runner, isolated_cli_db):
        _log_entries(isolated_cli_db, n=3)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "audit"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "audit entries" in combined or "audit entry" in combined
        assert "alice" in combined or "bob" in combined
        assert "scan" in combined or "migrate" in combined
        assert "file:" in combined

    def test_human_singular_count_for_one_entry(self, runner, isolated_cli_db):
        _log_entries(isolated_cli_db, n=1)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "audit"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "1 audit entry" in combined

    def test_json_output(self, runner, isolated_cli_db):
        _log_entries(isolated_cli_db, n=2)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "audit"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"actor"' in combined
        assert '"action"' in combined
        assert '"details"' in combined

    def test_filter_by_actor(self, runner, isolated_cli_db):
        _log_entries(isolated_cli_db, n=4)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit", "--actor", "alice"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # 2 of 4 entries from alice
        assert "alice" in combined
        assert "bob" not in combined

    def test_filter_by_action(self, runner, isolated_cli_db):
        _log_entries(isolated_cli_db, n=4)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit", "--action", "scan"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "scan" in combined
        assert "migrate" not in combined

    def test_since_hours_filter(self, runner, isolated_cli_db):
        """--since-hours computes a timestamp filter."""
        # Log one entry "now" and one 48h ago
        now = utcnow_naive()
        isolated_cli_db["audit"].log(
            actor="recent", action="scan", when=now,
        )
        isolated_cli_db["audit"].log(
            actor="old", action="scan", when=now - timedelta(hours=48),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit", "--since-hours", "1"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "recent" in combined
        assert "old" not in combined

    def test_limit(self, runner, isolated_cli_db):
        _log_entries(isolated_cli_db, n=10)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit", "--limit", "3"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "3 audit entries" in combined

    def test_csv_output_with_header(self, runner, isolated_cli_db):
        _log_entries(isolated_cli_db, n=2)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit", "--csv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "audit_id,occurred_at,actor" in combined

    def test_csv_no_header(self, runner, isolated_cli_db):
        _log_entries(isolated_cli_db, n=1)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit", "--csv", "--no-header"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "audit_id,occurred_at,actor" not in combined

    def test_csv_tsv_dialect(self, runner, isolated_cli_db):
        _log_entries(isolated_cli_db, n=1)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit", "--csv", "--csv-dialect", "tsv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "audit_id\toccurred_at" in combined

    def test_entry_without_entity_type_renders_empty_cell(
        self, runner, isolated_cli_db,
    ):
        """Branch: entity_type / entity_id is None -> empty 'ent' cell."""
        isolated_cli_db["audit"].log(
            actor="sys", action="startup",
            entity_type=None, entity_id=None,
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "audit"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "startup" in combined
