"""Focused coverage tests for plugins/core/lineage_fuzzy_dup.py.

Sub-ship v1.7.118 of Round 2 Tier 2.

Closes 17 uncovered lines + 3 partial branches:

* Lines 44-48: module-level ppdeep import-fallback chain (vendored
  copy missing → fall back to PyPI package → fall back to None).
  Exercised via importlib.reload with sys.modules patched.
* Line 62: `_compare is None` short-circuit.
* Line 70: same-curator_id short-circuit.
* Lines 77-93: the actual comparison body (try _compare, threshold
  check, canonicalization swap, edge construction).
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime
from uuid import UUID

import pytest

import curator.plugins.core.lineage_fuzzy_dup as lfd
from curator.models.file import FileEntity
from curator.models.lineage import LineageKind


NOW = datetime(2026, 5, 13, 12, 0, 0)


def _make_entity(
    *,
    curator_id: UUID,
    fuzzy_hash: str | None = "3:abc:def",
    xxhash: str | None = None,
) -> FileEntity:
    return FileEntity(
        curator_id=curator_id,
        source_id="local",
        source_path=f"/{curator_id}.txt",
        size=10,
        mtime=NOW,
        fuzzy_hash=fuzzy_hash,
        xxhash3_128=xxhash,
    )


# ---------------------------------------------------------------------------
# Module-level import fallback chain (44-48)
# ---------------------------------------------------------------------------


def test_module_import_falls_back_to_pypi_ppdeep_when_vendored_missing(
    monkeypatch,
):
    # Lines 45-46: vendored ppdeep raises ImportError → secondary
    # `from ppdeep import compare` succeeds → _compare gets the
    # PyPI compare function. Force the vendored to fail.
    monkeypatch.setitem(sys.modules, "curator._vendored.ppdeep", None)
    # Provide a fake `ppdeep` module so the secondary import succeeds.
    import types
    fake_ppdeep = types.ModuleType("ppdeep")
    fake_ppdeep.compare = lambda a, b: 50  # arbitrary
    monkeypatch.setitem(sys.modules, "ppdeep", fake_ppdeep)

    reloaded = importlib.reload(lfd)
    assert reloaded._compare is not None


def test_module_import_falls_back_to_none_when_both_missing(monkeypatch):
    # Lines 47-48: vendored ppdeep raises ImportError AND `ppdeep`
    # also raises → _compare = None.
    monkeypatch.setitem(sys.modules, "curator._vendored.ppdeep", None)
    monkeypatch.setitem(sys.modules, "ppdeep", None)

    reloaded = importlib.reload(lfd)
    assert reloaded._compare is None

    # Reload back to normal state for other tests.
    monkeypatch.undo()
    importlib.reload(lfd)


# ---------------------------------------------------------------------------
# _compare is None short-circuit (62)
# ---------------------------------------------------------------------------


def test_returns_none_when_compare_unavailable(monkeypatch):
    # Line 62: _compare is None → return None.
    monkeypatch.setattr(lfd, "_compare", None)
    plugin = lfd.Plugin()
    a = _make_entity(curator_id=UUID("11111111-1111-1111-1111-111111111111"))
    b = _make_entity(curator_id=UUID("22222222-2222-2222-2222-222222222222"))
    assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None


# ---------------------------------------------------------------------------
# Both missing fuzzy_hash short-circuit (line 67, branch coverage)
# ---------------------------------------------------------------------------


def test_returns_none_when_either_fuzzy_hash_missing(monkeypatch):
    # Line 67: one or both files lack fuzzy_hash → return None.
    monkeypatch.setattr(lfd, "_compare", lambda a, b: 100)
    plugin = lfd.Plugin()
    a = _make_entity(
        curator_id=UUID("11111111-1111-1111-1111-111111111111"),
        fuzzy_hash=None,
    )
    b = _make_entity(
        curator_id=UUID("22222222-2222-2222-2222-222222222222"),
        fuzzy_hash="3:abc:def",
    )
    assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None


# ---------------------------------------------------------------------------
# Same curator_id short-circuit (70)
# ---------------------------------------------------------------------------


def test_returns_none_for_same_curator_id(monkeypatch):
    monkeypatch.setattr(lfd, "_compare", lambda a, b: 100)
    plugin = lfd.Plugin()
    cid = UUID("11111111-1111-1111-1111-111111111111")
    entity = _make_entity(curator_id=cid, fuzzy_hash="3:abc:def")
    assert plugin.curator_compute_lineage(file_a=entity, file_b=entity) is None


# ---------------------------------------------------------------------------
# Exact-hash duplicate handled elsewhere (line 74-75)
# ---------------------------------------------------------------------------


def test_returns_none_when_xxhash_matches(monkeypatch):
    # Lines 74-75: lineage_hash_dup owns exact-match edges; we skip.
    monkeypatch.setattr(lfd, "_compare", lambda a, b: 100)
    plugin = lfd.Plugin()
    a = _make_entity(
        curator_id=UUID("11111111-1111-1111-1111-111111111111"),
        fuzzy_hash="3:abc:def",
        xxhash="same_xxhash",
    )
    b = _make_entity(
        curator_id=UUID("22222222-2222-2222-2222-222222222222"),
        fuzzy_hash="3:abc:def",
        xxhash="same_xxhash",
    )
    assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None


# ---------------------------------------------------------------------------
# _compare raises (77-81)
# ---------------------------------------------------------------------------


def test_returns_none_when_compare_raises(monkeypatch):
    def boom(a, b):
        raise RuntimeError("malformed hashes")
    monkeypatch.setattr(lfd, "_compare", boom)
    plugin = lfd.Plugin()
    a = _make_entity(curator_id=UUID("11111111-1111-1111-1111-111111111111"))
    b = _make_entity(curator_id=UUID("22222222-2222-2222-2222-222222222222"))
    assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None


# ---------------------------------------------------------------------------
# Similarity threshold (83-84)
# ---------------------------------------------------------------------------


def test_returns_none_when_similarity_below_threshold(monkeypatch):
    # Line 83-84: similarity < 70 → return None.
    monkeypatch.setattr(lfd, "_compare", lambda a, b: 50)
    plugin = lfd.Plugin()
    a = _make_entity(curator_id=UUID("11111111-1111-1111-1111-111111111111"))
    b = _make_entity(curator_id=UUID("22222222-2222-2222-2222-222222222222"))
    assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None


def test_returns_none_when_similarity_is_none(monkeypatch):
    # Line 83 branch: similarity is None → return None.
    monkeypatch.setattr(lfd, "_compare", lambda a, b: None)
    plugin = lfd.Plugin()
    a = _make_entity(curator_id=UUID("11111111-1111-1111-1111-111111111111"))
    b = _make_entity(curator_id=UUID("22222222-2222-2222-2222-222222222222"))
    assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None


# ---------------------------------------------------------------------------
# Edge construction + canonicalization swap (86-100)
# ---------------------------------------------------------------------------


def test_emits_near_duplicate_edge_above_threshold(monkeypatch):
    # Lines 86-100: similarity >= 70 → emit NEAR_DUPLICATE edge with
    # confidence=similarity/100. file_a < file_b canonical order.
    monkeypatch.setattr(lfd, "_compare", lambda a, b: 85)
    plugin = lfd.Plugin()
    smaller = UUID("00000000-0000-0000-0000-000000000001")
    bigger = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    a = _make_entity(curator_id=smaller, fuzzy_hash="3:abc:def")
    b = _make_entity(curator_id=bigger, fuzzy_hash="3:xyz:def")

    edge = plugin.curator_compute_lineage(file_a=a, file_b=b)
    assert edge is not None
    assert edge.edge_kind == LineageKind.NEAR_DUPLICATE
    assert edge.from_curator_id == smaller
    assert edge.to_curator_id == bigger
    assert edge.confidence == 0.85
    assert "85%" in (edge.notes or "")


def test_emits_edge_with_canonicalization_swap(monkeypatch):
    # Lines 89-90: file_a's curator_id > file_b's → swap so smaller
    # becomes `from`.
    monkeypatch.setattr(lfd, "_compare", lambda a, b: 90)
    plugin = lfd.Plugin()
    smaller = UUID("00000000-0000-0000-0000-000000000001")
    bigger = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    # Pass bigger first to trigger swap.
    a = _make_entity(curator_id=bigger, fuzzy_hash="3:abc:def")
    b = _make_entity(curator_id=smaller, fuzzy_hash="3:xyz:def")

    edge = plugin.curator_compute_lineage(file_a=a, file_b=b)
    assert edge is not None
    assert edge.from_curator_id == smaller
    assert edge.to_curator_id == bigger
