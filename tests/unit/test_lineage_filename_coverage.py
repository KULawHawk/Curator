"""Focused coverage tests for plugins/core/lineage_filename.py.

Sub-ship v1.7.115 (final Tier 1) of Round 2.

Closes:
* Line 67: `_parse_versioned` falls through all patterns → None
* Lines 74-75: `_version_sort_key` ValueError → tuple fallback
* Line 88: same curator_id short-circuit
* Line 100: one parsed result is None → return None
* Lines 121-122: file_b is newer → swap so file_b is the "to"
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from curator.models.file import FileEntity
from curator.models.lineage import LineageKind
from curator.plugins.core.lineage_filename import (
    Plugin,
    _parse_versioned,
    _version_sort_key,
)


NOW = datetime(2026, 5, 13, 12, 0, 0)


def _make_entity(*, curator_id: UUID, source_path: str) -> FileEntity:
    return FileEntity(
        curator_id=curator_id,
        source_id="local",
        source_path=source_path,
        size=10,
        mtime=NOW,
    )


# ---------------------------------------------------------------------------
# _parse_versioned no-match (67)
# ---------------------------------------------------------------------------


def test_parse_versioned_returns_none_when_no_pattern_matches():
    # Line 67: no pattern matches → return None.
    # A bare filename like "readme" with no extension matches nothing.
    assert _parse_versioned("readme") is None
    # Also test something with extension but no version markers.
    assert _parse_versioned("plainfile.txt") is None


# ---------------------------------------------------------------------------
# _version_sort_key fallback (74-75)
# ---------------------------------------------------------------------------


def test_version_sort_key_returns_string_tuple_on_value_error():
    # Lines 74-75: int() raises ValueError on non-numeric → fall back
    # to returning (version,) as a string tuple.
    result = _version_sort_key("alpha")
    assert result == ("alpha",)


# ---------------------------------------------------------------------------
# Plugin.curator_compute_lineage defensives (88, 100, 121-122)
# ---------------------------------------------------------------------------


def test_returns_none_for_same_curator_id():
    # Line 88: file_a.curator_id == file_b.curator_id → return None.
    plugin = Plugin()
    cid = UUID("11111111-1111-1111-1111-111111111111")
    entity = _make_entity(curator_id=cid, source_path="/a/Report_v1.txt")
    assert plugin.curator_compute_lineage(file_a=entity, file_b=entity) is None


def test_returns_none_when_one_filename_has_no_version_pattern():
    # Line 100: one of the two parsed results is None → return None.
    plugin = Plugin()
    a = _make_entity(
        curator_id=UUID("11111111-1111-1111-1111-111111111111"),
        source_path="/a/Report_v1.txt",
    )
    b = _make_entity(
        curator_id=UUID("22222222-2222-2222-2222-222222222222"),
        source_path="/a/readme",  # matches no pattern
    )
    assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None


def test_emits_edge_with_file_a_as_newer_version():
    # Lines 121-122: file_a's version is GREATER than file_b's →
    # swap so from=b, to=a (older→newer).
    plugin = Plugin()
    a = _make_entity(
        curator_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        source_path="/a/Doc_v3.txt",
    )
    b = _make_entity(
        curator_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        source_path="/a/Doc_v1.txt",
    )
    edge = plugin.curator_compute_lineage(file_a=a, file_b=b)
    assert edge is not None
    # b (v1) → a (v3) — older to newer
    assert edge.from_curator_id == b.curator_id
    assert edge.to_curator_id == a.curator_id
    assert edge.edge_kind == LineageKind.VERSION_OF
    assert "1 -> 3" in (edge.notes or "")
