"""Pydantic entity models for Curator.

This package defines the entity types that flow through Curator's storage
and service layers. All entities inherit from :class:`CuratorEntity` which
provides the three-tier attribute model (Fixed / Flex / Computed) per
DESIGN.md §3.1.

Entities are deliberately persistence-agnostic: a Repository in
``curator.storage.repositories`` is responsible for SQL ↔ pydantic
conversion, NOT the entity itself.
"""

from curator.models.audit import AuditEntry
from curator.models.base import CuratorEntity
from curator.models.bundle import BundleEntity, BundleMembership
from curator.models.file import FileEntity
from curator.models.jobs import ScanJob
from curator.models.lineage import LineageEdge, LineageKind
from curator.models.results import (
    BundleProposal,
    BundleProposalMember,
    ConfirmationResult,
    FileClassification,
    RuleAction,
    RuleActionKind,
    ValidationResult,
)
from curator.models.source import SourceConfig
from curator.models.trash import TrashRecord
from curator.models.types import ChangeEvent, ChangeKind, FileInfo, FileStat, SourcePluginInfo

__all__ = [
    # Base
    "CuratorEntity",
    # Core entities
    "FileEntity",
    "BundleEntity",
    "BundleMembership",
    "LineageEdge",
    "LineageKind",
    "TrashRecord",
    "AuditEntry",
    "SourceConfig",
    "ScanJob",
    # Source plugin types
    "FileInfo",
    "FileStat",
    "ChangeEvent",
    "ChangeKind",
    "SourcePluginInfo",
    # Plugin result types
    "FileClassification",
    "ValidationResult",
    "BundleProposal",
    "BundleProposalMember",
    "ConfirmationResult",
    "RuleAction",
    "RuleActionKind",
]
