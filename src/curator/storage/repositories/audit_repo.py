"""Repository for :class:`AuditEntry`.

DESIGN.md §4.5 / §17.

The audit log is append-only. There is no ``update`` or ``delete`` method
by design — corrections happen by appending a new entry that references
the original. This is the forensic-grade trail Constitution-governed
deployments need.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from curator.models.audit import AuditEntry
from curator.storage.connection import CuratorDB
from curator.storage.repositories._helpers import json_dumps, json_loads


class AuditRepository:
    """Append + query interface for the audit log.

    There are no update / delete methods on purpose. The audit log is
    intentionally immutable.
    """

    def __init__(self, db: CuratorDB):
        self.db = db

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    def insert(self, entry: AuditEntry) -> int:
        """Insert an audit entry. Returns the assigned ``audit_id``."""
        with self.db.conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_log (
                    occurred_at, actor, action, entity_type, entity_id, details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.occurred_at,
                    entry.actor,
                    entry.action,
                    entry.entity_type,
                    entry.entity_id,
                    json_dumps(entry.details),
                ),
            )
            audit_id = cursor.lastrowid
            entry.audit_id = audit_id  # update the in-memory entity
            return audit_id

    def log(
        self,
        actor: str,
        action: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        details: dict[str, Any] | None = None,
        when: datetime | None = None,
    ) -> int:
        """Convenience: build and insert an :class:`AuditEntry` in one call."""
        entry = AuditEntry(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
            occurred_at=when or datetime.utcnow(),
        )
        return self.insert(entry)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, audit_id: int) -> AuditEntry | None:
        cursor = self.db.conn().execute(
            "SELECT * FROM audit_log WHERE audit_id = ?", (audit_id,)
        )
        row = cursor.fetchone()
        return self._row_to_entry(row) if row else None

    def query(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        actor: str | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 1000,
    ) -> list[AuditEntry]:
        clauses: list[str] = []
        params: list[Any] = []

        if since is not None:
            clauses.append("occurred_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("occurred_at < ?")
            params.append(until)
        if actor is not None:
            clauses.append("actor = ?")
            params.append(actor)
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if entity_type is not None:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)

        where = " AND ".join(clauses) if clauses else "1"
        sql = f"SELECT * FROM audit_log WHERE {where} ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def count(self) -> int:
        cursor = self.db.conn().execute("SELECT COUNT(*) FROM audit_log")
        return cursor.fetchone()[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_entry(self, row) -> AuditEntry:
        return AuditEntry(
            audit_id=row["audit_id"],
            occurred_at=row["occurred_at"],
            actor=row["actor"],
            action=row["action"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            details=json_loads(row["details_json"]) or {},
        )
