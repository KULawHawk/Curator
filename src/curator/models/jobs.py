"""ScanJob — a scan operation record.

DESIGN.md §3.9.

Even in CLI mode, scans are tracked as ``ScanJob`` records so that:
  * progress is visible across long-running scans
  * resumption is possible after a crash
  * service mode can return a job_id and let clients poll status
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from curator.models.base import CuratorEntity


ScanStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class ScanJob(CuratorEntity):
    """A scan operation, tracked in the ``scan_jobs`` table."""

    job_id: UUID = Field(default_factory=uuid4)
    status: str = Field(default="queued", description="One of: queued, running, completed, failed, cancelled")
    source_id: str
    root_path: str
    options: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    files_seen: int = Field(default=0, ge=0)
    files_hashed: int = Field(default=0, ge=0)
    error: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock duration if the job has both start and end timestamps."""
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    @property
    def is_terminal(self) -> bool:
        """True if the job is in a final state (won't change without external action)."""
        return self.status in {"completed", "failed", "cancelled"}
