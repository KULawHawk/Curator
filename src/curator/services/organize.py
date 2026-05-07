"""Organize service — plan / stage / revert for the F1 smart drive organizer.

Phase Gamma Milestones Gamma-1 + Gamma-2. Three modes:

  * **Plan mode** (v0.20) — :meth:`plan` walks the indexed files for
    a source and partitions them into SAFE / CAUTION / REFUSE buckets
    via :class:`SafetyService`. With ``organize_type="music"`` and a
    target_root, also proposes canonical destination paths via
    :class:`MusicService`.

  * **Stage mode** (v0.22) — :meth:`stage` actually moves SAFE-bucket
    files with proposals into a staging tree rooted at ``stage_root``.
    The staging layout mirrors what apply mode would produce, but
    rooted at a different directory so the user can review before
    committing. Writes a JSON manifest at ``<stage_root>/.curator_stage_manifest.json``
    so :meth:`revert_stage` can undo every move.

  * **Revert mode** (v0.22) — :meth:`revert_stage` reads the manifest
    and moves each staged file back to its original location. If the
    original path is now occupied (e.g. user copied something else
    there during review), the file is left in staging and reported.

  * **Apply mode** (v0.23, future) — moves staged files from
    ``stage_root`` to ``target_root``. Equivalent to running stage with
    ``stage_root == target_root``, but with extra collision-handling
    UX in the CLI layer.

Audit + reversibility: every stage move and every revert move writes
an audit entry via :class:`AuditRepository` so the operation is
introspectable after the fact even if the manifest gets deleted.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable

from loguru import logger

from curator.models.file import FileEntity
from curator.services.code_project import CodeProject, CodeProjectService
from curator.services.music import MusicService
from curator.services.musicbrainz import MusicBrainzClient
from curator.services.document import DocumentService
from curator.services.photo import PhotoService
from curator.services.safety import (
    SafetyConcern,
    SafetyLevel,
    SafetyReport,
    SafetyService,
)
from curator.storage.queries import FileQuery
from curator.storage.repositories.audit_repo import AuditRepository
from curator.storage.repositories.file_repo import FileRepository


# Manifest filename written into the stage_root after a stage operation.
# The leading dot keeps it out of the way of the user's organized files.
STAGE_MANIFEST_NAME = ".curator_stage_manifest.json"


@dataclass
class OrganizeBucket:
    """Files grouped into one safety level, with summary stats.

    Concerns are aggregated across all files in the bucket so we can
    report things like "312 CAUTION files: 89 are app-data, 201 are
    inside a project, 22 are symlinks." A single file may appear under
    multiple concern keys if it had multiple concerns.

    ``proposals`` maps a file's curator_id (as str) to a proposed
    destination path. Populated by ``OrganizeService.plan`` when a
    type-specific pipeline (e.g. ``--type music``) is active. Empty
    when running the basic plan with no template.
    """

    files: list[FileEntity] = field(default_factory=list)
    total_size: int = 0
    by_concern: dict[SafetyConcern, list[FileEntity]] = field(
        default_factory=lambda: defaultdict(list)
    )
    proposals: dict[str, str] = field(default_factory=dict)

    def add(
        self,
        file: FileEntity,
        report: SafetyReport,
        *,
        proposed_destination: str | None = None,
    ) -> None:
        """Append ``file`` to this bucket and record its concerns."""
        self.files.append(file)
        self.total_size += file.size or 0
        for concern, _detail in report.concerns:
            self.by_concern[concern].append(file)
        if proposed_destination is not None:
            self.proposals[str(file.curator_id)] = proposed_destination

    @property
    def count(self) -> int:
        return len(self.files)

    def concern_counts(self) -> dict[SafetyConcern, int]:
        return {k: len(v) for k, v in self.by_concern.items()}


@dataclass
class OrganizePlan:
    """Result of :meth:`OrganizeService.plan`."""

    source_id: str
    root_prefix: str | None
    safe: OrganizeBucket = field(default_factory=OrganizeBucket)
    caution: OrganizeBucket = field(default_factory=OrganizeBucket)
    refuse: OrganizeBucket = field(default_factory=OrganizeBucket)
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    target_root: str | None = None  # set when organize_type was used

    @property
    def total_files(self) -> int:
        return self.safe.count + self.caution.count + self.refuse.count

    @property
    def total_size(self) -> int:
        return self.safe.total_size + self.caution.total_size + self.refuse.total_size

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    def bucket_for(self, level: SafetyLevel) -> OrganizeBucket:
        return {
            SafetyLevel.SAFE: self.safe,
            SafetyLevel.CAUTION: self.caution,
            SafetyLevel.REFUSE: self.refuse,
        }[level]


# ---------------------------------------------------------------------------
# Stage / revert types
# ---------------------------------------------------------------------------


class StageOutcome(str, Enum):
    """Per-file outcome of a stage move."""

    MOVED = "moved"
    SKIPPED_NO_PROPOSAL = "skipped_no_proposal"  # SAFE but no destination proposed
    SKIPPED_COLLISION = "skipped_collision"      # destination already exists
    FAILED = "failed"                            # IOError, permission, etc.


@dataclass
class StageMove:
    """Record of a single file's stage operation."""

    curator_id: str
    original: str
    staged: str | None  # None when outcome != MOVED
    outcome: StageOutcome
    error: str | None = None


