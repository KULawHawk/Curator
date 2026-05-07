"""Phase Beta v0.14: LSH-vs-O(n) equivalence test for LineageService.

The contract (``docs/PHASE_BETA_LSH.md``): the LSH-routed candidate
selection produces the **same set of confirmed lineage edges** as the
O(n) DB-scan path. The downstream ppdeep ``compare()`` is the actual
correctness gate; the index just narrows the candidate set.

This test runs the same controlled corpus through both paths and
asserts edge-set equality.

Skipped when ``datasketch`` isn't installed (Phase Beta optional dep).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip the entire module if the optional dep is missing.
pytest.importorskip("datasketch")

from curator.services.fuzzy_index import FuzzyIndex  # noqa: E402
from curator.services.lineage import LineageService  # noqa: E402


pytestmark = pytest.mark.integration


def _make_corpus(tmp_tree: Path) -> list[Path]:
    """Create a small corpus with a known mix of duplicate / near-dup /
    distinct files. Crafted so multiple lineage kinds get exercised.
    """
    files: list[Path] = []

    # Pair 1: byte-identical → DUPLICATE edge (same xxhash bucket)
    text_a = "the quick brown fox jumps over the lazy dog\n" * 30
    (tmp_tree / "exact_dup_1.txt").write_text(text_a)
    (tmp_tree / "exact_dup_2.txt").write_text(text_a)

    # Pair 2: near-identical (one word changed) → NEAR_DUPLICATE candidate.
    # ssdeep should flag these as similar; the LSH path must surface
    # the same pairing.
    base = "alpha bravo charlie delta echo foxtrot golf hotel india juliet " * 50
    (tmp_tree / "near_dup_1.txt").write_text(base + "kilo lima mike\n")
    (tmp_tree / "near_dup_2.txt").write_text(base + "kilo lima november\n")

    # Pair 3: completely distinct content (should NOT pair with anything)
    (tmp_tree / "distinct_1.txt").write_text("zulu yankee xray whiskey " * 80)
    (tmp_tree / "distinct_2.txt").write_text("4-quart saucepan recipe omelet " * 80)

    files = sorted(tmp_tree.glob("*.txt"))
    return files


def _confirmed_edge_keys(edges) -> set[tuple[str, str, str]]:
    """Reduce a list of LineageEdges to a comparable set of identity tuples.

    We compare on (kind, sorted-pair-of-curator-ids) so direction
    canonicalization differences (Q13) don't trip equivalence checks.
    """
    keys = set()
    for e in edges:
        a, b = sorted([str(e.from_curator_id), str(e.to_curator_id)])
        keys.add((e.edge_kind.value, a, b))
    return keys


def test_lsh_path_matches_baseline(make_file, tmp_tree, services, repos, db_path):
    """Same corpus, same detectors → same edges via either path.

    This is the v0.14 correctness contract. If it fails, the LSH
    parameter tuning (threshold / num_perm / n-gram size) needs to be
    revisited.
    """
    # Build the corpus and run a normal scan (no LSH yet — baseline path)
    _make_corpus(tmp_tree)
    services.scan.scan(source_id="local", root=str(tmp_tree))

    # Snapshot the baseline edge set produced by the O(n) path.
    baseline_files = list(repos.files.iter_all())
    baseline_edges = []
    for f in baseline_files:
        baseline_edges.extend(repos.lineage.get_edges_for(f.curator_id))
    baseline_keys = _confirmed_edge_keys(baseline_edges)

    # Wipe persisted edges so we can re-run lineage cleanly via LSH.
    for f in baseline_files:
        repos.lineage.delete_for_file(f.curator_id)

    # Build a fresh LineageService instance with a populated FuzzyIndex.
    fuzzy_index = FuzzyIndex()
    for f in repos.files.find_with_fuzzy_hash():
        if f.fuzzy_hash:
            fuzzy_index.add(f.curator_id, f.fuzzy_hash)

    lsh_service = LineageService(
        services.lineage.pm, repos.files, repos.lineage,
        fuzzy_index=fuzzy_index,
    )

    # Re-run lineage for every file via the LSH-enabled service.
    for f in baseline_files:
        lsh_service.compute_for_file(f, persist=True)

    # Snapshot the LSH-produced edge set.
    lsh_edges = []
    for f in baseline_files:
        lsh_edges.extend(repos.lineage.get_edges_for(f.curator_id))
    lsh_keys = _confirmed_edge_keys(lsh_edges)

    # The headline assertion: the LSH path produces the SAME set of
    # confirmed edges as the O(n) path.
    assert lsh_keys == baseline_keys, (
        f"LSH path edges differ from baseline.\n"
        f"  Baseline only: {baseline_keys - lsh_keys}\n"
        f"  LSH only:      {lsh_keys - baseline_keys}\n"
    )

    # Sanity: at least the exact-duplicate pair surfaced (otherwise
    # the test isn't actually testing anything).
    assert any(k[0] == "duplicate" for k in baseline_keys), (
        "Test corpus didn't produce a DUPLICATE edge — fixture broken"
    )


def test_lineage_service_works_without_fuzzy_index(make_file, tmp_tree, services, repos):
    """Sanity: LineageService still functions when fuzzy_index=None.

    This is the Phase Alpha behavior; v0.14 must not regress it.
    """
    _make_corpus(tmp_tree)
    services.scan.scan(source_id="local", root=str(tmp_tree))

    # Default ``services.lineage`` was built without a fuzzy_index
    # (the conftest doesn't provide one). Verify edges still happen.
    files = list(repos.files.iter_all())
    total_edges = sum(len(repos.lineage.get_edges_for(f.curator_id)) for f in files)
    assert total_edges >= 1, (
        "Even without a FuzzyIndex, the O(n) DUPLICATE path should fire "
        "on byte-identical files."
    )


def test_self_maintaining_index_picks_up_files_during_scan(
    make_file, tmp_tree, services, repos
):
    """The FuzzyIndex should auto-populate as compute_for_file runs,
    so files within the same scan can find each other via LSH.

    Construction: process files one at a time through a LineageService
    that has an empty FuzzyIndex. By the end, the index should contain
    every file that had a fuzzy_hash.
    """
    _make_corpus(tmp_tree)
    services.scan.scan(source_id="local", root=str(tmp_tree))

    # Fresh service with an empty index.
    empty_index = FuzzyIndex()
    lsh_service = LineageService(
        services.lineage.pm, repos.files, repos.lineage,
        fuzzy_index=empty_index,
    )
    assert len(empty_index) == 0

    files_with_hash = [
        f for f in repos.files.iter_all() if f.fuzzy_hash
    ]
    assert len(files_with_hash) >= 4, "corpus should produce several fuzzy hashes"

    for f in files_with_hash:
        lsh_service.compute_for_file(f, persist=False)

    # After processing every file, all of them should be indexed.
    assert len(empty_index) == len(files_with_hash)
    for f in files_with_hash:
        assert f.curator_id in empty_index


def test_runtime_pre_populates_fuzzy_index(make_file, tmp_tree, services, repos, db_path):
    """The CuratorRuntime helper ``_build_fuzzy_index_if_available``
    should walk the DB and pre-populate the index at startup.

    This proves the runtime wiring path actually works end-to-end.
    """
    _make_corpus(tmp_tree)
    services.scan.scan(source_id="local", root=str(tmp_tree))

    from curator.cli.runtime import _build_fuzzy_index_if_available
    idx = _build_fuzzy_index_if_available(repos.files)

    assert idx is not None, "datasketch is installed; helper shouldn't return None"

    expected = {
        f.curator_id for f in repos.files.iter_all() if f.fuzzy_hash
    }
    assert len(idx) == len(expected)
    for cid in expected:
        assert cid in idx
