"""Repository for the hash cache.

DESIGN.md §4.5 / §7.3.

The hash cache stores ``(source_id, source_path) -> (xxhash, md5, fuzzy_hash)``
keyed by mtime + size. Subsequent scans skip re-hashing unchanged files.

The cache is independent of FileEntity: a path can be cached even if it
was never inserted into ``files`` (e.g., during a dry-run scan). When
the hash pipeline computes a hash, it always upserts here.

Note: this repo intentionally has no entity model — it deals in tuples
of primitive values. A `CachedHash` typed result keeps things readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from curator.models.file import FileEntity
from curator.storage.connection import CuratorDB


@dataclass
class CachedHash:
    """One row from the hash_cache table."""

    source_id: str
    source_path: str
    mtime: datetime
    size: int
    xxhash3_128: str | None
    md5: str | None
    fuzzy_hash: str | None
    computed_at: datetime


class HashCacheRepository:
    """Read/write/invalidate operations on the hash cache."""

    def __init__(self, db: CuratorDB):
        self.db = db

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def upsert(self, entry: CachedHash) -> None:
        """Insert or replace a cache entry."""
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO hash_cache (
                    source_id, source_path, mtime, size,
                    xxhash3_128, md5, fuzzy_hash, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.source_id, entry.source_path, entry.mtime, entry.size,
                    entry.xxhash3_128, entry.md5, entry.fuzzy_hash, entry.computed_at,
                ),
            )

    def upsert_from_file(self, file: FileEntity) -> None:
        """Convenience: build a CachedHash from a FileEntity and upsert."""
        if not file.has_full_hash:
            # Nothing to cache yet.
            return
        self.upsert(
            CachedHash(
                source_id=file.source_id,
                source_path=file.source_path,
                mtime=file.mtime,
                size=file.size,
                xxhash3_128=file.xxhash3_128,
                md5=file.md5,
                fuzzy_hash=file.fuzzy_hash,
                computed_at=datetime.utcnow(),
            )
        )

    def invalidate(self, source_id: str, source_path: str) -> None:
        """Remove a single entry from the cache."""
        with self.db.conn() as conn:
            conn.execute(
                "DELETE FROM hash_cache WHERE source_id = ? AND source_path = ?",
                (source_id, source_path),
            )

    def invalidate_source(self, source_id: str) -> int:
        """Remove all entries for a source. Returns count deleted."""
        with self.db.conn() as conn:
            cursor = conn.execute(
                "DELETE FROM hash_cache WHERE source_id = ?", (source_id,)
            )
            return cursor.rowcount

    def purge_older_than(self, *, days: int) -> int:
        """Delete cache entries computed more than N days ago. Returns count deleted."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        with self.db.conn() as conn:
            cursor = conn.execute(
                "DELETE FROM hash_cache WHERE computed_at < ?", (cutoff,)
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, source_id: str, source_path: str) -> CachedHash | None:
        cursor = self.db.conn().execute(
            "SELECT * FROM hash_cache WHERE source_id = ? AND source_path = ?",
            (source_id, source_path),
        )
        row = cursor.fetchone()
        return self._row_to_entry(row) if row else None

    def get_if_fresh(
        self, source_id: str, source_path: str, *, mtime: datetime, size: int
    ) -> CachedHash | None:
        """Get the cached hash only if mtime + size match the live file.

        This is the hot path: the hash pipeline calls this for every file
        and skips hashing if a fresh entry is returned.
        """
        entry = self.get(source_id, source_path)
        if entry is None:
            return None
        if entry.mtime != mtime or entry.size != size:
            return None
        return entry

    def count(self) -> int:
        cursor = self.db.conn().execute("SELECT COUNT(*) FROM hash_cache")
        return cursor.fetchone()[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_entry(self, row) -> CachedHash:
        return CachedHash(
            source_id=row["source_id"],
            source_path=row["source_path"],
            mtime=row["mtime"],
            size=row["size"],
            xxhash3_128=row["xxhash3_128"],
            md5=row["md5"],
            fuzzy_hash=row["fuzzy_hash"],
            computed_at=row["computed_at"],
        )
