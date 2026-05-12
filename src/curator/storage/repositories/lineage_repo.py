"""Repository for :class:`LineageEdge`.

DESIGN.md §4.5 / §8.

Lineage is direction-aware in the schema (``from`` -> ``to``) but most
edge kinds are conceptually symmetric. Use :meth:`get_edges_for` when you
want both directions; ``get_edges_from`` / ``get_edges_to`` for one.
"""

from __future__ import annotations

import sqlite3
from uuid import UUID

from curator.models.lineage import LineageEdge, LineageKind
from curator.storage.connection import CuratorDB
from curator.storage.repositories._helpers import str_to_uuid, uuid_to_str


class LineageRepository:
    """CRUD for lineage edges."""

    def __init__(self, db: CuratorDB):
        self.db = db

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def insert(self, edge: LineageEdge, *, on_conflict: str = "ignore") -> bool:
        """Insert an edge.

        Args:
            on_conflict: ``"ignore"`` (default) → silently skip duplicates;
                         ``"replace"`` → overwrite existing matching edge;
                         ``"raise"`` → propagate sqlite3.IntegrityError.

        Returns:
            True if a row was inserted, False if it was a duplicate that
            was ignored. (Always True for ``on_conflict="replace"``.)
        """
        clause = {
            "ignore": "INSERT OR IGNORE",
            "replace": "INSERT OR REPLACE",
            "raise": "INSERT",
        }[on_conflict]

        try:
            with self.db.conn() as conn:
                cursor = conn.execute(
                    f"""
                    {clause} INTO lineage_edges (
                        edge_id, from_curator_id, to_curator_id, edge_kind,
                        confidence, detected_by, detected_at, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid_to_str(edge.edge_id),
                        uuid_to_str(edge.from_curator_id),
                        uuid_to_str(edge.to_curator_id),
                        edge.edge_kind.value,
                        edge.confidence,
                        edge.detected_by,
                        edge.detected_at,
                        edge.notes,
                    ),
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            if on_conflict == "raise":
                raise
            return False

    def delete(self, edge_id: UUID) -> None:
        with self.db.conn() as conn:
            conn.execute(
                "DELETE FROM lineage_edges WHERE edge_id = ?",
                (uuid_to_str(edge_id),),
            )

    def delete_for_file(self, curator_id: UUID) -> int:
        """Delete all edges touching a file. Returns count deleted."""
        cid = uuid_to_str(curator_id)
        with self.db.conn() as conn:
            cursor = conn.execute(
                "DELETE FROM lineage_edges WHERE from_curator_id = ? OR to_curator_id = ?",
                (cid, cid),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, edge_id: UUID) -> LineageEdge | None:
        cursor = self.db.conn().execute(
            "SELECT * FROM lineage_edges WHERE edge_id = ?",
            (uuid_to_str(edge_id),),
        )
        row = cursor.fetchone()
        return self._row_to_edge(row) if row else None

    def get_edges_from(self, curator_id: UUID) -> list[LineageEdge]:
        cursor = self.db.conn().execute(
            "SELECT * FROM lineage_edges WHERE from_curator_id = ?",
            (uuid_to_str(curator_id),),
        )
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def get_edges_to(self, curator_id: UUID) -> list[LineageEdge]:
        cursor = self.db.conn().execute(
            "SELECT * FROM lineage_edges WHERE to_curator_id = ?",
            (uuid_to_str(curator_id),),
        )
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def get_edges_for(self, curator_id: UUID) -> list[LineageEdge]:
        """All edges touching this file (either direction)."""
        cid = uuid_to_str(curator_id)
        cursor = self.db.conn().execute(
            "SELECT * FROM lineage_edges WHERE from_curator_id = ? OR to_curator_id = ?",
            (cid, cid),
        )
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def get_edges_between(
        self,
        from_id: UUID,
        to_id: UUID,
        *,
        kind: LineageKind | None = None,
    ) -> list[LineageEdge]:
        """Edges from a specific source to a specific target. Optionally filter by kind."""
        sql = """
            SELECT * FROM lineage_edges
            WHERE from_curator_id = ? AND to_curator_id = ?
        """
        params = [uuid_to_str(from_id), uuid_to_str(to_id)]
        if kind is not None:
            sql += " AND edge_kind = ?"
            params.append(kind.value)
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def list_by_kind(
        self,
        kind: LineageKind,
        *,
        min_confidence: float = 0.0,
        limit: int | None = None,
    ) -> list[LineageEdge]:
        sql = "SELECT * FROM lineage_edges WHERE edge_kind = ? AND confidence >= ?"
        params: list = [kind.value, min_confidence]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def query_by_confidence(
        self,
        *,
        min_confidence: float = 0.0,
        max_confidence: float = 1.0,
        limit: int | None = None,
    ) -> list[LineageEdge]:
        """Edges with confidence in ``[min, max)`` (max is exclusive).

        Used by the GUI's Inbox "Pending review" section to surface
        edges in the [escalate_threshold, auto_confirm_threshold) band
        (DESIGN.md §8.2 confidence thresholds).
        """
        sql = (
            "SELECT * FROM lineage_edges "
            "WHERE confidence >= ? AND confidence < ? "
            "ORDER BY confidence DESC, detected_at DESC, rowid DESC"
        )
        params: list = [min_confidence, max_confidence]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_edge(self, row) -> LineageEdge:
        return LineageEdge(
            edge_id=str_to_uuid(row["edge_id"]),
            from_curator_id=str_to_uuid(row["from_curator_id"]),
            to_curator_id=str_to_uuid(row["to_curator_id"]),
            edge_kind=LineageKind(row["edge_kind"]),
            confidence=row["confidence"],
            detected_by=row["detected_by"],
            detected_at=row["detected_at"],
            notes=row["notes"],
        )
