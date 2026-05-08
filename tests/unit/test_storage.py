"""Storage / repository tests.

These exercise the 8 repositories against a real (temp-path) SQLite
DB. They cover insert/get/update/delete and the named query methods
that the services depend on.

Promoted from the Step 2 14-assertion smoke test.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest

from curator.models import (
    AuditEntry,
    BundleEntity,
    BundleMembership,
    FileEntity,
    LineageEdge,
    LineageKind,
    ScanJob,
    SourceConfig,
    TrashRecord,
)
from curator.storage.queries import FileQuery
from curator.storage.repositories.hash_cache_repo import CachedHash


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

class TestSourceRepository:
    def test_insert_then_list_all(self, repos):
        repos.sources.insert(SourceConfig(source_id="local", source_type="local"))
        assert len(repos.sources.list_all()) == 1

    def test_list_enabled_filters_disabled(self, repos):
        repos.sources.insert(SourceConfig(source_id="a", source_type="local"))
        repos.sources.insert(SourceConfig(source_id="b", source_type="local", enabled=False))
        enabled = repos.sources.list_enabled()
        assert len(enabled) == 1
        assert enabled[0].source_id == "a"

    def test_set_enabled_toggles(self, repos):
        repos.sources.insert(SourceConfig(source_id="a", source_type="local"))
        repos.sources.set_enabled("a", False)
        assert repos.sources.get("a").enabled is False
        repos.sources.set_enabled("a", True)
        assert repos.sources.get("a").enabled is True


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

class TestFileRepository:
    def test_insert_then_get(self, repos, local_source):
        f = FileEntity(
            source_id="local", source_path="/tmp/a",
            size=10, mtime=datetime.utcnow(),
        )
        repos.files.insert(f)
        loaded = repos.files.get(f.curator_id)
        assert loaded is not None
        assert loaded.source_path == "/tmp/a"

    def test_flex_attrs_round_trip_through_db(self, repos, local_source):
        f = FileEntity(
            source_id="local", source_path="/tmp/a",
            size=10, mtime=datetime.utcnow(),
        )
        f.set_flex("topic", "stats")
        f.set_flex("priority", 5)
        repos.files.insert(f)
        loaded = repos.files.get(f.curator_id)
        assert loaded.flex.get("topic") == "stats"
        assert loaded.flex.get("priority") == 5

    def test_find_by_path(self, repos, local_source):
        f = FileEntity(
            source_id="local", source_path="/tmp/x",
            size=1, mtime=datetime.utcnow(),
        )
        repos.files.insert(f)
        found = repos.files.find_by_path("local", "/tmp/x")
        assert found is not None
        assert found.curator_id == f.curator_id

    def test_find_by_hash_returns_only_active_by_default(self, repos, local_source):
        h = "deadbeef" * 4
        f1 = FileEntity(source_id="local", source_path="/a", size=1,
                        mtime=datetime.utcnow(), xxhash3_128=h)
        f2 = FileEntity(source_id="local", source_path="/b", size=1,
                        mtime=datetime.utcnow(), xxhash3_128=h)
        repos.files.insert(f1)
        repos.files.insert(f2)
        repos.files.mark_deleted(f2.curator_id)

        active = repos.files.find_by_hash(h)
        assert len(active) == 1

        all_files = repos.files.find_by_hash(h, include_deleted=True)
        assert len(all_files) == 2

    def test_find_candidates_by_size_excludes_self(self, repos, local_source):
        f = FileEntity(source_id="local", source_path="/a", size=100, mtime=datetime.utcnow())
        repos.files.insert(f)
        cands = repos.files.find_candidates_by_size(100, exclude_curator_id=f.curator_id)
        assert cands == []

    def test_count_default_excludes_deleted(self, repos, local_source):
        f1 = FileEntity(source_id="local", source_path="/a", size=1, mtime=datetime.utcnow())
        f2 = FileEntity(source_id="local", source_path="/b", size=1, mtime=datetime.utcnow())
        repos.files.insert(f1)
        repos.files.insert(f2)
        repos.files.mark_deleted(f2.curator_id)
        assert repos.files.count() == 1
        assert repos.files.count(include_deleted=True) == 2


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

class TestLineageRepository:
    def test_insert_returns_true_on_first_insert(self, repos, local_source):
        a = FileEntity(source_id="local", source_path="/a", size=1, mtime=datetime.utcnow())
        b = FileEntity(source_id="local", source_path="/b", size=1, mtime=datetime.utcnow())
        repos.files.insert(a)
        repos.files.insert(b)
        edge = LineageEdge(
            from_curator_id=a.curator_id, to_curator_id=b.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=1.0,
            detected_by="test",
        )
        assert repos.lineage.insert(edge) is True

    def test_insert_with_on_conflict_ignore_returns_false_on_dup(self, repos, local_source):
        a = FileEntity(source_id="local", source_path="/a", size=1, mtime=datetime.utcnow())
        b = FileEntity(source_id="local", source_path="/b", size=1, mtime=datetime.utcnow())
        repos.files.insert(a)
        repos.files.insert(b)
        edge = LineageEdge(
            from_curator_id=a.curator_id, to_curator_id=b.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=1.0,
            detected_by="test",
        )
        assert repos.lineage.insert(edge) is True
        # Second insert with the same edge_id won't actually run because
        # the unique constraint is on (from, to, kind, detector). Build a
        # second edge with a different edge_id but same key fields:
        edge2 = LineageEdge(
            from_curator_id=a.curator_id, to_curator_id=b.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=0.99,
            detected_by="test",
        )
        assert repos.lineage.insert(edge2, on_conflict="ignore") is False

    def test_get_edges_for_returns_either_direction(self, repos, local_source):
        a = FileEntity(source_id="local", source_path="/a", size=1, mtime=datetime.utcnow())
        b = FileEntity(source_id="local", source_path="/b", size=1, mtime=datetime.utcnow())
        repos.files.insert(a)
        repos.files.insert(b)
        edge = LineageEdge(
            from_curator_id=a.curator_id, to_curator_id=b.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=1.0,
            detected_by="test",
        )
        repos.lineage.insert(edge)
        # Either endpoint should retrieve the edge.
        assert len(repos.lineage.get_edges_for(a.curator_id)) == 1
        assert len(repos.lineage.get_edges_for(b.curator_id)) == 1


# ---------------------------------------------------------------------------
# Hash cache
# ---------------------------------------------------------------------------

class TestHashCacheRepository:
    def test_get_if_fresh_returns_entry_when_mtime_size_match(self, repos):
        now = datetime(2026, 1, 1, 12, 0, 0)
        repos.cache.upsert(CachedHash(
            source_id="local", source_path="/a",
            mtime=now, size=100,
            xxhash3_128="abc123", md5=None, fuzzy_hash=None,
            computed_at=datetime.utcnow(),
        ))
        cached = repos.cache.get_if_fresh("local", "/a", mtime=now, size=100)
        assert cached is not None
        assert cached.xxhash3_128 == "abc123"

    def test_get_if_fresh_returns_none_on_mtime_mismatch(self, repos):
        now = datetime(2026, 1, 1, 12, 0, 0)
        later = datetime(2026, 1, 2, 12, 0, 0)
        repos.cache.upsert(CachedHash(
            source_id="local", source_path="/a",
            mtime=now, size=100,
            xxhash3_128="abc123", md5=None, fuzzy_hash=None,
            computed_at=datetime.utcnow(),
        ))
        assert repos.cache.get_if_fresh("local", "/a", mtime=later, size=100) is None

    def test_get_if_fresh_returns_none_on_size_mismatch(self, repos):
        now = datetime(2026, 1, 1, 12, 0, 0)
        repos.cache.upsert(CachedHash(
            source_id="local", source_path="/a",
            mtime=now, size=100,
            xxhash3_128="abc123", md5=None, fuzzy_hash=None,
            computed_at=datetime.utcnow(),
        ))
        assert repos.cache.get_if_fresh("local", "/a", mtime=now, size=101) is None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class TestAuditRepository:
    def test_log_then_query_round_trips_details_json(self, repos):
        details = {"reason": "test", "count": 5}
        repos.audit.log(actor="user", action="test", details=details)
        results = repos.audit.query(action="test")
        assert len(results) == 1
        assert results[0].details == details

    def test_query_by_actor(self, repos):
        repos.audit.log(actor="alice", action="x")
        repos.audit.log(actor="bob", action="y")
        assert len(repos.audit.query(actor="alice")) == 1
        assert len(repos.audit.query(actor="bob")) == 1


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------

class TestBundleRepository:
    def test_add_membership_then_get(self, repos, local_source):
        f = FileEntity(source_id="local", source_path="/a", size=1, mtime=datetime.utcnow())
        repos.files.insert(f)
        b = BundleEntity(bundle_type="manual", name="test")
        repos.bundles.insert(b)
        repos.bundles.add_membership(BundleMembership(
            bundle_id=b.bundle_id, curator_id=f.curator_id, role="primary",
        ))
        members = repos.bundles.get_memberships(b.bundle_id)
        assert len(members) == 1
        assert members[0].role == "primary"

    def test_member_count_matches_get_memberships(self, repos, local_source):
        b = BundleEntity(bundle_type="manual", name="test")
        repos.bundles.insert(b)
        # Insert two files + memberships
        for path in ["/a", "/b"]:
            f = FileEntity(source_id="local", source_path=path, size=1, mtime=datetime.utcnow())
            repos.files.insert(f)
            repos.bundles.add_membership(BundleMembership(
                bundle_id=b.bundle_id, curator_id=f.curator_id,
            ))
        assert repos.bundles.member_count(b.bundle_id) == 2


# ---------------------------------------------------------------------------
# Trash
# ---------------------------------------------------------------------------

class TestTrashRepository:
    def test_insert_then_get(self, repos, local_source):
        f = FileEntity(source_id="local", source_path="/a", size=1, mtime=datetime.utcnow())
        repos.files.insert(f)
        record = TrashRecord(
            curator_id=f.curator_id,
            original_source_id="local",
            original_path="/a",
            trashed_by="user",
            reason="test",
            bundle_memberships_snapshot=[{"bundle_id": "x", "role": "y"}],
            file_attrs_snapshot={"k": "v"},
        )
        repos.trash.insert(record)
        loaded = repos.trash.get(f.curator_id)
        assert loaded is not None
        assert loaded.bundle_memberships_snapshot == [{"bundle_id": "x", "role": "y"}]
        assert loaded.file_attrs_snapshot == {"k": "v"}


# ---------------------------------------------------------------------------
# Scan jobs
# ---------------------------------------------------------------------------

class TestScanJobRepository:
    def test_insert_then_update_status(self, repos):
        job = ScanJob(source_id="local", root_path="/", status="running",
                      started_at=datetime.utcnow())
        repos.jobs.insert(job)
        repos.jobs.update_status(job.job_id, "completed")
        loaded = repos.jobs.get(job.job_id)
        assert loaded.status == "completed"

    def test_update_counters(self, repos):
        job = ScanJob(source_id="local", root_path="/", status="running",
                      started_at=datetime.utcnow())
        repos.jobs.insert(job)
        repos.jobs.update_counters(job.job_id, files_seen=5, files_hashed=3)
        loaded = repos.jobs.get(job.job_id)
        assert loaded.files_seen == 5
        assert loaded.files_hashed == 3


# ---------------------------------------------------------------------------
# FileQuery
# ---------------------------------------------------------------------------

class TestFileQuery:
    def test_query_filters_by_extension(self, repos, local_source):
        for ext, name in [(".py", "/a.py"), (".md", "/b.md"), (".py", "/c.py")]:
            repos.files.insert(FileEntity(
                source_id="local", source_path=name,
                size=1, mtime=datetime.utcnow(), extension=ext,
            ))
        py_files = repos.files.query(FileQuery(extensions=[".py"]))
        assert len(py_files) == 2

    def test_query_size_range(self, repos, local_source):
        for size, path in [(10, "/a"), (50, "/b"), (200, "/c")]:
            repos.files.insert(FileEntity(
                source_id="local", source_path=path,
                size=size, mtime=datetime.utcnow(),
            ))
        mid = repos.files.query(FileQuery(min_size=20, max_size=100))
        assert len(mid) == 1


# ---------------------------------------------------------------------------
# Migration jobs (Tracer Phase 2 -- v1.1.0a2)
# ---------------------------------------------------------------------------

from curator.models import MigrationJob, MigrationProgress
from uuid import uuid4 as _uuid4


def _make_progress(job_id, *, src_path="/src/a.mp3", dst_path="/dst/a.mp3",
                   safety_level="safe", status="pending", **kw):
    """Helper: construct a MigrationProgress with sensible defaults."""
    return MigrationProgress(
        job_id=job_id,
        curator_id=_uuid4(),
        src_path=src_path,
        dst_path=dst_path,
        size=kw.pop("size", 100),
        safety_level=safety_level,
        status=status,
        **kw,
    )


class TestMigrationJobRepository:
    def test_insert_job_then_get_round_trips(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/src",
            dst_source_id="local", dst_root="/dst",
            status="queued",
            options={"workers": 4, "verify_hash": True, "keep_source": False},
        )
        repos.migration_jobs.insert_job(job)
        loaded = repos.migration_jobs.get_job(job.job_id)
        assert loaded is not None
        assert loaded.src_source_id == "local"
        assert loaded.src_root == "/src"
        assert loaded.status == "queued"
        assert loaded.options == {"workers": 4, "verify_hash": True, "keep_source": False}
        assert loaded.files_total == 0  # default

    def test_get_job_missing_returns_none(self, repos):
        assert repos.migration_jobs.get_job(_uuid4()) is None

    def test_update_job_status_running_populates_started_at(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d",
            status="queued",
        )
        repos.migration_jobs.insert_job(job)
        repos.migration_jobs.update_job_status(job.job_id, "running")
        loaded = repos.migration_jobs.get_job(job.job_id)
        assert loaded.status == "running"
        assert loaded.started_at is not None
        assert loaded.completed_at is None

    def test_update_job_status_terminal_populates_completed_at(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d",
            status="running",
        )
        repos.migration_jobs.insert_job(job)
        repos.migration_jobs.update_job_status(
            job.job_id, "completed",
        )
        loaded = repos.migration_jobs.get_job(job.job_id)
        assert loaded.status == "completed"
        assert loaded.completed_at is not None

    def test_update_job_status_with_error(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        repos.migration_jobs.update_job_status(
            job.job_id, "failed", error="write hook missing",
        )
        loaded = repos.migration_jobs.get_job(job.job_id)
        assert loaded.status == "failed"
        assert loaded.error == "write hook missing"

    def test_increment_job_counts_accumulates(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        repos.migration_jobs.increment_job_counts(
            job.job_id, copied=3, bytes_copied=900,
        )
        repos.migration_jobs.increment_job_counts(
            job.job_id, copied=2, skipped=1, bytes_copied=600,
        )
        loaded = repos.migration_jobs.get_job(job.job_id)
        assert loaded.files_copied == 5
        assert loaded.files_skipped == 1
        assert loaded.files_failed == 0
        assert loaded.bytes_copied == 1500

    def test_set_files_total(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="queued",
        )
        repos.migration_jobs.insert_job(job)
        repos.migration_jobs.set_files_total(job.job_id, 187)
        loaded = repos.migration_jobs.get_job(job.job_id)
        assert loaded.files_total == 187

    def test_list_jobs_most_recent_first_with_limit(self, repos):
        # Insert 3 jobs with explicit started_at to force ordering
        jobs = []
        for i in range(3):
            j = MigrationJob(
                src_source_id="local", src_root=f"/s{i}",
                dst_source_id="local", dst_root=f"/d{i}",
                status="completed",
                started_at=datetime(2026, 5, 1 + i, 12, 0),
            )
            repos.migration_jobs.insert_job(j)
            jobs.append(j)
        listed = repos.migration_jobs.list_jobs(limit=2)
        assert len(listed) == 2
        # Most recent first: started_at 2026-05-03 then 2026-05-02
        assert listed[0].src_root == "/s2"
        assert listed[1].src_root == "/s1"

    def test_list_jobs_status_filter(self, repos):
        for status in ("queued", "running", "completed", "failed"):
            repos.migration_jobs.insert_job(MigrationJob(
                src_source_id="local", src_root=f"/s_{status}",
                dst_source_id="local", dst_root=f"/d_{status}",
                status=status,
            ))
        running = repos.migration_jobs.list_jobs(status="running")
        assert len(running) == 1
        assert running[0].status == "running"

    def test_seed_progress_rows_bulk_insert(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="queued",
        )
        repos.migration_jobs.insert_job(job)
        rows = [
            _make_progress(job.job_id, src_path=f"/src/{i}", dst_path=f"/dst/{i}")
            for i in range(5)
        ]
        repos.migration_jobs.seed_progress_rows(job.job_id, rows)
        all_progress = repos.migration_jobs.query_progress(job.job_id)
        assert len(all_progress) == 5
        assert all(p.status == "pending" for p in all_progress)

    def test_seed_progress_rows_empty_list_is_noop(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="queued",
        )
        repos.migration_jobs.insert_job(job)
        repos.migration_jobs.seed_progress_rows(job.job_id, [])
        assert repos.migration_jobs.query_progress(job.job_id) == []

    def test_next_pending_progress_claims_one_row(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        rows = [
            _make_progress(job.job_id, src_path=f"/src/{i}.mp3")
            for i in range(3)
        ]
        repos.migration_jobs.seed_progress_rows(job.job_id, rows)

        claimed = repos.migration_jobs.next_pending_progress(job.job_id)
        assert claimed is not None
        assert claimed.status == "in_progress"
        assert claimed.started_at is not None
        # Persisted update reflects the claim
        re_loaded = repos.migration_jobs.get_progress(
            job.job_id, claimed.curator_id,
        )
        assert re_loaded.status == "in_progress"

    def test_next_pending_progress_returns_none_when_empty(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        # No progress rows seeded
        assert repos.migration_jobs.next_pending_progress(job.job_id) is None

    def test_next_pending_progress_orders_by_src_path(self, repos):
        """Workers should claim rows in deterministic alphabetical order
        by src_path -- gives reproducible test runs and makes audit logs
        easier to reason about."""
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        # Seed in non-alphabetical insert order
        rows = [
            _make_progress(job.job_id, src_path="/src/zebra.mp3"),
            _make_progress(job.job_id, src_path="/src/apple.mp3"),
            _make_progress(job.job_id, src_path="/src/mango.mp3"),
        ]
        repos.migration_jobs.seed_progress_rows(job.job_id, rows)
        first = repos.migration_jobs.next_pending_progress(job.job_id)
        assert first.src_path == "/src/apple.mp3"

    def test_update_progress_terminal_populates_completed_at(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        row = _make_progress(job.job_id)
        repos.migration_jobs.seed_progress_rows(job.job_id, [row])
        repos.migration_jobs.update_progress(
            job.job_id, row.curator_id,
            status="completed", outcome="moved",
            verified_xxhash="abc123",
        )
        loaded = repos.migration_jobs.get_progress(job.job_id, row.curator_id)
        assert loaded.status == "completed"
        assert loaded.outcome == "moved"
        assert loaded.verified_xxhash == "abc123"
        assert loaded.completed_at is not None

    def test_update_progress_failed_with_error(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        row = _make_progress(job.job_id)
        repos.migration_jobs.seed_progress_rows(job.job_id, [row])
        repos.migration_jobs.update_progress(
            job.job_id, row.curator_id,
            status="failed", outcome="hash_mismatch",
            error="hash mismatch: src=abc dst=def",
        )
        loaded = repos.migration_jobs.get_progress(job.job_id, row.curator_id)
        assert loaded.status == "failed"
        assert loaded.outcome == "hash_mismatch"
        assert "hash mismatch" in loaded.error

    def test_reset_in_progress_to_pending(self, repos):
        """Resume helper: rows left as 'in_progress' from a dead worker
        return to 'pending' so a fresh worker can pick them up."""
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        rows = [
            _make_progress(job.job_id, src_path=f"/src/{i}.mp3")
            for i in range(3)
        ]
        repos.migration_jobs.seed_progress_rows(job.job_id, rows)

        # Claim two rows (simulate two workers that died)
        c1 = repos.migration_jobs.next_pending_progress(job.job_id)
        c2 = repos.migration_jobs.next_pending_progress(job.job_id)
        assert c1 is not None and c2 is not None

        # Reset and verify the third (untouched) row is still pending
        # while the two claimed rows return to pending
        reset_count = repos.migration_jobs.reset_in_progress_to_pending(job.job_id)
        assert reset_count == 2

        pending = repos.migration_jobs.query_progress(job.job_id, status="pending")
        assert len(pending) == 3

    def test_query_progress_status_filter(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        rows = [
            _make_progress(job.job_id, src_path=f"/src/{i}.mp3",
                           status="pending" if i < 2 else "completed")
            for i in range(4)
        ]
        repos.migration_jobs.seed_progress_rows(job.job_id, rows)
        completed = repos.migration_jobs.query_progress(
            job.job_id, status="completed",
        )
        assert len(completed) == 2
        assert all(p.status == "completed" for p in completed)

    def test_count_progress_by_status_histogram(self, repos):
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="running",
        )
        repos.migration_jobs.insert_job(job)
        rows = []
        for i in range(5):
            rows.append(_make_progress(
                job.job_id, src_path=f"/src/{i}.mp3",
                status="completed" if i < 3 else ("failed" if i == 3 else "pending"),
            ))
        repos.migration_jobs.seed_progress_rows(job.job_id, rows)
        hist = repos.migration_jobs.count_progress_by_status(job.job_id)
        assert hist == {"completed": 3, "failed": 1, "pending": 1}

    def test_delete_job_cascades_progress_rows(self, repos):
        """FK ON DELETE CASCADE removes progress rows when the job is deleted."""
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="completed",
        )
        repos.migration_jobs.insert_job(job)
        rows = [_make_progress(job.job_id, src_path=f"/src/{i}") for i in range(3)]
        repos.migration_jobs.seed_progress_rows(job.job_id, rows)
        assert len(repos.migration_jobs.query_progress(job.job_id)) == 3

        repos.migration_jobs.delete_job(job.job_id)
        assert repos.migration_jobs.get_job(job.job_id) is None
        assert repos.migration_jobs.query_progress(job.job_id) == []

    def test_options_json_round_trips_complex_dict(self, repos):
        """options_json is the forward-compat escape hatch -- it must
        round-trip arbitrary JSON-able payloads."""
        job = MigrationJob(
            src_source_id="local", src_root="/s",
            dst_source_id="local", dst_root="/d", status="queued",
            options={
                "workers": 4,
                "verify_hash": True,
                "keep_source": False,
                "include_caution": False,
                "includes": ["**/*.mp3", "**/*.flac"],
                "excludes": ["**/draft/**"],
                "path_prefix": "Pink Floyd",
            },
        )
        repos.migration_jobs.insert_job(job)
        loaded = repos.migration_jobs.get_job(job.job_id)
        assert loaded.options["includes"] == ["**/*.mp3", "**/*.flac"]
        assert loaded.options["path_prefix"] == "Pink Floyd"
        assert loaded.options["workers"] == 4
