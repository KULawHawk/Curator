"""Unit tests for :class:`curator.services.fuzzy_index.FuzzyIndex`.

Phase Beta gate #1, v0.13. See ``docs/PHASE_BETA_LSH.md`` for the spec
these tests enforce.

All tests use ``pytest.importorskip("datasketch")`` so they skip cleanly
when the optional ``[beta]`` extra isn't installed.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest


# Skip the entire module if datasketch isn't available — it's a Phase
# Beta optional dep, not a Phase Alpha core dep.
pytest.importorskip("datasketch")

# These imports MUST come after importorskip; otherwise pytest tries to
# import the module at collection time and the skip never fires.
from curator.services.fuzzy_index import (  # noqa: E402
    DEFAULT_NUM_PERM,
    DEFAULT_THRESHOLD,
    FuzzyIndex,
    _ngrams,
    _parse_fuzzy_hash,
)


# ---------------------------------------------------------------------------
# Helpers — synthesize ssdeep-style hashes deterministically from a seed.
# ---------------------------------------------------------------------------

# We don't actually need to compute *real* ssdeep hashes; the FuzzyIndex
# only cares that the input parses as ``"<bs>:<s1>:<s2>"``. We do want
# realistic similarity behavior though, so we vary s1/s2 in controlled
# ways that map cleanly to MinHash overlap.

def _hash_from_text(text: str, block_size: int = 96) -> str:
    """Build a fake ssdeep hash from a deterministic text string.

    Splits ``text`` into roughly half-and-half and uses each half as
    s1/s2. Real ssdeep hashes look richer than this, but for testing
    candidate selection what matters is that overlapping inputs share
    n-grams and disjoint inputs don't.
    """
    if not text:
        text = "x"
    midpoint = max(1, len(text) // 2)
    s1 = text[:midpoint] or "x"
    s2 = text[midpoint:] or "y"
    # ssdeep s1/s2 are 64-char-max base64-ish strings; we just need
    # something that won't collide trivially.
    return f"{block_size}:{s1}:{s2}"


def _u(n: int) -> UUID:
    """Deterministic UUID from a small int (for stable test fixtures)."""
    return UUID(int=n)


# ---------------------------------------------------------------------------
# Helper-level tests — _parse_fuzzy_hash and _ngrams
# ---------------------------------------------------------------------------

class TestParseFuzzyHash:
    def test_parses_well_formed_hash(self):
        bs, s1, s2 = _parse_fuzzy_hash("96:abc123:xyz")
        assert bs == 96
        assert s1 == "abc123"
        assert s2 == "xyz"

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be str"):
            _parse_fuzzy_hash(123)  # type: ignore[arg-type]

    def test_rejects_wrong_part_count(self):
        with pytest.raises(ValueError, match="3 colon-separated"):
            _parse_fuzzy_hash("96:abc")
        with pytest.raises(ValueError, match="3 colon-separated"):
            _parse_fuzzy_hash("96:abc:def:ghi")

    def test_rejects_non_integer_block_size(self):
        with pytest.raises(ValueError, match="integer block size"):
            _parse_fuzzy_hash("notanumber:abc:def")


class TestNgrams:
    def test_short_input_returns_empty(self):
        assert _ngrams("ab", n=3) == set()

    def test_basic_3grams(self):
        # "abcde" → {"abc", "bcd", "cde"}
        result = _ngrams("abcde", n=3)
        assert result == {b"abc", b"bcd", b"cde"}

    def test_overlapping_inputs_share_grams(self):
        a = _ngrams("the quick brown fox", n=3)
        b = _ngrams("the quick lazy dog", n=3)
        # "the", "he ", "e q", " qu", "qui", "uic", "ick", "ck " all in both
        overlap = a & b
        assert len(overlap) >= 5


# ---------------------------------------------------------------------------
# Core FuzzyIndex contract
# ---------------------------------------------------------------------------

class TestEmptyIndex:
    def test_len_is_zero(self):
        idx = FuzzyIndex()
        assert len(idx) == 0

    def test_query_returns_empty(self):
        idx = FuzzyIndex()
        assert idx.query(_hash_from_text("anything goes here")) == []

    def test_default_construction_uses_documented_constants(self):
        idx = FuzzyIndex()
        assert idx._threshold == DEFAULT_THRESHOLD
        assert idx._num_perm == DEFAULT_NUM_PERM


class TestAddAndQuery:
    def test_add_then_query_finds_self(self):
        idx = FuzzyIndex(threshold=0.3)  # loose threshold so the same hash matches
        h = _hash_from_text("the quick brown fox jumps over the lazy dog")
        idx.add(_u(1), h)
        results = idx.query(h)
        assert _u(1) in results

    def test_add_then_query_finds_close_neighbors(self):
        # Two hashes built from heavily overlapping text → MinHash sigs
        # share most n-grams → LSH puts them in the same bucket.
        idx = FuzzyIndex(threshold=0.3)
        h1 = _hash_from_text("the quick brown fox jumps over the lazy dog and runs away")
        h2 = _hash_from_text("the quick brown fox jumps over the lazy cat and runs away")
        idx.add(_u(1), h1)
        idx.add(_u(2), h2)

        results = idx.query(h1)
        assert _u(2) in results, (
            "Two near-identical hashes should bucket together at threshold 0.3"
        )

    def test_unrelated_hashes_dont_match(self):
        idx = FuzzyIndex(threshold=0.5)
        h1 = _hash_from_text("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        h2 = _hash_from_text("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
        idx.add(_u(1), h1)

        results = idx.query(h2)
        assert _u(1) not in results, (
            "Wholly disjoint inputs should NOT be flagged as candidates"
        )


class TestRemoveAndClear:
    def test_remove_eliminates_from_results(self):
        idx = FuzzyIndex(threshold=0.3)
        h = _hash_from_text("the quick brown fox jumps over the lazy dog")
        idx.add(_u(1), h)
        assert _u(1) in idx.query(h)

        idx.remove(_u(1))
        assert _u(1) not in idx.query(h)
        assert len(idx) == 0

    def test_remove_unknown_id_is_silent_noop(self):
        idx = FuzzyIndex()
        # Should not raise.
        idx.remove(_u(999))
        assert len(idx) == 0

    def test_clear_resets_to_empty(self):
        idx = FuzzyIndex(threshold=0.3)
        for i, txt in enumerate(["alpha", "beta", "gamma"]):
            idx.add(_u(i), _hash_from_text(txt * 20))
        assert len(idx) == 3

        idx.clear()
        assert len(idx) == 0
        assert idx.query(_hash_from_text("alpha alpha alpha")) == []


class TestIdempotency:
    def test_double_add_is_idempotent(self):
        """Adding the same id twice should leave len() at 1, not 2.

        Real-world: LineageService might re-add a file after metadata
        change. We don't want the LSH to have stale duplicate entries.
        """
        idx = FuzzyIndex(threshold=0.3)
        h = _hash_from_text("the quick brown fox" * 5)
        idx.add(_u(1), h)
        idx.add(_u(1), h)
        assert len(idx) == 1

    def test_double_add_with_different_hash_uses_latest(self):
        idx = FuzzyIndex(threshold=0.3)
        h1 = _hash_from_text("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        h2 = _hash_from_text("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
        idx.add(_u(1), h1)
        idx.add(_u(1), h2)  # overwrites
        assert len(idx) == 1
        # Querying with the new hash finds it; with the old hash, doesn't.
        assert _u(1) in idx.query(h2)
        assert _u(1) not in idx.query(h1)


class TestThresholdBehavior:
    def test_threshold_must_be_in_open_zero_to_one(self):
        with pytest.raises(ValueError, match="threshold"):
            FuzzyIndex(threshold=0.0)
        with pytest.raises(ValueError, match="threshold"):
            FuzzyIndex(threshold=1.5)
        with pytest.raises(ValueError, match="threshold"):
            FuzzyIndex(threshold=-0.1)

    def test_num_perm_must_be_at_least_two(self):
        with pytest.raises(ValueError, match="num_perm"):
            FuzzyIndex(num_perm=1)

    def test_higher_threshold_drops_borderline_matches(self):
        # Build two hashes with maybe-30%-overlap. At threshold 0.3
        # they bucket; at threshold 0.9 they don't.
        h1 = _hash_from_text("alpha bravo charlie delta echo foxtrot golf hotel")
        h2 = _hash_from_text("alpha bravo zulu yankee xray whiskey victor uniform")

        loose = FuzzyIndex(threshold=0.3)
        loose.add(_u(1), h1)
        loose_hits = loose.query(h2)

        tight = FuzzyIndex(threshold=0.9)
        tight.add(_u(1), h1)
        tight_hits = tight.query(h2)

        # Tighter threshold should be at most as permissive as the looser
        # one (and almost always strictly less so for partial overlaps).
        assert len(tight_hits) <= len(loose_hits)


class TestInputValidation:
    def test_invalid_curator_id_type_raises(self):
        idx = FuzzyIndex()
        with pytest.raises(TypeError, match="UUID"):
            idx.add("not-a-uuid", _hash_from_text("x"))  # type: ignore[arg-type]

    def test_malformed_fuzzy_hash_raises(self):
        idx = FuzzyIndex()
        with pytest.raises(ValueError, match="3 colon-separated"):
            idx.add(_u(1), "missing-colons")
        with pytest.raises(ValueError):
            idx.query("not even close")


class TestContainerProtocol:
    def test_in_operator_works(self):
        idx = FuzzyIndex(threshold=0.3)
        idx.add(_u(1), _hash_from_text("xyzxyzxyz" * 10))
        assert _u(1) in idx
        assert _u(2) not in idx

    def test_len_after_multiple_adds(self):
        idx = FuzzyIndex(threshold=0.3)
        for i in range(5):
            idx.add(_u(i), _hash_from_text(f"text-{i}-" * 8))
        assert len(idx) == 5
