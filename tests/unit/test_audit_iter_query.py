"""Tests for v1.7.39 streaming audit-export via AuditRepository.iter_query().

Strategy:
  * Unit tests of iter_query against a real :class:`CuratorDB` (no
    subprocess) verify:
      - Correctness equivalence with :meth:`query` across various
        filter combinations.
      - Generator semantics (yields lazily, can be iterated once,
        works with islice / itertools).
      - The shared SQL builder produces identical results for both.
  * Integration tests verify the audit-export CLI still produces
    correct output now that it streams via iter_query, with the
    rows_exported counter staying accurate.

Memory-bound verification (not memory profiling): we exercise the
streaming path with enough rows that fetchall() would be wasteful
(10k+ entries), then confirm output is correct row-by-row.
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models.audit import AuditEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_runtime(tmp_path):
    """A CuratorRuntime with the audit table seeded with N=50 entries."""
    db_path = tmp_path / "v1739.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    # Seed 50 audit entries with varying actors / actions
    base = datetime(2026, 1, 1, 12, 0, 0)
    actors = ["alice", "bob", "carol"]
    actions = ["scan", "migrate", "trash", "restore"]
    for i in range(50):
        rt.audit_repo.log(
            actor=actors[i % len(actors)],
            action=actions[i % len(actions)],
            entity_type="file" if i % 2 == 0 else "source",
            entity_id=f"entity_{i:03d}",
            details={"i": i, "tag": f"tag_{i % 7}"},
            when=base + timedelta(minutes=i),
        )
    return rt


# ---------------------------------------------------------------------------
# Generator semantics
# ---------------------------------------------------------------------------


class TestIterQueryGeneratorSemantics:
    """Verify iter_query() is a real generator, not a list-in-disguise."""

    def test_iter_query_returns_generator(self, populated_runtime):
        """The return type quacks like a generator (has __next__)."""
        result = populated_runtime.audit_repo.iter_query(limit=10)
        assert hasattr(result, "__next__"), (
            f"iter_query should return a generator; got {type(result).__name__}"
        )
        assert hasattr(result, "__iter__")

    def test_iter_query_yields_audit_entries(self, populated_runtime):
        """Each yielded value is an AuditEntry."""
        gen = populated_runtime.audit_repo.iter_query(limit=5)
        first = next(gen)
        assert isinstance(first, AuditEntry)
        assert first.audit_id is not None
        assert first.actor in ("alice", "bob", "carol")

    def test_iter_query_can_partial_consume(self, populated_runtime):
        """You can stop iterating early without consuming all rows."""
        gen = populated_runtime.audit_repo.iter_query(limit=50)
        # Take just 3 of the 50
        from itertools import islice
        first_three = list(islice(gen, 3))
        assert len(first_three) == 3

    def test_iter_query_exhausts_once(self, populated_runtime):
        """A generator is single-use; once exhausted, no more rows."""
        gen = populated_runtime.audit_repo.iter_query(limit=2)
        list(gen)  # consume all
        assert list(gen) == [], "Generator should be exhausted after full iteration"


# ---------------------------------------------------------------------------
# Correctness equivalence with query()
# ---------------------------------------------------------------------------


class TestIterQueryMatchesQuery:
    """Verify iter_query and query return the same rows under identical filters."""

    def test_no_filter_equivalence(self, populated_runtime):
        """Full table: same rows, same order."""
        list_from_query = populated_runtime.audit_repo.query(limit=100)
        list_from_iter = list(populated_runtime.audit_repo.iter_query(limit=100))
        assert len(list_from_query) == len(list_from_iter) == 50
        for a, b in zip(list_from_query, list_from_iter):
            assert a.audit_id == b.audit_id
            assert a.actor == b.actor
            assert a.action == b.action

    def test_actor_filter_equivalence(self, populated_runtime):
        """--actor filter: same rows from both."""
        from_query = populated_runtime.audit_repo.query(actor="alice", limit=100)
        from_iter = list(
            populated_runtime.audit_repo.iter_query(actor="alice", limit=100)
        )
        assert len(from_query) == len(from_iter) > 0
        assert all(e.actor == "alice" for e in from_iter)

    def test_action_filter_equivalence(self, populated_runtime):
        """--action filter: same rows from both."""
        from_query = populated_runtime.audit_repo.query(action="migrate", limit=100)
        from_iter = list(
            populated_runtime.audit_repo.iter_query(action="migrate", limit=100)
        )
        assert len(from_query) == len(from_iter) > 0
        assert all(e.action == "migrate" for e in from_iter)

    def test_time_filter_equivalence(self, populated_runtime):
        """since/until filters: same rows from both."""
        since = datetime(2026, 1, 1, 12, 10, 0)
        until = datetime(2026, 1, 1, 12, 30, 0)
        from_query = populated_runtime.audit_repo.query(
            since=since, until=until, limit=100,
        )
        from_iter = list(populated_runtime.audit_repo.iter_query(
            since=since, until=until, limit=100,
        ))
        assert len(from_query) == len(from_iter)
        assert all(since <= e.occurred_at < until for e in from_iter)

    def test_limit_applied(self, populated_runtime):
        """LIMIT is honored by the streaming version."""
        result = list(populated_runtime.audit_repo.iter_query(limit=5))
        assert len(result) == 5

    def test_combined_filters_equivalence(self, populated_runtime):
        """Multiple filters at once: same rows from both."""
        from_query = populated_runtime.audit_repo.query(
            actor="bob", entity_type="file", limit=100,
        )
        from_iter = list(populated_runtime.audit_repo.iter_query(
            actor="bob", entity_type="file", limit=100,
        ))
        assert len(from_query) == len(from_iter)
        for e in from_iter:
            assert e.actor == "bob"
            assert e.entity_type == "file"


# ---------------------------------------------------------------------------
# Shared SQL builder
# ---------------------------------------------------------------------------


def test_build_query_sql_and_params_returns_tuple(populated_runtime):
    """The shared helper returns (sql_string, params_tuple)."""
    sql, params = populated_runtime.audit_repo._build_query_sql_and_params(
        actor="alice", limit=10,
    )
    assert isinstance(sql, str)
    assert isinstance(params, tuple)
    assert "WHERE" in sql
    assert "ORDER BY occurred_at DESC" in sql
    assert "LIMIT ?" in sql
    # The final param should be the limit
    assert params[-1] == 10


def test_build_query_sql_no_filters_uses_where_1(populated_runtime):
    """With no filters, the helper emits WHERE 1 to keep the SQL valid."""
    sql, params = populated_runtime.audit_repo._build_query_sql_and_params(limit=5)
    assert "WHERE 1" in sql
    assert params == (5,)


# ---------------------------------------------------------------------------
# Bulk-streaming verification
# ---------------------------------------------------------------------------


def test_iter_query_handles_many_rows(tmp_path):
    """Streaming 10k rows works without errors.

    Not a true memory-profile test (that would need tracemalloc), just
    verification that the path doesn't have any subtle bug at scale.
    """
    db_path = tmp_path / "v1739_bulk.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )

    # Seed 10000 entries -- the SQLite COMMIT cost is the bottleneck so
    # we batch with a single connection context if available; otherwise
    # the test takes a few seconds but doesn't fail.
    base = datetime(2026, 1, 1)
    for i in range(10000):
        rt.audit_repo.log(
            actor=f"actor_{i % 10}",
            action=f"action_{i % 5}",
            when=base + timedelta(seconds=i),
        )

    # Stream all 10k -- iterate fully and verify count
    count = 0
    for entry in rt.audit_repo.iter_query(limit=20000):
        count += 1
        assert isinstance(entry, AuditEntry)
    assert count == 10000


# ---------------------------------------------------------------------------
# CLI audit-export integration
# ---------------------------------------------------------------------------


def _run_curator(args: list[str], db_path: Path) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, "-m", "curator.cli.main", "--db", str(db_path)] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    return result.returncode, result.stdout, result.stderr


def test_audit_export_streaming_count_accurate(tmp_path):
    """audit-export reports the same count as the underlying iter_query.

    v1.7.39: the CLI now tracks rows_exported via a counter incremented
    during the streaming write loop, rather than via len(entries) on a
    materialized list. Verify the counter stays accurate.
    """
    db_path = tmp_path / "v1739_export.db"
    out = tmp_path / "export.jsonl"
    # Add a few sources to seed audit entries (each "sources add" logs once)
    for i in range(5):
        code, _, _ = _run_curator(
            ["sources", "add", f"src_{i}", "--type", "local"], db_path,
        )
        assert code == 0

    code, stdout, _ = _run_curator(
        ["audit-export", "--to", str(out), "--format", "jsonl"], db_path,
    )
    assert code == 0
    # Output should contain 5 lines (one per source.add audit entry)
    content = out.read_text(encoding="utf-8")
    line_count = content.count("\n")
    assert line_count == 5, f"Expected 5 lines; got {line_count}\n{content[:300]}"
    # Each line is valid JSON
    for line in content.strip().split("\n"):
        rec = json.loads(line)
        assert "audit_id" in rec
        assert "occurred_at" in rec


def test_audit_export_streaming_csv(tmp_path):
    """audit-export --format csv works correctly with streaming."""
    db_path = tmp_path / "v1739_csv.db"
    out = tmp_path / "export.csv"
    code, _, _ = _run_curator(
        ["sources", "add", "csv_test", "--type", "local"], db_path,
    )
    assert code == 0
    code, _, _ = _run_curator(
        ["audit-export", "--to", str(out), "--format", "csv"], db_path,
    )
    assert code == 0
    content = out.read_text(encoding="utf-8")
    # Parse as CSV: header + 1 data row
    rows = list(csv.reader(io.StringIO(content)))
    assert rows[0] == [
        "audit_id", "occurred_at", "actor", "action",
        "entity_type", "entity_id", "details_json",
    ]
    assert len(rows) >= 2, f"Expected header + at least 1 data row; got {rows}"


def test_audit_export_empty_db_zero_rows(tmp_path):
    """audit-export against fresh DB produces zero data rows (header only for CSV)."""
    db_path = tmp_path / "v1739_empty.db"
    out_csv = tmp_path / "empty.csv"
    code, _, _ = _run_curator(
        ["audit-export", "--to", str(out_csv), "--format", "csv"], db_path,
    )
    assert code == 0
    # CSV: only the header row should be present (no data)
    content = out_csv.read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(content)))
    assert len(rows) == 1, f"Expected only header; got {rows}"
    assert rows[0][0] == "audit_id"
