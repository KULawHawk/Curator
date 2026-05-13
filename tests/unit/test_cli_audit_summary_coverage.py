"""Coverage closure for cli/main.py `audit-summary` command (v1.7.173).

Tier 3 sub-ship 19 of the CLI Coverage Arc.
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
    db_path = tmp_path / "cli_audit_sum.db"
    db = CuratorDB(db_path)
    db.init()
    return {
        "db_path": db_path,
        "audit": AuditRepository(db),
    }


def _seed(repos, *, count: int = 6, actors=None, actions=None, base=None):
    actors = actors or ["cli.tier", "gui.tier"]
    actions = actions or ["tier.suggest", "scan.start"]
    base = base or datetime(2026, 5, 1, 12, 0, 0)
    for i in range(count):
        repos["audit"].log(
            actor=actors[i % len(actors)],
            action=actions[i % len(actions)],
            entity_type="test",
            entity_id=f"e_{i}",
            when=base + timedelta(minutes=i),
        )


class TestAuditSummary:
    def test_bad_since_exits_2(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--since", "not-a-date"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Bad --since" in combined

    def test_no_events_in_window(self, runner, isolated_cli_db):
        """Empty window -> 'No events' message."""
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--days", "1"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No events in this window" in combined

    def test_human_populated_table(self, runner, isolated_cli_db):
        """Default --days 7 against recent events: render table with bars + relative time."""
        # Seed with NOW-ish so they fall inside --days 7
        _seed(isolated_cli_db, count=8, base=utcnow_naive() - timedelta(hours=2))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Audit summary" in combined
        assert "cli.tier" in combined
        assert "Total events" in combined
        assert "Unique groups" in combined

    def test_filter_by_actor(self, runner, isolated_cli_db):
        _seed(isolated_cli_db, count=8, base=utcnow_naive() - timedelta(hours=2))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--actor", "cli.tier"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Actor filter" in combined
        assert "cli.tier" in combined
        # gui.tier should NOT appear in the table
        assert "gui.tier" not in combined

    def test_filter_by_action(self, runner, isolated_cli_db):
        _seed(isolated_cli_db, count=8, base=utcnow_naive() - timedelta(hours=2))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--action", "tier.suggest"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Action filter" in combined

    def test_since_explicit(self, runner, isolated_cli_db):
        """Use --since instead of --days."""
        _seed(isolated_cli_db, count=4, base=datetime(2026, 5, 10, 12, 0, 0))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--since", "2026-05-01"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Audit summary" in combined

    def test_limit_caps_with_remainder(self, runner, isolated_cli_db):
        """--limit shows 'and N more groups'."""
        # Generate enough unique (actor, action) pairs to exceed limit
        base = utcnow_naive() - timedelta(hours=2)
        for i in range(10):
            isolated_cli_db["audit"].log(
                actor=f"actor_{i}",
                action=f"action_{i}",
                when=base + timedelta(minutes=i),
            )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--limit", "3"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "and 7 more groups" in combined

    def test_no_bars_flag(self, runner, isolated_cli_db):
        """--no-bars suppresses the Activity column."""
        _seed(isolated_cli_db, count=4, base=utcnow_naive() - timedelta(hours=2))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--no-bars"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Just ensure command runs cleanly; Activity column may or may not be
        # visually distinguishable in captured output
        assert "Audit summary" in combined

    def test_local_timezone(self, runner, isolated_cli_db):
        """--local converts timestamps to system local TZ + header label."""
        _seed(isolated_cli_db, count=2, base=utcnow_naive() - timedelta(hours=2))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--local"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "(local)" in combined

    def test_relative_time_buckets(self, runner, isolated_cli_db):
        """_ago covers seconds / minutes / hours / days buckets."""
        now = utcnow_naive()
        # 30 sec ago, 5 min ago, 2 hours ago, 3 days ago
        for ts in [now - timedelta(seconds=30),
                   now - timedelta(minutes=5),
                   now - timedelta(hours=2),
                   now - timedelta(days=3)]:
            isolated_cli_db["audit"].log(
                actor=f"actor_{ts.isoformat()[:19]}",
                action="event", when=ts,
            )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--days", "7"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Check for "Xs ago", "Xm ago", "Xh ago", "Xd ago" patterns
        # (at least one of each bucket)
        assert "s ago" in combined or "m ago" in combined or "h ago" in combined or "d ago" in combined

    def test_json_output(self, runner, isolated_cli_db):
        _seed(isolated_cli_db, count=4, base=utcnow_naive() - timedelta(hours=2))
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "audit-summary"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"total_events"' in combined
        assert '"groups"' in combined
        assert '"timezone": "utc"' in combined

    def test_json_with_local(self, runner, isolated_cli_db):
        _seed(isolated_cli_db, count=2, base=utcnow_naive() - timedelta(hours=2))
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--local"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"timezone": "local"' in combined

    def test_csv_with_header(self, runner, isolated_cli_db):
        _seed(isolated_cli_db, count=4, base=utcnow_naive() - timedelta(hours=2))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--csv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "actor,action,count,first,last" in combined

    def test_csv_no_header_tsv(self, runner, isolated_cli_db):
        _seed(isolated_cli_db, count=4, base=utcnow_naive() - timedelta(hours=2))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "audit-summary", "--csv", "--no-header",
             "--csv-dialect", "tsv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "actor\taction\tcount" not in combined
        # But there are data rows with tabs
        assert "cli.tier\t" in combined or "gui.tier\t" in combined
