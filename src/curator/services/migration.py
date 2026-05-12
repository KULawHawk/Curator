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
from curator._compat.datetime import utcnow_naive
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, ClassVar
from uuid import UUID, uuid4

import pluggy
import xxhash
from loguru import logger

from curator.models.audit import AuditEntry
from curator.models.file import FileEntity
from curator.models.migration import MigrationJob, MigrationProgress
from curator.services.migration_retry import retry_transient_errors
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
    COPIED = "copied"  # keep_source=True: dst created+verified, src untouched, index NOT updated
    SKIPPED_NOT_SAFE = "skipped_not_safe"  # CAUTION or REFUSE per SafetyService
    SKIPPED_COLLISION = "skipped_collision"  # destination exists; --on-conflict=skip (default)
    SKIPPED_DB_GUARD = "skipped_db_guard"  # source IS the curator.db file
    HASH_MISMATCH = "hash_mismatch"  # verify failed; src untouched, dst removed
    FAILED = "failed"  # generic IO / OS exception during copy
    # Phase 3 P2: --on-conflict resolution outcomes per design v0.2 §4.6
    MOVED_OVERWROTE_WITH_BACKUP = "moved_overwrote_with_backup"  # dst renamed to <name>.curator-backup-<ts><ext>, then move proceeded
    MOVED_RENAMED_WITH_SUFFIX = "moved_renamed_with_suffix"  # move went to <name>.curator-<n><ext> instead
    FAILED_DUE_TO_CONFLICT = "failed_due_to_conflict"  # --on-conflict=fail or backup/rename setup raised


class MigrationConflictError(RuntimeError):
    """Raised when --on-conflict=fail and a destination collision is encountered.

    Carries enough context for the caller (apply() or run_job()) to surface
    a clean error to the CLI and abort the job. Cross-source paths raise
    this from inside :meth:`MigrationService._cross_source_transfer` when
    the plugin's ``curator_source_write`` raises ``FileExistsError``
    under fail mode.

    See ``docs/TRACER_PHASE_3_DESIGN.md`` v0.2 §4.6 (DM-4).
    """

    def __init__(self, dst_path: str, *, src_path: str | None = None) -> None:
        self.dst_path = dst_path
        self.src_path = src_path
        super().__init__(
            f"destination already exists with --on-conflict=fail: {dst_path}"
        )


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
    started_at: datetime = field(default_factory=utcnow_naive)
    completed_at: datetime | None = None

    # Phase 3 P2: counts now span the new conflict-resolution outcome
    # variants. moved_count picks up MOVED_OVERWROTE_WITH_BACKUP +
    # MOVED_RENAMED_WITH_SUFFIX so the user-facing 'moved' figure
    # reflects the actual transfer count. failed_count picks up
    # FAILED_DUE_TO_CONFLICT alongside FAILED + HASH_MISMATCH.
    _MOVED_VARIANTS: ClassVar[tuple["MigrationOutcome", ...]] = (
        MigrationOutcome.MOVED,
        MigrationOutcome.COPIED,
        MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP,
        MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX,
    )
    _FAILED_VARIANTS: ClassVar[tuple["MigrationOutcome", ...]] = (
        MigrationOutcome.FAILED,
        MigrationOutcome.HASH_MISMATCH,
        MigrationOutcome.FAILED_DUE_TO_CONFLICT,
    )

    @property
    def moved_count(self) -> int:
        return sum(1 for m in self.moves if m.outcome in self._MOVED_VARIANTS)

    @property
    def skipped_count(self) -> int:
        return sum(
            1 for m in self.moves
            if m.outcome and m.outcome.value.startswith("skipped")
        )

    @property
    def failed_count(self) -> int:
        return sum(1 for m in self.moves if m.outcome in self._FAILED_VARIANTS)

    @property
    def bytes_moved(self) -> int:
        return sum(m.size for m in self.moves if m.outcome in self._MOVED_VARIANTS)

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_HASH_CHUNK_SIZE = 64 * 1024  # 64KB chunks; same as hash_pipeline


