"""DUPLICATE lineage detector — exact byte-identical files.

DESIGN.md §8.3.1.

If two files have the same xxhash3_128 fingerprint, they're DUPLICATEs
with confidence 1.0. This is the cheapest, highest-confidence detector
and runs first in the lineage pipeline.

DUPLICATE is a SYMMETRIC relationship — there's no meaningful
direction to it. To make the unique constraint
``(from_curator_id, to_curator_id, edge_kind, detected_by)`` correctly
deduplicate when the detector runs on both ``(A, B)`` and ``(B, A)``,
we canonicalize so the lexically-smaller curator_id is always ``from``.
See open question Q13 in BUILD_TRACKER.md.
"""

from __future__ import annotations

from curator.models.file import FileEntity
from curator.models.lineage import LineageEdge, LineageKind
from curator.plugins.hookspecs import hookimpl


DETECTOR_NAME = "curator.core.lineage_hash_dup"


class Plugin:
    """DUPLICATE detector via xxhash3_128 equality."""

    @hookimpl
    def curator_compute_lineage(
        self,
        file_a: FileEntity,
        file_b: FileEntity,
    ) -> LineageEdge | None:
        # Both files must have full hashes to compare.
        if not (file_a.xxhash3_128 and file_b.xxhash3_128):
            return None

        # Defensive: don't claim a file is a duplicate of itself.
        if file_a.curator_id == file_b.curator_id:
            return None

        if file_a.xxhash3_128 != file_b.xxhash3_128:
            return None

        # Canonicalize direction (Q13). DUPLICATE is symmetric, so we
        # always emit ``from = min(curator_id), to = max(curator_id)``.
        # That way (A, B) and (B, A) calls collapse to the same edge.
        if str(file_a.curator_id) > str(file_b.curator_id):
            file_a, file_b = file_b, file_a

        return LineageEdge(
            from_curator_id=file_a.curator_id,
            to_curator_id=file_b.curator_id,
            edge_kind=LineageKind.DUPLICATE,
            confidence=1.0,
            detected_by=DETECTOR_NAME,
        )
