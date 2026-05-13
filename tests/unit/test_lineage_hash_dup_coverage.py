"""Focused coverage tests for plugins/core/lineage_hash_dup.py.

Sub-ship v1.7.114 of Round 2 Tier 1.

Closes branch 50->53 — the True arm of the canonicalization swap
where `str(file_a.curator_id) > str(file_b.curator_id)` triggers a
swap so the lex-smaller curator_id is always the `from`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from curator.models.file import FileEntity
from curator.models.lineage import LineageKind
from curator.plugins.core.lineage_hash_dup import Plugin


NOW = datetime(2026, 5, 13, 12, 0, 0)


def _make_entity(*, curator_id: UUID, xxhash: str) -> FileEntity:
    return FileEntity(
        curator_id=curator_id,
        source_id="local",
        source_path=f"/{curator_id}.txt",
        size=10,
        mtime=NOW,
        xxhash3_128=xxhash,
    )


def test_canonicalizes_so_smaller_curator_id_is_from():
    # Branch 50->53 True arm: file_a's curator_id is LEXICALLY GREATER
    # than file_b's → swap so from=min(), to=max() canonical order.
    plugin = Plugin()
    # Construct UUIDs where str(a) > str(b)
    bigger = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    smaller = UUID("00000000-0000-0000-0000-000000000001")
    file_a = _make_entity(curator_id=bigger, xxhash="hash_x")
    file_b = _make_entity(curator_id=smaller, xxhash="hash_x")

    edge = plugin.curator_compute_lineage(file_a=file_a, file_b=file_b)
    assert edge is not None
    # Despite a being passed as file_a, the canonicalization swap
    # makes the smaller one the `from`.
    assert edge.from_curator_id == smaller
    assert edge.to_curator_id == bigger
    assert edge.edge_kind == LineageKind.DUPLICATE
    assert edge.confidence == 1.0


def test_returns_edge_when_called_in_canonical_order():
    # Branch 50->53 False arm sanity check (already covered by other
    # tests, but kept here for documentation of the symmetric case).
    plugin = Plugin()
    smaller = UUID("00000000-0000-0000-0000-000000000001")
    bigger = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    file_a = _make_entity(curator_id=smaller, xxhash="hash_y")
    file_b = _make_entity(curator_id=bigger, xxhash="hash_y")

    edge = plugin.curator_compute_lineage(file_a=file_a, file_b=file_b)
    assert edge is not None
    assert edge.from_curator_id == smaller
    assert edge.to_curator_id == bigger
