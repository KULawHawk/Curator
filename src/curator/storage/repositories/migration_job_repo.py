"""Repository for :class:`MigrationJob` + :class:`MigrationProgress`.

DESIGN_PHASE_DELTA.md §M.4 / docs/TRACER_PHASE_2_DESIGN.md §4.

Tracer Phase 2 persistent state. Mirrors the pattern of
:class:`ScanJobRepository` for the job rows; adds per-progress-row
methods that workers and resume use.

Critical method: :meth:`next_pending_progress` is the atomic-claim
primitive that lets multiple workers share a job without double-claiming
the same row. It uses ``BEGIN IMMEDIATE`` to acquire SQLite's reserved
lock, runs SELECT + UPDATE as a single transaction, and returns the
claimed row (or None if no pending rows remain).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from curator.models.migration import MigrationJob, MigrationProgress
from curator.storage.connection import CuratorDB
from curator.storage.repositories._helpers import (
    json_dumps,
    json_loads,
    str_to_uuid,
    uuid_to_str,
)


class MigrationJobRepository:
    """CRUD for migration jobs + their per-file progress rows."""

    def __init__(self, db: CuratorDB):
        self.db = db

    # ------------------------------------------------------------------
    # Job-level mutations
    # ------------------------------------------------------------------

    def insert_job(self, job: MigrationJob) -> None:
        """Insert a new ``migration_jobs`` row. Caller is responsible
        for calling :meth:`seed_progress_rows` afterwards if the plan
        is non-empty."""
        with self.db.conn() as conn:
            conn.execute(
                """
                INSERT INTO migration_jobs (
                    job_id, src_source_id, src_root,
                    dst_source_id, dst_root, status, options_json,
                    started_at, completed_at,
                    files_total, files_copied, files_skipped, files_failed,
                    bytes_copied, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid_to_str(job.job_id),
                    job.src_source_id, job.src_root,
                    job.dst_source_id, job.dst_root,
                    job.status, json_dumps(job.options),
                    job.started_at, job.completed_at,
                    job.files_total, job.files_copied,
                    job.files_skipped, job.files_failed,
                    job.bytes_copied, job.error,
                ),
            )

    def update_job_status(
        self,
        job_id: UUID,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        """Quick status update with appropriate timestamp side effects.

        ``running`` populates ``started_at`` if not already set.
        Terminal statuses (completed / failed / cancelled / partial)
        populate ``completed_at`` and the error if provided.
        """
        with self.db.conn() as conn:
            if status in ("completed", "failed", "cancelled", "partial"):
                conn.execute(
                    """
                    UPDATE migration_jobs
                    SET status = ?, completed_at = ?, error = ?
                    WHERE job_id = ?
                    """,
                    (status, datetime.utcnow(), error, uuid_to_str(job_id)),
                )
            elif status == "running":
                conn.execute(
                    """
                    UPDATE migration_jobs
                    SET status = ?,
                        started_at = COALESCE(started_at, ?)
                    WHERE job_id = ?
                    """,
                    (status, datetime.utcnow(), uuid_to_str(job_id)),
                )
            else:
                conn.execute(
                    "UPDATE migration_jobs SET status = ? WHERE job_id = ?",
                    (status, uuid_to_str(job_id)),
                )

    def increment_job_counts(
        self,
        job_id: UUID,
        *,
        copied: int = 0,
        skipped: int = 0,
        failed: int = 0,
        bytes_copied: int = 0,
    ) -> None:
        """Increment job-level rollup counters atomically.

        Workers call this after each per-file outcome so the GUI's
        progress bar can read fresh counters without iterating the
        progress table.
        """
        with self.db.conn() as conn:
            conn.execute(
                """
                UPDATE migration_jobs SET
                    files_copied = files_copied + ?,
                    files_skipped = files_skipped + ?,
                    files_failed = files_failed + ?,
                    bytes_copied = bytes_copied + ?
                WHERE job_id = ?
                """,
                (copied, skipped, failed, bytes_copied, uuid_to_str(job_id)),
            )

    def set_files_total(self, job_id: UUID, files_total: int) -> None:
        """Set the planned-files-count at job creation time."""
        with self.db.conn() as conn:
            conn.execute(
                "UPDATE migration_jobs SET files_total = ? WHERE job_id = ?",
                (files_total, uuid_to_str(job_id)),
            )

    def delete_job(self, job_id: UUID) -> None:
        """Delete a job and (via FK ON DELETE CASCADE) all its progress rows."""
        with self.db.conn() as conn:
            conn.execute(
                "DELETE FROM migration_jobs WHERE job_id = ?",
                (uuid_to_str(job_id),),
            )

    # ------------------------------------------------------------------
    # Job-level reads
    # ------------------------------------------------------------------

    def get_job(self, job_id: UUID) -> MigrationJob | None:
        cursor = self.db.conn().execute(
            "SELECT * FROM migration_jobs WHERE job_id = ?",
            (uuid_to_str(job_id),),
        )
        row = cursor.fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[MigrationJob]:
        """Most-recent jobs first.

        ``status=None`` returns ALL statuses; pass e.g. ``status='running'``
        to filter. ``limit`` caps the result size; pass a large number
        for an effective "all."
        """
        if status is None:
            cursor = self.db.conn().execute(
                """
                SELECT * FROM migration_jobs
                ORDER BY COALESCE(started_at, completed_at) DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            cursor = self.db.conn().execute(
                """
                SELECT * FROM migration_jobs WHERE status = ?
                ORDER BY COALESCE(started_at, completed_at) DESC
                LIMIT ?
                """,
                (status, limit),
            )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Progress mutations
    # ------------------------------------------------------------------

    def seed_progress_rows(
        self,
        job_id: UUID,
        rows: list[MigrationProgress],
    ) -> None:
        """Bulk insert pending progress rows. One transaction per call.

        Caller is responsible for ensuring ``job_id`` matches the
        ``MigrationProgress.job_id`` of every row (we don't enforce
        because that's wasted work for the common case where the
        caller just constructed all the rows).
        """
        if not rows:
            return
        params = [
            (
                uuid_to_str(r.job_id),
                uuid_to_str(r.curator_id),
                r.src_path,
                r.dst_path,
                r.src_xxhash,
                r.verified_xxhash,
                r.size,
                r.safety_level,
                r.status,
                r.outcome,
                r.error,
                r.started_at,
                r.completed_at,
            )
            for r in rows
        ]
        with self.db.conn() as conn:
            conn.executemany(
                """
                INSERT INTO migration_progress (
                    job_id, curator_id, src_path, dst_path,
                    src_xxhash, verified_xxhash, size, safety_level,
                    status, outcome, error,
                    started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )

    def next_pending_progress(self, job_id: UUID) -> MigrationProgress | None:
        """Atomically claim the next pending row for a worker.

        Implementation: a single transaction with BEGIN IMMEDIATE
        acquires SQLite's reserved lock. SELECT one row with
        ``status='pending'``, then UPDATE that row's ``status`` to
        ``'in_progress'`` + populate ``started_at``. Return the claimed
        row.

        If no pending rows exist, returns None and rolls back cleanly.

        This is the workhorse for worker concurrency. Multiple workers
        calling this concurrently get distinct rows (or None when the
        queue is empty); never the same row.
        """
        with self.db.conn() as conn:
            # BEGIN IMMEDIATE acquires the reserved lock before any other
            # writer. Without this, two readers could both SELECT the
            # same row, then race on UPDATE; this avoids that.
            try:
                conn.execute("BEGIN IMMEDIATE")
            except Exception:
                # Already in a transaction (test harness or nested usage);
                # carry on. The outer transaction provides isolation.
                pass

            cursor = conn.execute(
                """
                SELECT * FROM migration_progress
                WHERE job_id = ? AND status = 'pending'
                ORDER BY src_path ASC
                LIMIT 1
                """,
                (uuid_to_str(job_id),),
            )
            row = cursor.fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None

            now = datetime.utcnow()
            conn.execute(
                """
                UPDATE migration_progress
                SET status = 'in_progress', started_at = ?
                WHERE job_id = ? AND curator_id = ?
                """,
                (now, row["job_id"], row["curator_id"]),
            )
            conn.execute("COMMIT")

            # Construct the claimed row with its fresh started_at
            progress = self._row_to_progress(row)
            progress.status = "in_progress"
            progress.started_at = now
            return progress

    def update_progress(
        self,
        job_id: UUID,
        curator_id: UUID,
        *,
        status: str,
        outcome: str | None = None,
        error: str | None = None,
        verified_xxhash: str | None = None,
        src_xxhash: str | None = None,
    ) -> None:
        """Update a per-file row's terminal state.

        ``status`` should typically be 'completed', 'skipped', or
        'failed'. Setting ``status`` to a terminal value populates
        ``completed_at``. Pass any subset of the optional fields to
        update them; un-passed fields are left as-is.
        """
        with self.db.conn() as conn:
            sets = ["status = ?"]
            params: list = [status]

            if outcome is not None:
                sets.append("outcome = ?"); params.append(outcome)
            if error is not None:
                sets.append("error = ?"); params.append(error)
            if verified_xxhash is not None:
                sets.append("verified_xxhash = ?"); params.append(verified_xxhash)
            if src_xxhash is not None:
                sets.append("src_xxhash = ?"); params.append(src_xxhash)

            if status in ("completed", "skipped", "failed"):
                sets.append("completed_at = ?"); params.append(datetime.utcnow())

            params.extend([uuid_to_str(job_id), uuid_to_str(curator_id)])

            conn.execute(
                f"""
                UPDATE migration_progress
                SET {', '.join(sets)}
                WHERE job_id = ? AND curator_id = ?
                """,
                tuple(params),
            )

    def reset_in_progress_to_pending(self, job_id: UUID) -> int:
        """Resume helper: rows left as 'in_progress' from a dead worker
        are returned to 'pending' so a fresh worker can pick them up.

        Per docs/TRACER_PHASE_2_DESIGN.md §5.4: rows are marked
        'completed' AFTER the FileEntity update but BEFORE the trash
        step. So an 'in_progress' row at resume time means the index
        update did NOT happen for that file -- safe to redo.

        Returns the number of rows reset.
        """
        with self.db.conn() as conn:
            cursor = conn.execute(
                """
                UPDATE migration_progress
                SET status = 'pending', started_at = NULL
                WHERE job_id = ? AND status = 'in_progress'
                """,
                (uuid_to_str(job_id),),
            )
            return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # Progress reads
    # ------------------------------------------------------------------

    def get_progress(
        self, job_id: UUID, curator_id: UUID,
    ) -> MigrationProgress | None:
        cursor = self.db.conn().execute(
            """
            SELECT * FROM migration_progress
            WHERE job_id = ? AND curator_id = ?
            """,
            (uuid_to_str(job_id), uuid_to_str(curator_id)),
        )
        row = cursor.fetchone()
        return self._row_to_progress(row) if row else None

    def query_progress(
        self,
        job_id: UUID,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[MigrationProgress]:
        """List progress rows for a job, optionally filtered by status."""
        sql = "SELECT * FROM migration_progress WHERE job_id = ?"
        params: list = [uuid_to_str(job_id)]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY src_path ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = self.db.conn().execute(sql, tuple(params))
        return [self._row_to_progress(row) for row in cursor.fetchall()]

    def count_progress_by_status(self, job_id: UUID) -> dict[str, int]:
        """Return ``{status: count}`` for all progress rows in a job.

        Useful for ``curator migrate --status <job_id>`` rendering and
        for the GUI's progress histogram.
        """
        cursor = self.db.conn().execute(
            """
            SELECT status, COUNT(*) AS n
            FROM migration_progress
            WHERE job_id = ?
            GROUP BY status
            """,
            (uuid_to_str(job_id),),
        )
        return {row["status"]: row["n"] for row in cursor.fetchall()}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_job(self, row) -> MigrationJob:
        return MigrationJob(
            job_id=str_to_uuid(row["job_id"]),
            src_source_id=row["src_source_id"],
            src_root=row["src_root"],
            dst_source_id=row["dst_source_id"],
            dst_root=row["dst_root"],
            status=row["status"],
            options=json_loads(row["options_json"]) or {},
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            files_total=row["files_total"],
            files_copied=row["files_copied"],
            files_skipped=row["files_skipped"],
            files_failed=row["files_failed"],
            bytes_copied=row["bytes_copied"],
            error=row["error"],
        )

    def _row_to_progress(self, row) -> MigrationProgress:
        return MigrationProgress(
            job_id=str_to_uuid(row["job_id"]),
            curator_id=str_to_uuid(row["curator_id"]),
            src_path=row["src_path"],
            dst_path=row["dst_path"],
            src_xxhash=row["src_xxhash"],
            verified_xxhash=row["verified_xxhash"],
            size=row["size"],
            safety_level=row["safety_level"],
            status=row["status"],
            outcome=row["outcome"],
            error=row["error"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )


__all__ = ["MigrationJobRepository"]
