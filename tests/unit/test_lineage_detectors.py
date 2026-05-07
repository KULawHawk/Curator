"""Lineage detector tests.

Each detector gets its own test class. Detectors are exercised directly
via their plugin hookimpl so we don't depend on the rest of the
LineageService machinery for these tests.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from curator.models import FileEntity, LineageKind
from curator.plugins.core import (
    lineage_filename,
    lineage_fuzzy_dup,
    lineage_hash_dup,
)


def _make_file(*, path: str, size: int = 100, xxhash: str | None = None,
               fuzzy_hash: str | None = None) -> FileEntity:
    """Helper to build a FileEntity with the fields detectors care about."""
    return FileEntity(
        source_id="local",
        source_path=path,
        size=size,
        mtime=datetime.utcnow(),
        xxhash3_128=xxhash,
        fuzzy_hash=fuzzy_hash,
    )


# ---------------------------------------------------------------------------
# DUPLICATE detector
# ---------------------------------------------------------------------------

class TestLineageHashDup:
    def test_identical_xxhash_emits_duplicate_edge(self):
        plugin = lineage_hash_dup.Plugin()
        h = "deadbeef" * 4
        a = _make_file(path="/a", xxhash=h)
        b = _make_file(path="/b", xxhash=h)
        edge = plugin.curator_compute_lineage(file_a=a, file_b=b)
        assert edge is not None
        assert edge.edge_kind == LineageKind.DUPLICATE
        assert edge.confidence == 1.0
        assert edge.detected_by == "curator.core.lineage_hash_dup"

    def test_different_xxhash_no_edge(self):
        plugin = lineage_hash_dup.Plugin()
        a = _make_file(path="/a", xxhash="aaaa" * 8)
        b = _make_file(path="/b", xxhash="bbbb" * 8)
        assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None

    def test_missing_hash_no_edge(self):
        plugin = lineage_hash_dup.Plugin()
        a = _make_file(path="/a", xxhash=None)
        b = _make_file(path="/b", xxhash="bbbb" * 8)
        assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None

    def test_same_curator_id_no_edge(self):
        plugin = lineage_hash_dup.Plugin()
        f = _make_file(path="/a", xxhash="abc" * 11)
        # Pair file with itself — should not emit a self-loop.
        assert plugin.curator_compute_lineage(file_a=f, file_b=f) is None


# ---------------------------------------------------------------------------
# VERSION_OF detector
# ---------------------------------------------------------------------------

class TestLineageFilename:
    def test_v1_v2_in_same_dir_emits_version_of(self):
        plugin = lineage_filename.Plugin()
        a = _make_file(path="/work/Stats_v1.bas")
        b = _make_file(path="/work/Stats_v2.bas")
        edge = plugin.curator_compute_lineage(file_a=a, file_b=b)
        assert edge is not None
        assert edge.edge_kind == LineageKind.VERSION_OF
        # Direction goes from older (v1) to newer (v2).
        assert edge.from_curator_id == a.curator_id
        assert edge.to_curator_id == b.curator_id

    def test_paren_copy_pattern(self):
        # The detector requires BOTH filenames to match a version pattern.
        # Two parenthesized copies qualify; an unversioned base does not
        # (would require a base-match extension to the detector).
        plugin = lineage_filename.Plugin()
        a = _make_file(path="/work/Report (1).docx")
        b = _make_file(path="/work/Report (2).docx")
        edge = plugin.curator_compute_lineage(file_a=a, file_b=b)
        assert edge is not None
        assert edge.edge_kind == LineageKind.VERSION_OF

    def test_different_directories_no_edge(self):
        # Filename version detection is same-directory only — prevents
        # cross-tree false positives.
        plugin = lineage_filename.Plugin()
        a = _make_file(path="/work/Stats_v1.bas")
        b = _make_file(path="/archive/Stats_v2.bas")
        assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None

    def test_different_basenames_no_edge(self):
        plugin = lineage_filename.Plugin()
        a = _make_file(path="/work/Stats_v1.bas")
        b = _make_file(path="/work/Other_v2.bas")
        assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None

    def test_same_version_no_edge(self):
        # If both files claim the same version we don't emit anything —
        # they're not in a v1→v2 relationship.
        plugin = lineage_filename.Plugin()
        a = _make_file(path="/work/Stats_v1.bas")
        b = _make_file(path="/work/Stats_v1.bas")  # identical
        assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None


# ---------------------------------------------------------------------------
# NEAR_DUPLICATE detector
# ---------------------------------------------------------------------------

class TestLineageFuzzyDup:
    def test_no_fuzzy_hash_no_edge(self):
        plugin = lineage_fuzzy_dup.Plugin()
        a = _make_file(path="/a", fuzzy_hash=None)
        b = _make_file(path="/b", fuzzy_hash=None)
        assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None

    def test_same_xxhash_returns_none_lets_dup_detector_handle(self):
        # If files are exact duplicates (same xxhash), the fuzzy detector
        # bows out so we don't emit redundant edges. lineage_hash_dup
        # owns DUPLICATE edges.
        plugin = lineage_fuzzy_dup.Plugin()
        h = "abc123" * 5
        a = _make_file(
            path="/a", xxhash=h,
            fuzzy_hash="3:UkLKKIUKact:UAIGi",
        )
        b = _make_file(
            path="/b", xxhash=h,
            fuzzy_hash="3:UkLKKIUKact:UAIGi",
        )
        assert plugin.curator_compute_lineage(file_a=a, file_b=b) is None
