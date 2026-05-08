"""MigrationJob + MigrationProgress -- persistent records for Tracer.

DESIGN_PHASE_DELTA.md §M.4 / docs/TRACER_PHASE_2_DESIGN.md §4.

These are the persistent counterparts to the in-memory types in
``curator.services.migration`` (``MigrationPlan`` / ``MigrationReport`` /
``MigrationMove``). The transient types stay in services for the
plan-and-apply-it-once Phase 1 path; the persistent types in this
module power Phase 2's resumable, worker-pool-able, GUI-trackable
migrations.

Why two state machines (status vs outcome)?

  * ``status`` is the OPERATIONAL state: can a worker pick this up?
    Pending → in_progress → terminal.
  * ``outcome`` is the RESULT enum: what actually happened? Mirrors
    :class:`curator.services.migration.MigrationOutcome` -- moved /
    skipped_not_safe / skipped_collision / skipped_db_guard /
    hash_mismatch / failed.

A row with ``status='completed', outcome='moved'`` is a healthy success.
``status='completed', outcome='hash_mismatch'`` is a verified-and-recorded
failure (we tried, the hash didn't match, source intact, dst removed).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from curator.models.base import CuratorEntity


# Job-level statuses (top of the migration_jobs table).
MigrationJobStatus = Literal[
    "queued",      # plan persisted, run_job not yet called
    "running",     # workers actively pulling pending rows
    "completed",   # all rows reached a terminal state with no failures
    "failed",      # top-level fatal error (e.g., write hook missing); apply may not have run
    "cancelled",   # abort_job called
    "partial",    # job ran to completion but some per-file rows had outcome in (failed, hash_mismatch)
]


# Per-file operational statuses (migration_progress table).
MigrationProgressStatus = Literal[
    "pending",     # awaiting a worker
    "in_progress", # claimed by a worker; mid-execution
    "completed",   # terminal; per-file work succeeded (outcome will be 'moved' or 'skipped_*')
    "skipped",     # terminal; gate refused (CAUTION/REFUSE/collision/db_guard)
    "failed",     # terminal; per-file work hit an exception or hash mismatch
]


class MigrationJob(CuratorEntity):
    """A migration operation, tracked in the ``migration_jobs`` table."""

    job_id: UUID = Field(default_factory=uuid4)
    src_source_id: str
    src_root: str
    dst_source_id: str
    dst_root: str
    status: str = Field(default="queued")
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Flag values used to create this job (workers, verify_hash, "
                    "ext, include, exclude, source_action, include_caution, etc).",
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None
    files_total: int = Field(default=0, ge=0)
    files_copied: int = Field(default=0, ge=0)
    files_skipped: int = Field(default=0, ge=0)
    files_failed: int = Field(default=0, ge=0)
    bytes_copied: int = Field(default=0, ge=0)
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
        return self.status in {"completed", "failed", "cancelled", "partial"}

    @property
    def is_same_source(self) -> bool:
        """True if this is a same-source migration (Phase 1 capable)."""
        return self.src_source_id == self.dst_source_id


class MigrationProgress(CuratorEntity):
    """Per-file row in the ``migration_progress`` table.

    Composite primary key is ``(job_id, curator_id)``. The same file can
    appear in MULTIPLE migration jobs (e.g., after a failed/cancelled job
    is retried as a new job_id), but exactly once per job.
    """

    job_id: UUID
    curator_id: UUID
    src_path: str
    dst_path: str
    src_xxhash: str | None = None
    verified_xxhash: str | None = None
    size: int = Field(default=0, ge=0)
    safety_level: str = Field(
        description="Safety verdict at plan time: 'safe' | 'caution' | 'refuse'.",
    )
    status: str = Field(default="pending")
    outcome: str | None = Field(
        default=None,
        description="MigrationOutcome.value once status is terminal; else None.",
    )
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        """True if the row's operational status is final."""
        return self.status in {"completed", "skipped", "failed"}

    @property
    def is_pending(self) -> bool:
        """True if a worker can still claim this row."""
        return self.status == "pending"

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


__all__ = [
    "MigrationJob",
    "MigrationJobStatus",
    "MigrationProgress",
    "MigrationProgressStatus",
]
