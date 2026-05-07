"""Repository implementations for Curator's storage layer.

DESIGN.md §4.5.

One repository per entity type. Each repository takes a :class:`CuratorDB`
in its constructor and exposes a small, named API surface for that entity.

Conventions:
  * Mutations: ``insert``, ``update``, ``upsert``, ``delete`` (where applicable).
  * Single-entity reads: ``get`` (returns Optional[Entity]).
  * Multi-entity reads: ``find_*``, ``list_*``, ``query``.
  * Soft-deletes: only ``FileRepository`` distinguishes hard from soft.
  * Audit log: append-only (no update/delete).
"""

from curator.storage.repositories.audit_repo import AuditRepository
from curator.storage.repositories.bundle_repo import BundleRepository
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.hash_cache_repo import CachedHash, HashCacheRepository
from curator.storage.repositories.job_repo import ScanJobRepository
from curator.storage.repositories.lineage_repo import LineageRepository
from curator.storage.repositories.source_repo import SourceRepository
from curator.storage.repositories.trash_repo import TrashRepository

__all__ = [
    "AuditRepository",
    "BundleRepository",
    "CachedHash",
    "FileRepository",
    "HashCacheRepository",
    "LineageRepository",
    "ScanJobRepository",
    "SourceRepository",
    "TrashRepository",
]
