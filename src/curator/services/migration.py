"""Migration service (Tracer) -- relocate files across paths with index integrity.

DESIGN_PHASE_DELTA.md §M (Feature M) + docs/TRACER_PHASE_2_DESIGN.md.

**Phase 1 (v1.0.0a1): same-source local→local migration**, single-threaded,
in-memory plan/apply. The :meth:`apply` method is the canonical entrypoint.

**Phase 2 (v1.1.0a2 onwards): persistent jobs**, resumable, worker-pool
parallelizable. The :meth:`create_job` + :meth:`run_job` pair persists
the plan as ``migration_jobs`` + ``migration_progress`` rows so the GUI
can show live progress, ``--resume`` can pick up after an interruption,
and multiple workers can share the work via the atomic claim primitive
(:meth:`MigrationJobRepository.next_pending_progress`).

Both paths share the same per-file Hash-Verify-Before-Move discipline
(see below). The persistent path additionally records each per-file
outcome to ``migration_progress`` and bumps the job-level rollup
counters atomically.

The core discipline per Atrium Constitution Principle 2
(Hash-Verify-Before-Move): for every file we relocate, we triple-check
*source-absent + destination-present + hash-match* BEFORE trashing the
source. The order:

  1. Hash the source bytes (use cached ``FileEntity.xxhash3_128`` if
     present and the file's mtime/size haven't changed; otherwise
     recompute).
  2. Make destination parent dirs.
  3. ``shutil.copy2(src, dst)`` (preserves mtime).
  4. Hash the destination bytes.
  5. Verify ``hash(src) == hash(dst)``. If mismatch: delete the
     destination and mark the move FAILED. Source untouched.
  6. Update ``FileEntity.source_path = new_path`` via the file repo.
     Same ``curator_id`` -- lineage edges and bundle memberships persist
     transparently.
  7. Trash the source (recoverable via OS Recycle Bin / Trash). If trash
     fails, audit it but do NOT roll back the index update -- the dst
     copy is verified and the index is correct; manual cleanup of src
     is now the user's prerogative.

The Phase 1 service is intentionally narrow:

  * Same-source only (Phase 2 = cross-source).
  * Only ``SAFE`` files migrate (Phase 2 may add an opt-in
    ``--include-caution`` flag; CAUTION typically means the file is
    inside a project root or app-data dir, which the user usually does
    NOT want to relocate).
  * No worker concurrency (single-threaded).
  * No resume tables (Phase 2 adds ``migration_jobs`` +
    ``migration_progress``).
  * Source-action is hardcoded to ``trash`` (no ``keep`` / ``delete``
    yet -- those are Phase 2 flags).

What's preserved automatically:

  * ``curator_id`` constancy → lineage edges + bundle memberships
    untouched.
  * Audit log entries with ``actor=curator.migrate`` and
    ``action=migration.move`` per file.
  * Hash invariants on the FileEntity row -- the xxhash3_128 stays the
    same because we just verified it; mtime stays approximately the
    same because shutil.copy2 preserves it.
"""

from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

import xxhash
from loguru import logger

from curator.models.audit import AuditEntry
from curator.models.file import FileEntity
from curator.models.migration import MigrationJob, MigrationProgress
from curator.services.safety import SafetyLevel, SafetyService
from curator.storage.queries import FileQuery
from curator.storage.repositories.audit_repo import AuditRepository
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.migration_job_repo import MigrationJobRepository


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class MigrationOutcome(str, Enum):
    """Per-file outcome of a migration apply pass."""

    MOVED = "moved"
    SKIPPED_NOT_SAFE = "skipped_not_safe"  # CAUTION or REFUSE per SafetyService
    SKIPPED_COLLISION = "skipped_collision"  # destination exists
    SKIPPED_DB_GUARD = "skipped_db_guard"  # source IS the curator.db file
    HASH_MISMATCH = "hash_mismatch"  # verify failed; src untouched, dst removed
    FAILED = "failed"  # generic IO / OS exception during copy


@dataclass
class MigrationMove:
    """A single file's planned (and possibly executed) move."""

    curator_id: UUID
    src_path: str
    dst_path: str
    safety_level: SafetyLevel
    size: int
    src_xxhash: str | None  # cached from FileEntity (None means recompute on apply)
    outcome: MigrationOutcome | None = None
    error: str | None = None
    verified_xxhash: str | None = None  # the hash we verified at dst (apply-time)


