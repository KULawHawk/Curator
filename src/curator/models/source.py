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
from typing import Any

from pydantic import Field

from curator.models.base import CuratorEntity


def _utcnow() -> datetime:
    return datetime.utcnow()


class SourceConfig(CuratorEntity):
    """Configuration for a source plugin instance."""

    source_id: str = Field(..., description="Unique identifier, e.g. 'local' or 'gdrive:jake@example.com'")
    source_type: str = Field(..., description="Plugin source_type, e.g. 'local', 'gdrive'")
    display_name: str | None = Field(None, description="Human-readable name")
    config: dict[str, Any] = Field(default_factory=dict, description="Plugin-specific configuration")
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)