@dataclass
class StageReport:
    """Summary of a :meth:`OrganizeService.stage` call."""

    stage_root: str
    started_at: datetime
    completed_at: datetime | None = None
    moves: list[StageMove] = field(default_factory=list)

    @property
    def moved_count(self) -> int:
        return sum(1 for m in self.moves if m.outcome == StageOutcome.MOVED)

    @property
    def skipped_count(self) -> int:
        return sum(
            1 for m in self.moves
            if m.outcome in (StageOutcome.SKIPPED_NO_PROPOSAL, StageOutcome.SKIPPED_COLLISION)
        )

    @property
    def failed_count(self) -> int:
        return sum(1 for m in self.moves if m.outcome == StageOutcome.FAILED)

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


class RevertOutcome(str, Enum):
    """Per-file outcome of a revert."""

    RESTORED = "restored"                        # back at original path
    SKIPPED_ORIGINAL_OCCUPIED = "skipped_original_occupied"
    SKIPPED_STAGED_MISSING = "skipped_staged_missing"
    FAILED = "failed"


@dataclass
class RevertMove:
    curator_id: str
    original: str
    staged: str
    outcome: RevertOutcome
    error: str | None = None


@dataclass
class RevertReport:
    stage_root: str
    started_at: datetime
    completed_at: datetime | None = None
    moves: list[RevertMove] = field(default_factory=list)

    @property
    def restored_count(self) -> int:
        return sum(1 for m in self.moves if m.outcome == RevertOutcome.RESTORED)

    @property
    def skipped_count(self) -> int:
        return sum(
            1 for m in self.moves
            if m.outcome in (
                RevertOutcome.SKIPPED_ORIGINAL_OCCUPIED,
                RevertOutcome.SKIPPED_STAGED_MISSING,
            )
        )

    @property
    def failed_count(self) -> int:
        return sum(1 for m in self.moves if m.outcome == RevertOutcome.FAILED)

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


# ---------------------------------------------------------------------------
# OrganizeService
# ---------------------------------------------------------------------------