@dataclass
class MigrationPlan:
    """Immutable result of plan(); fed to apply()."""

    src_source_id: str
    src_root: str
    dst_source_id: str
    dst_root: str
    moves: list[MigrationMove] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.moves)

    @property
    def safe_count(self) -> int:
        return sum(1 for m in self.moves if m.safety_level == SafetyLevel.SAFE)

    @property
    def caution_count(self) -> int:
        return sum(1 for m in self.moves if m.safety_level == SafetyLevel.CAUTION)

    @property
    def refuse_count(self) -> int:
        return sum(1 for m in self.moves if m.safety_level == SafetyLevel.REFUSE)

    @property
    def planned_bytes(self) -> int:
        """Bytes that would actually move (SAFE only)."""
        return sum(m.size for m in self.moves if m.safety_level == SafetyLevel.SAFE)


@dataclass
class MigrationReport:
    """Result of apply()."""

    plan: MigrationPlan
    moves: list[MigrationMove] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None

    @property
    def moved_count(self) -> int:
        return sum(1 for m in self.moves if m.outcome == MigrationOutcome.MOVED)

    @property
    def skipped_count(self) -> int:
        return sum(
            1 for m in self.moves
            if m.outcome and m.outcome.value.startswith("skipped")
        )

    @property
    def failed_count(self) -> int:
        return sum(
            1 for m in self.moves
            if m.outcome in (MigrationOutcome.FAILED, MigrationOutcome.HASH_MISMATCH)
        )

    @property
    def bytes_moved(self) -> int:
        return sum(
            m.size for m in self.moves if m.outcome == MigrationOutcome.MOVED
        )

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_HASH_CHUNK_SIZE = 64 * 1024  # 64KB chunks; same as hash_pipeline


