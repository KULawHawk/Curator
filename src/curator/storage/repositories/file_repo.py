"""Repository for :class:`FileEntity`.

DESIGN.md §4.5.

Phase Alpha API surface. Methods are organized as:

  * Mutations: ``insert``, ``update``, ``upsert``, ``mark_deleted``,
    ``undelete``, ``delete`` (hard).
  * Single-entity reads: ``get``, ``find_by_path``.
  * Multi-entity reads: ``find_by_hash``, ``find_by_md5``,
    ``find_by_fuzzy_hash``, ``find_candidates_by_size``, ``query``.
  * Stats: ``count``.
"""

from __future__ import annotations

from datetime import datetime
from curator._compat.datetime import utcnow_naive
from typing import Iterable
from uuid import UUID

from curator.models.file import FileEntity
from curator.storage.connection import CuratorDB
from curator.storage.queries import FileQuery
from curator.storage.repositories._helpers import (
    load_flex_attrs,
    save_flex_attrs,
    str_to_uuid,
    uuid_to_str,
)


_FLEX_TABLE = "file_flex_attrs"
_FLEX_PK = "curator_id"


class FileRepository:
    """CRUD + queries for files."""

    def __init__(self, db: CuratorDB):
        self.db = db

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def insert(self, file: FileEntity) -> None:
        """Insert a new file row plus its flex attrs.

        Raises:
            sqlite3.IntegrityError: if (source_id, source_path) already exists.
        """
        cid = uuid_to_str(file.curator_id)
        supersedes_str = (
            uuid_to_str(file.supersedes_id) if file.supersedes_id is not None else None
        )
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT INTO files (
                    curator_id, source_id, source_path,
                    size, mtime, ctime, inode,
                    xxhash3_128, md5, fuzzy_hash,
                    file_type, extension, file_type_confidence,
                    seen_at, last_scanned_at, deleted_at,
                    status, supersedes_id, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cid, file.source_id, file.source_path,
                    file.size, file.mtime, file.ctime, file.inode,
                    file.xxhash3_128, file.md5, file.fuzzy_hash,
                    file.file_type, file.extension, file.file_type_confidence,
                    file.seen_at, file.last_scanned_at, file.deleted_at,
                    file.status, supersedes_str, file.expires_at,
                ),
            )
            save_flex_attrs(conn, _FLEX_TABLE, _FLEX_PK, cid, file.flex)

    def update(self, file: FileEntity) -> None:
        """Update all mutable fields of an existing file row.

        Replaces flex attrs (any keys not in ``file.flex`` are left intact —
        use :meth:`replace_flex_attrs` for full replacement).
        """
        cid = uuid_to_str(file.curator_id)
        supersedes_str = (
            uuid_to_str(file.supersedes_id) if file.supersedes_id is not None else None
        )
        with self.db.conn() as conn:
            conn.execute(
                """
                UPDATE files SET
                    source_id = ?, source_path = ?,
                    size = ?, mtime = ?, ctime = ?, inode = ?,
                    xxhash3_128 = ?, md5 = ?, fuzzy_hash = ?,
                    file_type = ?, extension = ?, file_type_confidence = ?,
                    seen_at = ?, last_scanned_at = ?, deleted_at = ?,
                    status = ?, supersedes_id = ?, expires_at = ?
                WHERE curator_id = ?
                """,
                (
                    file.source_id, file.source_path,
                    file.size, file.mtime, file.ctime, file.inode,
                    file.xxhash3_128, file.md5, file.fuzzy_hash,
                    file.file_type, file.extension, file.file_type_confidence,
                    file.seen_at, file.last_scanned_at, file.deleted_at,
                    file.status, supersedes_str, file.expires_at,
                    cid,
                ),
            )
            save_flex_attrs(conn, _FLEX_TABLE, _FLEX_PK, cid, file.flex)

    def upsert(self, file: FileEntity) -> None:
        """Insert if new, update if existing (by curator_id).

        Convenience method for scan loops. If you have a known-new entity,
        prefer :meth:`insert` for clarity.
        """
        existing = self.get(file.curator_id)
        if existing is None:
            self.insert(file)
        else:
            self.update(file)

    def mark_deleted(self, curator_id: UUID, when: datetime | None = None) -> None:
        """Soft-delete: set ``deleted_at`` to the given time (default: now)."""
        when = when or utcnow_naive()
        cid = uuid_to_str(curator_id)
        with self.db.conn() as conn:
            conn.execute(
                "UPDATE files SET deleted_at = ? WHERE curator_id = ?",
                (when, cid),
            )

    def undelete(self, curator_id: UUID) -> None:
        """Reverse a soft-delete (used during restore)."""
        cid = uuid_to_str(curator_id)
        with self.db.conn() as conn:
            conn.execute(
                "UPDATE files SET deleted_at = NULL WHERE curator_id = ?",
                (cid,),
            )

    def delete(self, curator_id: UUID) -> None:
        """Hard-delete a file row. Cascades to flex attrs and lineage edges.

        DOES NOT cascade to bundle_memberships (also FK-cascaded) or
        trash_registry (also FK-cascaded). Reserved for purges and tests;
        normal user-driven deletion goes through :meth:`mark_deleted`.
        """
        cid = uuid_to_str(curator_id)
        with self.db.conn() as conn:
            conn.execute("DELETE FROM files WHERE curator_id = ?", (cid,))

    # ------------------------------------------------------------------
    # Single-entity reads
    # ------------------------------------------------------------------

    def get(self, curator_id: UUID) -> FileEntity | None:
        """Get a file by curator_id, including its flex attrs."""
        cid = uuid_to_str(curator_id)
        cursor = self.db.conn().execute(
            "SELECT * FROM files WHERE curator_id = ?", (cid,)
        )
        row = cursor.fetchone()
        return self._row_to_entity(row) if row else None

    def find_by_path(self, source_id: str, source_path: str) -> FileEntity | None:
        """Find a file by its source_id + source_path (the natural key)."""
        cursor = self.db.conn().execute(
            "SELECT * FROM files WHERE source_id = ? AND source_path = ?",
            (source_id, source_path),
        )
        row = cursor.fetchone()
        return self._row_to_entity(row) if row else None

    # ------------------------------------------------------------------
    # Multi-entity reads
    # ------------------------------------------------------------------

    def find_by_hash(self, xxhash3_128: str, *, include_deleted: bool = False) -> list[FileEntity]:
        """Find all files with this xxhash. Used for duplicate detection."""
        sql = "SELECT * FROM files WHERE xxhash3_128 = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        cursor = self.db.conn().execute(sql, (xxhash3_128,))
        return [self._row_to_entity(row) for row in cursor.fetchall()]

    def find_by_md5(self, md5: str, *, include_deleted: bool = False) -> list[FileEntity]:
        sql = "SELECT * FROM files WHERE md5 = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        cursor = self.db.conn().execute(sql, (md5,))
        return [self._row_to_entity(row) for row in cursor.fetchall()]

    def find_by_fuzzy_hash(self, fuzzy_hash: str, *, include_deleted: bool = False) -> list[FileEntity]:
        sql = "SELECT * FROM files WHERE fuzzy_hash = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        cursor = self.db.conn().execute(sql, (fuzzy_hash,))
        return [self._row_to_entity(row) for row in cursor.fetchall()]

    def find_candidates_by_size(
        self,
        size: int,
        *,
        exclude_curator_id: UUID | None = None,
        include_deleted: bool = False,
    ) -> list[FileEntity]:
        """Find files of the same size, optionally excluding a given file.

        Used by the hash pipeline (Stage 1: size grouping) and lineage
        candidate selection.
        """
        sql = "SELECT * FROM files WHERE size = ?"
        params: list = [size]
        if exclude_curator_id is not None:
            sql += " AND curator_id != ?"
            params.append(uuid_to_str(exclude_curator_id))
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_entity(row) for row in cursor.fetchall()]

    def find_with_fuzzy_hash(self) -> list[FileEntity]:
        """All files that have a fuzzy hash (candidates for fuzzy comparison)."""
        cursor = self.db.conn().execute(
            "SELECT * FROM files WHERE fuzzy_hash IS NOT NULL AND deleted_at IS NULL"
        )
        return [self._row_to_entity(row) for row in cursor.fetchall()]

    def query(self, query: FileQuery) -> list[FileEntity]:
        """Run a composable :class:`FileQuery` and return matching entities.

        Flex-attr filtering (if any) is applied in Python after fetch.
        """
        sql, params = query.build_sql()
        cursor = self.db.conn().execute(sql, tuple(params))
        results = [self._row_to_entity(row) for row in cursor.fetchall()]
        if query.flex_attrs:
            results = [
                f for f in results
                if all(f.flex.get(k) == v for k, v in query.flex_attrs.items())
            ]
        return results

    def iter_all(
        self,
        *,
        source_id: str | None = None,
        include_deleted: bool = False,
        batch_size: int = 1000,
    ) -> Iterable[FileEntity]:
        """Iterate over all files, batched for memory efficiency.

        Useful for full-table scans (e.g., fuzzy-hash lineage computation
        across the entire index).
        """
        offset = 0
        while True:
            sql = "SELECT * FROM files WHERE 1"
            params: list = []
            if source_id is not None:
                sql += " AND source_id = ?"
                params.append(source_id)
            if not include_deleted:
                sql += " AND deleted_at IS NULL"
            sql += " ORDER BY curator_id LIMIT ? OFFSET ?"
            params.extend([batch_size, offset])
            cursor = self.db.conn().execute(sql, tuple(params))
            rows = cursor.fetchall()
            if not rows:
                return
            for row in rows:
                yield self._row_to_entity(row)
            offset += len(rows)
            if len(rows) < batch_size:
                return

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def count(self, *, source_id: str | None = None, include_deleted: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM files WHERE 1"
        params: list = []
        if source_id is not None:
            sql += " AND source_id = ?"
            params.append(source_id)
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        cursor = self.db.conn().execute(sql, tuple(params))
        return cursor.fetchone()[0]

    # ------------------------------------------------------------------
    # T-C02 classification (v1.7.3)
    # ------------------------------------------------------------------

    _VALID_STATUSES = frozenset({"vital", "active", "provisional", "junk"})

    def update_status(
        self,
        curator_id: UUID,
        status: str,
        *,
        supersedes_id: UUID | None = None,
        expires_at: datetime | None = None,
        clear_supersedes: bool = False,
        clear_expires: bool = False,
    ) -> None:
        """Update classification fields without touching the rest of the row.

        ``status`` must be one of ``vital`` / ``active`` / ``provisional`` /
        ``junk``. ``supersedes_id`` and ``expires_at`` are optional; pass
        ``clear_supersedes=True`` or ``clear_expires=True`` to NULL them out
        explicitly (passing ``None`` for the value leaves the existing
        column unchanged, since None is the sentinel for "don't touch").

        Raises:
            ValueError: if ``status`` is not in the allowed set.
        """
        if status not in self._VALID_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}; must be one of "
                f"{sorted(self._VALID_STATUSES)}"
            )
        cid = uuid_to_str(curator_id)
        sets: list[str] = ["status = ?"]
        params: list = [status]
        if clear_supersedes:
            sets.append("supersedes_id = NULL")
        elif supersedes_id is not None:
            sets.append("supersedes_id = ?")
            params.append(uuid_to_str(supersedes_id))
        if clear_expires:
            sets.append("expires_at = NULL")
        elif expires_at is not None:
            sets.append("expires_at = ?")
            params.append(expires_at)
        params.append(cid)
        sql = f"UPDATE files SET {', '.join(sets)} WHERE curator_id = ?"
        with self.db.conn() as conn:
            conn.execute(sql, tuple(params))

    def count_by_status(
        self,
        *,
        source_id: str | None = None,
        include_deleted: bool = False,
    ) -> dict[str, int]:
        """Return a dict mapping each of the 4 status buckets to its count.

        Every bucket is present in the output even if zero (so callers can
        rely on key existence). Unknown statuses observed in the DB are
        also included.
        """
        sql = "SELECT status, COUNT(*) FROM files WHERE 1"
        params: list = []
        if source_id is not None:
            sql += " AND source_id = ?"
            params.append(source_id)
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " GROUP BY status"
        cursor = self.db.conn().execute(sql, tuple(params))
        rows = cursor.fetchall()
        out: dict[str, int] = {s: 0 for s in self._VALID_STATUSES}
        for row in rows:
            out[row[0]] = row[1]
        return out

    def query_by_status(
        self,
        status: str,
        *,
        source_id: str | None = None,
        limit: int | None = None,
        include_deleted: bool = False,
    ) -> list[FileEntity]:
        """Return all files with the given status."""
        sql = "SELECT * FROM files WHERE status = ?"
        params: list = [status]
        if source_id is not None:
            sql += " AND source_id = ?"
            params.append(source_id)
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY seen_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_entity(row) for row in cursor.fetchall()]

    def find_expiring_before(
        self,
        when: datetime,
        *,
        source_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[FileEntity]:
        """Return all files with ``expires_at <= when`` and ``expires_at IS NOT NULL``.

        Useful for cleanup-tab and tiered-storage policies that want to
        flag soon-to-expire files.
        """
        sql = (
            "SELECT * FROM files WHERE expires_at IS NOT NULL AND expires_at <= ?"
        )
        params: list = [when]
        if source_id is not None:
            sql += " AND source_id = ?"
            params.append(source_id)
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY expires_at ASC"
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_entity(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_entity(self, row) -> FileEntity:
        """Convert a sqlite3.Row to a FileEntity, including flex attrs."""
        # v1.7.3 (T-C02): defensively pull the new classification columns
        # using ``row.keys()`` check so this still works against rows from
        # pre-migration-003 databases (although migrations run at startup
        # so that should never happen in practice).
        row_keys = row.keys() if hasattr(row, "keys") else []
        status = row["status"] if "status" in row_keys else "active"
        supersedes_raw = row["supersedes_id"] if "supersedes_id" in row_keys else None
        expires_at = row["expires_at"] if "expires_at" in row_keys else None
        supersedes_id = str_to_uuid(supersedes_raw) if supersedes_raw else None

        entity = FileEntity(
            curator_id=str_to_uuid(row["curator_id"]),
            source_id=row["source_id"],
            source_path=row["source_path"],
            size=row["size"],
            mtime=row["mtime"],
            ctime=row["ctime"],
            inode=row["inode"],
            xxhash3_128=row["xxhash3_128"],
            md5=row["md5"],
            fuzzy_hash=row["fuzzy_hash"],
            file_type=row["file_type"],
            extension=row["extension"],
            file_type_confidence=row["file_type_confidence"],
            seen_at=row["seen_at"],
            last_scanned_at=row["last_scanned_at"],
            deleted_at=row["deleted_at"],
            status=status,
            supersedes_id=supersedes_id,
            expires_at=expires_at,
        )
        # Load flex attrs
        flex = load_flex_attrs(self.db.conn(), _FLEX_TABLE, _FLEX_PK, row["curator_id"])
        for k, v in flex.items():
            entity.set_flex(k, v)
        return entity
