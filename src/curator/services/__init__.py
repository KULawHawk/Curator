"""Service layer — Tier 2 in the architecture (DESIGN.md §2.1).

Services orchestrate work across the storage layer (Tier 1) and plugin
framework (Tier 3). Each service has a clear responsibility:

    * :class:`AuditService` — append entries to the audit log with Loguru
      structured-logging integration.
    * :class:`ClassificationService` — runs ``curator_classify_file``
      plugins and selects the best result.
    * :class:`HashPipeline` — multi-stage hash pipeline (DESIGN.md §7)
      that populates xxhash3_128 / md5 / fuzzy_hash on FileEntity batches.
    * :class:`LineageService` — finds candidate files and runs
      ``curator_compute_lineage`` plugins, persisting confirmed edges.
    * :class:`BundleService` — manual bundles, plugin proposals, member
      management.
    * :class:`TrashService` — dual-trash with snapshot-based restore.
    * :class:`ScanService` — the orchestrator that ties enumerate +
      hash + classify + lineage together.

Services intentionally don't import each other directly; they're
composed by callers (CLI, REST API, scan orchestrator).
"""

from curator.services.audit import AuditService
from curator.services.bundle import BundleService
from curator.services.classification import ClassificationService
from curator.services.cleanup import (
    ApplyOutcome,
    ApplyReport,
    ApplyResult,
    CleanupFinding,
    CleanupKind,
    CleanupReport,
    CleanupService,
)
from curator.services.document import DocumentService
from curator.services.hash_pipeline import HashPipeline, HashPipelineStats
from curator.services.lineage import LineageService
from curator.services.music import MusicService
from curator.services.musicbrainz import MusicBrainzClient, MusicBrainzMatch
from curator.services.organize import (
    OrganizeBucket,
    OrganizePlan,
    OrganizeService,
    RevertMove,
    RevertOutcome,
    RevertReport,
    StageMove,
    StageOutcome,
    StageReport,
)
from curator.services.photo import PhotoService
from curator.services.safety import (
    SafetyConcern,
    SafetyLevel,
    SafetyReport,
    SafetyService,
)
from curator.services.scan import ScanReport, ScanService
from curator.services.trash import (
    FileNotFoundError,
    NotInTrashError,
    RestoreImpossibleError,
    RestoreVetoed,
    Send2TrashUnavailableError,
    TrashError,
    TrashService,
    TrashVetoed,
)

__all__ = [
    # Services
    "AuditService",
    "BundleService",
    "ClassificationService",
    "CleanupFinding",
    "CleanupKind",
    "CleanupReport",
    "CleanupService",
    "DocumentService",
    "HashPipeline",
    "HashPipelineStats",
    "LineageService",
    "MusicBrainzClient",
    "MusicBrainzMatch",
    "MusicService",
    "OrganizeBucket",
    "OrganizePlan",
    "OrganizeService",
    "PhotoService",
    "RevertMove",
    "RevertOutcome",
    "RevertReport",
    "StageMove",
    "StageOutcome",
    "StageReport",
    "SafetyService",
    "ScanReport",
    "ScanService",
    "TrashService",
    # Safety types
    "SafetyConcern",
    "SafetyLevel",
    "SafetyReport",
    # Trash exceptions
    "TrashError",
    "TrashVetoed",
    "RestoreVetoed",
    "NotInTrashError",
    "RestoreImpossibleError",
    "Send2TrashUnavailableError",
    "FileNotFoundError",
]
