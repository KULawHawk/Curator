"""Repository for :class:`ScanJob`.

DESIGN.md §4.5 / §3.9.

Scan jobs are tracked even in CLI mode for resumability and progress
visibility. Service mode uses them for async polling endpoints.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from curator.models.jobs import ScanJob
from curator.storage.connection import CuratorDB
from curator.storage.repositories._helpers import json_dumps, json_loads, str_to_uuid, uuid_to_str


class ScanJobRepository:
    """CRUD for scan jobs."""

    def __init__(self, db: CuratorDB):
        self.db = db

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def insert(self, job: ScanJob) -> None:
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT INTO scan_jobs (
                    job_id, status, source_id, root_path, options_json,
                    started_at, completed_at, files_seen, files_hashed, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid_to_str(job.job_id),
                    job.status,
                    job.source_id,
                    job.root_path,
                    json_dumps(job.options),
                    job.started_at,
                    job.completed_at,
                    job.files_seen,
                    job.files_hashed,
                    job.error,
                ),
            )

    def update(self, job: ScanJob) -> None:
        with self.db.conn() as conn:
            conn.execute(
                """
                UPDATE scan_jobs SET
                    status = ?, source_id = ?, root_path = ?, options_json = ?,
                    started_at = ?, completed_at = ?,
                    files_seen = ?, files_hashed = ?, error = ?
                WHERE job_id = ?
                """,
                (
                    job.status,
                    job.source_id,
                    job.root_path,
                    json_dumps(job.options),
                    job.started_at,
                    job.completed_at,
                    job.files_seen,
                    job.files_hashed,
                    job.error,
                    uuid_to_str(job.job_id),
                ),
            )

    def update_status(self, job_id: UUID, status: str, *, error: str | None = None) -> None:
        """Quick status update without rewriting the whole row."""
        with self.db.conn() as conn:
            if status in ("completed", "failed", "cancelled"):
                conn.execute(
                    """
                    UPDATE scan_jobs SET status = ?, completed_at = ?, error = ?
                    WHERE job_id = ?
                    """,
                    (status, datetime.utcnow(), error, uuid_to_str(job_id)),
                )
            elif status == "running":
                conn.execute(
                    "UPDATE scan_jobs SET status = ?, started_at = ? WHERE job_id = ?",
                    (status, datetime.utcnow(), uuid_to_str(job_id)),
                )
            else:
                conn.execute(
                    "UPDATE scan_jobs SET status = ? WHERE job_id = ?",
                    (status, uuid_to_str(job_id)),
                )

    def update_counters(self, job_id: UUID, *, files_seen: int, files_hashed: int) -> None:
        """Increment-style counter update for progress reporting."""
        with self.db.conn() as conn:
            conn.execute(
                "UPDATE scan_jobs SET files_seen = ?, files_hashed = ? WHERE job_id = ?",
                (files_seen, files_hashed, uuid_to_str(job_id)),
            )

    def delete(self, job_id: UUID) -> None:
        with self.db.conn() as conn:
            conn.execute("DELETE FROM scan_jobs WHERE job_id = ?", (uuid_to_str(job_id),))

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, job_id: UUID) -> ScanJob | None:
        cursor = self.db.conn().execute(
            "SELECT * FROM scan_jobs WHERE job_id = ?", (uuid_to_str(job_id),)
        )
        row = cursor.fetchone()
        return self._row_to_job(row) if row else None

    def list_recent(self, *, limit: int = 50) -> list[ScanJob]:
        """Most recent jobs first (by started_at, falling back to completed_at)."""
        cursor = self.db.conn().execute(
            """
            SELECT * FROM scan_jobs
            ORDER BY COALESCE(started_at, completed_at) DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def list_by_status(self, status: str) -> list[ScanJob]:
        cursor = self.db.conn().execute(
            "SELECT * FROM scan_jobs WHERE status = ? ORDER BY started_at DESC",
            (status,),
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_job(self, row) -> ScanJob:
        return ScanJob(
            job_id=str_to_uuid(row["job_id"]),
            status=row["status"],
            source_id=row["source_id"],
            root_path=row["root_path"],
            options=json_loads(row["options_json"]) or {},
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            files_seen=row["files_seen"],
            files_hashed=row["files_hashed"],
            error=row["error"],
        )
