"""Coverage closure for ``curator.storage.repositories.hash_cache_repo`` (v1.7.135).

Targets the 14 uncovered lines:
- 68-71: ``upsert_from_file`` (both branches)
- 86-87: ``invalidate(source_id, source_path)``
- 94-98: ``invalidate_source(source_id)``
- 102-107: ``purge_older_than(days)``
- 137-138: ``count()``
"""

from __future__ import annotations

from datetime import datetime, timedelta

from curator._compat.datetime import utcnow_naive
from curator.models import FileEntity
from curator.storage.repositories.hash_cache_repo import CachedHash


def _entry(source_id: str, path: str, *, computed_at: datetime | None = None) -> CachedHash:
    return CachedHash(
        source_id=source_id,
        source_path=path,
        mtime=datetime(2026, 1, 15),
        size=100,
        xxhash3_128="xxhashval",
        md5="md5val",
        fuzzy_hash=None,
        computed_at=computed_at or utcnow_naive(),
    )


class TestUpsertFromFile:
    def test_skips_when_no_full_hash(self, repos):
        """Line 68-70: file without xxhash3_128 returns early (no insert)."""
        f = FileEntity(
            source_id="local", source_path="/x", size=1, mtime=utcnow_naive(),
            xxhash3_128=None,  # no full hash
        )
        repos.cache.upsert_from_file(f)
        assert repos.cache.get("local", "/x") is None

    def test_upserts_when_full_hash_present(self, repos):
        f = FileEntity(
            source_id="local", source_path="/y", size=2, mtime=utcnow_naive(),
            xxhash3_128="ff", md5="mm",
        )
        repos.cache.upsert_from_file(f)
        cached = repos.cache.get("local", "/y")
        assert cached is not None
        assert cached.xxhash3_128 == "ff"
        assert cached.md5 == "mm"


class TestInvalidate:
    def test_invalidate_removes_single_entry(self, repos):
        repos.cache.upsert(_entry("local", "/a"))
        repos.cache.upsert(_entry("local", "/b"))
        repos.cache.invalidate("local", "/a")
        assert repos.cache.get("local", "/a") is None
        assert repos.cache.get("local", "/b") is not None

    def test_invalidate_missing_entry_noop(self, repos):
        repos.cache.invalidate("local", "/nonexistent")  # must not raise


class TestInvalidateSource:
    def test_invalidate_source_removes_all_for_source(self, repos):
        repos.cache.upsert(_entry("local", "/a"))
        repos.cache.upsert(_entry("local", "/b"))
        repos.cache.upsert(_entry("gdrive:x", "/c"))
        deleted = repos.cache.invalidate_source("local")
        assert deleted == 2
        assert repos.cache.get("local", "/a") is None
        assert repos.cache.get("gdrive:x", "/c") is not None

    def test_invalidate_source_empty_returns_zero(self, repos):
        assert repos.cache.invalidate_source("unknown") == 0


class TestPurgeOlderThan:
    def test_purges_old_entries(self, repos):
        old_when = utcnow_naive() - timedelta(days=10)
        recent_when = utcnow_naive() - timedelta(days=1)
        repos.cache.upsert(_entry("local", "/old", computed_at=old_when))
        repos.cache.upsert(_entry("local", "/new", computed_at=recent_when))

        deleted = repos.cache.purge_older_than(days=7)
        assert deleted == 1
        assert repos.cache.get("local", "/old") is None
        assert repos.cache.get("local", "/new") is not None


class TestCount:
    def test_count_returns_row_total(self, repos):
        assert repos.cache.count() == 0
        repos.cache.upsert(_entry("local", "/a"))
        repos.cache.upsert(_entry("local", "/b"))
        assert repos.cache.count() == 2
