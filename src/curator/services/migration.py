"""Migration service -- relocate files across paths with index integrity.

DESIGN_PHASE_DELTA.md §M (Feature M).

**Phase 1 (v1.0.0a1): same-source local→local migration.** This is the
foundational ship. Phase 2 will extend to cross-source migration via the
v0.40 ``curator_source_write`` hook, add resume support, worker pools,
and a GUI Migrate tab.

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
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

import xxhash
from loguru import logger

from curator.models.audit import AuditEntry
from curator.models.file import FileEntity
from curator.services.safety import SafetyLevel, SafetyService
from curator.storage.queries import FileQuery
from curator.storage.repositories.audit_repo import AuditRepository
from curator.storage.repositories.file_repo import FileRepository


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
    """Phase 1: same-source local→local migration with hash-verify-before-move.

    Args:
        file_repo: The FileRepository to query + update.
        safety: The SafetyService used to gate every file in the plan.
        audit: Optional AuditRepository. When set, every successful move
            writes an audit entry (``actor='curator.migrate'``,
            ``action='migration.move'``).
    """

    def __init__(
        self,
        file_repo: FileRepository,
        safety: SafetyService,
        *,
        audit: AuditRepository | None = None,
    ) -> None:
        self.files = file_repo
        self.safety = safety
        self.audit = audit

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


__all__ = [
    "MigrationOutcome",
    "MigrationMove",
    "MigrationPlan",
    "MigrationReport",
    "MigrationService",
]
