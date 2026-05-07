"""Property-based tests for lineage edge symmetry.

Open question Q13 (resolved 2026-05-06): symmetric edge kinds were
emitted with arbitrary direction, so calling a detector on ``(A, B)``
and then ``(B, A)`` produced two distinct edges that the unique
constraint ``(from, to, kind, detector)`` couldn't collapse. Fixed by
having symmetric detectors normalize so ``from = min(curator_id)``,
``to = max(curator_id)``.

These tests now ENFORCE the invariant. If anyone re-introduces the
asymmetry, the third test will catch it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from hypothesis import given, settings, strategies as st

from curator.models import FileEntity, LineageKind
from curator.plugins.core import lineage_hash_dup


pytestmark = pytest.mark.property


# Generate UUIDs deterministically from int seeds so tests are reproducible.
uuid_st = st.integers(min_value=0, max_value=2**64 - 1).map(lambda i: UUID(int=i))


def _file_with(curator_id: UUID, xxhash: str | None) -> FileEntity:
    f = FileEntity(
        source_id="local",
        source_path=f"/tmp/{curator_id}",
        size=100,
        mtime=datetime.utcnow(),
        xxhash3_128=xxhash,
    )
    # Override the auto-generated curator_id with our deterministic one.
    f.curator_id = curator_id
    return f


@given(uuid_a=uuid_st, uuid_b=uuid_st)
@settings(max_examples=50, deadline=None)
def test_dup_detector_never_emits_self_edges(uuid_a, uuid_b):
    """Even with same xxhash, a file should never link to itself."""
    plugin = lineage_hash_dup.Plugin()
    h = "deadbeef" * 4
    a = _file_with(uuid_a, h)
    edge = plugin.curator_compute_lineage(file_a=a, file_b=a)
    assert edge is None


@given(uuid_a=uuid_st, uuid_b=uuid_st)
@settings(max_examples=50, deadline=None)
def test_dup_detector_returns_edge_for_distinct_files_with_same_hash(uuid_a, uuid_b):
    """Pre-condition for the dedup invariant: detector emits an edge."""
    if uuid_a == uuid_b:
        return  # same UUID, skipped (covered by previous test)
    plugin = lineage_hash_dup.Plugin()
    h = "deadbeef" * 4
    a = _file_with(uuid_a, h)
    b = _file_with(uuid_b, h)
    edge = plugin.curator_compute_lineage(file_a=a, file_b=b)
    assert edge is not None
    assert edge.edge_kind == LineageKind.DUPLICATE


@given(uuid_a=uuid_st, uuid_b=uuid_st)
@settings(max_examples=50, deadline=None)
def test_dup_detector_emits_canonical_direction_for_symmetric_edges(uuid_a, uuid_b):
    """Symmetric DUPLICATE edges always have ``from <= to`` (Q13).

    This invariant lets the unique constraint ``(from, to, kind, detector)``
    de-dup symmetric relationships across calls. Calling the detector
    with ``(A, B)`` and then with ``(B, A)`` must produce the same
    canonical edge.
    """
    if uuid_a == uuid_b:
        return
    plugin = lineage_hash_dup.Plugin()
    h = "deadbeef" * 4
    a = _file_with(uuid_a, h)
    b = _file_with(uuid_b, h)

    edge_ab = plugin.curator_compute_lineage(file_a=a, file_b=b)
    edge_ba = plugin.curator_compute_lineage(file_a=b, file_b=a)
    assert edge_ab is not None and edge_ba is not None

    # The canonical form puts the lexically-smaller UUID first, regardless
    # of call order.
    smaller = min(uuid_a, uuid_b, key=str)
    larger = max(uuid_a, uuid_b, key=str)

    assert edge_ab.from_curator_id == smaller
    assert edge_ab.to_curator_id == larger
    assert edge_ba.from_curator_id == smaller
    assert edge_ba.to_curator_id == larger