# Sentinel for keyword arguments whose default is "keep current setting"
# rather than "reset to a hard-coded value." v1.4.1 fix per BUILD_TRACKER
# v1.5.0 candidate (promoted to v1.4.1 patch).
#
# Used in :meth:`MigrationService.apply` and :meth:`MigrationService.run_job`
# for the ``max_retries`` and ``on_conflict`` policy kwargs. Before v1.4.1,
# both methods accepted ``max_retries: int = 3`` and ``on_conflict: str =
# "skip"`` and unconditionally called ``self.set_max_retries(max_retries)`` /
# ``self.set_on_conflict_mode(on_conflict)`` at entry, which silently
# overwrote any prior call to ``set_max_retries()`` /
# ``set_on_conflict_mode()`` made by library callers. v1.4.1 changes the
# defaults to ``_UNCHANGED`` and only invokes the setter when the caller
# explicitly passes a value, so the sequence
#
#     service.set_max_retries(7)
#     service.apply(plan)
#
# now actually uses 7 retries during apply().
#
# Type annotation for parameters using this sentinel is ``Any``: type
# checkers can't enforce ``int | _UNCHANGED`` cleanly without a custom
# class, and ``set_max_retries`` / ``set_on_conflict_mode`` already
# validate at runtime. The annotation is treated as documentation rather
# than enforcement.
_UNCHANGED: Any = object()


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
        pm: "pluggy.PluginManager | None" = None,
        source_repo: "SourceRepository | None" = None,
        metadata_stripper: "MetadataStripper | None" = None,
    ) -> None:
        self.files = file_repo
        self.safety = safety
        self.audit = audit
        self.migration_jobs = migration_jobs
        self.pm = pm
        # v1.7.29: T-B07 v1.8 completion. When both are provided AND the
        # destination source has share_visibility='public', apply() will
        # auto-invoke metadata_stripper.strip_file() on each successfully
        # migrated file. Both are optional for backward compatibility:
        # callers that don't supply them get the legacy behavior
        # (migration only, no stripping).
        self.source_repo = source_repo
        self.metadata_stripper = metadata_stripper
        # Phase 3 retry policy (DM-1, DM-2, DM-3). Read by
        # `migration_retry.retry_transient_errors` decorator wrapping
        # `_cross_source_transfer`. Set per-job via :meth:`set_max_retries`.
        self._max_retries: int = 3
        self._retry_backoff_cap: float = 60.0
        # Phase 3 P2 conflict-resolution policy (DM-4). Read by
        # collision-handling logic in :meth:`apply` Gate 3, in
        # :meth:`_execute_one_persistent_same_source`, and in
        # :meth:`_cross_source_transfer`'s FileExistsError catch.
        # Valid: 'skip' (default; preserves v1.2.0 behavior),
        # 'fail', 'overwrite-with-backup', 'rename-with-suffix'.
        # Set per-job via :meth:`set_on_conflict_mode`.
        self._on_conflict_mode: str = "skip"
        # Phase 2 worker-pool abort signaling: maps job_id -> Event
        # set when ``abort_job`` is called. Workers check between files.
        self._abort_events: dict[UUID, threading.Event] = {}
        self._abort_lock = threading.Lock()

    def set_max_retries(self, n: int) -> None:
        """Configure per-job retry budget for transient errors.

        Sets ``self._max_retries`` clamped to ``[0, 10]``. Read by
        :func:`migration_retry.retry_transient_errors` decorator wrapping
        :meth:`_cross_source_transfer`. ``n=0`` disables retry entirely
        (immediate FAILED on first transient error).

        v1.4.1+: calling this method before :meth:`apply` or
        :meth:`run_job` now sticks. Both methods only invoke this setter
        when the caller explicitly passes ``max_retries=N``; if the kwarg
        is omitted (default sentinel), the current setting is preserved.
        Two equivalent patterns:

        1. **Direct kwarg (preferred for one-shot use):** pass
           ``max_retries=N`` directly to ``apply(plan, max_retries=N)`` or
           ``run_job(job_id, max_retries=N)``.
        2. **Sticky setter (preferred for library callers configuring
           the service once):** call ``service.set_max_retries(N)`` once,
           then call ``apply()`` / ``run_job()`` with no ``max_retries``
           kwarg; the value sticks across multiple calls.

        See ``docs/TRACER_PHASE_3_DESIGN.md`` v0.2 §3 DM-2.
        """
        if n < 0:
            n = 0
        if n > 10:
            n = 10
        self._max_retries = n

    _VALID_CONFLICT_MODES: ClassVar[frozenset[str]] = frozenset({
        "skip", "fail", "overwrite-with-backup", "rename-with-suffix",
    })

    def set_on_conflict_mode(self, mode: str) -> None:
        """Configure per-job destination-collision policy (Phase 3 P2 DM-4).

        Valid values:

        * ``'skip'`` (default) -- existing dst leaves the file untouched;
          outcome is :attr:`MigrationOutcome.SKIPPED_COLLISION`. Preserves
          v1.2.0 behavior exactly.
        * ``'fail'`` -- raise :class:`MigrationConflictError` on the first
          collision; per-file outcome is
          :attr:`MigrationOutcome.FAILED_DUE_TO_CONFLICT`. The CLI
          surfaces this as exit code 1.
        * ``'overwrite-with-backup'`` -- rename existing dst to
          ``<name>.curator-backup-<UTC-iso8601><ext>`` (atomic on the
          same filesystem), then proceed with the move. Per-file outcome
          is :attr:`MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP`.
          v1.4.0+: cross-source paths use the new
          :func:`curator_source_rename` hook instead of the v1.3.0
          degrade-to-skip; plugins that don't implement the hook
          retain the v1.3.0 behavior.
        * ``'rename-with-suffix'`` -- migrate to ``<name>.curator-<n><ext>``
          where ``n`` is the lowest available integer in [1, 9999]. Per-
          file outcome is :attr:`MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX`.
          v1.4.0+: cross-source paths use the FileExistsError retry-
          write loop via :func:`curator_source_rename`.

        Unknown modes raise :class:`ValueError` so the CLI can surface
        a clean error before the migration starts.

        v1.4.1+: calling this method before :meth:`apply` or
        :meth:`run_job` now sticks. Both methods only invoke this setter
        when the caller explicitly passes ``on_conflict=mode``; if the
        kwarg is omitted (default sentinel), the current setting is
        preserved. See :meth:`set_max_retries` for the parallel pattern.

        See ``docs/TRACER_PHASE_3_DESIGN.md`` v0.2 §4.6 (DM-4) and
        ``docs/TRACER_PHASE_4_DESIGN.md`` v0.3 IMPLEMENTED for the
        v1.4.0 cross-source extension.
        """
        if mode not in self._VALID_CONFLICT_MODES:
            raise ValueError(
                f"unknown on_conflict mode: {mode!r}. "
                f"Valid: {sorted(self._VALID_CONFLICT_MODES)}"
            )
        self._on_conflict_mode = mode

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
        includes: list[str] | None = None,
        excludes: list[str] | None = None,
        path_prefix: str | None = None,
    ) -> MigrationPlan:
        """Build a migration plan: every file under ``src_root`` partitioned
        by SafetyService verdict with a computed destination path.

        Phase 1: ``dst_source_id`` defaults to ``src_source_id`` (same-source).
        Cross-source migration in Phase 2 (Session B).

        Filter composition (all applied; ALL must pass):
          1. Files must be under ``src_root`` (and ``src_root + path_prefix`` if set).
          2. ``extensions`` whitelist (case-insensitive).
          3. ``includes`` glob whitelist (file must match at least ONE).
          4. ``excludes`` glob blacklist (file must match NONE).

        Args:
            src_source_id: The source plugin id whose files we're migrating.
            src_root: Path prefix; only files under this prefix are candidates.
            dst_root: Path prefix at the destination; relative subpaths are
                preserved (so ``src_root/A/B.mp3`` lands at ``dst_root/A/B.mp3``).
            dst_source_id: Defaults to ``src_source_id`` for Phase 1.
            extensions: Optional list of extensions to filter candidates
                (e.g. ``['.mp3', '.flac']``). Case-insensitive; leading
                dot optional.
            includes: Optional list of glob patterns to whitelist files
                by relative-to-src_root path. Phase 2. Repeatable; file
                must match AT LEAST ONE include if any are specified.
            excludes: Optional list of glob patterns to blacklist files
                by relative-to-src_root path. Phase 2. Repeatable; file
                must match NO excludes.
            path_prefix: Optional sub-path under ``src_root`` to narrow
                the selection. E.g. ``src_root='C:/Music'``,
                ``path_prefix='Pink Floyd'`` only considers files under
                ``C:/Music/Pink Floyd/``. Dst paths still preserve the
                full relative-to-src_root subpath.

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

        # Normalize glob filters (Phase 2)
        include_patterns = list(includes) if includes else None
        exclude_patterns = list(excludes) if excludes else None

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

        # Compute the actual query prefix (src_root + optional path_prefix)
        query_prefix = src_root
        if path_prefix:
            query_prefix = str(Path(src_root) / path_prefix)

        # Query the file index for candidates
        try:
            candidates = self.files.query(
                FileQuery(
                    source_ids=[src_source_id],
                    source_path_starts_with=query_prefix,
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

            # Glob filters (Phase 2): match against the relative path
            # under src_root so users write "**/*.flac" not "C:/Music/**/*.flac".
            if include_patterns is not None or exclude_patterns is not None:
                try:
                    rel = str(Path(f.source_path).relative_to(Path(src_root)))
                except ValueError:
                    # File is not actually under src_root despite the query;
                    # skip defensively.
                    continue
                # Normalize separators so globs work the same on Win/Mac/Linux
                rel_norm = rel.replace("\\", "/")
                if include_patterns is not None:
                    if not any(fnmatch(rel_norm, pat) for pat in include_patterns):
                        continue
                if exclude_patterns is not None:
                    if any(fnmatch(rel_norm, pat) for pat in exclude_patterns):
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
        keep_source: bool = False,
        include_caution: bool = False,
        max_retries: Any = _UNCHANGED,
        on_conflict: Any = _UNCHANGED,
        no_autostrip: bool = False,
    ) -> MigrationReport:
        """Execute the plan with hash-verify-before-move per file.

        Only SAFE moves run by default. CAUTION + REFUSE files are
        recorded with outcome ``SKIPPED_NOT_SAFE``. With
        ``include_caution=True``, CAUTION files are also eligible (REFUSE
        is still always skipped).

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
            keep_source: When True (Phase 2 ``--keep-source`` flag),
                after dst is created and verified, leave src untouched
                AND do NOT update the FileEntity index pointer. Outcome
                is :attr:`MigrationOutcome.COPIED` instead of MOVED.
                Audit action is ``migration.copy`` instead of
                ``migration.move``. The next ``curator scan`` will pick
                up dst as a new file. Default False (Phase 1 trash-source
                semantics preserved).
            include_caution: When True (Phase 2 ``--include-caution``
                flag), CAUTION-level files are eligible for migration
                alongside SAFE. REFUSE is always skipped regardless.
                Default False.
            max_retries: Per-job retry budget for transient errors
                (clamped to ``[0, 10]``). v1.4.1+: defaults to the
                sentinel ``_UNCHANGED``; if not explicitly passed, the
                current ``self._max_retries`` setting (initialized in
                ``__init__`` to ``3``, possibly modified via
                :meth:`set_max_retries`) is preserved across the call.
                Pass an ``int`` to override.
            on_conflict: Destination-collision policy. Valid values:
                ``'skip'``, ``'fail'``, ``'overwrite-with-backup'``,
                ``'rename-with-suffix'``. v1.4.1+: defaults to the
                sentinel ``_UNCHANGED``; if not explicitly passed, the
                current ``self._on_conflict_mode`` setting (initialized
                in ``__init__`` to ``'skip'``, possibly modified via
                :meth:`set_on_conflict_mode`) is preserved across the
                call. Pass a string to override.

        Returns:
            A :class:`MigrationReport` with one :class:`MigrationMove`
            per planned move (regardless of outcome).
        """
        # v1.4.1: only invoke setters when the caller explicitly passes
        # a value. The sentinel default preserves any prior call to
        # set_max_retries() / set_on_conflict_mode() the library caller
        # may have made -- the previous unconditional overwrite-at-entry
        # behavior was a footgun documented in the v1.5.0 candidate entry
        # of BUILD_TRACKER.md and now closed.
        if max_retries is not _UNCHANGED:
            self.set_max_retries(max_retries)
        if on_conflict is not _UNCHANGED:
            self.set_on_conflict_mode(on_conflict)

        report = MigrationReport(plan=plan)

        # v1.7.29: T-B07 v1.8 completion. Resolve destination source's
        # sharing posture once, before the per-move loop. If 'public' AND
        # we have both source_repo and metadata_stripper available, every
        # successfully migrated file will get its metadata auto-stripped
        # in-place after the verified move completes. Both deps are
        # optional; callers wiring an old-style MigrationService get the
        # legacy behavior (migration only, no stripping).
        #
        # v1.7.35: callers can override the auto-strip via no_autostrip=True
        # (surfaced as the --no-autostrip CLI flag). When the destination
        # IS public, the override is audit-logged with action
        # 'migration.autostrip.opted_out' so downstream tooling can see
        # why a strip didn't happen on a posture that would normally
        # trigger it. When the destination is NOT public, the override
        # is a no-op (no strip was going to happen anyway).
        auto_strip = False
        if (self.source_repo is not None
                and self.metadata_stripper is not None):
            dst_source = self.source_repo.get(plan.dst_source_id)
            if dst_source is not None and dst_source.share_visibility == "public":
                if no_autostrip:
                    # v1.7.35: explicit caller opt-out via --no-autostrip
                    if self.audit is not None:
                        self.audit.log(
                            actor="curator.migration",
                            action="migration.autostrip.opted_out",
                            entity_type="source",
                            entity_id=plan.dst_source_id,
                            details={
                                "reason": "caller passed no_autostrip=True (--no-autostrip)",
                                "plan_move_count": len(plan.moves),
                                "dst_share_visibility": dst_source.share_visibility,
                            },
                        )
                else:
                    auto_strip = True
                    if self.audit is not None:
                        self.audit.log(
                            actor="curator.migration",
                            action="migration.autostrip.enabled",
                            entity_type="source",
                            entity_id=plan.dst_source_id,
                            details={
                                "reason": "dst_source has share_visibility=public",
                                "plan_move_count": len(plan.moves),
                            },
                        )

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

            # Gate 1: REFUSE always skipped; CAUTION skipped unless include_caution
            if move.safety_level == SafetyLevel.REFUSE:
                move.outcome = MigrationOutcome.SKIPPED_NOT_SAFE
                report.moves.append(move)
                continue
            if (move.safety_level == SafetyLevel.CAUTION
                    and not include_caution):
                move.outcome = MigrationOutcome.SKIPPED_NOT_SAFE
                report.moves.append(move)
                continue

            # Gate 2: DB guard
            if (db_path_guard is not None
                    and Path(move.src_path) == db_path_guard):
                move.outcome = MigrationOutcome.SKIPPED_DB_GUARD
                report.moves.append(move)
                continue

            # Gate 3: collision check + Phase 3 P2 conflict resolution.
            # On skip/fail (or backup/rename setup failure): the helper
            # sets move.outcome and returns short_circuit=True; we append
            # and continue. On overwrite-with-backup or rename-with-
            # suffix success: dst was prepared and we proceed with the
            # move; the outcome override is applied AFTER _execute_one
            # sets MOVED.
            dst_p = Path(move.dst_path)
            outcome_override: MigrationOutcome | None = None
            if dst_p.exists():
                short_circuit, outcome_override = self._resolve_collision(
                    move, dst_p,
                )
                if short_circuit:
                    report.moves.append(move)
                    if (move.outcome == MigrationOutcome.FAILED_DUE_TO_CONFLICT
                            and self._on_conflict_mode == "fail"):
                        # --on-conflict=fail aborts the whole pass on the
                        # first collision per design DM-4.
                        report.completed_at = utcnow_naive()
                        raise MigrationConflictError(
                            move.dst_path, src_path=move.src_path,
                        )
                    continue

            # Execute the move with hash-verify-before-move
            self._execute_one(
                move,
                verify_hash=verify_hash,
                keep_source=keep_source,
                src_source_id=plan.src_source_id,
                dst_source_id=plan.dst_source_id,
            )

            # If conflict resolution prepared dst (overwrite-with-backup
            # or rename-with-suffix) and the move succeeded, replace the
            # plain MOVED with the variant outcome so MigrationReport's
            # tallies (moved_count, bytes_moved) and downstream JSON
            # consumers can distinguish them.
            if outcome_override is not None and move.outcome == MigrationOutcome.MOVED:
                move.outcome = outcome_override

            report.moves.append(move)

            # v1.7.29: auto-strip metadata when destination source has
            # share_visibility='public'. Runs ONLY after a successful
            # move (any MOVED variant or COPIED). Failures are logged
            # to audit but don't fail the migration -- the file is
            # already at its destination with verified hash; only the
            # metadata-cleanliness goal is missed.
            if auto_strip and move.outcome in (
                MigrationOutcome.MOVED,
                MigrationOutcome.COPIED,
                MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP,
                MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX,
            ):
                self._auto_strip_metadata(move)

            # Phase 3 P2: --on-conflict=fail aborts on the first collision,
            # including cross-source collisions detected inside
            # _execute_one (which set outcome=FAILED_DUE_TO_CONFLICT but
            # didn't raise so the move could be appended to the report
            # first).
            if (move.outcome == MigrationOutcome.FAILED_DUE_TO_CONFLICT
                    and self._on_conflict_mode == "fail"):
                report.completed_at = utcnow_naive()
                raise MigrationConflictError(
                    move.dst_path, src_path=move.src_path,
                )

        report.completed_at = utcnow_naive()
        return report

    def _auto_strip_metadata(self, move: MigrationMove) -> None:
        """v1.7.29: T-B07 v1.8 completion.

        Strip metadata (EXIF / docProps / PDF metadata / ICC profile,
        depending on file type) from a successfully migrated file when
        the destination source has ``share_visibility='public'``.

        Modifies the destination file in-place via a temp file +
        atomic rename. Failures are logged to the audit trail but do
        NOT fail the migration: the file is already at its destination
        with verified hash; only the metadata-cleanliness goal is
        missed, and the analyst can re-run :command:`curator export-clean`
        manually.

        Emits one of these audit events per file:
          * ``migration.metadata_stripped`` -- success, includes
            bytes_in/bytes_out + list of removed metadata field names.
          * ``migration.metadata_strip_failed`` -- exception during
            stripping (e.g. corrupt destination). Migration considered
            successful; only the strip step failed.

        Called only from apply() and only after a successful move
        (MOVED, COPIED, MOVED_OVERWROTE_WITH_BACKUP, or
        MOVED_RENAMED_WITH_SUFFIX). The caller is responsible for
        that gating.
        """
        if self.metadata_stripper is None:
            return

        # Local imports to avoid circular dependency at module load time.
        from curator.services.metadata_stripper import StripOutcome

        dst_path = Path(move.dst_path)
        if not dst_path.exists():
            return  # Defensive: destination already vanished

        # Strip to a temp file alongside the destination, then atomic
        # rename. Using the same parent dir means the rename is atomic
        # within the destination's filesystem.
        tmp_path = dst_path.with_suffix(
            dst_path.suffix + ".curator_autostrip"
        )
        try:
            result = self.metadata_stripper.strip_file(dst_path, tmp_path)
            if result.outcome in (StripOutcome.STRIPPED, StripOutcome.PASSTHROUGH):
                if tmp_path.exists():
                    # Atomic replace -- on Windows, replace() is atomic
                    # within the same volume.
                    tmp_path.replace(dst_path)
                if self.audit is not None:
                    self.audit.log(
                        actor="curator.migration",
                        action="migration.metadata_stripped",
                        entity_type="file",
                        entity_id=str(move.curator_id),
                        details={
                            "dst_path": move.dst_path,
                            "outcome": result.outcome.value,
                            "bytes_in": result.bytes_in,
                            "bytes_out": result.bytes_out,
                            "fields_removed": result.metadata_fields_removed,
                        },
                    )
            else:
                # SKIPPED or FAILED -- clean up the temp file
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                if self.audit is not None and result.outcome == StripOutcome.FAILED:
                    self.audit.log(
                        actor="curator.migration",
                        action="migration.metadata_strip_failed",
                        entity_type="file",
                        entity_id=str(move.curator_id),
                        details={
                            "dst_path": move.dst_path,
                            "outcome": result.outcome.value,
                            "error": result.error,
                        },
                    )
        except Exception as e:  # noqa: BLE001 - defensive boundary
            # Strip failed catastrophically; cleanup + audit but don't
            # propagate. The migration itself succeeded.
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            if self.audit is not None:
                self.audit.log(
                    actor="curator.migration",
                    action="migration.metadata_strip_failed",
                    entity_type="file",
                    entity_id=str(move.curator_id),
                    details={
                        "dst_path": move.dst_path,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

    def _execute_one(
        self, move: MigrationMove, *, verify_hash: bool,
        keep_source: bool = False,
        src_source_id: str | None = None,
        dst_source_id: str | None = None,
    ) -> None:
        """Per-file move discipline. Mutates ``move`` in place.

        Dispatches on whether the migration crosses a source boundary:

        * **Same-source** (default; ``src_source_id == dst_source_id``
          or either is None): in-process ``shutil.copy2`` + filesystem
          hash verification. The fast path. See
          :meth:`_execute_one_same_source`.
        * **Cross-source**: hook-mediated bytes transfer via
          ``curator_source_read_bytes`` + ``curator_source_write`` +
          re-stream verify + ``curator_source_delete``. Required for
          local↔gdrive (and any future cross-plugin pair). See
          :meth:`_execute_one_cross_source`.

        When ``keep_source`` is True, steps 6 (index update) and 7
        (trash) are skipped; outcome is :attr:`MigrationOutcome.COPIED`
        and the audit action is ``migration.copy``. Applies to both
        same-source and cross-source paths.
        """
        # Default to same-source if source IDs weren't provided
        # (backward-compat with pre-Session-B callers).
        if dst_source_id is None:
            dst_source_id = src_source_id
        cross_source = (
            src_source_id is not None
            and dst_source_id is not None
            and self._is_cross_source(src_source_id, dst_source_id)
        )
        if cross_source:
            self._execute_one_cross_source(
                move, verify_hash=verify_hash, keep_source=keep_source,
                src_source_id=src_source_id, dst_source_id=dst_source_id,
            )
            return
        # v1.6.1: pass source_id (src == dst for same-source) so the
        # helper-emitted audit events get a complete cross_source/source_id
        # detail schema. Without this, phase 1 same-source events were
        # missing the cross_source flag that downstream consumers (citation
        # plugin v0.2+) need to filter cross-source events.
        self._execute_one_same_source(
            move, verify_hash=verify_hash, keep_source=keep_source,
            source_id=src_source_id,
        )

    def _execute_one_same_source(
        self, move: MigrationMove, *, verify_hash: bool,
        keep_source: bool = False,
        source_id: str | None = None,
    ) -> None:
        """Same-source per-file discipline (the existing Phase 1 fast path).

        Uses ``shutil.copy2`` for the bytes transfer and re-reads the dst
        file from the local filesystem to compute the verification hash.

        v1.6.1: ``source_id`` (defaults to None for backward compat with
        pre-Session-B callers) is passed through to the audit helpers so
        emitted events include ``src_source_id`` / ``dst_source_id`` /
        ``cross_source`` keys uniformly with the cross-source path.
        """
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

            if keep_source:
                # keep-source: dst created+verified, src untouched, index NOT updated
                move.outcome = MigrationOutcome.COPIED
                self._audit_copy(
                    move,
                    src_source_id=source_id,
                    dst_source_id=source_id,
                )
                return

            # Step 6: update index (curator_id stays, source_path changes)
            self._update_index(move)

            # Step 7: trash source (best-effort; index already correct)
            self._trash_source(src_p, move)

            move.outcome = MigrationOutcome.MOVED

            # Step 8: audit (only on success)
            self._audit_move(
                move,
                src_source_id=source_id,
                dst_source_id=source_id,
            )

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

    def _execute_one_cross_source(
        self, move: MigrationMove, *, verify_hash: bool,
        keep_source: bool = False,
        src_source_id: str, dst_source_id: str,
    ) -> None:
        """Cross-source per-file discipline (Session B; Phase 4 P2 cross-source conflict resolution).

        Uses :meth:`_cross_source_transfer` for the bytes phase. Phase 4
        P2 (v1.4.0+) replaces v1.3.0's degrade-to-skip for cross-source
        ``overwrite-with-backup`` and ``rename-with-suffix`` with full
        implementations using the new :func:`curator_source_rename` hook
        (overwrite path) and the FileExistsError retry-write pattern
        (suffix path). Plugins that don't implement
        ``curator_source_rename`` continue to see the v1.3.0 degrade-to-
        skip behavior (strictly additive backward compat per design DM-4).
        """
        try:
            outcome, actual_dst_file_id, verified_hash = (
                self._cross_source_transfer(
                    src_source_id=src_source_id,
                    src_file_id=move.src_path,
                    src_xxhash=move.src_xxhash,
                    dst_source_id=dst_source_id,
                    dst_path=move.dst_path,
                    verify_hash=verify_hash,
                )
            )
        except Exception as e:  # noqa: BLE001 -- transfer failure boundary
            move.outcome = MigrationOutcome.FAILED
            move.error = f"{type(e).__name__}: {e}"
            return

        move.verified_xxhash = verified_hash

        if outcome == MigrationOutcome.HASH_MISMATCH:
            move.outcome = outcome
            move.error = (
                f"hash mismatch: src={move.src_xxhash} dst={verified_hash}"
            )
            return

        # Phase 4 P2: cross-source collision dispatch.
        # On SKIPPED_COLLISION, dispatch on self._on_conflict_mode:
        #   skip (default)        -> keep SKIPPED_COLLISION outcome (v1.2.0 behavior)
        #   fail                  -> mark FAILED_DUE_TO_CONFLICT (apply() raises)
        #   overwrite-with-backup -> resolve existing dst file_id, rename it,
        #                            re-attempt transfer, finalize as
        #                            MOVED_OVERWROTE_WITH_BACKUP. Plugin-fallback:
        #                            degrade to v1.3.0 skip-with-warning if the
        #                            plugin doesn't implement curator_source_rename
        #                            or rename can't find/rename the existing file.
        #   rename-with-suffix    -> retry-write loop with .curator-N suffix names
        #                            until success or n=9999 cap. Finalize as
        #                            MOVED_RENAMED_WITH_SUFFIX. 9999 exhaustion
        #                            degrades to skip.
        final_outcome = MigrationOutcome.MOVED
        if outcome == MigrationOutcome.SKIPPED_COLLISION:
            mode = self._on_conflict_mode
            if mode == "skip":
                move.outcome = outcome
                return
            if mode == "fail":
                move.outcome = MigrationOutcome.FAILED_DUE_TO_CONFLICT
                move.error = (
                    f"destination already exists with --on-conflict=fail "
                    f"(cross-source): {move.dst_path}"
                )
                self._audit_conflict(move, mode="fail",
                                     details_extra={"cross_source": True})
                return
            if mode == "overwrite-with-backup":
                retry_result = self._cross_source_overwrite_with_backup(
                    move, verify_hash=verify_hash,
                    src_source_id=src_source_id,
                    dst_source_id=dst_source_id,
                )
                if retry_result is None:
                    return  # degraded; move.outcome already set
                outcome, actual_dst_file_id, verified_hash = retry_result
                move.verified_xxhash = verified_hash
                final_outcome = MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP
            elif mode == "rename-with-suffix":
                retry_result = self._cross_source_rename_with_suffix(
                    move, verify_hash=verify_hash,
                    src_source_id=src_source_id,
                    dst_source_id=dst_source_id,
                )
                if retry_result is None:
                    return  # degraded; move.outcome already set
                outcome, actual_dst_file_id, verified_hash = retry_result
                move.verified_xxhash = verified_hash
                final_outcome = MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX
            else:
                # Unreachable given set_on_conflict_mode validation, but defensive.
                move.outcome = outcome
                return

        # Bytes successfully transferred + verified (initial OR retry).
        # Update dst_path to whatever the dst plugin actually produced
        # (e.g., for local: same path; for gdrive: a Drive file ID).
        move.dst_path = actual_dst_file_id

        if keep_source:
            move.outcome = MigrationOutcome.COPIED
            self._audit_copy(
                move,
                src_source_id=src_source_id,
                dst_source_id=dst_source_id,
            )
            return

        # Step 6: update FileEntity -- BOTH source_id AND source_path change
        try:
            entity = self.files.get(move.curator_id)
            if entity is None:
                raise RuntimeError(
                    f"FileEntity {move.curator_id} vanished during migration"
                )
            entity.source_id = dst_source_id
            entity.source_path = actual_dst_file_id
            self.files.update(entity)
        except Exception as e:  # noqa: BLE001
            move.outcome = MigrationOutcome.FAILED
            move.error = f"index update failed: {type(e).__name__}: {e}"
            return

        # Step 7: trash src via plugin hook (to_trash=True is recoverable)
        try:
            deleted = self._hook_first_result(
                "curator_source_delete",
                source_id=src_source_id,
                file_id=move.src_path,
                to_trash=True,
            )
            if not deleted:
                move.error = (
                    (move.error or "") +
                    f" [trash failed: src plugin returned {deleted!r}]"
                ).strip()
        except Exception as e:  # noqa: BLE001 -- trash failure is non-fatal
            logger.warning(
                "MigrationService: cross-source trash failed for {p}: {e}",
                p=move.src_path, e=e,
            )
            move.error = (
                (move.error or "") +
                f" [trash failed: {type(e).__name__}: {e}]"
            ).strip()

        move.outcome = final_outcome
        self._audit_move(
            move,
            src_source_id=src_source_id,
            dst_source_id=dst_source_id,
        )

    def _cross_source_overwrite_with_backup(
        self,
        move: MigrationMove,
        *,
        verify_hash: bool,
        src_source_id: str,
        dst_source_id: str,
    ) -> tuple[MigrationOutcome, str, str | None] | None:
        """Cross-source ``overwrite-with-backup`` retry flow (Phase 4 P2).

        Steps:
          1. Resolve existing dst's file_id via
             :meth:`_find_existing_dst_file_id_for_overwrite`. None means
             we can't proceed (no stat or enumerate match).
          2. Compute backup_name from :meth:`_compute_backup_path`.
          3. Call ``curator_source_rename(dst_source_id, file_id, backup_name)``
             via :meth:`_attempt_cross_source_backup_rename`. Failure
             means plugin doesn't implement OR rename failed.
          4. Re-attempt :meth:`_cross_source_transfer`. Failure leaves
             the renamed backup in place per design DM-5 (best-effort
             rollback would double the failure paths and may itself fail
             racy).

        On any failure, mutates ``move`` to the v1.3.0 degrade-to-skip
        shape (outcome=SKIPPED_COLLISION, audit captures the reason)
        and returns ``None`` to signal the caller. On success, returns
        ``(MigrationOutcome.MOVED, actual_dst_file_id, verified_hash)``.
        """
        # Step 1: resolve existing dst's file_id
        existing_file_id = self._find_existing_dst_file_id_for_overwrite(
            dst_source_id, move.dst_path,
        )
        if existing_file_id is None:
            logger.warning(
                "MigrationService: cross-source overwrite-with-backup "
                "degraded to skip for {p} (could not resolve existing dst file_id)",
                p=move.src_path,
            )
            self._audit_conflict(
                move,
                mode="overwrite-with-backup-degraded-cross-source",
                details_extra={
                    "cross_source": True,
                    "reason": "could not resolve existing dst file_id",
                    "fallback": "skipped",
                },
            )
            move.outcome = MigrationOutcome.SKIPPED_COLLISION
            return None

        # Step 2: compute backup_name
        backup_name = self._compute_backup_path(Path(move.dst_path)).name

        # Step 3: call rename hook
        success, error = self._attempt_cross_source_backup_rename(
            dst_source_id, existing_file_id, backup_name,
        )
        if not success:
            logger.warning(
                "MigrationService: cross-source overwrite-with-backup "
                "degraded to skip for {p} ({err})",
                p=move.src_path, err=error or "rename hook unavailable",
            )
            self._audit_conflict(
                move,
                mode="overwrite-with-backup-degraded-cross-source",
                details_extra={
                    "cross_source": True,
                    "reason": error or "rename hook unavailable",
                    "fallback": "skipped",
                },
            )
            move.outcome = MigrationOutcome.SKIPPED_COLLISION
            return None

        # Audit the successful rename BEFORE the retry so audit reflects
        # exactly what happened even if the retry fails (DM-5).
        self._audit_conflict(
            move,
            mode="overwrite-with-backup",
            details_extra={
                "cross_source": True,
                "backup_name": backup_name,
                "existing_file_id": existing_file_id,
            },
        )

        # Step 4: re-attempt cross-source transfer
        try:
            retry_outcome, retry_dst_file_id, retry_verified_hash = (
                self._cross_source_transfer(
                    src_source_id=src_source_id,
                    src_file_id=move.src_path,
                    src_xxhash=move.src_xxhash,
                    dst_source_id=dst_source_id,
                    dst_path=move.dst_path,
                    verify_hash=verify_hash,
                )
            )
        except Exception as e:  # noqa: BLE001
            move.outcome = MigrationOutcome.FAILED
            move.error = (
                f"{type(e).__name__}: {e} "
                f"[backup at {backup_name} preserved per DM-5]"
            )
            return None

        if retry_outcome == MigrationOutcome.HASH_MISMATCH:
            move.outcome = retry_outcome
            move.error = (
                f"hash mismatch: src={move.src_xxhash} dst={retry_verified_hash} "
                f"[backup at {backup_name} preserved per DM-5]"
            )
            move.verified_xxhash = retry_verified_hash
            return None

        if retry_outcome == MigrationOutcome.SKIPPED_COLLISION:
            # Extremely rare: dst slot still busy after backup rename
            # (concurrent write race). Backup is preserved per DM-5.
            move.outcome = MigrationOutcome.SKIPPED_COLLISION
            move.error = (
                f"cross-source overwrite-with-backup retry collision "
                f"[backup at {backup_name} preserved per DM-5]"
            )
            return None

        return (retry_outcome, retry_dst_file_id, retry_verified_hash)

    def _cross_source_rename_with_suffix(
        self,
        move: MigrationMove,
        *,
        verify_hash: bool,
        src_source_id: str,
        dst_source_id: str,
    ) -> tuple[MigrationOutcome, str, str | None] | None:
        """Cross-source ``rename-with-suffix`` retry-write loop (Phase 4 P2).

        Loops n=1..9999, computing ``<name>.curator-<n><ext>`` via
        :meth:`_compute_suffix_name` and calling
        :meth:`_cross_source_transfer` until one of:

        * Success: returns
          ``(MigrationOutcome.MOVED, actual_dst_file_id, verified_hash)``.
          Audit captures suffix_n + original_dst + renamed_dst.
          ``move.dst_path`` is updated to the suffix variant.
        * HASH_MISMATCH on a particular suffix attempt: returns None;
          ``move.outcome`` set to HASH_MISMATCH. (Retry would just hit
          the same hash mismatch.)
        * Transfer exception: returns None; ``move.outcome`` set to FAILED.
        * 9999 exhausted: returns None; ``move.outcome`` set to
          SKIPPED_COLLISION; audit captures fallback reason.

        Per DM-3, no exists-probe hookspec is needed because
        ``curator_source_write(overwrite=False)`` already raises
        FileExistsError (which surfaces here as SKIPPED_COLLISION from
        :meth:`_cross_source_transfer`).
        """
        original_dst = move.dst_path
        for n in range(1, 10_000):
            candidate_dst_path = str(
                self._compute_suffix_name(Path(original_dst), n)
            )
            try:
                outcome2, actual_dst_file_id, verified_hash2 = (
                    self._cross_source_transfer(
                        src_source_id=src_source_id,
                        src_file_id=move.src_path,
                        src_xxhash=move.src_xxhash,
                        dst_source_id=dst_source_id,
                        dst_path=candidate_dst_path,
                        verify_hash=verify_hash,
                    )
                )
            except Exception as e:  # noqa: BLE001
                move.outcome = MigrationOutcome.FAILED
                move.error = f"{type(e).__name__}: {e}"
                return None

            if outcome2 == MigrationOutcome.SKIPPED_COLLISION:
                continue  # try next suffix
            if outcome2 == MigrationOutcome.HASH_MISMATCH:
                move.outcome = outcome2
                move.error = (
                    f"hash mismatch: src={move.src_xxhash} dst={verified_hash2}"
                )
                move.verified_xxhash = verified_hash2
                return None

            # Success at suffix n
            self._audit_conflict(
                move,
                mode="rename-with-suffix",
                details_extra={
                    "cross_source": True,
                    "original_dst": original_dst,
                    "renamed_dst": candidate_dst_path,
                    "suffix_n": n,
                },
            )
            return (outcome2, actual_dst_file_id, verified_hash2)

        # 9999 exhausted
        logger.warning(
            "MigrationService: cross-source rename-with-suffix exhausted "
            "9999 candidates for {p}; degrading to skip",
            p=move.src_path,
        )
        self._audit_conflict(
            move,
            mode="rename-with-suffix-degraded-cross-source",
            details_extra={
                "cross_source": True,
                "reason": "9999 suffixes exhausted",
                "fallback": "skipped",
            },
        )
        move.outcome = MigrationOutcome.SKIPPED_COLLISION
        return None

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

    # ==================================================================
    # Phase 3 P2: collision-resolution helpers (DM-4)
    # ==================================================================

    @staticmethod
    def _compute_backup_path(dst_p: Path) -> Path:
        """Generate ``<name>.curator-backup-<UTC-iso8601><ext>`` next to dst.

        Example: ``C:/music/foo.mp3`` -> ``C:/music/foo.curator-backup-2026-05-08T17-30-00Z.mp3``.
        Compact ISO 8601 (colons replaced with hyphens) so the filename is
        legal on Windows. UTC-fixed because servers + dev machines may sit
        in different timezones.
        """
        timestamp = utcnow_naive().strftime("%Y-%m-%dT%H-%M-%SZ")
        stem = dst_p.stem
        ext = dst_p.suffix
        new_name = f"{stem}.curator-backup-{timestamp}{ext}"
        return dst_p.with_name(new_name)

    @staticmethod
    def _find_available_suffix(dst_p: Path) -> tuple[Path, int]:
        """Find the lowest n in [1, 9999] such that ``<name>.curator-<n><ext>`` doesn't exist.

        Returns ``(new_path, n)``. Raises :class:`RuntimeError` if all
        9999 candidate suffixes are already taken (extreme edge case).
        Suffix-N files from prior runs are correctly skipped because the
        existence check probes the filesystem each time.
        """
        stem = dst_p.stem
        ext = dst_p.suffix
        for n in range(1, 10_000):
            candidate = dst_p.with_name(f"{stem}.curator-{n}{ext}")
            if not candidate.exists():
                return (candidate, n)
        raise RuntimeError(
            f"no available .curator-N{ext} suffix in [1, 9999] for {dst_p}"
        )

    @staticmethod
    def _compute_suffix_name(dst_p: Path, n: int) -> Path:
        """Compute ``<name>.curator-<n><ext>`` for a given n (Phase 4 P2 helper).

        Sister of :meth:`_find_available_suffix` for the cross-source
        retry-write loop where existence is probed implicitly via
        ``curator_source_write(overwrite=False)`` raising
        ``FileExistsError`` (DM-3 in TRACER_PHASE_4_DESIGN.md v0.2).
        Caller increments n on each FileExistsError until success or
        exhaustion at n=9999.

        Example: ``C:/music/foo.mp3``, ``n=3`` -> ``C:/music/foo.curator-3.mp3``.
        """
        stem = dst_p.stem
        ext = dst_p.suffix
        return dst_p.with_name(f"{stem}.curator-{n}{ext}")

    def _find_existing_dst_file_id_for_overwrite(
        self, dst_source_id: str, dst_path: str,
    ) -> str | None:
        """Resolve dst_path to an existing file's file_id (Phase 4 P2 helper).

        Used by cross-source ``overwrite-with-backup`` to find the file
        that's blocking the write so we can call
        ``curator_source_rename(dst_source_id, file_id, backup_name)``
        on it.

        Two-strategy resolution:

        1. **Stat-as-file_id** (works for local-style sources where
           ``file_id == path``): try
           ``curator_source_stat(dst_source_id, dst_path)``. A non-None
           FileStat means dst_path IS a valid file_id.
        2. **Enumerate-and-match** (works for cloud sources where
           ``file_id`` is distinct from the display path): call
           ``curator_source_enumerate(dst_source_id, parent_id, {})``
           and find the FileInfo whose display name matches the target.

        Returns the resolved file_id, or ``None`` if neither strategy
        finds a match (caller degrades to v1.3.0 skip-with-warning).
        """
        # Strategy 1: stat with dst_path as file_id (local-style).
        try:
            stat_result = self._hook_first_result(
                "curator_source_stat",
                source_id=dst_source_id, file_id=dst_path,
            )
        except Exception:  # noqa: BLE001 -- defensive
            stat_result = None
        if stat_result is not None:
            return dst_path

        # Strategy 2: enumerate parent and match by display name.
        dst_p = Path(dst_path)
        parent_id = str(dst_p.parent)
        target_name = dst_p.name
        try:
            iter_or_none = self._hook_first_result(
                "curator_source_enumerate",
                source_id=dst_source_id, root=parent_id, options={},
            )
        except Exception:  # noqa: BLE001 -- defensive
            return None
        if iter_or_none is None:
            return None
        try:
            for info in iter_or_none:
                # FileInfo.path is display name for cloud, full path for
                # local. Match either equality OR basename equality so
                # both shapes work without hardcoding source-type.
                info_basename = Path(info.path).name
                if info.path == target_name or info_basename == target_name:
                    return info.file_id
        except Exception as e:  # noqa: BLE001 -- defensive on iterator
            logger.warning(
                "MigrationService: enumerate iteration failed for {sid}/{p}: {e}",
                sid=dst_source_id, p=parent_id, e=e,
            )
            return None
        return None

    def _attempt_cross_source_backup_rename(
        self,
        dst_source_id: str,
        existing_file_id: str,
        backup_name: str,
    ) -> tuple[bool, str | None]:
        """Call ``curator_source_rename`` to move existing dst out of the way (Phase 4 P2).

        Returns ``(success, error_message_or_None)``. ``success=False``
        means the rename hook either returned None (plugin doesn't
        implement) or raised; caller degrades to v1.3.0 skip-with-
        warning. ``success=True`` means the existing dst is renamed
        and the caller can re-attempt the cross-source transfer with
        the original dst_path.
        """
        if self.pm is None:
            return (False, "no plugin manager available")
        try:
            hook = getattr(self.pm.hook, "curator_source_rename")
        except AttributeError:
            return (False, "curator_source_rename hookspec not registered")
        try:
            results = hook(
                source_id=dst_source_id,
                file_id=existing_file_id,
                new_name=backup_name,
                overwrite=False,
            )
        except FileExistsError as e:
            # Concurrent backup at the same name (extremely rare). Audit
            # + degrade per design DM-3 / DM-4.
            return (False, f"backup name collision: {e}")
        except Exception as e:  # noqa: BLE001
            return (False, f"{type(e).__name__}: {e}")
        # Pluggy returns a list; first non-None is the owning plugin's result.
        if not isinstance(results, list):
            results = [results]
        renamed = next((r for r in results if r is not None), None)
        if renamed is None:
            return (False, "plugin does not implement curator_source_rename")
        return (True, None)

    def _emit_progress_audit_conflict(
        self,
        progress: "MigrationProgress",
        *,
        mode: str,
        details_extra: dict[str, Any] | None = None,
    ) -> None:
        """Persistent-path sister of :meth:`_audit_conflict`.

        Emits ``migration.conflict_resolved`` with ``job_id`` in the
        details dict for cross-reference with the migration_jobs row.
        Best-effort; emission failures never propagate. Used by
        :meth:`_cross_source_overwrite_with_backup_for_progress` and
        :meth:`_cross_source_rename_with_suffix_for_progress`.
        """
        if self.audit is None:
            return
        details: dict[str, Any] = {
            "src_path": progress.src_path,
            "dst_path": progress.dst_path,
            "mode": mode,
            "size": progress.size,
            "job_id": str(progress.job_id),
        }
        if details_extra:
            details.update(details_extra)
        try:
            self.audit.log(
                actor="curator.migrate",
                action="migration.conflict_resolved",
                entity_type="file",
                entity_id=str(progress.curator_id),
                details=details,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MigrationService: conflict audit failed for {cid}: {e}",
                cid=progress.curator_id, e=e,
            )

    def _cross_source_overwrite_with_backup_for_progress(
        self,
        progress: "MigrationProgress",
        *,
        verify_hash: bool,
        src_source_id: str,
        dst_source_id: str,
    ) -> tuple[MigrationOutcome, str, str | None] | None:
        """Persistent-path sister of :meth:`_cross_source_overwrite_with_backup`.

        Same algorithm; emits audit with ``job_id`` via
        :meth:`_emit_progress_audit_conflict`. Returns
        ``(MigrationOutcome.MOVED, actual_dst_file_id, verified_hash)``
        on success; returns ``None`` on degrade-to-skip (caller returns
        ``(SKIPPED_COLLISION, None)``).
        """
        existing_file_id = self._find_existing_dst_file_id_for_overwrite(
            dst_source_id, progress.dst_path,
        )
        if existing_file_id is None:
            logger.warning(
                "MigrationService: cross-source overwrite-with-backup "
                "degraded to skip for {p} (could not resolve existing dst file_id)",
                p=progress.src_path,
            )
            self._emit_progress_audit_conflict(
                progress,
                mode="overwrite-with-backup-degraded-cross-source",
                details_extra={
                    "cross_source": True,
                    "reason": "could not resolve existing dst file_id",
                    "fallback": "skipped",
                },
            )
            return None

        backup_name = self._compute_backup_path(Path(progress.dst_path)).name
        success, error = self._attempt_cross_source_backup_rename(
            dst_source_id, existing_file_id, backup_name,
        )
        if not success:
            logger.warning(
                "MigrationService: cross-source overwrite-with-backup "
                "degraded to skip for {p} ({err})",
                p=progress.src_path, err=error or "rename hook unavailable",
            )
            self._emit_progress_audit_conflict(
                progress,
                mode="overwrite-with-backup-degraded-cross-source",
                details_extra={
                    "cross_source": True,
                    "reason": error or "rename hook unavailable",
                    "fallback": "skipped",
                },
            )
            return None

        # Audit the rename BEFORE the retry so audit reflects exactly
        # what happened even if the retry fails (DM-5).
        self._emit_progress_audit_conflict(
            progress,
            mode="overwrite-with-backup",
            details_extra={
                "cross_source": True,
                "backup_name": backup_name,
                "existing_file_id": existing_file_id,
            },
        )

        # Re-attempt the cross-source transfer.
        # On retry exception OR retry-time HASH_MISMATCH OR retry-time
        # SKIPPED_COLLISION (extremely rare race), we let the caller
        # surface FAILED / HASH_MISMATCH / SKIPPED_COLLISION and the
        # backup is preserved per DM-5. We re-raise so the worker loop's
        # exception boundary records the right outcome.
        retry_outcome, retry_dst_file_id, retry_verified_hash = (
            self._cross_source_transfer(
                src_source_id=src_source_id,
                src_file_id=progress.src_path,
                src_xxhash=progress.src_xxhash,
                dst_source_id=dst_source_id,
                dst_path=progress.dst_path,
                verify_hash=verify_hash,
            )
        )
        if retry_outcome != MigrationOutcome.MOVED:
            # HASH_MISMATCH or unexpected SKIPPED_COLLISION on retry
            # surfaces back to the caller; the worker maps it appropriately.
            return (retry_outcome, retry_dst_file_id, retry_verified_hash)
        return (MigrationOutcome.MOVED, retry_dst_file_id, retry_verified_hash)

    def _cross_source_rename_with_suffix_for_progress(
        self,
        progress: "MigrationProgress",
        *,
        verify_hash: bool,
        src_source_id: str,
        dst_source_id: str,
    ) -> tuple[MigrationOutcome, str, str | None] | None:
        """Persistent-path sister of :meth:`_cross_source_rename_with_suffix`.

        Same algorithm; emits audit with ``job_id`` via
        :meth:`_emit_progress_audit_conflict`. Returns
        ``(MigrationOutcome.MOVED, actual_dst_file_id, verified_hash)``
        on success (caller MUST update progress.dst_path to the
        suffix variant); returns ``None`` on 9999 exhaustion or
        retry-time HASH_MISMATCH.
        """
        original_dst = progress.dst_path
        for n in range(1, 10_000):
            candidate_dst_path = str(
                self._compute_suffix_name(Path(original_dst), n)
            )
            outcome2, actual_dst_file_id, verified_hash2 = (
                self._cross_source_transfer(
                    src_source_id=src_source_id,
                    src_file_id=progress.src_path,
                    src_xxhash=progress.src_xxhash,
                    dst_source_id=dst_source_id,
                    dst_path=candidate_dst_path,
                    verify_hash=verify_hash,
                )
            )
            if outcome2 == MigrationOutcome.SKIPPED_COLLISION:
                continue  # try next suffix
            if outcome2 == MigrationOutcome.HASH_MISMATCH:
                # Surface the hash mismatch; suffix retries would just
                # hit the same mismatch (same src bytes -> same hash).
                return (outcome2, actual_dst_file_id, verified_hash2)
            # Success at suffix n.
            self._emit_progress_audit_conflict(
                progress,
                mode="rename-with-suffix",
                details_extra={
                    "cross_source": True,
                    "original_dst": original_dst,
                    "renamed_dst": candidate_dst_path,
                    "suffix_n": n,
                },
            )
            # Caller must update progress.dst_path = actual_dst_file_id
            # (or candidate_dst_path) so the entity update + audit_move
            # use the correct path.
            return (outcome2, actual_dst_file_id, verified_hash2)

        # 9999 exhausted
        logger.warning(
            "MigrationService: cross-source rename-with-suffix exhausted "
            "9999 candidates for {p}; degrading to skip",
            p=progress.src_path,
        )
        self._emit_progress_audit_conflict(
            progress,
            mode="rename-with-suffix-degraded-cross-source",
            details_extra={
                "cross_source": True,
                "reason": "9999 suffixes exhausted",
                "fallback": "skipped",
            },
        )
        return None

    def _audit_conflict(
        self,
        move: MigrationMove,
        *,
        mode: str,
        details_extra: dict[str, Any] | None = None,
    ) -> None:
        """Emit a ``migration.conflict_resolved`` audit event. Best-effort.

        Distinct from ``migration.move`` so audit log queries can find
        every conflict resolution that fired regardless of whether the
        downstream move ultimately succeeded. The ``mode`` field reflects
        the conflict-resolution policy that was applied (or attempted +
        failed). Audit emission failures never propagate.
        """
        if self.audit is None:
            return
        details: dict[str, Any] = {
            "src_path": move.src_path,
            "dst_path": move.dst_path,
            "mode": mode,
            "size": move.size,
        }
        if details_extra:
            details.update(details_extra)
        try:
            self.audit.log(
                actor="curator.migrate",
                action="migration.conflict_resolved",
                entity_type="file",
                entity_id=str(move.curator_id),
                details=details,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MigrationService: conflict audit failed for {cid}: {e}",
                cid=move.curator_id, e=e,
            )

    def _resolve_collision(
        self, move: MigrationMove, dst_p: Path,
    ) -> tuple[bool, MigrationOutcome | None]:
        """Apply ``self._on_conflict_mode`` policy when ``dst_p`` exists.

        Mutates ``move`` in place per the active mode:

        * ``skip``: sets ``move.outcome = SKIPPED_COLLISION``; returns
          ``(short_circuit=True, override=None)``. Caller should append
          and move on.
        * ``fail``: sets ``move.outcome = FAILED_DUE_TO_CONFLICT`` +
          ``move.error``; emits ``migration.conflict_resolved``; returns
          ``(short_circuit=True, override=None)``.
        * ``overwrite-with-backup``: renames ``dst_p`` to backup path
          (atomic on same FS); emits audit; returns
          ``(short_circuit=False, override=MOVED_OVERWROTE_WITH_BACKUP)``.
          Caller proceeds with the move; on success replaces ``MOVED``
          with the override.
        * ``rename-with-suffix``: mutates ``move.dst_path`` to a free
          ``.curator-<n><ext>`` path; emits audit; returns
          ``(short_circuit=False, override=MOVED_RENAMED_WITH_SUFFIX)``.

        OS errors during the rename (e.g. cross-volume rename, missing
        parent dir, permission denied) are caught and turn the outcome
        into ``FAILED_DUE_TO_CONFLICT`` with the error message attached.

        See ``docs/TRACER_PHASE_3_DESIGN.md`` v0.2 §4.6 (DM-4).
        """
        mode = self._on_conflict_mode

        if mode == "skip":
            move.outcome = MigrationOutcome.SKIPPED_COLLISION
            return (True, None)

        if mode == "fail":
            move.outcome = MigrationOutcome.FAILED_DUE_TO_CONFLICT
            move.error = (
                f"destination already exists with --on-conflict=fail: "
                f"{move.dst_path}"
            )
            self._audit_conflict(move, mode="fail")
            return (True, None)

        if mode == "overwrite-with-backup":
            backup_path = self._compute_backup_path(dst_p)
            try:
                dst_p.rename(backup_path)
            except OSError as e:
                move.outcome = MigrationOutcome.FAILED_DUE_TO_CONFLICT
                move.error = (
                    f"backup rename failed for {dst_p} -> {backup_path}: "
                    f"{type(e).__name__}: {e}"
                )
                self._audit_conflict(
                    move, mode="overwrite-with-backup-failed",
                    details_extra={"backup_path": str(backup_path),
                                   "error": move.error},
                )
                return (True, None)
            self._audit_conflict(
                move, mode="overwrite-with-backup",
                details_extra={"backup_path": str(backup_path)},
            )
            return (False, MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP)

        if mode == "rename-with-suffix":
            try:
                new_path, suffix_n = self._find_available_suffix(dst_p)
            except RuntimeError as e:
                move.outcome = MigrationOutcome.FAILED_DUE_TO_CONFLICT
                move.error = str(e)
                self._audit_conflict(
                    move, mode="rename-with-suffix-failed",
                    details_extra={"error": str(e)},
                )
                return (True, None)
            original_dst = move.dst_path
            move.dst_path = str(new_path)
            self._audit_conflict(
                move, mode="rename-with-suffix",
                details_extra={
                    "original_dst": original_dst,
                    "renamed_dst": str(new_path),
                    "suffix_n": suffix_n,
                },
            )
            return (False, MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX)

        # Unreachable given set_on_conflict_mode validation, but be defensive
        logger.warning(
            "MigrationService: unknown on_conflict mode {m}, falling back to skip",
            m=mode,
        )
        move.outcome = MigrationOutcome.SKIPPED_COLLISION
        return (True, None)

    def _resolve_collision_for_progress(
        self,
        progress: MigrationProgress,
        dst_p: Path,
    ) -> tuple[bool, MigrationOutcome | None, Path | None, str | None]:
        """Sister of :meth:`_resolve_collision` for the persistent (Phase 2) path.

        Operates on :class:`MigrationProgress` instead of
        :class:`MigrationMove`. Returns the same kind of decision but as
        a 4-tuple because progress doesn't carry an in-memory outcome
        field that the resolver can mutate (the worker writes it to the
        DB instead).

        Returns ``(short_circuit, outcome_override, new_dst, conflict_error)``:

        * ``skip``: ``(True, None, None, None)`` -- caller returns SKIPPED_COLLISION.
        * ``fail``: ``(True, None, None, error_msg)`` -- caller raises
          :class:`MigrationConflictError`; the worker turns that into
          status='failed', outcome=FAILED_DUE_TO_CONFLICT.
        * ``overwrite-with-backup`` success: ``(False, MOVED_OVERWROTE_WITH_BACKUP, None, None)``.
          dst was renamed to backup; caller proceeds with progress.dst_path unchanged.
        * ``rename-with-suffix`` success: ``(False, MOVED_RENAMED_WITH_SUFFIX, new_path, None)``.
          caller MUST update progress.dst_path = str(new_path).
        * Setup error (overwrite/rename can't proceed): ``(True, None, None, error_msg)``.

        Emits ``migration.conflict_resolved`` audit events with the
        ``job_id`` field for cross-reference.
        """
        mode = self._on_conflict_mode

        def _emit_audit(audit_mode: str, extra: dict[str, Any] | None = None) -> None:
            if self.audit is None:
                return
            details: dict[str, Any] = {
                "src_path": progress.src_path,
                "dst_path": progress.dst_path,
                "mode": audit_mode,
                "size": progress.size,
                "job_id": str(progress.job_id),
            }
            if extra:
                details.update(extra)
            try:
                self.audit.log(
                    actor="curator.migrate",
                    action="migration.conflict_resolved",
                    entity_type="file",
                    entity_id=str(progress.curator_id),
                    details=details,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "MigrationService: conflict audit failed for {cid}: {e}",
                    cid=progress.curator_id, e=e,
                )

        if mode == "skip":
            return (True, None, None, None)

        if mode == "fail":
            err = (
                f"destination already exists with --on-conflict=fail: "
                f"{progress.dst_path}"
            )
            _emit_audit("fail")
            return (True, None, None, err)

        if mode == "overwrite-with-backup":
            backup_path = self._compute_backup_path(dst_p)
            try:
                dst_p.rename(backup_path)
            except OSError as e:
                err = (
                    f"backup rename failed for {dst_p} -> {backup_path}: "
                    f"{type(e).__name__}: {e}"
                )
                _emit_audit(
                    "overwrite-with-backup-failed",
                    extra={"backup_path": str(backup_path), "error": err},
                )
                return (True, None, None, err)
            _emit_audit(
                "overwrite-with-backup",
                extra={"backup_path": str(backup_path)},
            )
            return (False, MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP, None, None)

        if mode == "rename-with-suffix":
            try:
                new_path, suffix_n = self._find_available_suffix(dst_p)
            except RuntimeError as e:
                _emit_audit(
                    "rename-with-suffix-failed",
                    extra={"error": str(e)},
                )
                return (True, None, None, str(e))
            _emit_audit(
                "rename-with-suffix",
                extra={
                    "original_dst": progress.dst_path,
                    "renamed_dst": str(new_path),
                    "suffix_n": suffix_n,
                },
            )
            return (False, MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX, new_path, None)

        # Unreachable given set_on_conflict_mode validation
        logger.warning(
            "MigrationService: unknown on_conflict mode {m}, falling back to skip",
            m=mode,
        )
        return (True, None, None, None)

    def _audit_move(
        self,
        move: MigrationMove,
        *,
        src_source_id: str | None = None,
        dst_source_id: str | None = None,
    ) -> None:
        """Append an audit entry for a successful move. Best-effort.

        v1.6.1: emits ``src_source_id`` / ``dst_source_id`` / ``cross_source``
        in details when source IDs are provided. ``cross_source`` is
        derived from ``src_source_id != dst_source_id``. When both are
        None (legacy callers), the source-related keys are omitted to
        preserve backward compat with the pre-v1.6.1 schema.
        """
        if self.audit is None:
            return
        try:
            details: dict[str, Any] = {
                "src_path": move.src_path,
                "dst_path": move.dst_path,
                "size": move.size,
                "xxhash3_128": move.verified_xxhash or move.src_xxhash,
            }
            if src_source_id is not None and dst_source_id is not None:
                details["src_source_id"] = src_source_id
                details["dst_source_id"] = dst_source_id
                details["cross_source"] = src_source_id != dst_source_id
            entry = AuditEntry(
                actor="curator.migrate",
                action="migration.move",
                entity_type="file",
                entity_id=str(move.curator_id),
                details=details,
            )
            self.audit.insert(entry)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MigrationService: audit append failed for {cid}: {e}",
                cid=move.curator_id, e=e,
            )

    def _audit_copy(
        self,
        move: MigrationMove,
        *,
        src_source_id: str | None = None,
        dst_source_id: str | None = None,
    ) -> None:
        """Append an audit entry for a successful keep-source copy. Best-effort.

        Distinct from :meth:`_audit_move` so audit log queries can
        differentiate ``migration.move`` (index re-pointed, src trashed)
        from ``migration.copy`` (dst created, src + index untouched).

        v1.6.1: emits ``src_source_id`` / ``dst_source_id`` / ``cross_source``
        in details when source IDs are provided (see :meth:`_audit_move`
        docstring for backward-compat semantics).
        """
        if self.audit is None:
            return
        try:
            details: dict[str, Any] = {
                "src_path": move.src_path,
                "dst_path": move.dst_path,
                "size": move.size,
                "xxhash3_128": move.verified_xxhash or move.src_xxhash,
            }
            if src_source_id is not None and dst_source_id is not None:
                details["src_source_id"] = src_source_id
                details["dst_source_id"] = dst_source_id
                details["cross_source"] = src_source_id != dst_source_id
            entry = AuditEntry(
                actor="curator.migrate",
                action="migration.copy",
                entity_type="file",
                entity_id=str(move.curator_id),
                details=details,
            )
            self.audit.insert(entry)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MigrationService: audit append failed for {cid}: {e}",
                cid=move.curator_id, e=e,
            )

    # ==================================================================
    # Cross-source helpers (Session B): hook-mediated bytes transfer
    # ==================================================================

    def _is_cross_source(
        self, src_source_id: str, dst_source_id: str,
    ) -> bool:
        """True if a migration crosses a source boundary.

        Same-source (default) uses the in-process ``shutil.copy2`` fast
        path. Cross-source goes through the plugin hooks
        (``curator_source_read_bytes`` + ``curator_source_write`` +
        ``curator_source_delete``) so the operation works for any pair
        of registered source plugins.
        """
        return src_source_id != dst_source_id

    def _can_write_to_source(self, source_id: str) -> bool:
        """True if a plugin owns this source_id AND advertises ``supports_write``.

        Used by the CLI capability check to refuse cross-source
        migrations whose destination plugin does not implement
        :meth:`curator_source_write`. Returns False if the service
        was constructed without a ``pm``.
        """
        if self.pm is None:
            return False
        try:
            infos = self.pm.hook.curator_source_register()
        except Exception:  # noqa: BLE001 -- defensive; bad plugin shouldn't crash us
            return False
        if not isinstance(infos, list):
            infos = [infos]
        for info in infos:
            if info is None:
                continue
            # source_type is the prefix; ``local`` owns ``local`` and ``local:*``
            if (source_id == info.source_type
                    or source_id.startswith(f"{info.source_type}:")):
                return bool(getattr(info, "supports_write", False))
        return False

    def _hook_first_result(self, hook_name: str, **kwargs: Any) -> Any:
        """Call a hook and return the first non-None result, or None.

        Pluggy hooks return a list of all plugin results; for source
        hooks where exactly one plugin should match a given
        ``source_id``, we collapse to that one (or ``None``).
        """
        if self.pm is None:
            return None
        try:
            hook = getattr(self.pm.hook, hook_name)
        except AttributeError:
            return None
        try:
            results = hook(**kwargs)
        except FileExistsError:
            # Caller-significant: cross-source collision (write hook
            # raised because dst already exists with overwrite=False).
            raise
        except Exception as e:  # noqa: BLE001 -- defensive
            logger.warning(
                "MigrationService: hook {h} raised: {e}", h=hook_name, e=e,
            )
            return None
        if not isinstance(results, list):
            return results
        return next((r for r in results if r is not None), None)

    def _read_bytes_via_hook(
        self, source_id: str, file_id: str,
    ) -> bytes | None:
        """Read a complete file's bytes via ``curator_source_read_bytes``.

        Loops in 64KB chunks until the plugin returns ``None`` or empty
        bytes (EOF). Returns ``None`` if no plugin owns this source_id
        (i.e. the very first chunk request returned ``None``).
        """
        chunks: list[bytes] = []
        offset = 0
        while True:
            chunk = self._hook_first_result(
                "curator_source_read_bytes",
                source_id=source_id, file_id=file_id,
                offset=offset, length=_HASH_CHUNK_SIZE,
            )
            if chunk is None:
                if offset == 0:
                    return None  # no plugin owned this source_id
                break  # plugin signaled EOF
            if not chunk:
                break  # empty bytes = EOF
            chunks.append(chunk)
            if len(chunk) < _HASH_CHUNK_SIZE:
                break  # short read = last chunk
            offset += len(chunk)
        return b"".join(chunks)

    @retry_transient_errors
    def _cross_source_transfer(
        self,
        *,
        src_source_id: str,
        src_file_id: str,
        src_xxhash: str | None,
        dst_source_id: str,
        dst_path: str,
        verify_hash: bool,
    ) -> tuple[MigrationOutcome, str, str | None]:
        """Read bytes from src via hook, write to dst via hook, verify.

        Returns ``(outcome, actual_dst_file_id, verified_hash)``.

        Per docs/TRACER_PHASE_2_DESIGN.md §5.3, verification re-streams
        from the destination via ``curator_source_read_bytes`` and
        recomputes the xxhash3_128 to compare against the source hash.
        Works for any pair of registered source plugins as long as both
        implement ``curator_source_read_bytes`` and the destination
        implements ``curator_source_write``.

        On ``HASH_MISMATCH``: dst is deleted (best-effort), src untouched.
        On ``SKIPPED_COLLISION``: dst already existed, nothing written.
        Any other unexpected condition raises -- caller turns that into
        ``MigrationOutcome.FAILED`` with an error message.

        Note: per the v0.40 hookspec contract, ``curator_source_write``
        takes ``data: bytes`` (whole-file in-memory). Streaming is
        Phase γ+. So this helper buffers the full file in RAM.
        """
        # Step 1: read src bytes via hook
        src_bytes = self._read_bytes_via_hook(src_source_id, src_file_id)
        if src_bytes is None:
            raise RuntimeError(
                f"cross-source: no plugin handled curator_source_read_bytes "
                f"for src_source_id={src_source_id!r}"
            )

        # Step 2: src hash -- prefer cached, else compute from in-memory bytes
        computed_src_hash: str | None = None
        if verify_hash:
            computed_src_hash = (
                src_xxhash or xxhash.xxh3_128(src_bytes).hexdigest()
            )

        # Step 3: write dst via hook (whole bytes in memory per hookspec)
        dst_p = Path(dst_path)
        parent_id = str(dst_p.parent)
        name = dst_p.name
        try:
            write_result = self._hook_first_result(
                "curator_source_write",
                source_id=dst_source_id,
                parent_id=parent_id,
                name=name,
                data=src_bytes,
                mtime=None,
                overwrite=False,
            )
        except FileExistsError:
            return (MigrationOutcome.SKIPPED_COLLISION, dst_path, None)
        if write_result is None:
            raise RuntimeError(
                f"cross-source: no plugin handled curator_source_write "
                f"for dst_source_id={dst_source_id!r}"
            )
        actual_dst_file_id = write_result.file_id

        # Step 4-5: verify by re-reading the dst back through the hook
        verified_hash: str | None = None
        if verify_hash:
            dst_bytes_back = self._read_bytes_via_hook(
                dst_source_id, actual_dst_file_id,
            )
            if dst_bytes_back is None:
                # Can't verify -- delete dst and propagate as FAILED
                self._hook_first_result(
                    "curator_source_delete",
                    source_id=dst_source_id,
                    file_id=actual_dst_file_id,
                    to_trash=False,
                )
                raise RuntimeError(
                    f"cross-source verify failed: couldn't re-read dst "
                    f"{dst_source_id}/{actual_dst_file_id}"
                )
            verified_hash = xxhash.xxh3_128(dst_bytes_back).hexdigest()
            if computed_src_hash and verified_hash != computed_src_hash:
                # Mismatch: delete dst (best-effort), return HASH_MISMATCH
                self._hook_first_result(
                    "curator_source_delete",
                    source_id=dst_source_id,
                    file_id=actual_dst_file_id,
                    to_trash=False,
                )
                return (
                    MigrationOutcome.HASH_MISMATCH,
                    actual_dst_file_id, verified_hash,
                )

        # Step 6: post-write notification (v1.1.1+).
        # The bytes are written, hash-verified (if requested), and
        # we're about to return success. Fire ``curator_source_write_post``
        # so plugins like ``curatorplug-atrium-safety`` can perform an
        # INDEPENDENT verification by re-reading dst and comparing hashes
        # themselves. If a safety plugin raises here (e.g.
        # ``ComplianceError``), the exception propagates and the caller
        # turns it into ``MigrationOutcome.FAILED``.
        self._invoke_post_write_hook(
            source_id=dst_source_id,
            file_id=actual_dst_file_id,
            src_xxhash=computed_src_hash,
            written_bytes_len=len(src_bytes),
        )

        return (MigrationOutcome.MOVED, actual_dst_file_id, verified_hash)

    def _invoke_post_write_hook(
        self,
        *,
        source_id: str,
        file_id: str,
        src_xxhash: str | None,
        written_bytes_len: int,
    ) -> None:
        """Fire ``curator_source_write_post`` after a successful write.

        v1.1.1+. Lets plugins (e.g. ``curatorplug-atrium-safety``) do an
        independent post-write verification. Pluggy invokes ALL plugins
        implementing the hook; exceptions from any plugin propagate to
        the caller (this is intentional -- it's how safety plugins
        *refuse* a non-compliant write by raising ``ComplianceError``).

        If the service was constructed without a ``pm``, the hook is a
        no-op (the post-write notification simply doesn't fire). This
        keeps the existing test fixtures that build MigrationService
        with ``pm=None`` working without modification.

        Note this method does NOT swallow exceptions. The caller (a
        per-file path in ``_cross_source_transfer``) is already inside
        an exception-boundary that turns hook-raised exceptions into
        ``MigrationOutcome.FAILED`` with the exception's message in
        ``MigrationMove.error`` -- which is exactly the soft-enforcement
        UX the safety plugin's design (DM-1) ratified.
        """
        if self.pm is None:
            return
        try:
            hook = getattr(self.pm.hook, "curator_source_write_post")
        except AttributeError:
            return  # hookspec not registered -- gracefully no-op
        hook(
            source_id=source_id,
            file_id=file_id,
            src_xxhash=src_xxhash,
            written_bytes_len=written_bytes_len,
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
        keep_source: bool = False,
        on_progress: Callable[[MigrationProgress], None] | None = None,
        max_retries: Any = _UNCHANGED,
        on_conflict: Any = _UNCHANGED,
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
            keep_source: When True, after dst is created and verified,
                leave src untouched AND skip the FileEntity index
                update. Per-file outcome is :attr:`MigrationOutcome.COPIED`;
                audit action is ``migration.copy``. Default False
                (move semantics: index re-pointed + src trashed).
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

        # v1.4.1: resolve effective retries via three-tier precedence:
        #   1. Explicit kwarg (``max_retries=N``) -> always wins
        #   2. Persisted ``job.options['max_retries']`` -> if no explicit kwarg
        #   3. Current ``self._max_retries`` -> if neither of the above
        # The sentinel default lets us distinguish "caller passed nothing"
        # from "caller explicitly passed 3" -- the previous code couldn't
        # tell them apart and used 3-as-magic-default for the inheritance
        # check. Resumed jobs still inherit their original retry policy
        # without the user having to re-specify the flag, but a fresh
        # call without kwargs preserves any prior `set_max_retries()`.
        if max_retries is not _UNCHANGED:
            self.set_max_retries(max_retries)
        else:
            try:
                persisted = job.options.get("max_retries") if job.options else None
            except (AttributeError, TypeError):
                persisted = None
            if persisted is not None:
                try:
                    self.set_max_retries(int(persisted))
                except (TypeError, ValueError):
                    pass  # leave self._max_retries unchanged
            # else: leave self._max_retries unchanged (sticky from set_max_retries() or __init__)

        # v1.4.1: same three-tier resolution for on_conflict.
        if on_conflict is not _UNCHANGED:
            try:
                self.set_on_conflict_mode(on_conflict)
            except ValueError:
                # Caller passed an invalid mode; surface ValueError so
                # the CLI can show a clean error before the migration
                # starts (matches pre-v1.4.1 behavior).
                raise
        else:
            try:
                persisted_mode = (
                    job.options.get("on_conflict") if job.options else None
                )
            except (AttributeError, TypeError):
                persisted_mode = None
            if persisted_mode is not None:
                try:
                    self.set_on_conflict_mode(str(persisted_mode))
                except ValueError:
                    # Persisted options had a stale/unknown value; fall
                    # back to skip rather than refusing to resume.
                    logger.warning(
                        "MigrationService.run_job: invalid persisted "
                        "on_conflict={m!r}, falling back to skip",
                        m=persisted_mode,
                    )
                    self.set_on_conflict_mode("skip")
            # else: leave self._on_conflict_mode unchanged

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
        # Phase 2 Session B: workers need src + dst source IDs to
        # decide whether to use the cross-source hook path or the
        # same-source shutil fast path.
        src_source_id = job.src_source_id
        dst_source_id = job.dst_source_id
        try:
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = [
                    pool.submit(
                        self._worker_loop, job_id,
                        src_source_id, dst_source_id,
                        verify_hash, keep_source, abort_event, on_progress,
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
        src_source_id: str,
        dst_source_id: str,
        verify_hash: bool,
        keep_source: bool,
        abort_event: threading.Event,
        on_progress: Callable[[MigrationProgress], None] | None,
    ) -> None:
        """Per-worker loop: claim a row, execute, record outcome, repeat.

        Exits when ``abort_event`` is set OR no pending rows remain.
        Per-file work is atomic (no mid-file abort).

        Session B: ``src_source_id`` + ``dst_source_id`` are passed to
        :meth:`_execute_one_persistent` so it can dispatch to the
        cross-source hook path when they differ.
        """
        while not abort_event.is_set():
            progress = self.migration_jobs.next_pending_progress(job_id)
            if progress is None:
                return  # queue empty

            try:
                outcome, verified_hash = self._execute_one_persistent(
                    progress,
                    verify_hash=verify_hash,
                    keep_source=keep_source,
                    src_source_id=src_source_id,
                    dst_source_id=dst_source_id,
                )

                if outcome in (
                    MigrationOutcome.MOVED, MigrationOutcome.COPIED,
                    MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP,
                    MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX,
                ):
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

            except MigrationConflictError as e:  # noqa: BLE001 -- Phase 3 P2 fail mode
                # --on-conflict=fail collision; record FAILED_DUE_TO_CONFLICT
                # specifically (distinct from generic FAILED) so the
                # report's failed_count includes it AND audit log queries
                # can find conflict-specific failures.
                self.migration_jobs.update_progress(
                    job_id, progress.curator_id,
                    status="failed",
                    outcome=MigrationOutcome.FAILED_DUE_TO_CONFLICT.value,
                    error=str(e),
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
        keep_source: bool = False,
        src_source_id: str | None = None,
        dst_source_id: str | None = None,
    ) -> tuple[MigrationOutcome, str | None]:
        """Per-file Hash-Verify-Before-Move for the persistent path.

        Returns ``(outcome, verified_xxhash_or_None)``. Caller (worker
        loop) records the outcome to ``migration_progress``.

        Dispatches on whether the migration crosses a source boundary:
        same-source uses the in-process ``shutil.copy2`` fast path;
        cross-source uses :meth:`_cross_source_transfer` (hooks).

        When ``keep_source`` is True, after dst is created and verified,
        steps 6 (index update) and 7 (trash) are skipped; outcome is
        :attr:`MigrationOutcome.COPIED` and audit action is
        ``migration.copy`` (not ``migration.move``).

        Raises on unexpected exceptions; the worker catches and records
        ``status='failed', outcome='failed'``.
        """
        # Default to same-source if source IDs weren't provided
        # (backward-compat with pre-Session-B callers).
        if dst_source_id is None:
            dst_source_id = src_source_id
        cross_source = (
            src_source_id is not None
            and dst_source_id is not None
            and self._is_cross_source(src_source_id, dst_source_id)
        )
        if cross_source:
            return self._execute_one_persistent_cross_source(
                progress, verify_hash=verify_hash, keep_source=keep_source,
                src_source_id=src_source_id, dst_source_id=dst_source_id,
            )
        # v1.6.1: pass source_id (src == dst for same-source) for the
        # same audit-detail symmetry as phase 1; see _execute_one_same_source.
        return self._execute_one_persistent_same_source(
            progress, verify_hash=verify_hash, keep_source=keep_source,
            source_id=src_source_id,
        )

    def _execute_one_persistent_same_source(
        self,
        progress: MigrationProgress,
        *,
        verify_hash: bool,
        keep_source: bool = False,
        source_id: str | None = None,
    ) -> tuple[MigrationOutcome, str | None]:
        """Same-source persistent path (existing Phase 2 fast path).

        Uses ``shutil.copy2`` for the bytes transfer and re-reads the dst
        file from the local filesystem to compute the verification hash.

        v1.6.1: ``source_id`` (defaults to None for backward compat) is
        included in inline audit emissions so phase 2 same-source events
        carry the same ``cross_source`` / ``src_source_id`` /
        ``dst_source_id`` keys that phase 2 cross-source events do.
        """
        src_p = Path(progress.src_path)
        dst_p = Path(progress.dst_path)

        # Defensive collision check at apply time (the row was pending
        # so it wasn't pre-skipped, but a parallel migration could have
        # created the dst file in the meantime). Phase 3 P2: dispatch on
        # self._on_conflict_mode -- run_job already configured it from
        # the run_job parameter or persisted job.options['on_conflict'].
        outcome_override: MigrationOutcome | None = None
        if dst_p.exists():
            short_circuit, outcome_override, new_dst, conflict_error = (
                self._resolve_collision_for_progress(progress, dst_p)
            )
            if short_circuit:
                if conflict_error is not None:
                    # fail mode (or backup/rename setup error) -- worker
                    # records FAILED_DUE_TO_CONFLICT against the progress row.
                    raise MigrationConflictError(
                        progress.dst_path, src_path=progress.src_path,
                    )
                return (MigrationOutcome.SKIPPED_COLLISION, None)
            # Backup/rename succeeded -- continue with the prepared dst.
            if new_dst is not None:
                progress.dst_path = str(new_dst)
                dst_p = new_dst

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

        if keep_source:
            # keep-source: dst created+verified, src untouched, index NOT updated
            if self.audit is not None:
                try:
                    details: dict[str, Any] = {
                        "src_path": progress.src_path,
                        "dst_path": progress.dst_path,
                        "size": progress.size,
                        "xxhash3_128": verified_hash or src_hash,
                        "job_id": str(progress.job_id),
                    }
                    if source_id is not None:
                        # v1.6.1: same-source events carry src/dst source_id
                        # (both equal) and cross_source=False for schema parity
                        # with phase 2 cross-source emissions.
                        details["src_source_id"] = source_id
                        details["dst_source_id"] = source_id
                        details["cross_source"] = False
                    entry = AuditEntry(
                        actor="curator.migrate",
                        action="migration.copy",
                        entity_type="file",
                        entity_id=str(progress.curator_id),
                        details=details,
                    )
                    self.audit.insert(entry)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "MigrationService: audit append failed for {cid}: {e}",
                        cid=progress.curator_id, e=e,
                    )
            return (MigrationOutcome.COPIED, verified_hash)

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
                details: dict[str, Any] = {
                    "src_path": progress.src_path,
                    "dst_path": progress.dst_path,
                    "size": progress.size,
                    "xxhash3_128": verified_hash or src_hash,
                    "job_id": str(progress.job_id),
                }
                if source_id is not None:
                    # v1.6.1: see _audit_move/_audit_copy v1.6.1 docstring
                    details["src_source_id"] = source_id
                    details["dst_source_id"] = source_id
                    details["cross_source"] = False
                entry = AuditEntry(
                    actor="curator.migrate",
                    action="migration.move",
                    entity_type="file",
                    entity_id=str(progress.curator_id),
                    details=details,
                )
                self.audit.insert(entry)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "MigrationService: audit append failed for {cid}: {e}",
                    cid=progress.curator_id, e=e,
                )

        # Phase 3 P2: if conflict resolution prepared dst, surface the variant
        # outcome (MOVED_OVERWROTE_WITH_BACKUP / MOVED_RENAMED_WITH_SUFFIX) so the
        # report distinguishes them from plain MOVED.
        if outcome_override is not None:
            return (outcome_override, verified_hash)
        return (MigrationOutcome.MOVED, verified_hash)

    def _execute_one_persistent_cross_source(
        self,
        progress: MigrationProgress,
        *,
        verify_hash: bool,
        keep_source: bool = False,
        src_source_id: str,
        dst_source_id: str,
    ) -> tuple[MigrationOutcome, str | None]:
        """Cross-source persistent path (Session B).

        Uses :meth:`_cross_source_transfer` for the bytes phase, then
        updates the FileEntity's ``source_id`` AND ``source_path`` (both
        change for cross-source moves) and trashes the src via
        ``curator_source_delete``. Audit entries include the job_id
        for cross-reference.
        """
        outcome, actual_dst_file_id, verified_hash = (
            self._cross_source_transfer(
                src_source_id=src_source_id,
                src_file_id=progress.src_path,
                src_xxhash=progress.src_xxhash,
                dst_source_id=dst_source_id,
                dst_path=progress.dst_path,
                verify_hash=verify_hash,
            )
        )

        if outcome == MigrationOutcome.HASH_MISMATCH:
            return (outcome, verified_hash)
        # Phase 4 P2: cross-source collision dispatch (mirrors
        # _execute_one_cross_source's apply-time path).
        # On SKIPPED_COLLISION, dispatch on self._on_conflict_mode:
        #   skip                  -> return SKIPPED_COLLISION (v1.2.0 behavior)
        #   fail                  -> raise MigrationConflictError (worker maps to FAILED_DUE_TO_CONFLICT)
        #   overwrite-with-backup -> _cross_source_overwrite_with_backup_for_progress;
        #                            on success, finalize as MOVED_OVERWROTE_WITH_BACKUP
        #   rename-with-suffix    -> _cross_source_rename_with_suffix_for_progress;
        #                            on success, mutate progress.dst_path + finalize as
        #                            MOVED_RENAMED_WITH_SUFFIX
        # Plugins that don't implement curator_source_rename or 9999
        # exhaustion degrade to v1.3.0 skip-with-warning behavior
        # (helpers emit audit + return None).
        final_outcome = MigrationOutcome.MOVED
        if outcome == MigrationOutcome.SKIPPED_COLLISION:
            mode = self._on_conflict_mode
            if mode == "skip":
                return (outcome, None)
            if mode == "fail":
                if self.audit is not None:
                    try:
                        self.audit.log(
                            actor="curator.migrate",
                            action="migration.conflict_resolved",
                            entity_type="file",
                            entity_id=str(progress.curator_id),
                            details={
                                "src_path": progress.src_path,
                                "dst_path": progress.dst_path,
                                "mode": "fail",
                                "size": progress.size,
                                "job_id": str(progress.job_id),
                                "cross_source": True,
                            },
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "MigrationService: conflict audit failed for {cid}: {e}",
                            cid=progress.curator_id, e=e,
                        )
                raise MigrationConflictError(
                    progress.dst_path, src_path=progress.src_path,
                )
            if mode == "overwrite-with-backup":
                retry_result = (
                    self._cross_source_overwrite_with_backup_for_progress(
                        progress, verify_hash=verify_hash,
                        src_source_id=src_source_id,
                        dst_source_id=dst_source_id,
                    )
                )
                if retry_result is None:
                    return (MigrationOutcome.SKIPPED_COLLISION, None)
                retry_outcome2, actual_dst_file_id, verified_hash = retry_result
                if retry_outcome2 != MigrationOutcome.MOVED:
                    # HASH_MISMATCH or unexpected SKIPPED_COLLISION on retry.
                    return (retry_outcome2, verified_hash)
                final_outcome = MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP
            elif mode == "rename-with-suffix":
                retry_result = (
                    self._cross_source_rename_with_suffix_for_progress(
                        progress, verify_hash=verify_hash,
                        src_source_id=src_source_id,
                        dst_source_id=dst_source_id,
                    )
                )
                if retry_result is None:
                    return (MigrationOutcome.SKIPPED_COLLISION, None)
                retry_outcome2, actual_dst_file_id, verified_hash = retry_result
                if retry_outcome2 != MigrationOutcome.MOVED:
                    return (retry_outcome2, verified_hash)
                final_outcome = MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX
                # Update progress.dst_path so post-transfer audit + entity
                # update use the suffix variant. The DB row's dst_path is
                # not persisted-update here; the audit + entity reflect
                # the actual final dst.
                progress.dst_path = actual_dst_file_id
            else:
                # Unreachable given set_on_conflict_mode validation, defensive.
                return (outcome, None)

        # Bytes successfully transferred + verified.
        if keep_source:
            # keep-source: dst created+verified, src untouched, index NOT updated
            if self.audit is not None:
                try:
                    entry = AuditEntry(
                        actor="curator.migrate",
                        action="migration.copy",
                        entity_type="file",
                        entity_id=str(progress.curator_id),
                        details={
                            "src_source_id": src_source_id,
                            "src_path": progress.src_path,
                            "dst_source_id": dst_source_id,
                            "dst_path": actual_dst_file_id,
                            "size": progress.size,
                            "xxhash3_128": verified_hash or progress.src_xxhash,
                            "job_id": str(progress.job_id),
                            "cross_source": True,
                        },
                    )
                    self.audit.insert(entry)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "MigrationService: audit append failed for {cid}: {e}",
                        cid=progress.curator_id, e=e,
                    )
            return (MigrationOutcome.COPIED, verified_hash)

        # Step 6: update FileEntity -- BOTH source_id AND source_path change
        entity = self.files.get(progress.curator_id)
        if entity is None:
            raise RuntimeError(
                f"FileEntity {progress.curator_id} vanished during migration"
            )
        entity.source_id = dst_source_id
        entity.source_path = actual_dst_file_id
        self.files.update(entity)

        # Step 7: trash src via plugin hook (to_trash=True is recoverable)
        try:
            self._hook_first_result(
                "curator_source_delete",
                source_id=src_source_id,
                file_id=progress.src_path,
                to_trash=True,
            )
        except Exception as e:  # noqa: BLE001 -- trash failure is non-fatal
            logger.warning(
                "MigrationService: cross-source trash failed for {p}: {e}",
                p=progress.src_path, e=e,
            )

        # Step 8: audit (with job_id + cross_source marker)
        if self.audit is not None:
            try:
                entry = AuditEntry(
                    actor="curator.migrate",
                    action="migration.move",
                    entity_type="file",
                    entity_id=str(progress.curator_id),
                    details={
                        "src_source_id": src_source_id,
                        "src_path": progress.src_path,
                        "dst_source_id": dst_source_id,
                        "dst_path": actual_dst_file_id,
                        "size": progress.size,
                        "xxhash3_128": verified_hash or progress.src_xxhash,
                        "job_id": str(progress.job_id),
                        "cross_source": True,
                    },
                )
                self.audit.insert(entry)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "MigrationService: audit append failed for {cid}: {e}",
                    cid=progress.curator_id, e=e,
                )

        return (final_outcome, verified_hash)

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
            started_at=job.started_at or utcnow_naive(),
            completed_at=job.completed_at,
        )


__all__ = [
    "MigrationOutcome",
    "MigrationConflictError",
    "MigrationMove",
    "MigrationPlan",
    "MigrationReport",
    "MigrationService",
]
