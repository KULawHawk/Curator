"""Audit service — append-only log with Loguru integration.

DESIGN.md §17.3.

Wraps :class:`AuditRepository` and adds two ergonomic concerns:

  1. Every audit entry is also emitted as a Loguru event with
     ``audit=True`` extra and structured fields. This means a single
     ``AuditService.log()`` call both persists the action and writes
     it to the configured Loguru sinks (stderr, file, etc.).

  2. A ``bind()`` helper returns a Loguru-style child logger preloaded
     with actor/action context, useful inside long-running services
     that emit many audit events for the same actor.

Design intent: the AuditRepository is the source of truth for forensic
queries; Loguru is the operational stream. This service makes them
move together so we never write one without the other.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from curator.models.audit import AuditEntry
from curator.storage.repositories.audit_repo import AuditRepository


class AuditService:
    """Append + observe audit events.

    Args:
        audit_repo: the persistent audit-log repository.
    """

    def __init__(self, audit_repo: AuditRepository):
        self.repo = audit_repo

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
        """Append an audit entry. Returns the assigned ``audit_id``.

        Also emits a Loguru ``info``-level event with ``audit=True`` so
        log sinks that filter on this can capture only audit events.
        """
        details = details or {}
        audit_id = self.repo.log(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            when=when,
        )

        # Stream to Loguru for operational visibility. The "audit=True"
        # extra is the marker; downstream config can route audit events
        # to a dedicated sink/file if desired.
        logger.bind(
            audit=True,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            audit_id=audit_id,
            **details,
        ).info("{actor} performed {action}", actor=actor, action=action)
        return audit_id

    def insert(self, entry: AuditEntry) -> int:
        """Insert a pre-built :class:`AuditEntry`. Use ``log()`` for ergonomic call sites."""
        audit_id = self.repo.insert(entry)
        logger.bind(audit=True, **entry.details).info(
            "{actor} performed {action}", actor=entry.actor, action=entry.action,
        )
        return audit_id

    def bind(self, *, actor: str, **default_details: Any) -> "_BoundAuditLogger":
        """Return a logger bound to a specific actor and default details.

        Convenient inside services that emit many audit events with the
        same actor::

            audit = service.bind(actor="curator.scan")
            audit("started", source_id="local", root="/tmp")
            audit("completed", files_seen=42)
        """
        return _BoundAuditLogger(self, actor=actor, default_details=default_details)


class _BoundAuditLogger:
    """Helper returned by :meth:`AuditService.bind` for repeat-call ergonomics."""

    __slots__ = ("_service", "_actor", "_defaults")

    def __init__(self, service: AuditService, *, actor: str, default_details: dict[str, Any]):
        self._service = service
        self._actor = actor
        self._defaults = default_details

    def __call__(
        self,
        action: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        **details: Any,
    ) -> int:
        merged = {**self._defaults, **details}
        return self._service.log(
            actor=self._actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=merged,
        )
