"""SourceConfig — configuration record for a source plugin instance.

DESIGN.md §3.8.

A "source" is a place files come from: local filesystem, Google Drive
account, OneDrive account, Dropbox, etc. Each source plugin can be
instantiated multiple times (e.g., two Google Drive accounts) — each
instance is a distinct ``SourceConfig`` row identified by ``source_id``.

Conventional ``source_id`` formats:
  * ``"local"`` — single local FS source
  * ``"gdrive:<account_email>"`` — per-account Google Drive
  * ``"onedrive:<account_email>"`` — per-account OneDrive
  * ``"dropbox:<account_email>"`` — per-account Dropbox

The ``config`` dict is plugin-specific and validated against the JSON
Schema returned by the plugin's ``curator_source_register`` hook.
"""

from __future__ import annotations

from datetime import datetime
from curator._compat.datetime import utcnow_naive
from typing import Any

from pydantic import Field

from curator.models.base import CuratorEntity


def _utcnow() -> datetime:
    return utcnow_naive()


class SourceConfig(CuratorEntity):
    """Configuration for a source plugin instance."""

    source_id: str = Field(..., description="Unique identifier, e.g. 'local' or 'gdrive:jake@example.com'")
    source_type: str = Field(..., description="Plugin source_type, e.g. 'local', 'gdrive'")
    display_name: str | None = Field(None, description="Human-readable name")
    config: dict[str, Any] = Field(default_factory=dict, description="Plugin-specific configuration")
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)
    # v1.7.29: T-B07 v1.8 completion. Sharing posture controls whether
    # MigrationService auto-strips metadata when files are migrated to
    # this source as destination. Valid values: 'private' (default,
    # no stripping), 'team' (no stripping, reserved for finer-grained
    # policy), 'public' (auto-strip EXIF/docProps/PDF metadata after
    # each verified move).
    share_visibility: str = Field(
        default="private",
        description="Sharing posture: 'private' | 'team' | 'public'. "
                    "When 'public', MigrationService auto-strips metadata.",
    )
