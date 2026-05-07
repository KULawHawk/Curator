"""Repository for :class:`TrashRecord`.

DESIGN.md §4.5 / §10.

A TrashRecord is the metadata Curator needs to restore a file later. The
file's row in ``files`` is soft-deleted (deleted_at set) but kept; this
table adds the snapshot data on top.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from curator.models.trash import TrashRecord
from curator.storage.connection import CuratorDB
from curator.storage.repositories._helpers import (
    json_dumps,
    json_loads,
    str_to_uuid,
    uuid_to_str,
)


class TrashRepository:
    """CRUD for trash records."""

    def __init__(self, db: CuratorDB):
        self.db = db

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def insert(self, record: TrashRecord) -> None:
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT INTO trash_registry (
                    curator_id, original_source_id, original_path, file_hash,
                    trashed_at, trashed_by, reason,
                    bundle_memberships_snapshot_json, file_attrs_snapshot_json,
                    os_trash_location, restore_path_override
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid_to_str(record.curator_id),
                    record.original_source_id,
                    record.original_path,
                    record.file_hash,
                    record.trashed_at,
                    record.trashed_by,
                    record.reason,
                    json_dumps(record.bundle_memberships_snapshot),
                    json_dumps(record.file_attrs_snapshot),
                    record.os_trash_location,
                    record.restore_path_override,
                ),
            )

    def delete(self, curator_id: UUID) -> None:
        """Remove a trash record (used when a file is restored)."""
        with self.db.conn() as conn:
            conn.execute(
                "DELETE FROM trash_registry WHERE curator_id = ?",
                (uuid_to_str(curator_id),),
            )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, curator_id: UUID) -> TrashRecord | None:
        cursor = self.db.conn().execute(
            "SELECT * FROM trash_registry WHERE curator_id = ?",
            (uuid_to_str(curator_id),),
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def list(
        self,
        *,
        since: datetime | None = None,
        actor: str | None = None,
        limit: int | None = None,
    ) -> list[TrashRecord]:
        sql = "SELECT * FROM trash_registry WHERE 1"
        params: list = []
        if since is not None:
            sql += " AND trashed_at >= ?"
            params.append(since)
        if actor is not None:
            sql += " AND trashed_by = ?"
            params.append(actor)
        sql += " ORDER BY trashed_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def count(self) -> int:
        cursor = self.db.conn().execute("SELECT COUNT(*) FROM trash_registry")
        return cursor.fetchone()[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_record(self, row) -> TrashRecord:
        return TrashRecord(
            curator_id=str_to_uuid(row["curator_id"]),
            original_source_id=row["original_source_id"],
            original_path=row["original_path"],
            file_hash=row["file_hash"],
            trashed_at=row["trashed_at"],
            trashed_by=row["trashed_by"],
            reason=row["reason"],
            bundle_memberships_snapshot=json_loads(row["bundle_memberships_snapshot_json"]) or [],
            file_attrs_snapshot=json_loads(row["file_attrs_snapshot_json"]) or {},
            os_trash_location=row["os_trash_location"],
            restore_path_override=row["restore_path_override"],
        )
