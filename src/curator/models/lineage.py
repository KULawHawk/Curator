"""LineageEdge — a relationship between two files.

DESIGN.md §3.5 and §8.

Edges are directional in the schema (``from`` → ``to``) but most edge
kinds are conceptually symmetric (DUPLICATE A→B implies DUPLICATE B→A).
Lineage detector plugins decide direction based on their own logic
(e.g., for VERSION_OF, the older file is ``from``, newer is ``to``).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import Field

from curator.models.base import CuratorEntity


def _utcnow() -> datetime:
    return datetime.utcnow()


class LineageKind(str, Enum):
    """Kinds of relationship Curator tracks between files.

    Confidence-threshold table per DESIGN.md §8.2:

    +-------------------+------------------------+--------------------+
    | Kind              | Auto-confirm threshold | Escalate threshold |
    +===================+========================+====================+
    | DUPLICATE         | 1.0 (always)           | N/A                |
    | NEAR_DUPLICATE    | >= 0.95                | 0.7 <= x < 0.95    |
    | DERIVED_FROM      | >= 0.90                | 0.6 <= x < 0.90    |
    | VERSION_OF        | >= 0.85                | 0.6 <= x < 0.85    |
    | REFERENCED_BY     | 1.0 (literal mention)  | N/A                |
    | SAME_LOGICAL_FILE | >= 0.95                | 0.7 <= x < 0.95    |
    +-------------------+------------------------+--------------------+
    """

    #: Byte-identical (same xxhash).
    DUPLICATE = "duplicate"
    #: High fuzzy hash similarity (>=70%).
    NEAR_DUPLICATE = "near_duplicate"
    #: One is a transformation of the other (export, conversion).
    DERIVED_FROM = "derived_from"
    #: Explicit version chain (filename pattern + content similarity).
    VERSION_OF = "version_of"
    #: One literally mentions / links to / imports the other.
    REFERENCED_BY = "referenced_by"
    #: Different paths, same conceptual file (often cross-source).
    SAME_LOGICAL_FILE = "same_logical_file"


class LineageEdge(CuratorEntity):
    """A relationship between two files, with confidence and provenance."""

    edge_id: UUID = Field(default_factory=uuid4)
    from_curator_id: UUID
    to_curator_id: UUID
    edge_kind: LineageKind
    confidence: float = Field(..., ge=0.0, le=1.0)
    detected_by: str = Field(
        ..., description="Identifier of the plugin/detector that found this edge"
    )
    detected_at: datetime = Field(default_factory=_utcnow)
    notes: str | None = Field(
        None, description="Optional plugin-specific info (e.g., 'fuzzy similarity: 87%')"
    )
