"""Scan service — the orchestrator that ties everything together.

DESIGN.md §2.2.1.

A scan walks a source root, hashes/classifies/lineage-detects each file,
persists results, and tracks progress as a :class:`ScanJob` row. This
is the spine of Phase Alpha: every CLI command that touches the index
ultimately routes through here.

Steps:
    1. Look up (or create) the :class:`SourceConfig` row.
    2. Create a :class:`ScanJob` row with status='running'.
    3. Audit ``scan.start``.
    4. For each :class:`FileInfo` from ``curator_source_enumerate``:
        a. Find existing :class:`FileEntity` by (source_id, path) or
           create new with a fresh ``curator_id``.
        b. Update mutable fields (size, mtime, last_scanned_at, inode).
        c. Persist (insert / update).
    5. Run the hash pipeline on the batch (cache shortcircuits unchanged).
    6. For each file: classify + lineage-detect + persist updates.
    7. Update ScanJob status='completed' with counters.
    8. Audit ``scan.complete``.

Errors during a single file don't fail the whole scan — they're counted
in ``ScanReport.errors`` and the scan continues. Scan-level failures
(source plugin unavailable, etc.) raise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from curator._compat.datetime import utcnow_naive
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

import pluggy
from loguru import logger

from curator.models.file import FileEntity
from curator.models.jobs import ScanJob
from curator.models.source import SourceConfig
from curator.models.types import FileInfo
from curator.services.audit import AuditService
from curator.services.classification import ClassificationService
from curator.services.hash_pipeline import HashPipeline, HashPipelineStats
from curator.services.lineage import LineageService
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.job_repo import ScanJobRepository
from curator.storage.repositories.source_repo import SourceRepository


@dataclass
class ScanReport:
    """Summary of a completed scan, returned by :meth:`ScanService.scan`.

    A successful scan still has potentially-nonzero ``errors`` —
    individual file failures are tolerated. Caller should examine
    this to decide whether to retry / surface to user.
    """

    job_id: UUID
    source_id: str
    root: str
    started_at: datetime
    completed_at: datetime | None = None

    files_seen: int = 0
    files_new: int = 0
    files_updated: int = 0
    files_unchanged: int = 0
    files_hashed: int = 0
    cache_hits: int = 0
    bytes_read: int = 0
    fuzzy_hashes_computed: int = 0
    classifications_assigned: int = 0
    lineage_edges_created: int = 0
    files_deleted: int = 0   # Phase Beta v0.17: paths that vanished off disk
    errors: int = 0
    error_paths: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


class ScanService:
    """End-to-end scan orchestrator."""

    def __init__(
        self,
        plugin_manager: pluggy.PluginManager,
        file_repo: FileRepository,
        source_repo: SourceRepository,
        job_repo: ScanJobRepository,
        hash_pipeline: HashPipeline,
        classification: ClassificationService,
        lineage: LineageService,
        audit: AuditService,
    ):
        self.pm = plugin_manager
        self.files = file_repo
        self.sources = source_repo
        self.jobs = job_repo
        self.hash_pipeline = hash_pipeline
        self.classification = classification
        self.lineage = lineage
        self.audit = audit

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(
        self,
        *,
        source_id: str,
        root: str,
        options: dict[str, Any] | None = None,
    ) -> ScanReport:
        """Run a full scan against a source root.

        Args:
            source_id: e.g. ``"local"``. Must already be registered as
                       a :class:`SourceConfig`, OR Curator will create
                       a new one with the matching ``source_type``
                       inferred from the source plugin's registration.
            root: path within the source to scan (for local: a directory).
            options: passed to ``curator_source_enumerate`` — may contain
                     ``ignore`` patterns, etc.

        Returns:
            A :class:`ScanReport` with full statistics.
        """
        options = options or {}
        started = utcnow_naive()

        # Ensure the source is registered.
        self._ensure_source(source_id)

        # Create scan job row.
        job = ScanJob(
            source_id=source_id,
            root_path=root,
            options=dict(options),
            status="running",
            started_at=started,
        )
        self.jobs.insert(job)

        report = ScanReport(
            job_id=job.job_id,
            source_id=source_id,
            root=root,
            started_at=started,
        )

        scan_log = self.audit.bind(
            actor="curator.scan",
            source_id=source_id,
            root=root,
            job_id=str(job.job_id),
        )
        scan_log("scan.start", options=options)

        try:
            self._run(source_id, root, options, report, job)
            self.jobs.update_status(job.job_id, "completed")
            scan_log(
                "scan.complete",
                files_seen=report.files_seen,
                files_hashed=report.files_hashed,
                files_new=report.files_new,
                files_updated=report.files_updated,
                lineage_edges_created=report.lineage_edges_created,
                errors=report.errors,
            )
        except Exception as e:
            self.jobs.update_status(job.job_id, "failed", error=str(e))
            scan_log("scan.failed", error=str(e), error_type=type(e).__name__)
            raise
        finally:
            report.completed_at = utcnow_naive()

        return report

    # ------------------------------------------------------------------
    # Public API: scan_paths (v0.17 — Tier 6 incremental scanning)
    # ------------------------------------------------------------------

    def scan_paths(
        self,
        *,
        source_id: str,
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ScanReport:
        """Process a specific list of paths within a source.

        Counterpart to :meth:`scan` for incremental updates: the caller
        provides a list of file paths (typically from
        :class:`WatchService` events) instead of a directory to walk.

        For each path:
          * If it exists on disk → stat it, upsert FileEntity, queue for
            hashing + classification + lineage.
          * If it doesn't exist → if there's an existing FileEntity for
            that (source_id, path), mark it deleted; otherwise skip.

        Hash pipeline + classification + lineage run exactly the same
        as a full scan — same code paths, same plugin hooks, same DB
        writes. The only thing that changes is enumeration.

        Args:
            source_id: e.g. ``"local"``. Source must already be registered.
            paths: absolute paths to process (NOT a root — individual files).
            options: reserved for future use; currently ignored.

        Returns:
            A :class:`ScanReport` with the same shape as :meth:`scan`,
            plus ``files_deleted`` reflecting paths that vanished.
        """
        options = options or {}
        started = utcnow_naive()

        # Ensure the source is registered.
        self._ensure_source(source_id)

        # Create scan job row — root_path is a synthetic marker so audit
        # queries can distinguish targeted scans from full ones.
        job = ScanJob(
            source_id=source_id,
            root_path=f"<paths:{len(paths)}>",
            options=dict(options),
            status="running",
            started_at=started,
        )
        self.jobs.insert(job)

        report = ScanReport(
            job_id=job.job_id,
            source_id=source_id,
            root=f"<paths:{len(paths)}>",
            started_at=started,
        )

        scan_log = self.audit.bind(
            actor="curator.scan",
            source_id=source_id,
            job_id=str(job.job_id),
            kind="incremental",
        )
        scan_log("scan.start", path_count=len(paths))

        try:
            self._run_paths(source_id, paths, report, job)
            self.jobs.update_status(job.job_id, "completed")
            scan_log(
                "scan.complete",
                files_seen=report.files_seen,
                files_hashed=report.files_hashed,
                files_new=report.files_new,
                files_updated=report.files_updated,
                files_deleted=report.files_deleted,
                lineage_edges_created=report.lineage_edges_created,
                errors=report.errors,
            )
        except Exception as e:
            self.jobs.update_status(job.job_id, "failed", error=str(e))
            scan_log("scan.failed", error=str(e), error_type=type(e).__name__)
            raise
        finally:
            report.completed_at = utcnow_naive()

        return report

    def _run_paths(
        self,
        source_id: str,
        paths: list[str],
        report: ScanReport,
        job: ScanJob,
    ) -> None:
        """Inner loop for :meth:`scan_paths` — reuses upsert + hash + post-process."""
        entities_to_process: list[FileEntity] = []
        seen: set[str] = set()

        for raw_path in paths:
            if raw_path in seen:
                continue  # caller may have duplicates; dedup defensively
            seen.add(raw_path)

            try:
                p = Path(raw_path)
            except (TypeError, ValueError):
                report.errors += 1
                report.error_paths.append(str(raw_path))
                continue

            if not p.exists():
                # Vanished from disk — mark deleted if we know about it.
                self._mark_path_deleted_if_known(source_id, str(p), report)
                continue

            if p.is_dir():
                # Tier 6 events should be file-level. If a directory
                # slips through, skip silently rather than recursing
                # (callers wanting recursive semantics should use scan()).
                logger.debug("scan_paths skipping directory: {p}", p=p)
                continue

            try:
                info = self._stat_to_file_info(p)
            except OSError as e:
                logger.warning("stat failed for {p}: {e}", p=p, e=e)
                report.errors += 1
                report.error_paths.append(str(p))
                continue

            try:
                entity = self._upsert_from_info(source_id, info, report)
                entities_to_process.append(entity)
            except Exception as e:
                logger.error("upsert failed for {p}: {e}", p=p, e=e)
                report.errors += 1
                report.error_paths.append(str(p))

        self.jobs.update_counters(
            job.job_id, files_seen=report.files_seen, files_hashed=0,
        )

        if not entities_to_process:
            return

        # Same hash pipeline + post-process as full scan.
        _, hash_stats = self.hash_pipeline.process(entities_to_process)
        report.cache_hits = hash_stats.cache_hits
        report.files_hashed = hash_stats.files_hashed
        report.bytes_read = hash_stats.bytes_read
        report.fuzzy_hashes_computed = hash_stats.fuzzy_hashes_computed
        report.errors += hash_stats.errors
        self.jobs.update_counters(
            job.job_id,
            files_seen=report.files_seen,
            files_hashed=report.files_hashed,
        )

        for f in entities_to_process:
            try:
                self._post_process_one(f, report)
            except Exception as e:
                logger.error(
                    "post-processing failed for {p}: {err}",
                    p=f.source_path, err=e,
                )
                report.errors += 1
                report.error_paths.append(f.source_path)

    def _stat_to_file_info(self, p: Path) -> FileInfo:
        """Build a :class:`FileInfo` from ``os.stat`` on a real file.

        Uses the same shape as ``local_source.enumerate`` produces, so
        the downstream upsert path is interchangeable.
        """
        st = p.stat()
        path_str = str(p)
        return FileInfo(
            file_id=path_str,
            path=path_str,
            size=st.st_size,
            mtime=datetime.fromtimestamp(st.st_mtime),
            ctime=datetime.fromtimestamp(st.st_ctime),
            is_directory=False,
            extras={"inode": st.st_ino} if hasattr(st, "st_ino") else {},
        )

    def _mark_path_deleted_if_known(
        self,
        source_id: str,
        path: str,
        report: ScanReport,
    ) -> None:
        """If a FileEntity exists for (source_id, path), soft-delete it.

        Doesn't touch the OS trash — the file's already gone from disk.
        Caller (the watcher) should NOT call this for files that were
        intentionally trashed via TrashService; that path goes through
        TrashService.send_to_trash instead.
        """
        existing = self.files.find_by_path(source_id, path)
        if existing is None:
            return
        if existing.deleted_at is not None:
            return  # already marked deleted; idempotent
        self.files.mark_deleted(existing.curator_id)
        report.files_deleted += 1
        report.files_seen += 1

    # ------------------------------------------------------------------
    # Internal phases (full-scan pipeline)
    # ------------------------------------------------------------------

    def _run(
        self,
        source_id: str,
        root: str,
        options: dict[str, Any],
        report: ScanReport,
        job: ScanJob,
    ) -> None:
        """The actual scan work; called inside a try block by ``scan()``."""

        # Phase 1: enumerate + persist FileEntity rows
        file_entities = list(self._enumerate_and_persist(source_id, root, options, report))
        self.jobs.update_counters(
            job.job_id,
            files_seen=report.files_seen,
            files_hashed=0,
        )

        if not file_entities:
            return

        # Phase 2: hash pipeline
        _, hash_stats = self.hash_pipeline.process(file_entities)
        report.cache_hits = hash_stats.cache_hits
        report.files_hashed = hash_stats.files_hashed
        report.bytes_read = hash_stats.bytes_read
        report.fuzzy_hashes_computed = hash_stats.fuzzy_hashes_computed
        report.errors += hash_stats.errors
        self.jobs.update_counters(
            job.job_id,
            files_seen=report.files_seen,
            files_hashed=report.files_hashed,
        )

        # Phase 3: classify + persist hash/classification updates + run lineage
        for f in file_entities:
            try:
                self._post_process_one(f, report)
            except Exception as e:
                logger.error(
                    "post-processing failed for {p}: {err}",
                    p=f.source_path, err=e,
                )
                report.errors += 1
                report.error_paths.append(f.source_path)

    # ------------------------------------------------------------------

    def _enumerate_and_persist(
        self,
        source_id: str,
        root: str,
        options: dict[str, Any],
        report: ScanReport,
    ) -> Iterable[FileEntity]:
        """Walk the source, persist each file, yield :class:`FileEntity`."""
        results = self.pm.hook.curator_source_enumerate(
            source_id=source_id, root=root, options=options
        )
        # Pick the first non-None iterator from the source plugin that
        # claims this source_id. Other plugins return None.
        iterator = next((it for it in results if it is not None), None)
        if iterator is None:
            raise RuntimeError(
                f"No source plugin registered for source_id={source_id!r}. "
                f"Did you register a SourceConfig with the right source_type?"
            )

        for info in iterator:
            try:
                entity = self._upsert_from_info(source_id, info, report)
                yield entity
            except Exception as e:
                logger.error(
                    "enumerate persist failed for {p}: {err}",
                    p=getattr(info, "path", "<unknown>"), err=e,
                )
                report.errors += 1
                report.error_paths.append(getattr(info, "path", "<unknown>"))

    def _upsert_from_info(
        self,
        source_id: str,
        info: FileInfo,
        report: ScanReport,
    ) -> FileEntity:
        """Find or create a :class:`FileEntity` from a :class:`FileInfo`.

        Strategy:
          * Look up by (source_id, source_path).
          * If found: update mutable fields, increment ``files_updated``
            or ``files_unchanged`` based on whether anything changed.
          * If new: assign a fresh curator_id, set fields from FileInfo,
            increment ``files_new``.
          * Either way: set ``last_scanned_at = now()`` and persist.

        ``source_path`` for local source is the absolute path (str).
        Cloud sources will use a per-source-stable identifier (file_id).
        """
        report.files_seen += 1

        existing = self.files.find_by_path(source_id, info.path)
        now = utcnow_naive()
        inode = info.extras.get("inode") if info.extras else None

        if existing is None:
            # New file — derive extension from the path.
            entity = FileEntity(
                source_id=source_id,
                source_path=info.path,
                size=info.size,
                mtime=info.mtime,
                ctime=info.ctime,
                inode=inode,
                extension=self._derive_extension(info.path),
                last_scanned_at=now,
            )
            self.files.insert(entity)
            report.files_new += 1
            return entity

        # Existing file — see if anything has changed that warrants
        # re-hashing. The hash pipeline's cache will short-circuit
        # if mtime + size still match.
        changed = (
            existing.size != info.size
            or existing.mtime != info.mtime
            or existing.inode != inode
        )

        existing.size = info.size
        existing.mtime = info.mtime
        existing.ctime = info.ctime
        existing.inode = inode
        existing.last_scanned_at = now
        if existing.extension is None:
            existing.extension = self._derive_extension(info.path)
        # If a previously-deleted file reappeared at the same path, undelete.
        if existing.deleted_at is not None:
            existing.deleted_at = None
        # Invalidate hashes if the file has actually changed; otherwise
        # the cached values are still correct.
        if changed:
            existing.xxhash3_128 = None
            existing.md5 = None
            existing.fuzzy_hash = None
            report.files_updated += 1
        else:
            report.files_unchanged += 1

        self.files.update(existing)
        return existing

    @staticmethod
    def _derive_extension(path: str) -> str | None:
        """Return the lowercased extension including the leading dot, or None."""
        from pathlib import Path
        suffix = Path(path).suffix
        return suffix.lower() if suffix else None

    # ------------------------------------------------------------------

    def _post_process_one(self, file: FileEntity, report: ScanReport) -> None:
        """Run classification + lineage + persist for a single file."""
        # Classification (in-place updates file_type / extension / confidence)
        chosen = self.classification.apply(file)
        if chosen is not None:
            report.classifications_assigned += 1

        # Persist hash + classification updates that the pipeline made.
        self.files.update(file)

        # Lineage detection. Edges are persisted by LineageService.
        edges = self.lineage.compute_for_file(file, persist=True)
        report.lineage_edges_created += len(edges)

    # ------------------------------------------------------------------
    # Source bootstrap
    # ------------------------------------------------------------------

    def _ensure_source(self, source_id: str) -> SourceConfig:
        """Look up ``source_id``; create if missing.

        For Phase Alpha: if the SourceConfig isn't there, infer the
        source_type by asking each registered source plugin via
        ``curator_source_register``. The plugin whose ``source_type`` is
        a prefix of ``source_id`` wins (e.g. ``"local"`` matches
        source_id ``"local"`` or ``"local:home"``).
        """
        existing = self.sources.get(source_id)
        if existing is not None:
            return existing

        # Discover plugin source_types
        infos = self.pm.hook.curator_source_register()
        for info in infos:
            if info is None:
                continue
            if source_id == info.source_type or source_id.startswith(
                f"{info.source_type}:"
            ):
                created = SourceConfig(
                    source_id=source_id,
                    source_type=info.source_type,
                    display_name=info.display_name,
                )
                self.sources.insert(created)
                return created

        raise RuntimeError(
            f"No source plugin matches source_id={source_id!r}. "
            f"Registered source_types: "
            f"{[info.source_type for info in infos if info is not None]}"
        )
