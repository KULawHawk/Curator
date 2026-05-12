"""AuditEntry — a logged action.

DESIGN.md §3.7 and §17.

Every meaningful action in Curator is logged: scans, classifications,
lineage detections, bundle changes, trash/restore, rule applications,
config changes, plugin loads. The audit log is append-only and serves
as the forensic trail Constitution-governed deployments need.
"""

from __future__ import annotations

from datetime import datetime
from curator._compat.datetime import utcnow_naive
from typing import Any

from pydantic import Field

from curator.models.base import CuratorEntity


def _utcnow() -> datetime:
    return utcnow_naive()


class AuditEntry(CuratorEntity):
    """A single logged action.

    Fields:
      * ``audit_id``: auto-incremented by SQLite. Set to ``-1`` before insert
        to indicate "not yet persisted"; the repository fills in the real
        value after INSERT.
      * ``actor``: who took the action — ``'user'``, ``'auto'``,
        ``'plugin:<name>'``, ``'service'``, or a custom identifier.
      * ``action``: what was done (free-form string; conventional values
        are documented in DESIGN.md §17.2).
      * ``entity_type`` / ``entity_id``: optional reference to the affected
        entity (``'file'`` / curator_id, ``'bundle'`` / bundle_id, etc.).
      * ``details``: structured action-specific data (serialized as JSON).
    """

    audit_id: int = Field(default=-1, description="Set by repository after INSERT")
    occurred_at: datetime = Field(default_factory=_utcnow)
    actor: str
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
