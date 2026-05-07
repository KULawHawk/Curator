"""CLI runtime container.

DESIGN.md §11.

Wires together DB + plugin manager + all repositories + all services.
The CLI's ``@app.callback()`` builds one of these (via
:func:`build_runtime`) and stashes it in ``ctx.obj`` so commands can
pull anything they need from one place.

This is the same wiring the future REST API and GUI will need; we keep
it CLI-adjacent for Phase Alpha because that's the only consumer.
Phase Gamma will likely promote this to ``curator.runtime``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pluggy
from loguru import logger

from curator.config import Config
from curator.plugins import get_plugin_manager
from curator.services import (
    AuditService,
    BundleService,
    ClassificationService,
    CleanupService,
    DocumentService,
    HashPipeline,
    LineageService,
    MigrationService,
    MusicService,
    OrganizeService,
    PhotoService,
    SafetyService,
    ScanService,
    TrashService,
)
from curator.storage import CuratorDB
from curator.storage.repositories import (
    AuditRepository,
    BundleRepository,
    FileRepository,
    HashCacheRepository,
    LineageRepository,
    ScanJobRepository,
    SourceRepository,
    TrashRepository,
)

if TYPE_CHECKING:  # pragma: no cover
    from curator.services.fuzzy_index import FuzzyIndex


@dataclass
class CuratorRuntime:
    """Everything a CLI command might need, in one place.

    Use the factory :func:`build_runtime` rather than constructing
    directly — the wiring order matters (DB.init() must run before any
    repository is used; plugin manager must be ready before services
    that depend on hooks).
    """

    config: Config
    db: CuratorDB
    pm: pluggy.PluginManager

    # Repositories
    file_repo: FileRepository
    bundle_repo: BundleRepository
    lineage_repo: LineageRepository
    trash_repo: TrashRepository
    audit_repo: AuditRepository
    source_repo: SourceRepository
    job_repo: ScanJobRepository
    cache_repo: HashCacheRepository

    # Services
    audit: AuditService
    classification: ClassificationService
    hash_pipeline: HashPipeline
    lineage: LineageService
    bundle: BundleService
    trash: TrashService
    scan: ScanService
    safety: SafetyService
    organize: OrganizeService
    music: MusicService
    photo: PhotoService
    document: DocumentService
    cleanup: CleanupService
    migration: MigrationService

    # Output controls (set by CLI flags)
    json_output: bool = False
    no_color: bool = False
    verbosity: int = 0  # -v / --verbose count


def build_runtime(
    config: Config | None = None,
    *,
    db_path_override: str | Path | None = None,
    json_output: bool = False,
    no_color: bool = False,
    verbosity: int = 0,
) -> CuratorRuntime:
    """Construct a fully-wired :class:`CuratorRuntime`.

    Args:
        config: pre-loaded Config. If None, calls ``Config.load()`` with
                no explicit path (uses the standard search order).
        db_path_override: forces a specific DB path (CLI ``--db`` flag).
        json_output: machine-readable command output mode.
        no_color: disable Rich color codes.
        verbosity: 0 = INFO, 1+ = DEBUG, -1 (quiet) = WARNING.
    """
    if config is None:
        config = Config.load()

    _configure_logging(config, verbosity=verbosity)

    db_path = Path(db_path_override) if db_path_override else config.db_path
    db = CuratorDB(db_path)
    db.init()

    pm = get_plugin_manager()

    # Repositories
    file_repo = FileRepository(db)
    bundle_repo = BundleRepository(db)
    lineage_repo = LineageRepository(db)
    trash_repo = TrashRepository(db)
    audit_repo = AuditRepository(db)
    source_repo = SourceRepository(db)
    job_repo = ScanJobRepository(db)
    cache_repo = HashCacheRepository(db)

    # Services
    audit = AuditService(audit_repo)
    classification = ClassificationService(pm)
    hash_pipeline = HashPipeline(pm, cache_repo)

    # Phase Beta v0.14: build an in-memory MinHash-LSH index for fuzzy
    # candidate selection if datasketch is installed (it lives in the
    # ``[beta]`` extras, not core). When unavailable, LineageService
    # transparently falls back to the O(n) scan path.
    fuzzy_index = _build_fuzzy_index_if_available(file_repo)

    lineage = LineageService(pm, file_repo, lineage_repo, fuzzy_index=fuzzy_index)
    bundle = BundleService(pm, bundle_repo, file_repo)
    trash = TrashService(pm, file_repo, trash_repo, bundle_repo, audit_repo)
    scan = ScanService(
        pm, file_repo, source_repo, job_repo,
        hash_pipeline, classification, lineage, audit,
    )
    safety = SafetyService()  # platform-aware defaults; user-extensible later
    music = MusicService()
    photo = PhotoService()
    document = DocumentService()
    from curator.services.code_project import CodeProjectService
    code = CodeProjectService()
    organize = OrganizeService(
        file_repo, safety,
        music=music, photo=photo, document=document, code=code,
        audit=audit_repo,
    )
    cleanup = CleanupService(safety, audit=audit_repo, file_repo=file_repo)
    # v1.0.0a1: Migration tool (Feature M Phase 1).
    migration = MigrationService(
        file_repo=file_repo, safety=safety, audit=audit_repo,
    )

    return CuratorRuntime(
        config=config,
        db=db,
        pm=pm,
        file_repo=file_repo,
        bundle_repo=bundle_repo,
        lineage_repo=lineage_repo,
        trash_repo=trash_repo,
        audit_repo=audit_repo,
        source_repo=source_repo,
        job_repo=job_repo,
        cache_repo=cache_repo,
        audit=audit,
        classification=classification,
        hash_pipeline=hash_pipeline,
        lineage=lineage,
        bundle=bundle,
        trash=trash,
        scan=scan,
        safety=safety,
        organize=organize,
        music=music,
        photo=photo,
        document=document,
        cleanup=cleanup,
        migration=migration,
        json_output=json_output,
        no_color=no_color,
        verbosity=verbosity,
    )


def _build_fuzzy_index_if_available(
    file_repo: FileRepository,
) -> "FuzzyIndex | None":
    """Try to build a populated :class:`FuzzyIndex`. Return None if
    ``datasketch`` isn't installed (Phase Beta optional dep).

    Pre-population sweep: read every file-with-fuzzy-hash from the DB
    and add to the index. Cost is O(n) once at runtime build, then all
    subsequent ``LineageService.compute_for_file`` calls hit the
    O(1)-average LSH path.
    """
    try:
        from curator.services.fuzzy_index import (
            FuzzyIndex,
            FuzzyIndexUnavailableError,
        )
    except ImportError:
        return None
    try:
        idx = FuzzyIndex()
    except FuzzyIndexUnavailableError:
        # datasketch not installed — silently skip; the O(n) fallback
        # in LineageService still works.
        return None

    populated = 0
    skipped = 0
    for f in file_repo.find_with_fuzzy_hash():
        if f.fuzzy_hash:
            try:
                idx.add(f.curator_id, f.fuzzy_hash)
                populated += 1
            except ValueError:
                # Malformed hash in DB — skip silently. The O(n) fallback
                # would have skipped it too (compare() would short-circuit).
                skipped += 1
    if populated:
        logger.debug(
            "FuzzyIndex pre-populated with {n} entries ({s} skipped malformed)",
            n=populated, s=skipped,
        )
    return idx


def _configure_logging(config: Config, *, verbosity: int) -> None:
    """Set up Loguru sinks per config + verbosity flags."""
    # Map verbosity to a log level (-q=WARNING, default=INFO, -v=DEBUG, -vv=TRACE).
    level_map = {
        -1: "WARNING",
        0: config.log_level,
        1: "DEBUG",
        2: "TRACE",
    }
    level = level_map.get(min(2, max(-1, verbosity)), config.log_level)

    logger.remove()  # drop the default stderr sink
    # Stderr sink: human-readable.
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level: <7}</level> "
            "<cyan>{name}</cyan> | <level>{message}</level>"
        ),
        colorize=True,
    )
    # File sink: full detail, append-only.
    log_path = config.log_path
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_path),
            level="DEBUG",
            rotation="10 MB",
            retention="14 days",
            enqueue=True,  # safe across threads
            backtrace=True,
            diagnose=False,  # no local-var dumps in production logs
        )
    except (OSError, PermissionError):  # pragma: no cover — defensive
        # Filesystem write failure is non-fatal; we still have stderr.
        logger.warning("Could not open log file at {}; using stderr only", log_path)
