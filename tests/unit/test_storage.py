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
