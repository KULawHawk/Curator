"""NEAR_DUPLICATE lineage detector — fuzzy hash similarity.

DESIGN.md §8.3.2.

If two files have similar (but not identical) fuzzy hashes, they're
NEAR_DUPLICATEs with confidence proportional to their similarity score.
We use ssdeep-style fuzzy hashing via :mod:`ppdeep`.

Threshold: similarity >= 70 (out of 100). Below that, the relationship
is too weak to claim. The confidence stored on the edge is
``similarity / 100`` so that a 95%-similar pair has confidence 0.95 and
will auto-confirm at the standard NEAR_DUPLICATE threshold (DESIGN §8.2).

Graceful degradation: if ``ppdeep`` is not installed (it'll be vendored
in Step 8 as ``curator._vendored.ppdeep``), the hookimpl is a no-op.
The hash pipeline similarly skips fuzzy-hash computation when ppdeep
is unavailable, so this is consistent.

NEAR_DUPLICATE is a SYMMETRIC relationship — there's no meaningful
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


DETECTOR_NAME = "curator.core.lineage_fuzzy_dup"
SIMILARITY_THRESHOLD = 70  # out of 100


# Try the vendored copy first (Step 8 destination), then the PyPI
# package (transitional). If neither is available, ``_compare`` is None
# and the hook short-circuits.
_compare = None
try:
    from curator._vendored.ppdeep import compare as _compare  # type: ignore[import-not-found]
except ImportError:
    try:
        from ppdeep import compare as _compare  # type: ignore[import-not-found]
    except ImportError:
        _compare = None


class Plugin:
    """NEAR_DUPLICATE detector via fuzzy-hash similarity."""

    @hookimpl
    def curator_compute_lineage(
        self,
        file_a: FileEntity,
        file_b: FileEntity,
    ) -> LineageEdge | None:
        # Hard requirement: ppdeep must be available.
        if _compare is None:
            return None

        # Both files must have fuzzy hashes (i.e. they were text-eligible
        # and the hash pipeline computed one).
        if not (file_a.fuzzy_hash and file_b.fuzzy_hash):
            return None

        if file_a.curator_id == file_b.curator_id:
            return None

        # If they're exact duplicates, the lineage_hash_dup plugin owns
        # the edge — we don't emit a redundant NEAR_DUPLICATE.
        if file_a.xxhash3_128 and file_b.xxhash3_128 and file_a.xxhash3_128 == file_b.xxhash3_128:
            return None

        try:
            similarity = _compare(file_a.fuzzy_hash, file_b.fuzzy_hash)
        except Exception:
            # Malformed hashes shouldn't crash the lineage pipeline.
            return None

        if similarity is None or similarity < SIMILARITY_THRESHOLD:
            return None

        # Canonicalize direction (Q13). NEAR_DUPLICATE is symmetric, so
        # we always emit ``from = min(curator_id), to = max(curator_id)``.
        # That way (A, B) and (B, A) calls collapse to the same edge.
        if str(file_a.curator_id) > str(file_b.curator_id):
            file_a, file_b = file_b, file_a

        confidence = similarity / 100.0
        return LineageEdge(
            from_curator_id=file_a.curator_id,
            to_curator_id=file_b.curator_id,
            edge_kind=LineageKind.NEAR_DUPLICATE,
            confidence=confidence,
            detected_by=DETECTOR_NAME,
            notes=f"fuzzy similarity: {similarity}%",
        )
