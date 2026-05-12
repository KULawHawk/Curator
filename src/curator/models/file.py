"""FileEntity — a file Curator knows about.

DESIGN.md §3.3.

Notes:
  * ``curator_id`` is a stable UUID assigned at first sighting and never
    changes through renames, moves, or even cross-source migration.
  * ``source_id`` + ``source_path`` together uniquely identify the file's
    current location. (UNIQUE constraint at the DB level.)
  * Hashes are populated lazily by the hash pipeline. ``None`` means
    "not yet computed" rather than "has no hash".
  * ``deleted_at`` is the soft-delete marker; the row remains so lineage
    edges and bundle memberships are preserved through trash.
"""

from __future__ import annotations

from datetime import datetime
from curator._compat.datetime import utcnow_naive
from uuid import UUID, uuid4

from pydantic import Field

from curator.models.base import CuratorEntity


def _utcnow() -> datetime:
    """Single source of timestamp truth (UTC)."""
    return utcnow_naive()


class FileEntity(CuratorEntity):
    """A file Curator knows about."""

    # === Identity ===
    curator_id: UUID = Field(default_factory=uuid4, description="Stable identifier (UUID)")
    source_id: str = Field(..., description="Source plugin identifier, e.g. 'local'")
    source_path: str = Field(..., description="Path within the source")

    # === File metadata ===
    size: int = Field(..., ge=0, description="Size in bytes")
    mtime: datetime = Field(..., description="Modification time")
    ctime: datetime | None = Field(None, description="Creation time (where supported)")
    inode: int | None = Field(None, description="Inode number (local FS only)")

    # === Hashes (multi-stage; populated lazily) ===
    xxhash3_128: str | None = Field(None, description="Primary fingerprint (xxhash3 128-bit, hex)")
    md5: str | None = Field(None, description="Secondary fingerprint (MD5, hex)")
    fuzzy_hash: str | None = Field(
        None, description="ppdeep fuzzy hash, only for text-eligible files"
    )

    # === Classification ===
    file_type: str | None = Field(None, description="MIME type from filetype.py")
    extension: str | None = Field(None, description="Lowercased file extension including dot")
    file_type_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Classifier confidence; 0.0 means not classified"
    )

    # === Tracking ===
    seen_at: datetime = Field(default_factory=_utcnow, description="First time Curator saw this file")
    last_scanned_at: datetime = Field(default_factory=_utcnow, description="Most recent scan timestamp")
    deleted_at: datetime | None = Field(
        None, description="Soft-delete marker; non-null means the file is in Curator's trash registry"
    )

    # === Classification taxonomy (v1.7.3, T-C02) ===
    # Coarse 4-bucket status for asset classification. The default is 'active'
    # which matches the pre-v1.7.3 implicit state.
    status: str = Field(
        default="active",
        description="Asset classification bucket: vital / active / provisional / junk",
    )
    supersedes_id: UUID | None = Field(
        None,
        description="Soft UUID reference to a file this one supersedes (e.g. v2 supersedes v1)",
    )
    expires_at: datetime | None = Field(
        None,
        description="Optional retention horizon; cleanup/tier-storage policies use this",
    )

    @property
    def is_deleted(self) -> bool:
        """True if this file is in the trash registry."""
        return self.deleted_at is not None

    @property
    def has_full_hash(self) -> bool:
        """True if the primary hash has been computed."""
        return self.xxhash3_128 is not None

    @property
    def is_text_eligible(self) -> bool:
        """True if the extension marks this as a candidate for fuzzy hashing."""
        # The authoritative TEXT_EXTENSIONS set lives in the hash pipeline;
        # this is a convenience check based on extension presence only.
        # The pipeline cross-references this against its own list.
        return self.extension is not None and self.extension.lower() in _TEXT_EXTENSIONS_HINT


# Hint set used by ``is_text_eligible``; the hash pipeline has its own
# authoritative set in curator.services.hash_pipeline.TEXT_EXTENSIONS.
# Kept in sync intentionally — duplicate is OK because this is a hint only.
_TEXT_EXTENSIONS_HINT: frozenset[str] = frozenset(
    {
        ".py", ".bas", ".vb", ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts", ".sql",
        ".csv", ".tsv", ".log", ".xml",
    }
)
