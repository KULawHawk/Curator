"""Shared shapes used by the Source plugin contract.

DESIGN.md §6.2.

These are NOT Curator entities (no UUID, not persisted). They're the
types that flow across the source plugin boundary: from
``curator_source_enumerate`` (yields :class:`FileInfo`), to
``curator_source_stat`` (returns :class:`FileStat`), to
``curator_source_watch`` (yields :class:`ChangeEvent`).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _SourceShape(BaseModel):
    """Shared base for source-contract shapes."""

    model_config = ConfigDict(
        validate_assignment=True,
        arbitrary_types_allowed=False,
        extra="ignore",
    )


class FileInfo(_SourceShape):
    """Source-agnostic file info from ``curator_source_enumerate``.

    The ``file_id`` is the source-specific stable identifier — for local FS
    that's the path string; for Google Drive that's the Drive file ID.
    Source plugins should return the same ``file_id`` for the same file
    across calls (so Curator can detect re-enumeration of known files).
    """

    file_id: str
    path: str = Field(..., description="Human-readable path within the source")
    size: int = Field(..., ge=0)
    mtime: datetime
    ctime: datetime | None = None
    is_directory: bool = False
    extras: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific extras (e.g. {'inode': N} for local, {'mime_type': ...} for gdrive)",
    )


class FileStat(_SourceShape):
    """Source-agnostic file stat result from ``curator_source_stat``.

    Similar to :class:`FileInfo` but for an already-known file: returns
    the *current* state of the file given its source_id and file_id.
    """

    file_id: str
    size: int = Field(..., ge=0)
    mtime: datetime
    ctime: datetime | None = None
    inode: int | None = None  # local FS only
    permissions: str | None = None  # source-specific representation
    extras: dict[str, Any] = Field(default_factory=dict)


class ChangeKind(str, Enum):
    """Type of change reported by ``curator_source_watch``."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    #: Synthesized from add+delete with same content; not all sources detect this.
    MOVED = "moved"


class ChangeEvent(_SourceShape):
    """A change notification from a source watcher."""

    kind: ChangeKind
    file_id: str
    path: str
    new_path: str | None = Field(
        None, description="For MOVED events: the new path (path field is the old path)"
    )
    timestamp: datetime


class SourcePluginInfo(_SourceShape):
    """Metadata returned from ``curator_source_register``.

    Source plugins describe themselves to Curator on load. The
    ``config_schema`` is a JSON Schema document that Curator uses to
    validate :class:`SourceConfig.config` for sources of this type.
    """

    source_type: str = Field(..., description="Plugin's source_type, e.g. 'local', 'gdrive'")
    display_name: str
    requires_auth: bool = Field(
        ..., description="True if this source needs OAuth or other credential setup"
    )
    supports_watch: bool = Field(
        ..., description="True if curator_source_watch is implemented"
    )
    supports_write: bool = Field(
        default=False,
        description="True if curator_source_write is implemented (v0.40+; required for migration target / sync target)",
    )
    config_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema for SourceConfig.config"
    )
