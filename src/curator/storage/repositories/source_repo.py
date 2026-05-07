"""Repository for :class:`SourceConfig`.

DESIGN.md §4.5 / §3.8.
"""

from __future__ import annotations

from curator.models.source import SourceConfig
from curator.storage.connection import CuratorDB
from curator.storage.repositories._helpers import json_dumps, json_loads


class SourceRepository:
    """CRUD for source configurations."""

    def __init__(self, db: CuratorDB):
        self.db = db

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def insert(self, source: SourceConfig) -> None:
        """Insert a new source. Raises IntegrityError if source_id exists."""
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT INTO sources (
                    source_id, source_type, display_name, config_json, enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.source_type,
                    source.display_name,
                    json_dumps(source.config),
                    1 if source.enabled else 0,
                    source.created_at,
                ),
            )

    def upsert(self, source: SourceConfig) -> None:
        """Insert or replace a source by source_id."""
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sources (
                    source_id, source_type, display_name, config_json, enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.source_type,
                    source.display_name,
                    json_dumps(source.config),
                    1 if source.enabled else 0,
                    source.created_at,
                ),
            )

    def update(self, source: SourceConfig) -> None:
        with self.db.conn() as conn:
            conn.execute(
                """
                UPDATE sources SET
                    source_type = ?, display_name = ?, config_json = ?, enabled = ?
                WHERE source_id = ?
                """,
                (
                    source.source_type,
                    source.display_name,
                    json_dumps(source.config),
                    1 if source.enabled else 0,
                    source.source_id,
                ),
            )

    def set_enabled(self, source_id: str, enabled: bool) -> None:
        with self.db.conn() as conn:
            conn.execute(
                "UPDATE sources SET enabled = ? WHERE source_id = ?",
                (1 if enabled else 0, source_id),
            )

    def delete(self, source_id: str) -> None:
        """Delete a source. Files reference sources with ON DELETE RESTRICT,
        so deletion fails if any FileEntity rows still reference it.
        """
        with self.db.conn() as conn:
            conn.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, source_id: str) -> SourceConfig | None:
        cursor = self.db.conn().execute(
            "SELECT * FROM sources WHERE source_id = ?", (source_id,)
        )
        row = cursor.fetchone()
        return self._row_to_source(row) if row else None

    def list_all(self) -> list[SourceConfig]:
        cursor = self.db.conn().execute("SELECT * FROM sources ORDER BY created_at")
        return [self._row_to_source(row) for row in cursor.fetchall()]

    def list_enabled(self) -> list[SourceConfig]:
        cursor = self.db.conn().execute(
            "SELECT * FROM sources WHERE enabled = 1 ORDER BY created_at"
        )
        return [self._row_to_source(row) for row in cursor.fetchall()]

    def list_by_type(self, source_type: str) -> list[SourceConfig]:
        cursor = self.db.conn().execute(
            "SELECT * FROM sources WHERE source_type = ? ORDER BY created_at",
            (source_type,),
        )
        return [self._row_to_source(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_source(self, row) -> SourceConfig:
        return SourceConfig(
            source_id=row["source_id"],
            source_type=row["source_type"],
            display_name=row["display_name"],
            config=json_loads(row["config_json"]) or {},
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )
