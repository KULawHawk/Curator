"""Repository for :class:`BundleEntity` and :class:`BundleMembership`.

DESIGN.md §4.5 / §9.

Bundles and their memberships are co-managed by this repository because
membership operations always involve a bundle context.
"""

from __future__ import annotations

from uuid import UUID

from curator.models.bundle import BundleEntity, BundleMembership
from curator.storage.connection import CuratorDB
from curator.storage.repositories._helpers import (
    load_flex_attrs,
    save_flex_attrs,
    str_to_uuid,
    uuid_to_str,
)


_FLEX_TABLE = "bundle_flex_attrs"
_FLEX_PK = "bundle_id"


class BundleRepository:
    """CRUD for bundles + memberships."""

    def __init__(self, db: CuratorDB):
        self.db = db

    # ------------------------------------------------------------------
    # Bundle mutations
    # ------------------------------------------------------------------

    def insert(self, bundle: BundleEntity) -> None:
        bid = uuid_to_str(bundle.bundle_id)
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT INTO bundles (
                    bundle_id, bundle_type, name, description, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    bid, bundle.bundle_type, bundle.name, bundle.description,
                    bundle.confidence, bundle.created_at,
                ),
            )
            save_flex_attrs(conn, _FLEX_TABLE, _FLEX_PK, bid, bundle.flex)

    def update(self, bundle: BundleEntity) -> None:
        bid = uuid_to_str(bundle.bundle_id)
        with self.db.conn() as conn:
            conn.execute(
                """
                UPDATE bundles SET
                    bundle_type = ?, name = ?, description = ?, confidence = ?
                WHERE bundle_id = ?
                """,
                (bundle.bundle_type, bundle.name, bundle.description, bundle.confidence, bid),
            )
            save_flex_attrs(conn, _FLEX_TABLE, _FLEX_PK, bid, bundle.flex)

    def delete(self, bundle_id: UUID) -> None:
        """Delete a bundle. Cascades to memberships and bundle_flex_attrs."""
        bid = uuid_to_str(bundle_id)
        with self.db.conn() as conn:
            conn.execute("DELETE FROM bundles WHERE bundle_id = ?", (bid,))

    # ------------------------------------------------------------------
    # Bundle reads
    # ------------------------------------------------------------------

    def get(self, bundle_id: UUID) -> BundleEntity | None:
        bid = uuid_to_str(bundle_id)
        cursor = self.db.conn().execute(
            "SELECT * FROM bundles WHERE bundle_id = ?", (bid,)
        )
        row = cursor.fetchone()
        return self._row_to_bundle(row) if row else None

    def list_all(self, *, bundle_type: str | None = None) -> list[BundleEntity]:
        sql = "SELECT * FROM bundles"
        params: list = []
        if bundle_type is not None:
            sql += " WHERE bundle_type = ?"
            params.append(bundle_type)
        sql += " ORDER BY created_at DESC, rowid DESC"
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_bundle(row) for row in cursor.fetchall()]

    def find_by_name(self, name: str) -> list[BundleEntity]:
        cursor = self.db.conn().execute(
            "SELECT * FROM bundles WHERE name = ?", (name,)
        )
        return [self._row_to_bundle(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Membership mutations
    # ------------------------------------------------------------------

    def add_membership(self, membership: BundleMembership) -> None:
        """Add a file to a bundle. Idempotent via INSERT OR REPLACE."""
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bundle_memberships (
                    bundle_id, curator_id, role, confidence, added_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    uuid_to_str(membership.bundle_id),
                    uuid_to_str(membership.curator_id),
                    membership.role,
                    membership.confidence,
                    membership.added_at,
                ),
            )

    def remove_membership(self, bundle_id: UUID, curator_id: UUID) -> None:
        with self.db.conn() as conn:
            conn.execute(
                "DELETE FROM bundle_memberships WHERE bundle_id = ? AND curator_id = ?",
                (uuid_to_str(bundle_id), uuid_to_str(curator_id)),
            )

    # ------------------------------------------------------------------
    # Membership reads
    # ------------------------------------------------------------------

    def get_memberships(self, bundle_id: UUID) -> list[BundleMembership]:
        """All members of a bundle."""
        cursor = self.db.conn().execute(
            # v1.7.64: secondary sort by rowid for deterministic ordering.
            # CURRENT_TIMESTAMP has second-level resolution in SQLite, so two
            # memberships added in the same call (e.g. create_manual with
            # multiple member_ids) get identical added_at values. Without a
            # secondary key, the order is implementation-defined and varies
            # between Python builds (Windows 3.11/3.12 vs 3.13). rowid is
            # monotonic by insertion, so it falls back to user intent order.
            "SELECT * FROM bundle_memberships WHERE bundle_id = ? "
            "ORDER BY added_at, rowid",
            (uuid_to_str(bundle_id),),
        )
        return [self._row_to_membership(row) for row in cursor.fetchall()]

    def get_memberships_for_file(self, curator_id: UUID) -> list[BundleMembership]:
        """All bundles a file belongs to."""
        cursor = self.db.conn().execute(
            "SELECT * FROM bundle_memberships WHERE curator_id = ?",
            (uuid_to_str(curator_id),),
        )
        return [self._row_to_membership(row) for row in cursor.fetchall()]

    def member_count(self, bundle_id: UUID) -> int:
        cursor = self.db.conn().execute(
            "SELECT COUNT(*) FROM bundle_memberships WHERE bundle_id = ?",
            (uuid_to_str(bundle_id),),
        )
        return cursor.fetchone()[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_bundle(self, row) -> BundleEntity:
        entity = BundleEntity(
            bundle_id=str_to_uuid(row["bundle_id"]),
            bundle_type=row["bundle_type"],
            name=row["name"],
            description=row["description"],
            confidence=row["confidence"],
            created_at=row["created_at"],
        )
        flex = load_flex_attrs(self.db.conn(), _FLEX_TABLE, _FLEX_PK, row["bundle_id"])
        for k, v in flex.items():
            entity.set_flex(k, v)
        return entity

    def _row_to_membership(self, row) -> BundleMembership:
        return BundleMembership(
            bundle_id=str_to_uuid(row["bundle_id"]),
            curator_id=str_to_uuid(row["curator_id"]),
            role=row["role"],
            confidence=row["confidence"],
            added_at=row["added_at"],
        )
