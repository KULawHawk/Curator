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
                    source_id, source_type, display_name, config_json, enabled, created_at, share_visibility
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.source_type,
                    source.display_name,
                    json_dumps(source.config),
                    1 if source.enabled else 0,
                    source.created_at,
                    source.share_visibility,
                ),
            )

    def upsert(self, source: SourceConfig) -> None:
        """Insert or replace a source by source_id."""
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sources (
                    source_id, source_type, display_name, config_json, enabled, created_at, share_visibility
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.source_type,
                    source.display_name,
                    json_dumps(source.config),
                    1 if source.enabled else 0,
                    source.created_at,
                    source.share_visibility,
                ),
            )

    def update(self, source: SourceConfig) -> None:
        """Update a source row by source_id.

        v1.7.49: ``source_type`` is now **immutable** at the repository
        layer. Attempting to update a source with a different
        ``source_type`` than the existing row raises ``ValueError``
        before any DB write happens.

        Rationale: changing ``source_type`` would invalidate the existing
        ``config_json`` against a different plugin's schema. The GUI
        already enforces this via a disabled combobox + tooltip in
        :class:`SourceAddDialog` (v1.7.40), but the repository was
        accepting type changes silently. v1.7.49 closes that gap at the
        data layer.

        Behavior preserved:
          * Updating ``source_type`` to the SAME value is allowed (no-op
            check passes).
          * Calling ``update()`` on a non-existent ``source_id`` is still
            a silent SQL no-op (matches pre-v1.7.49 behavior). Adding a
            "not found" error is a separate, larger-scope ship.
          * All other fields (``display_name``, ``config``, ``enabled``,
            ``share_visibility``) remain freely mutable.

        Migration path for callers that need to switch a source's plugin
        type: delete the source (which cascades-restricts on FileEntity
        rows that reference it) and re-insert with the new type.

        Args:
            source: The new :class:`SourceConfig` to write.

        Raises:
            ValueError: If ``source.source_type`` differs from the
                existing row's ``source_type``.
        """
        # v1.7.49: source_type immutability guard. Read the existing row
        # so we can compare types. If no row exists, fall through to the
        # SQL UPDATE (it'll affect 0 rows -- silent no-op, matches
        # historical behavior).
        existing = self.get(source.source_id)
        if existing is not None and existing.source_type != source.source_type:
            raise ValueError(
                f"source_type is immutable: cannot change "
                f"{existing.source_type!r} -> {source.source_type!r} "
                f"for source {source.source_id!r}. The existing "
                f"config_json was validated against the "
                f"{existing.source_type!r} plugin's schema and would "
                f"not be valid for {source.source_type!r}. Delete and "
                f"re-insert if you need to change the plugin type."
            )

        with self.db.conn() as conn:
            conn.execute(
                """
                UPDATE sources SET
                    source_type = ?, display_name = ?, config_json = ?, enabled = ?, share_visibility = ?
                WHERE source_id = ?
                """,
                (
                    source.source_type,
                    source.display_name,
                    json_dumps(source.config),
                    1 if source.enabled else 0,
                    source.share_visibility,
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
        cursor = self.db.conn().execute("SELECT * FROM sources ORDER BY created_at, rowid")
        return [self._row_to_source(row) for row in cursor.fetchall()]

    def list_enabled(self) -> list[SourceConfig]:
        cursor = self.db.conn().execute(
            "SELECT * FROM sources WHERE enabled = 1 ORDER BY created_at, rowid"
        )
        return [self._row_to_source(row) for row in cursor.fetchall()]

    def list_by_type(self, source_type: str) -> list[SourceConfig]:
        cursor = self.db.conn().execute(
            "SELECT * FROM sources WHERE source_type = ? ORDER BY created_at, rowid",
            (source_type,),
        )
        return [self._row_to_source(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_source(self, row) -> SourceConfig:
        # v1.7.29: share_visibility added via migration 004. Older rows
        # have the column with DEFAULT 'private' so this should always
        # be present, but we defensively fall back if the test fixture
        # uses a pre-004 schema snapshot.
        try:
            share_visibility = row["share_visibility"] or "private"
        except (IndexError, KeyError):
            share_visibility = "private"
        return SourceConfig(
            source_id=row["source_id"],
            source_type=row["source_type"],
            display_name=row["display_name"],
            config=json_loads(row["config_json"]) or {},
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            share_visibility=share_visibility,
        )