class OrganizeService:
    """Plan / stage / revert organize operations.

    Args:
        file_repo: Curator's :class:`FileRepository`.
        safety: a configured :class:`SafetyService`.
        music: a :class:`MusicService` (default-constructed if None).
        audit: optional :class:`AuditRepository`. When provided, stage
            and revert operations write audit entries. Plan mode never
            writes audit entries (it's read-only).
    """

    def __init__(
        self,
        file_repo: FileRepository,
        safety: SafetyService,
        music: MusicService | None = None,
        photo: PhotoService | None = None,
        document: DocumentService | None = None,
        code: CodeProjectService | None = None,
        mb_client: MusicBrainzClient | None = None,
        audit: AuditRepository | None = None,
    ) -> None:
        self.files = file_repo
        self.safety = safety
        self.music = music or MusicService()
        self.photo = photo or PhotoService()
        self.document = document or DocumentService()
        self.code = code or CodeProjectService()
        self.mb_client = mb_client  # None unless caller opts in
        self.audit = audit

    # ------------------------------------------------------------------
    # Plan mode
    # ------------------------------------------------------------------

    def plan(
        self,
        *,
        source_id: str,
        root_prefix: str | None = None,
        check_handles: bool = False,
        limit: int | None = None,
        organize_type: str | None = None,
        target_root: str | Path | None = None,
        enrich_mb: bool = False,
    ) -> OrganizePlan:
        """Build an :class:`OrganizePlan` for indexed files in a source.

        See module docstring for behavior. Plan mode is read-only.
        """
        if organize_type is not None and target_root is None:
            raise ValueError(
                "target_root is required when organize_type is set"
            )

        plan = OrganizePlan(
            source_id=source_id,
            root_prefix=root_prefix,
            started_at=datetime.utcnow(),
            target_root=str(target_root) if target_root is not None else None,
        )

        query = FileQuery(
            source_ids=[source_id],
            source_path_starts_with=root_prefix,
            deleted=False,
            limit=limit,
        )
        try:
            files = self.files.query(query)
        except Exception as e:
            logger.error("organize: file query failed: {e}", e=e)
            plan.completed_at = datetime.utcnow()
            return plan

        # For organize_type="code", discover projects ONCE up front by
        # walking root_prefix (or the union of file dirs if no prefix).
        # We then reuse this list for per-file project lookup so we
        # don't re-walk the tree N times.
        code_projects: list[CodeProject] = []
        if organize_type == "code":
            code_walk_root = root_prefix
            if code_walk_root is None and files:
                # No prefix specified: walk the common ancestor of all
                # files. For a typical organize call this IS the user's
                # source root, so the cost is bounded.
                # Use the first file's drive root as a coarse fallback.
                code_walk_root = str(Path(files[0].source_path).anchor)
            if code_walk_root:
                try:
                    code_projects = self.code.find_projects(code_walk_root)
                except Exception as e:
                    logger.warning(
                        "organize: code project discovery failed: {e}", e=e,
                    )

        for f in files:
            try:
                report = self.safety.check_path(
                    f.source_path,
                    check_handles=check_handles,
                )
            except Exception as e:
                logger.warning(
                    "organize: safety check failed for {p}: {e}",
                    p=f.source_path, e=e,
                )
                plan.refuse.files.append(f)
                plan.refuse.total_size += f.size or 0
                continue

            bucket = plan.bucket_for(report.level)

            proposed: str | None = None
            # For music/photo/document modes: only propose destinations
            # for SAFE files (don't auto-suggest reorganizing music in
            # app-data, etc.). For code mode: project files inherently
            # land in CAUTION (the .git/ marker triggers the project-root
            # safety concern), so we propose for CAUTION too -- the
            # user has explicitly opted in by passing --type code.
            should_propose = (
                report.level == SafetyLevel.SAFE
                or (organize_type == "code" and report.level == SafetyLevel.CAUTION)
            )
            if should_propose:
                if (
                    organize_type == "music"
                    and self.music.is_audio_file(f.source_path)
                ):
                    meta = self.music.read_tags(f.source_path)
                    if meta is not None and enrich_mb and self.mb_client is not None:
                        # v0.32: optionally enrich filename-only tracks
                        # via MusicBrainz. Only fires for files where
                        # the v0.27 filename heuristic was the source
                        # AND at least one of album/year/track is blank.
                        used_filename = meta.raw.get("_filename_source") == "true"
                        needs_enrichment = (
                            meta.album is None
                            or meta.year is None
                            or meta.track_number is None
                        )
                        if used_filename and needs_enrichment:
                            try:
                                meta = self.music.enrich_via_musicbrainz(
                                    meta, self.mb_client,
                                )
                            except Exception as e:  # noqa: BLE001
                                logger.debug(
                                    "organize: MB enrich failed for {p}: {e}",
                                    p=f.source_path, e=e,
                                )
                    if meta is not None and meta.has_useful_tags:
                        dest = self.music.propose_destination(
                            meta,
                            original_path=f.source_path,
                            target_root=target_root,  # type: ignore[arg-type]
                        )
                        proposed = str(dest)
                elif (
                    organize_type == "photo"
                    and self.photo.is_photo_file(f.source_path)
                ):
                    pmeta = self.photo.read_metadata(f.source_path)
                    # For photos, ALWAYS propose a destination if we got
                    # any metadata back (even mtime fallback). That's how
                    # we make sure the entire library lands somewhere.
                    if pmeta is not None:
                        dest = self.photo.propose_destination(
                            pmeta,
                            original_path=f.source_path,
                            target_root=target_root,  # type: ignore[arg-type]
                        )
                        proposed = str(dest)
                elif (
                    organize_type == "document"
                    and self.document.is_document_file(f.source_path)
                ):
                    dmeta = self.document.read_metadata(f.source_path)
                    # Same as photos: always propose if we got metadata,
                    # since mtime fallback always succeeds for files
                    # that exist.
                    if dmeta is not None:
                        dest = self.document.propose_destination(
                            dmeta,
                            original_path=f.source_path,
                            target_root=target_root,  # type: ignore[arg-type]
                        )
                        proposed = str(dest)
                elif organize_type == "code" and code_projects:
                    # Find which project (if any) contains this file,
                    # then propose ``{target}/{lang}/{name}/{relpath}``.
                    project = self.code.find_project_containing(
                        f.source_path, code_projects,
                    )
                    if project is not None:
                        dest = self.code.propose_destination(
                            project,
                            file_path=f.source_path,
                            target_root=target_root,  # type: ignore[arg-type]
                        )
                        if dest is not None:
                            proposed = str(dest)

            bucket.add(f, report, proposed_destination=proposed)

        plan.completed_at = datetime.utcnow()
        logger.debug(
            "organize plan: {n} files in {d:.2f}s "
            "(safe={s}, caution={c}, refuse={r})",
            n=plan.total_files,
            d=plan.duration_seconds or 0.0,
            s=plan.safe.count,
            c=plan.caution.count,
            r=plan.refuse.count,
        )
        return plan

    # ------------------------------------------------------------------
    # Stage mode
    # ------------------------------------------------------------------

    def stage(
        self,
        plan: OrganizePlan,
        *,
        stage_root: str | Path,
        mode: str = "stage",
    ) -> StageReport:
        """Move SAFE-bucket files with proposals into a destination tree.

        For each file in ``plan.safe.files`` that has a proposed
        destination, computes the destination's path RELATIVE to
        ``plan.target_root`` and rewrites it under ``stage_root``.

        Example: with target_root=``/Music`` and a proposal at
        ``/Music/Pink Floyd/The Wall/06 - Comfortably Numb.mp3``, and
        stage_root=``/staging``, the file moves to
        ``/staging/Pink Floyd/The Wall/06 - Comfortably Numb.mp3``.

        Files in CAUTION / REFUSE are NOT moved (they're not safe).
        SAFE files without a proposal (e.g. a non-audio SAFE file when
        organize_type=music) are recorded as SKIPPED_NO_PROPOSAL.

        Writes a manifest at ``<stage_root>/.curator_stage_manifest.json``
        recording every successful move so :meth:`revert_stage` can undo
        them later. Each successful move also writes an audit entry.

        Args:
            plan: an OrganizePlan with target_root set (i.e. produced
                  with organize_type=...).
            stage_root: where files should land. For Stage mode this is
                  a transient staging directory; for Apply mode this is
                  ``plan.target_root`` itself (final library location).
            mode: ``"stage"`` (default) or ``"apply"``. Controls the
                  audit actor/action strings (``organize.stage.move`` vs
                  ``organize.apply.move``) so the audit log can
                  distinguish a transient stage from a final apply.
                  The on-disk behavior is identical.

        Raises:
            ValueError: ``plan.target_root`` is None (i.e. plan was
                built without ``organize_type``, so it has no proposals).
        """
        if plan.target_root is None:
            raise ValueError(
                "stage requires a plan built with organize_type/target_root; "
                "plan.target_root is None"
            )

        actor = f"curator.organize.{mode}"
        action = f"organize.{mode}.move"

        stage_root_p = Path(stage_root).resolve()
        target_root_p = Path(plan.target_root).resolve()
        report = StageReport(
            stage_root=str(stage_root_p),
            started_at=datetime.utcnow(),
        )

        stage_root_p.mkdir(parents=True, exist_ok=True)

        for f in plan.safe.files:
            cid = str(f.curator_id)
            proposal = plan.safe.proposals.get(cid)
            if proposal is None:
                report.moves.append(StageMove(
                    curator_id=cid,
                    original=f.source_path,
                    staged=None,
                    outcome=StageOutcome.SKIPPED_NO_PROPOSAL,
                ))
                continue

            try:
                proposal_p = Path(proposal).resolve()
                relative = proposal_p.relative_to(target_root_p)
            except ValueError as e:
                # Proposal isn't under target_root — shouldn't happen
                # under normal plan generation but defend anyway.
                logger.warning(
                    "stage: proposal {p} not under target_root {t}: {e}",
                    p=proposal, t=plan.target_root, e=e,
                )
                report.moves.append(StageMove(
                    curator_id=cid,
                    original=f.source_path,
                    staged=None,
                    outcome=StageOutcome.FAILED,
                    error=f"proposal not under target_root: {e}",
                ))
                continue

            staged = stage_root_p / relative

            if staged.exists():
                report.moves.append(StageMove(
                    curator_id=cid,
                    original=f.source_path,
                    staged=str(staged),
                    outcome=StageOutcome.SKIPPED_COLLISION,
                ))
                continue

            try:
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(f.source_path, str(staged))
            except Exception as e:
                logger.error(
                    "stage move failed for {p}: {e}",
                    p=f.source_path, e=e,
                )
                report.moves.append(StageMove(
                    curator_id=cid,
                    original=f.source_path,
                    staged=str(staged),
                    outcome=StageOutcome.FAILED,
                    error=str(e),
                ))
                continue

            report.moves.append(StageMove(
                curator_id=cid,
                original=f.source_path,
                staged=str(staged),
                outcome=StageOutcome.MOVED,
            ))

            # v0.33: keep the index in sync with the on-disk move.
            # Update the FileEntity's source_path to the new location
            # so subsequent FileQuery / find_by_path calls reflect
            # reality rather than returning a phantom at the old path.
            self._sync_index_after_move(cid, str(staged))

            # Audit entry per move. Actor + action reflect mode
            # (stage vs apply) so audit consumers can distinguish.
            if self.audit is not None:
                try:
                    self.audit.log(
                        actor=actor,
                        action=action,
                        entity_type="file",
                        entity_id=cid,
                        details={
                            "original": f.source_path,
                            "staged": str(staged),
                            "stage_root": str(stage_root_p),
                            "target_root": plan.target_root,
                            "mode": mode,
                        },
                    )
                except Exception as e:  # pragma: no cover — defensive
                    logger.warning("{m} audit log failed: {e}", m=mode, e=e)

        report.completed_at = datetime.utcnow()

        # Write/merge the manifest. If a manifest already exists at the
        # stage_root (e.g. a prior partial run), merge our new entries
        # on top of it so revert can find both.
        self._write_manifest(stage_root_p, report)

        logger.info(
            "{mode} complete: moved={m} skipped={s} failed={f} in {d:.2f}s",
            mode=mode,
            m=report.moved_count,
            s=report.skipped_count,
            f=report.failed_count,
            d=report.duration_seconds or 0.0,
        )
        return report

    # ------------------------------------------------------------------
    # Apply mode
    # ------------------------------------------------------------------

    def apply(self, plan: OrganizePlan) -> StageReport:
        """Move SAFE-bucket files with proposals into their final destinations.

        Apply is the same machinery as :meth:`stage` but the destination
        IS ``plan.target_root`` — files land in their canonical home
        rather than a transient staging tree. The on-disk effect is
        equivalent to ``stage(plan, stage_root=plan.target_root)`` but
        with audit entries tagged ``organize.apply.move`` so audit
        consumers can tell a final apply apart from a preview stage.

        A manifest is still written at
        ``<target_root>/.curator_stage_manifest.json`` so
        :meth:`revert_stage` can undo the apply if the user changes
        their mind. The manifest filename keeps the ``stage`` prefix
        for backward compatibility with v0.22 manifests on disk.

        Returns:
            A :class:`StageReport`. Field semantics are unchanged.

        Raises:
            ValueError: ``plan.target_root`` is None.
        """
        if plan.target_root is None:
            raise ValueError(
                "apply requires a plan built with organize_type/target_root; "
                "plan.target_root is None"
            )
        return self.stage(
            plan,
            stage_root=plan.target_root,
            mode="apply",
        )

    def _write_manifest(
        self, stage_root: Path, report: StageReport
    ) -> None:
        """Append successful moves to the manifest file under stage_root."""
        manifest_path = stage_root / STAGE_MANIFEST_NAME
        existing: list[dict] = []
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except (OSError, json.JSONDecodeError):
                logger.warning(
                    "existing manifest at {p} is unreadable; rewriting",
                    p=manifest_path,
                )
                existing = []

        for move in report.moves:
            if move.outcome != StageOutcome.MOVED:
                continue
            existing.append({
                "curator_id": move.curator_id,
                "original": move.original,
                "staged": move.staged,
                "moved_at": report.started_at.isoformat(),
            })

        try:
            manifest_path.write_text(
                json.dumps(existing, indent=2), encoding="utf-8"
            )
        except OSError as e:
            logger.error(
                "failed to write stage manifest {p}: {e}",
                p=manifest_path, e=e,
            )

    # ------------------------------------------------------------------
    # Revert mode
    # ------------------------------------------------------------------

    def revert_stage(self, stage_root: str | Path) -> RevertReport:
        """Move every staged file back to its original location.

        Reads ``<stage_root>/.curator_stage_manifest.json`` and processes
        each entry. Possible per-entry outcomes:

          * **RESTORED** — staged file moved back to original.
          * **SKIPPED_STAGED_MISSING** — manifest references a path that
            no longer exists in staging (perhaps already reverted, or
            user deleted it).
          * **SKIPPED_ORIGINAL_OCCUPIED** — original path is now
            occupied by something else; we won't overwrite. User must
            resolve manually.
          * **FAILED** — IO error.

        Successfully reverted entries are removed from the manifest.
        If all entries succeed, the manifest file is deleted.

        Raises:
            FileNotFoundError: no manifest at stage_root.
        """
        stage_root_p = Path(stage_root).resolve()
        manifest_path = stage_root_p / STAGE_MANIFEST_NAME
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No stage manifest at {manifest_path}. "
                "Was this directory used by `curator organize --stage`?"
            )

        try:
            entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"manifest at {manifest_path} is unreadable: {e}"
            ) from e

        report = RevertReport(
            stage_root=str(stage_root_p),
            started_at=datetime.utcnow(),
        )

        remaining: list[dict] = []

        for entry in entries:
            cid = entry.get("curator_id", "")
            original = entry.get("original", "")
            staged = entry.get("staged", "")

            if not staged or not original:
                # Malformed manifest entry — keep it so the user can inspect.
                remaining.append(entry)
                continue

            staged_p = Path(staged)
            original_p = Path(original)

            if not staged_p.exists():
                report.moves.append(RevertMove(
                    curator_id=cid,
                    original=original,
                    staged=staged,
                    outcome=RevertOutcome.SKIPPED_STAGED_MISSING,
                ))
                # Don't keep this entry; staged file is gone.
                continue

            if original_p.exists():
                report.moves.append(RevertMove(
                    curator_id=cid,
                    original=original,
                    staged=staged,
                    outcome=RevertOutcome.SKIPPED_ORIGINAL_OCCUPIED,
                ))
                # Keep entry so user knows it's still pending.
                remaining.append(entry)
                continue

            try:
                original_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged_p), str(original_p))
            except Exception as e:
                logger.error(
                    "revert move failed for {p}: {e}",
                    p=staged, e=e,
                )
                report.moves.append(RevertMove(
                    curator_id=cid,
                    original=original,
                    staged=staged,
                    outcome=RevertOutcome.FAILED,
                    error=str(e),
                ))
                remaining.append(entry)
                continue

            report.moves.append(RevertMove(
                curator_id=cid,
                original=original,
                staged=staged,
                outcome=RevertOutcome.RESTORED,
            ))

            # v0.33: revert moves the file back to its original
            # location, so update the FileEntity's source_path to
            # match. Without this, the FileEntity would still point
            # at the staged location after the file moved away.
            self._sync_index_after_move(cid, original)

            if self.audit is not None:
                try:
                    self.audit.log(
                        actor="curator.organize.revert",
                        action="organize.revert.move",
                        entity_type="file",
                        entity_id=cid,
                        details={
                            "original": original,
                            "staged": staged,
                            "stage_root": str(stage_root_p),
                        },
                    )
                except Exception as e:  # pragma: no cover
                    logger.warning("revert audit log failed: {e}", e=e)

        report.completed_at = datetime.utcnow()

        # Update or remove the manifest.
        try:
            if remaining:
                manifest_path.write_text(
                    json.dumps(remaining, indent=2), encoding="utf-8"
                )
            else:
                manifest_path.unlink()
        except OSError as e:  # pragma: no cover
            logger.warning(
                "failed to update manifest at {p}: {e}",
                p=manifest_path, e=e,
            )

        logger.info(
            "revert complete: restored={r} skipped={s} failed={f} in {d:.2f}s",
            r=report.restored_count,
            s=report.skipped_count,
            f=report.failed_count,
            d=report.duration_seconds or 0.0,
        )
        return report

    # ------------------------------------------------------------------
    # Index sync (v0.33)
    # ------------------------------------------------------------------

    def _sync_index_after_move(
        self,
        curator_id_str: str,
        new_path: str,
    ) -> None:
        """Best-effort: update FileEntity.source_path after an on-disk move.

        Closes the phantom-file gap for organize moves: when stage()
        or apply() moves a file, the corresponding FileEntity in the
        index needs its source_path updated so subsequent queries
        return the file at its new location, not at the old path
        (which no longer exists on disk). Without this, scanning would
        treat the old path as deleted and the new path as new on the
        next pass, even though it's the same file.

        Best-effort throughout — NEVER raises and NEVER fails the
        parent move. Skip conditions:

          * ``self.files`` is None (defensive; should never happen).
          * ``UUID(curator_id_str)`` raises (malformed id).
          * ``self.files.get(...)`` raises or returns None.
          * ``self.files.update(...)`` raises (DB locked etc.).

        Revert calls this with the original path; stage and apply
        call it with the new staged path. The semantics are the same
        either way: "the file now lives at <new_path>; record that."
        """
        if self.files is None:  # pragma: no cover — defensive
            return
        from uuid import UUID
        try:
            cid = UUID(curator_id_str)
        except (ValueError, TypeError) as e:
            logger.debug(
                "organize index sync: bad curator_id {c}: {e}",
                c=curator_id_str, e=e,
            )
            return
        try:
            entity = self.files.get(cid)
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "organize index sync: file_repo.get failed for {c}: {e}",
                c=cid, e=e,
            )
            return
        if entity is None:
            # File wasn't in the index; nothing to update. This shouldn't
            # happen during organize because plan() pulled the entity
            # FROM the index, but we guard against the race anyway.
            return
        entity.source_path = new_path
        try:
            self.files.update(entity)
            logger.debug(
                "organize index sync: source_path -> {p} (curator_id={c})",
                p=new_path, c=cid,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "organize index sync: file_repo.update failed for {c}: {e}",
                c=cid, e=e,
            )


__all__ = [
    "STAGE_MANIFEST_NAME",
    "OrganizeBucket",
    "OrganizePlan",
    "OrganizeService",
    "RevertMove",
    "RevertOutcome",
    "RevertReport",
    "StageMove",
    "StageOutcome",
    "StageReport",
]
