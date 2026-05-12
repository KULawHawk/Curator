"""TrashRecord — metadata for a trashed file (used by restore).

DESIGN.md §3.6 and §10.

When a file is trashed via :class:`curator.services.trash.TrashService`,
a ``TrashRecord`` is created that snapshots everything Curator needs to
restore the file later: original location, hash for verification, bundle
memberships at trash time, and flex attrs.
"""

from __future__ import annotations

from datetime import datetime
from curator._compat.datetime import utcnow_naive
from typing import Any
from uuid import UUID

from pydantic import Field

from curator.models.base import CuratorEntity


def _utcnow() -> datetime:
    return utcnow_naive()


class TrashRecord(CuratorEntity):
    """Snapshot of a file's metadata at the moment it was trashed.

    The original :class:`FileEntity` row is soft-deleted (``deleted_at`` set)
    rather than removed, so that lineage edges and audit references continue
    to resolve. The TrashRecord adds the data we need ON TOP of that to
    perform a faithful restore.

    Fields:
      * ``curator_id``: matches the FileEntity (one-to-one).
      * ``bundle_memberships_snapshot``: list of dicts with shape
        ``{"bundle_id": str, "role": str, "confidence": float}``.
      * ``file_attrs_snapshot``: snapshot of ``file.flex`` at trash time.
      * ``os_trash_location``: where the OS trash put the file
        (Windows Recycle Bin path) when known. ``None`` means restore must
        be done manually from the OS trash.
      * ``restore_path_override``: alternative target path on restore.
    """

    curator_id: UUID
    original_source_id: str
    original_path: str
    file_hash: str | None = Field(
        None, description="xxhash of the file at trash time, for restore verification"
    )
    trashed_at: datetime = Field(default_factory=_utcnow)
    trashed_by: str = Field(
        ..., description="Actor: 'user', 'auto', 'plugin:<name>', 'service'"
    )
    reason: str = Field(..., description="Why this file was trashed")
    bundle_memberships_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    file_attrs_snapshot: dict[str, Any] = Field(default_factory=dict)
    os_trash_location: str | None = Field(
        None, description="Path within the OS trash, when discoverable"
    )
    restore_path_override: str | None = Field(
        None, description="Alternative restore destination, overriding original_path"
    )