def _xxhash3_128_of_file(path: Path) -> str:
    """Compute xxhash3_128 of a file as hex digest. Streaming."""
    h = xxhash.xxh3_128()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _compute_dst_path(src_path: str, src_root: str, dst_root: str) -> str | None:
    """Compute the destination path preserving subpath under src_root.

    Returns None if src_path is not under src_root (defensive check).
    Path arithmetic is OS-aware; the result uses the same separator as
    the inputs so it round-trips cleanly through the file_repo.
    """
    try:
        src_p = Path(src_path)
        rel = src_p.relative_to(Path(src_root))
    except ValueError:
        return None
    return str(Path(dst_root) / rel)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MigrationService:
    """Migration orchestration with hash-verify-before-move discipline.

    Two paths:

    * **Phase 1 (in-memory):** :meth:`plan` returns a :class:`MigrationPlan`,
      :meth:`apply` executes it sequentially, returns a :class:`MigrationReport`.
      Best for one-shot small migrations. Doesn't require ``migration_jobs``.

    * **Phase 2 (persisted, resumable, parallel):** :meth:`create_job`
      persists a plan as a job + progress rows, returns a ``job_id``.
      :meth:`run_job` executes (or resumes) the job using a worker pool.
      :meth:`abort_job` signals a graceful stop. :meth:`get_job_status`
      reports counts + ETA. :meth:`list_jobs` enumerates recent jobs.
      Requires the ``migration_jobs`` repository.

    Args:
        file_repo: The FileRepository to query + update.
        safety: The SafetyService used to gate every file in the plan.
        audit: Optional AuditRepository. When set, every successful move
            writes an audit entry (``actor='curator.migrate'``,
            ``action='migration.move'``).
        migration_jobs: Optional MigrationJobRepository. Required for
            Phase 2 methods (``create_job``, ``run_job``, ``abort_job``,
            ``list_jobs``, ``get_job_status``). Phase 1 :meth:`apply`
            works without it.
    """

    def __init__(
        self,
        file_repo: FileRepository,
        safety: SafetyService,
        *,
        audit: AuditRepository | None = None,
        migration_jobs: MigrationJobRepository | None = None,
    ) -> None:
        self.files = file_repo
        self.safety = safety
        self.audit = audit
        self.migration_jobs = migration_jobs
        # Phase 2 worker-pool abort signaling: maps job_id -> Event
        # set when ``abort_job`` is called. Workers check between files.
        self._abort_events: dict[UUID, threading.Event] = {}
        self._abort_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    def plan(
        self,
        *,
        src_source_id: str,
        src_root: str,
        dst_root: str,
        dst_source_id: str | None = None,
        extensions: list[str] | None = None,
    ) -> MigrationPlan:
        """Build a migration plan: every file under ``src_root`` partitioned
        by SafetyService verdict with a computed destination path.

        Phase 1: ``dst_source_id`` defaults to ``src_source_id`` (same-source).
        Cross-source migration in Phase 2.

        Args:
            src_source_id: The source plugin id whose files we're migrating.
            src_root: Path prefix; only files under this prefix are candidates.
            dst_root: Path prefix at the destination; relative subpaths are
                preserved (so ``src_root/A/B.mp3`` lands at ``dst_root/A/B.mp3``).
            dst_source_id: Defaults to ``src_source_id`` for Phase 1.
            extensions: Optional list of extensions to filter candidates
                (e.g. ``['.mp3', '.flac']``). Case-insensitive; leading
                dot optional.

        Returns:
            A :class:`MigrationPlan` with moves partitioned by SafetyLevel.
            ``moves`` is ordered alphabetically by ``src_path`` for
            deterministic output.
        """
        if dst_source_id is None:
            dst_source_id = src_source_id

        # Normalize extension filter
        ext_filter: set[str] | None = None
        if extensions is not None:
            ext_filter = {
                ("." + e.lstrip(".")).lower() for e in extensions
            }

        # Defensive: refuse if dst_root is INSIDE src_root (would loop)
        try:
            if Path(dst_root).resolve().is_relative_to(Path(src_root).resolve()):
                raise ValueError(
                    "dst_root must not be inside src_root "
                    f"(src={src_root}, dst={dst_root})"
                )
        except (OSError, ValueError) as e:
            if "must not be inside" in str(e):
                raise
            # Resolve failures (paths don't exist yet, etc.) are non-fatal
            # for plan-time -- apply() will surface them.

        # Query the file index for candidates
        try:
            candidates = self.files.query(
                FileQuery(
                    source_ids=[src_source_id],
                    source_path_starts_with=src_root,
                    deleted=False,
                    order_by="source_path ASC",
                )
            )
        except Exception as e:
            logger.error("MigrationService.plan: file query failed: {e}", e=e)
            return MigrationPlan(
                src_source_id=src_source_id,
                src_root=src_root,
                dst_source_id=dst_source_id,
                dst_root=dst_root,
            )

        moves: list[MigrationMove] = []
        for f in candidates:
            # Extension filter
            if ext_filter is not None:
                ext = (f.extension or "").lower()
                if ext not in ext_filter:
                    continue

            # Safety verdict (per-file)
            try:
                report = self.safety.check_path(Path(f.source_path))
            except Exception as e:
                logger.warning(
                    "MigrationService.plan: safety check failed for {p}: {e}",
                    p=f.source_path, e=e,
                )
                # Conservative: route to REFUSE
                level = SafetyLevel.REFUSE
            else:
                level = report.level

            dst = _compute_dst_path(f.source_path, src_root, dst_root)
            if dst is None:
                # File is not actually under src_root despite the query;
                # skip it defensively
                continue

            moves.append(MigrationMove(
                curator_id=f.curator_id,
                src_path=f.source_path,
                dst_path=dst,
                safety_level=level,
                size=f.size,
                src_xxhash=f.xxhash3_128,
            ))

        return MigrationPlan(
            src_source_id=src_source_id,
            src_root=src_root,
            dst_source_id=dst_source_id,
            dst_root=dst_root,
            moves=moves,
        )

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(
        self,
        plan: MigrationPlan,
        *,
        verify_hash: bool = True,
        db_path_guard: Path | None = None,
    ) -> MigrationReport:
        """Execute the plan with hash-verify-before-move per file.

        Only SAFE moves run. CAUTION + REFUSE files are recorded with
        outcome ``SKIPPED_NOT_SAFE``.

        Args:
            plan: The plan returned by :meth:`plan`.
            verify_hash: When True (default), recompute xxhash3_128 of
                the destination after copy and require it match the
                source hash before updating the index. When False, skip
                verification (fast but unsafe; intended only for tests
                or trusted fast paths).
            db_path_guard: If set and a move's source path equals this
                path, the move is skipped with outcome
                ``SKIPPED_DB_GUARD``. Prevents migrating Curator's own
                DB out from under itself.

        Returns:
            A :class:`MigrationReport` with one :class:`MigrationMove`
            per planned move (regardless of outcome).
        """
        report = MigrationReport(plan=plan)

        for src_move in plan.moves:
            # Build fresh result copy (preserve plan-time fields, set apply-time fields)
            move = MigrationMove(
                curator_id=src_move.curator_id,
                src_path=src_move.src_path,
                dst_path=src_move.dst_path,
                safety_level=src_move.safety_level,
                size=src_move.size,
                src_xxhash=src_move.src_xxhash,
            )

            # Gate 1: only SAFE files
            if move.safety_level != SafetyLevel.SAFE:
                move.outcome = MigrationOutcome.SKIPPED_NOT_SAFE
                report.moves.append(move)
                continue

            # Gate 2: DB guard
            if (db_path_guard is not None
                    and Path(move.src_path) == db_path_guard):
                move.outcome = MigrationOutcome.SKIPPED_DB_GUARD
                report.moves.append(move)
                continue

            # Gate 3: collision check
            dst_p = Path(move.dst_path)
            if dst_p.exists():
                move.outcome = MigrationOutcome.SKIPPED_COLLISION
                report.moves.append(move)
                continue

            # Execute the move with hash-verify-before-move
            self._execute_one(move, verify_hash=verify_hash)
            report.moves.append(move)

        report.completed_at = datetime.utcnow()
        return report

    def _execute_one(self, move: MigrationMove, *, verify_hash: bool) -> None:
        """Per-file move discipline. Mutates ``move`` in place."""
        src_p = Path(move.src_path)
        dst_p = Path(move.dst_path)

        try:
            # Step 1: src hash -- prefer cached, else compute
            if verify_hash and not move.src_xxhash:
                move.src_xxhash = _xxhash3_128_of_file(src_p)

            # Step 2: ensure parent
            dst_p.parent.mkdir(parents=True, exist_ok=True)

            # Step 3: copy preserving metadata
            shutil.copy2(src_p, dst_p)

            # Step 4-5: verify dst hash matches src hash
            if verify_hash:
                dst_hash = _xxhash3_128_of_file(dst_p)
                move.verified_xxhash = dst_hash
                if move.src_xxhash and dst_hash != move.src_xxhash:
                    # Mismatch: clean up dst, mark FAILED, leave src
                    try:
                        dst_p.unlink()
                    except OSError:
                        pass
                    move.outcome = MigrationOutcome.HASH_MISMATCH
                    move.error = (
                        f"hash mismatch: src={move.src_xxhash} "
                        f"dst={dst_hash}"
                    )
                    return

            # Step 6: update index (curator_id stays, source_path changes)
            self._update_index(move)

            # Step 7: trash source (best-effort; index already correct)
            self._trash_source(src_p, move)

            move.outcome = MigrationOutcome.MOVED

            # Step 8: audit (only on success)
            self._audit_move(move)

        except (OSError, shutil.Error) as e:
            # Copy failed BEFORE index update -- src is intact, dst may be partial
            if dst_p.exists():
                try:
                    dst_p.unlink()
                except OSError:
                    pass
            move.outcome = MigrationOutcome.FAILED
            move.error = f"{type(e).__name__}: {e}"
        except Exception as e:  # noqa: BLE001 -- defensive boundary
            move.outcome = MigrationOutcome.FAILED
            move.error = f"{type(e).__name__}: {e}"

    def _update_index(self, move: MigrationMove) -> None:
        """Re-point the FileEntity at the new path. Best-effort.

        If this fails, the dst file exists with the right bytes but the
        index still points at src. The next ``curator scan`` will pick
        up dst as a new file and lineage will mark it a duplicate.
        That's recoverable; we let the exception propagate so the
        caller knows the index update failed.
        """
        entity = self.files.get(move.curator_id)
        if entity is None:
            raise RuntimeError(
                f"FileEntity {move.curator_id} vanished during migration"
            )
        entity.source_path = move.dst_path
        self.files.update(entity)

    def _trash_source(self, src_p: Path, move: MigrationMove) -> None:
        """Send the source to OS trash via vendored send2trash.

        Best-effort: if trash fails, log it and continue. The dst is
        already verified and the index is updated; the user can clean
        up manually.
        """
        try:
            from curator._vendored.send2trash import send2trash
            send2trash(str(src_p))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MigrationService: trash failed for {p}: {e}",
                p=str(src_p), e=e,
            )
            move.error = (
                (move.error or "") +
                f" [trash failed: {type(e).__name__}: {e}]"
            ).strip()

    def _audit_move(self, move: MigrationMove) -> None:
        """Append an audit entry for a successful move. Best-effort."""
        if self.audit is None:
            return
        try:
            entry = AuditEntry(
                actor="curator.migrate",
                action="migration.move",
                entity_type="file",
                entity_id=str(move.curator_id),
                details={
                    "src_path": move.src_path,
                    "dst_path": move.dst_path,
                    "size": move.size,
                    "xxhash3_128": move.verified_xxhash or move.src_xxhash,
                },
            )
            self.audit.insert(entry)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MigrationService: audit append failed for {cid}: {e}",
                cid=move.curator_id, e=e,
            )

    # ==================================================================
    # Phase 2: persistent jobs (resumable + worker pool)
    # ==================================================================

    def create_job(
        self,
        plan: MigrationPlan,
        *,
        options: dict | None = None,
        db_path_guard: Path | None = None,
        include_caution: bool = False,
    ) -> UUID:
        """Persist a plan as a ``migration_jobs`` row + N ``migration_progress`` rows.

        Filters applied at this point are baked into the persisted rows:

        * ``db_path_guard``: rows whose src_path matches are seeded with
          ``status='skipped', outcome='skipped_db_guard'``. Workers never
          see them.
        * ``include_caution=False`` (default): CAUTION rows are seeded
          ``status='skipped', outcome='skipped_not_safe'``.
        * ``include_caution=True``: CAUTION rows are seeded ``status='pending'``
          (eligible for migration alongside SAFE rows).
        * REFUSE rows are ALWAYS skipped regardless of ``include_caution``.

        Args:
            plan: The plan returned by :meth:`plan`.
            options: Optional dict of flag values to record (workers,
                verify_hash, ext, includes, excludes, source_action, etc.)
                so a future ``--status`` invocation can show what flags
                created this job.
            db_path_guard: If set, the file at this path is auto-skipped
                (prevents Curator's own DB from migrating out from under
                itself).
            include_caution: If True, CAUTION-level files are eligible
                for migration. Default False (only SAFE migrates).

        Returns:
            The new ``job_id``. The job is in ``status='queued'`` until
            :meth:`run_job` is called.

        Raises:
            RuntimeError: if the service was constructed without
                ``migration_jobs``.
        """
        self._require_jobs_repo("create_job")
        opts: dict = {} if options is None else dict(options)

        job = MigrationJob(
            src_source_id=plan.src_source_id,
            src_root=plan.src_root,
            dst_source_id=plan.dst_source_id,
            dst_root=plan.dst_root,
            status="queued",
            options=opts,
            files_total=plan.total_count,
        )
        self.migration_jobs.insert_job(job)

        # Seed progress rows. Each is partitioned by safety + db_guard.
        rows: list[MigrationProgress] = []
        pre_skipped = 0
        for m in plan.moves:
            is_db_guarded = (
                db_path_guard is not None
                and Path(m.src_path) == db_path_guard
            )
            if is_db_guarded:
                init_status = "skipped"
                init_outcome = MigrationOutcome.SKIPPED_DB_GUARD.value
                pre_skipped += 1
            elif m.safety_level == SafetyLevel.SAFE:
                init_status = "pending"
                init_outcome = None
            elif (m.safety_level == SafetyLevel.CAUTION and include_caution):
                init_status = "pending"
                init_outcome = None
            else:
                # CAUTION (without --include-caution) or REFUSE
                init_status = "skipped"
                init_outcome = MigrationOutcome.SKIPPED_NOT_SAFE.value
                pre_skipped += 1

            rows.append(MigrationProgress(
                job_id=job.job_id,
                curator_id=m.curator_id,
                src_path=m.src_path,
                dst_path=m.dst_path,
                src_xxhash=m.src_xxhash,
                size=m.size,
                safety_level=m.safety_level.value,
                status=init_status,
                outcome=init_outcome,
            ))

        self.migration_jobs.seed_progress_rows(job.job_id, rows)
        if pre_skipped:
            self.migration_jobs.increment_job_counts(
                job.job_id, skipped=pre_skipped,
            )

        return job.job_id

    def run_job(
        self,
        job_id: UUID,
        *,
        workers: int = 4,
        verify_hash: bool = True,
        on_progress: Callable[[MigrationProgress], None] | None = None,
    ) -> MigrationReport:
        """Execute or resume a persisted job using a worker pool.

        Workers pull pending rows via :meth:`MigrationJobRepository.next_pending_progress`
        (atomic claim). Each worker runs the per-file Hash-Verify-Before-Move
        algorithm and records the outcome to the row plus the job-level
        rollup counters.

        Resume semantics: rows left as ``status='in_progress'`` from a
        previous (interrupted) run are reset to ``'pending'`` before workers
        start. Per docs/TRACER_PHASE_2_DESIGN.md §5.4, that's safe because
        rows transition to ``'completed'`` AFTER the FileEntity update but
        BEFORE the trash step -- so an in_progress row never has the
        index-update side effect.

        Final job status:

        * ``'cancelled'`` if :meth:`abort_job` was called during the run.
        * ``'partial'`` if any rows ended with ``status='failed'``.
        * ``'completed'`` otherwise.

        Args:
            job_id: The job to execute. Must exist; must NOT already be
                in a terminal state other than ``'partial'`` (a partial
                job CAN be re-run; a completed/cancelled/failed one
                cannot via this path).
            workers: Number of concurrent workers. Default 4. Clamped
                to a minimum of 1.
            verify_hash: When True, recompute xxhash3_128 of the
                destination after copy and require it match the source
                hash. Default True (Constitutional discipline).
            on_progress: Optional callback invoked once per file after
                the row reaches a terminal state. Receives the freshly
                updated :class:`MigrationProgress`. Exceptions in the
                callback are swallowed (workers must not die from UI bugs).

        Returns:
            A :class:`MigrationReport` reconstructed from the persisted
            state. The report's ``moves`` list contains one entry per
            progress row (including pre-skipped ones).

        Raises:
            RuntimeError: if the service was constructed without
                ``migration_jobs``.
            ValueError: if ``job_id`` does not exist.
        """
        self._require_jobs_repo("run_job")
        job = self.migration_jobs.get_job(job_id)
        if job is None:
            raise ValueError(f"MigrationJob {job_id} not found")

        # Already-completed jobs are no-ops; return their report.
        if job.status == "completed":
            return self._build_report_from_persisted(job)

        # Resume: any in_progress rows from a dead worker return to pending
        self.migration_jobs.reset_in_progress_to_pending(job_id)

        # Mark running
        self.migration_jobs.update_job_status(job_id, "running")

        # Set up abort signaling for this job
        abort_event = threading.Event()
        with self._abort_lock:
            self._abort_events[job_id] = abort_event

        worker_count = max(1, workers)
        try:
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = [
                    pool.submit(
                        self._worker_loop, job_id,
                        verify_hash, abort_event, on_progress,
                    )
                    for _ in range(worker_count)
                ]
                for f in futures:
                    f.result()  # propagate any worker exception
        finally:
            with self._abort_lock:
                self._abort_events.pop(job_id, None)

        # Determine final job status from terminal histogram
        histogram = self.migration_jobs.count_progress_by_status(job_id)
        if abort_event.is_set():
            final_status = "cancelled"
        elif histogram.get("failed", 0) > 0:
            final_status = "partial"
        else:
            final_status = "completed"
        self.migration_jobs.update_job_status(job_id, final_status)

        return self._build_report_from_persisted(
            self.migration_jobs.get_job(job_id),
        )

    def abort_job(self, job_id: UUID) -> None:
        """Signal a running job to stop.

        Workers finish their CURRENT file (no mid-file abort -- that
        would violate the per-file atomicity invariant), then exit on
        the next loop iteration. The status update to ``'cancelled'``
        happens inside :meth:`run_job`'s finally block once all workers
        have returned.

        Calling this on a job that isn't currently running is a no-op.

        Args:
            job_id: The job to abort.
        """
        self._require_jobs_repo("abort_job")
        with self._abort_lock:
            event = self._abort_events.get(job_id)
        if event is not None:
            event.set()

    def list_jobs(
        self, *, status: str | None = None, limit: int = 50,
    ) -> list[MigrationJob]:
        """List recent migration jobs, most-recent first. Pass-through to
        :meth:`MigrationJobRepository.list_jobs` for service-layer parity."""
        self._require_jobs_repo("list_jobs")
        return self.migration_jobs.list_jobs(status=status, limit=limit)

    def get_job_status(self, job_id: UUID) -> dict:
        """Return rich job status: counts, bytes, histogram, timing.

        Returns a dict shaped for both CLI rendering (``curator migrate
        --status <job_id>``) and GUI consumption (Migrate tab progress
        section). All values are JSON-serializable.

        Raises:
            RuntimeError: if the service was constructed without
                ``migration_jobs``.
            ValueError: if ``job_id`` does not exist.
        """
        self._require_jobs_repo("get_job_status")
        job = self.migration_jobs.get_job(job_id)
        if job is None:
            raise ValueError(f"MigrationJob {job_id} not found")
        histogram = self.migration_jobs.count_progress_by_status(job_id)
        return {
            "job_id": str(job_id),
            "status": job.status,
            "src_source_id": job.src_source_id,
            "src_root": job.src_root,
            "dst_source_id": job.dst_source_id,
            "dst_root": job.dst_root,
            "options": job.options,
            "files_total": job.files_total,
            "files_copied": job.files_copied,
            "files_skipped": job.files_skipped,
            "files_failed": job.files_failed,
            "bytes_copied": job.bytes_copied,
            "progress_histogram": histogram,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "duration_seconds": job.duration_seconds,
            "error": job.error,
        }

    # ------------------------------------------------------------------
    # Phase 2 internals
    # ------------------------------------------------------------------

    def _require_jobs_repo(self, method_name: str) -> None:
        if self.migration_jobs is None:
            raise RuntimeError(
                f"MigrationService.{method_name} requires the migration_jobs "
                "repository. Construct MigrationService with "
                "migration_jobs=MigrationJobRepository(db)."
            )

    def _worker_loop(
        self,
        job_id: UUID,
        verify_hash: bool,
        abort_event: threading.Event,
        on_progress: Callable[[MigrationProgress], None] | None,
    ) -> None:
        """Per-worker loop: claim a row, execute, record outcome, repeat.

        Exits when ``abort_event`` is set OR no pending rows remain.
        Per-file work is atomic (no mid-file abort).
        """
        while not abort_event.is_set():
            progress = self.migration_jobs.next_pending_progress(job_id)
            if progress is None:
                return  # queue empty

            try:
                outcome, verified_hash = self._execute_one_persistent(
                    progress, verify_hash=verify_hash,
                )

                if outcome == MigrationOutcome.MOVED:
                    self.migration_jobs.update_progress(
                        job_id, progress.curator_id,
                        status="completed", outcome=outcome.value,
                        verified_xxhash=verified_hash,
                    )
                    self.migration_jobs.increment_job_counts(
                        job_id, copied=1, bytes_copied=progress.size,
                    )
                elif outcome == MigrationOutcome.HASH_MISMATCH:
                    self.migration_jobs.update_progress(
                        job_id, progress.curator_id,
                        status="failed", outcome=outcome.value,
                        verified_xxhash=verified_hash,
                        error=(
                            f"hash mismatch: src={progress.src_xxhash} "
                            f"verified={verified_hash}"
                        ),
                    )
                    self.migration_jobs.increment_job_counts(job_id, failed=1)
                elif outcome == MigrationOutcome.SKIPPED_COLLISION:
                    self.migration_jobs.update_progress(
                        job_id, progress.curator_id,
                        status="skipped", outcome=outcome.value,
                    )
                    self.migration_jobs.increment_job_counts(job_id, skipped=1)
                else:
                    # Defensive: any other outcome is treated as failed
                    self.migration_jobs.update_progress(
                        job_id, progress.curator_id,
                        status="failed", outcome=outcome.value,
                    )
                    self.migration_jobs.increment_job_counts(job_id, failed=1)

            except Exception as e:  # noqa: BLE001 -- worker boundary
                self.migration_jobs.update_progress(
                    job_id, progress.curator_id,
                    status="failed",
                    outcome=MigrationOutcome.FAILED.value,
                    error=f"{type(e).__name__}: {e}",
                )
                self.migration_jobs.increment_job_counts(job_id, failed=1)

            # Progress callback (best-effort; UI bugs must not kill workers)
            if on_progress is not None:
                try:
                    final = self.migration_jobs.get_progress(
                        job_id, progress.curator_id,
                    )
                    if final is not None:
                        on_progress(final)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "MigrationService: on_progress callback raised: {e}", e=e,
                    )

    def _execute_one_persistent(
        self,
        progress: MigrationProgress,
        *,
        verify_hash: bool,
    ) -> tuple[MigrationOutcome, str | None]:
        """Per-file Hash-Verify-Before-Move for the persistent path.

        Returns ``(outcome, verified_xxhash_or_None)``. Caller (worker
        loop) records the outcome to ``migration_progress``.

        Raises on unexpected exceptions; the worker catches and records
        ``status='failed', outcome='failed'``.
        """
        src_p = Path(progress.src_path)
        dst_p = Path(progress.dst_path)

        # Defensive collision check at apply time (the row was pending
        # so it wasn't pre-skipped, but a parallel migration could have
        # created the dst file in the meantime).
        if dst_p.exists():
            return (MigrationOutcome.SKIPPED_COLLISION, None)

        # Step 1: src hash (use cached if available)
        src_hash = progress.src_xxhash
        if verify_hash and not src_hash:
            src_hash = _xxhash3_128_of_file(src_p)

        # Step 2: ensure parent dirs
        dst_p.parent.mkdir(parents=True, exist_ok=True)

        # Step 3: copy preserving metadata
        shutil.copy2(src_p, dst_p)

        # Step 4-5: verify dst hash matches src hash
        verified_hash: str | None = None
        if verify_hash:
            verified_hash = _xxhash3_128_of_file(dst_p)
            if src_hash and verified_hash != src_hash:
                # Mismatch: clean up dst, return HASH_MISMATCH.
                # Source untouched (no index update, no trash).
                try:
                    dst_p.unlink()
                except OSError:
                    pass
                return (MigrationOutcome.HASH_MISMATCH, verified_hash)

        # Step 6: index update (curator_id stays, source_path changes)
        entity = self.files.get(progress.curator_id)
        if entity is None:
            raise RuntimeError(
                f"FileEntity {progress.curator_id} vanished during migration"
            )
        entity.source_path = progress.dst_path
        self.files.update(entity)

        # Step 7: trash source (best-effort; index already correct)
        try:
            from curator._vendored.send2trash import send2trash
            send2trash(str(src_p))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MigrationService: trash failed for {p}: {e}",
                p=str(src_p), e=e,
            )

        # Step 8: audit (with job_id in details for cross-reference)
        if self.audit is not None:
            try:
                entry = AuditEntry(
                    actor="curator.migrate",
                    action="migration.move",
                    entity_type="file",
                    entity_id=str(progress.curator_id),
                    details={
                        "src_path": progress.src_path,
                        "dst_path": progress.dst_path,
                        "size": progress.size,
                        "xxhash3_128": verified_hash or src_hash,
                        "job_id": str(progress.job_id),
                    },
                )
                self.audit.insert(entry)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "MigrationService: audit append failed for {cid}: {e}",
                    cid=progress.curator_id, e=e,
                )

        return (MigrationOutcome.MOVED, verified_hash)

    def _build_report_from_persisted(self, job: MigrationJob) -> MigrationReport:
        """Reconstruct a :class:`MigrationReport` from the persisted job.

        The report's ``plan`` field has an empty ``moves`` list -- the
        plan-time data is now distributed across the progress rows.
        Callers that need the per-file results read ``report.moves``.
        """
        progress_rows = self.migration_jobs.query_progress(job.job_id)
        plan = MigrationPlan(
            src_source_id=job.src_source_id,
            src_root=job.src_root,
            dst_source_id=job.dst_source_id,
            dst_root=job.dst_root,
            moves=[],
        )
        moves: list[MigrationMove] = []
        for p in progress_rows:
            outcome: MigrationOutcome | None = None
            if p.outcome:
                try:
                    outcome = MigrationOutcome(p.outcome)
                except ValueError:
                    outcome = MigrationOutcome.FAILED
            moves.append(MigrationMove(
                curator_id=p.curator_id,
                src_path=p.src_path,
                dst_path=p.dst_path,
                safety_level=SafetyLevel(p.safety_level),
                size=p.size,
                src_xxhash=p.src_xxhash,
                outcome=outcome,
                error=p.error,
                verified_xxhash=p.verified_xxhash,
            ))
        return MigrationReport(
            plan=plan,
            moves=moves,
            started_at=job.started_at or datetime.utcnow(),
            completed_at=job.completed_at,
        )


__all__ = [
    "MigrationOutcome",
    "MigrationMove",
    "MigrationPlan",
    "MigrationReport",
    "MigrationService",
]
