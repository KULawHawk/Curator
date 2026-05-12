"""BundleEntity and BundleMembership — a group of related files.

DESIGN.md §3.4 and §9.

Bundles are sets of files that belong together (e.g., a clinical assessment
package, a course materials set, a project deliverable). A file can belong
to multiple bundles. Membership is per-(bundle, file) with role + confidence.
"""

from __future__ import annotations

from datetime import datetime
from curator._compat.datetime import utcnow_naive
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from curator.models.base import CuratorEntity


def _utcnow() -> datetime:
    return utcnow_naive()


# Conventional bundle types. ``plugin:<name>`` is also valid.
BundleType = Literal["manual", "auto"] | str

# Conventional roles. Other values are allowed (free-form string).
BundleRole = Literal["primary", "member", "related", "reference", "derivative", "attachment"] | str


class BundleEntity(CuratorEntity):
    """A group of related files that belong together.

    A bundle has at least one member with ``role='primary'``. The bundle's
    own confidence is 1.0 for ``manual`` types and proposed by the
    contributing plugin for ``auto`` / ``plugin:<name>`` types.
    """

    bundle_id: UUID = Field(default_factory=uuid4)
    bundle_type: str = Field(
        ..., description="One of: 'manual', 'auto', or 'plugin:<name>'"
    )
    name: str | None = Field(None, description="Human-readable bundle name")
    description: str | None = Field(None, description="Optional longer description")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=_utcnow)


class BundleMembership(CuratorEntity):
    """Membership of a single file in a single bundle.

    The ``(bundle_id, curator_id)`` pair is the primary key. A file can be
    a member of many bundles; each membership carries its own role and
    confidence.
    """

    bundle_id: UUID
    curator_id: UUID  # the file
    role: str = Field(default="member", description="Role within the bundle")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    added_at: datetime = Field(default_factory=_utcnow)
