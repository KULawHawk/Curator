"""Plugin result types — values returned by hook implementations.

DESIGN.md §5 hookspec catalog.

These shapes are NOT Curator entities (no UUID, not persisted directly).
They're the typed values that flow OUT of plugin hookimpls and INTO core
Curator services for processing.

  * :class:`FileClassification` ← curator_classify_file
  * :class:`ValidationResult` ← curator_validate_file
  * :class:`BundleProposal` ← curator_propose_bundle
  * :class:`ConfirmationResult` ← curator_pre_trash, curator_pre_restore
  * :class:`RuleAction` ← rule evaluation (not a hook return value but
    a service-internal type)
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _ResultShape(BaseModel):
    """Shared base for plugin result shapes."""

    model_config = ConfigDict(
        validate_assignment=True,
        arbitrary_types_allowed=False,
        extra="ignore",
    )


class FileClassification(_ResultShape):
    """Result of ``curator_classify_file``.

    A plugin returns this when it has an opinion about a file's type.
    Multiple plugins may return classifications for the same file;
    the Classification service picks the highest-confidence one (or
    aggregates per service policy).
    """

    file_type: str = Field(..., description="MIME type, e.g. 'application/pdf'")
    extension: str | None = Field(None, description="Suggested extension, e.g. '.pdf'")
    confidence: float = Field(..., ge=0.0, le=1.0)
    classifier: str = Field(..., description="Identifier of the classifying plugin")
    notes: str | None = None


class ValidationResult(_ResultShape):
    """Result of ``curator_validate_file``.

    A plugin returns this when it has performed an integrity check.
    ``ok=False`` indicates the file failed validation (e.g., a corrupt
    PDF). ``error`` contains a human-readable description when ``ok`` is
    False.
    """

    ok: bool
    detector: str = Field(..., description="Identifier of the validating plugin")
    confidence: float = Field(..., ge=0.0, le=1.0)
    error: str | None = Field(None, description="Error description when ok is False")
    details: dict[str, Any] = Field(default_factory=dict)


class BundleProposalMember(_ResultShape):
    """A proposed member within a :class:`BundleProposal`."""

    curator_id: UUID
    role: str = "member"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class BundleProposal(_ResultShape):
    """Result of ``curator_propose_bundle``.

    A plugin returns this when it identifies a pattern that would form a
    bundle. The user (or auto-confirmation logic) decides whether to
    materialize the bundle.
    """

    proposer: str = Field(..., description="Plugin name that produced this proposal")
    name: str
    description: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    members: list[BundleProposalMember]


class ConfirmationResult(_ResultShape):
    """Result of veto-style hooks like ``curator_pre_trash``.

    Plugins return this to gate a destructive operation. ``allow=False``
    blocks the operation; ``reason`` explains why (surfaced to the user).
    """

    allow: bool
    reason: str | None = None
    plugin: str = Field(..., description="Identifier of the gating plugin")


class RuleActionKind(str, Enum):
    """Kinds of action a rule can produce."""

    MOVE = "move"
    TAG = "tag"
    BUNDLE = "bundle"
    TRASH = "trash"
    NOTIFY = "notify"


class RuleAction(_ResultShape):
    """An action emitted by the rules engine.

    Different action kinds use different fields. The Rules service knows
    which fields to read based on ``kind``.
    """

    kind: RuleActionKind
    rule_name: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # MOVE
    target_path: str | None = None

    # TAG
    tag: str | None = None

    # BUNDLE
    bundle_name: str | None = None
    bundle_role: str | None = None

    # TRASH
    reason: str | None = None

    # NOTIFY
    message: str | None = None
