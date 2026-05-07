"""FuzzyIndex — LSH-based candidate selection for NEAR_DUPLICATE detection.

DESIGN.md §8.3.2 / Phase Beta gate #1 / scoping doc:
``docs/PHASE_BETA_LSH.md``.

Phase Alpha's NEAR_DUPLICATE detector compares every pair of files that
have a fuzzy hash. That works at small ``n``; at scale it's O(n) per
file. This module wraps a :class:`datasketch.MinHashLSH` to bring
candidate-set selection down to O(1) average.

The contract:

  * :meth:`add` — index a file by its ssdeep-style fuzzy hash.
  * :meth:`remove` — drop a file from the index.
  * :meth:`query` — given a fuzzy hash, return ``curator_id``s of files
    whose hashes are likely-similar (LSH bucket match). The actual
    ssdeep ``compare()`` still gates whether an edge gets emitted.
  * :meth:`clear` — wipe.
  * ``len(index)`` — count.

The MinHash is built from 3-grams of the concatenation of the ssdeep
hash's two body components (``s1 + s2``). The block-size prefix is
intentionally skipped — it's bimodal and would dominate the
similarity signal.

This module **lazy-imports** ``datasketch`` so that ``import curator``
still works when datasketch isn't installed (it lives in the
``[beta]`` extras, not core dependencies). The lazy import error is
caught and re-raised with a clear install instruction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:  # pragma: no cover
    # Type-checking only; runtime import is deferred to FuzzyIndex.__init__.
    from datasketch import MinHashLSH


# ---------------------------------------------------------------------------
# Constants — defaults sized for our NEAR_DUPLICATE threshold of 70/100.
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD: float = 0.5
"""Default Jaccard similarity threshold on the MinHash signatures.

Empirically tracks ``ppdeep.compare(...) >= 70`` (slight superset).
See ``docs/PHASE_BETA_LSH.md`` for the calibration table.
"""

DEFAULT_NUM_PERM: int = 128
"""Number of MinHash permutations. 128 is the datasketch default and
gives well-known optimal (b=16, r=8) banding for threshold 0.5 — so
the index works correctly even when scipy isn't installed (datasketch's
``_optimal_param`` falls back to the analytic optimum)."""

NGRAM_SIZE: int = 3
"""K-gram size for MinHash construction. 3 is small enough to capture
local structure, large enough to avoid every pair sharing trivial grams."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FuzzyIndexError(Exception):
    """Base for FuzzyIndex-specific errors."""


class FuzzyIndexUnavailableError(FuzzyIndexError):
    """``datasketch`` isn't installed."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_fuzzy_hash(fuzzy_hash: str) -> tuple[int, str, str]:
    """Split an ssdeep hash ``"<bs>:<s1>:<s2>"`` into its three parts.

    Raises :class:`ValueError` on malformed input.
    """
    if not isinstance(fuzzy_hash, str):
        raise ValueError(f"fuzzy_hash must be str, got {type(fuzzy_hash).__name__}")
    parts = fuzzy_hash.split(":")
    if len(parts) != 3:
        raise ValueError(
            f"fuzzy_hash must have 3 colon-separated parts, got {len(parts)}: "
            f"{fuzzy_hash!r}"
        )
    try:
        block_size = int(parts[0])
    except ValueError as e:
        raise ValueError(
            f"first part of fuzzy_hash must be an integer block size, "
            f"got {parts[0]!r}"
        ) from e
    return block_size, parts[1], parts[2]


def _ngrams(text: str, n: int = NGRAM_SIZE) -> set[bytes]:
    """Return the set of byte n-grams of ``text``.

    Bytes (not str) so the MinHash hashes them directly without re-encoding.
    Empty/short input → empty set (the LSH will gracefully treat it as
    "matches nothing").
    """
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) < n:
        return set()
    return {encoded[i : i + n] for i in range(len(encoded) - n + 1)}


def _build_minhash(fuzzy_hash: str, num_perm: int):
    """Construct a :class:`datasketch.MinHash` from the fuzzy hash.

    Raises :class:`FuzzyIndexUnavailableError` if datasketch can't be imported.
    """
    try:
        from datasketch import MinHash
    except ImportError as e:  # pragma: no cover — exercised when dep missing
        raise FuzzyIndexUnavailableError(
            "datasketch is not installed. Install it with: "
            "pip install 'curator[beta]'  (or just: pip install datasketch)"
        ) from e

    _, s1, s2 = _parse_fuzzy_hash(fuzzy_hash)
    grams = _ngrams(s1 + s2)

    m = MinHash(num_perm=num_perm)
    for g in grams:
        m.update(g)
    return m


# ---------------------------------------------------------------------------
# FuzzyIndex
# ---------------------------------------------------------------------------

class FuzzyIndex:
    """In-memory MinHash-LSH index over fuzzy hashes.

    See module docstring + ``docs/PHASE_BETA_LSH.md`` for the rationale.

    Phase Beta v0.13: pure in-memory, populated by the caller. v0.14
    will wire this into ``LineageService.find_candidates``; v0.15 will
    add a perf benchmark.
    """

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        num_perm: int = DEFAULT_NUM_PERM,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(
                f"threshold must be in (0.0, 1.0], got {threshold}"
            )
        if num_perm < 2:
            raise ValueError(f"num_perm must be >= 2, got {num_perm}")

        try:
            from datasketch import MinHashLSH
        except ImportError as e:
            raise FuzzyIndexUnavailableError(
                "datasketch is not installed. Install it with: "
                "pip install 'curator[beta]'  (or just: pip install datasketch)"
            ) from e

        self._threshold = threshold
        self._num_perm = num_perm
        self._lsh: MinHashLSH = MinHashLSH(threshold=threshold, num_perm=num_perm)
        # We track curator_ids ourselves so we can support remove() —
        # MinHashLSH's own ``remove`` requires the same key we inserted with.
        self._known_ids: set[UUID] = set()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add(self, curator_id: UUID, fuzzy_hash: str) -> None:
        """Index ``curator_id`` under its fuzzy hash.

        Idempotent: if ``curator_id`` is already present, the existing
        signature is replaced (matches the user expectation that "add"
        of the same key updates).
        """
        if not isinstance(curator_id, UUID):
            raise TypeError(
                f"curator_id must be UUID, got {type(curator_id).__name__}"
            )
        m = _build_minhash(fuzzy_hash, self._num_perm)
        key = str(curator_id)
        if curator_id in self._known_ids:
            # MinHashLSH raises if you re-insert the same key, so we have
            # to remove-then-insert to make add() idempotent.
            self._lsh.remove(key)
        self._lsh.insert(key, m)
        self._known_ids.add(curator_id)

    def remove(self, curator_id: UUID) -> None:
        """Drop ``curator_id`` from the index. Silent no-op if absent."""
        if curator_id not in self._known_ids:
            return
        self._lsh.remove(str(curator_id))
        self._known_ids.discard(curator_id)

    def clear(self) -> None:
        """Wipe everything. Lighter than ``__init__`` since it preserves
        the threshold/num_perm settings."""
        try:
            from datasketch import MinHashLSH
        except ImportError as e:  # pragma: no cover
            raise FuzzyIndexUnavailableError(str(e)) from e
        self._lsh = MinHashLSH(
            threshold=self._threshold, num_perm=self._num_perm
        )
        self._known_ids.clear()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def query(self, fuzzy_hash: str) -> list[UUID]:
        """Return ``curator_id``s of files likely-similar to ``fuzzy_hash``.

        Empty index → empty list. Malformed input → :class:`ValueError`
        (validated even on an empty index — bad input is always an
        error, not silently swallowed).
        Hits include the source id if it was added to this index — caller
        must filter that out if undesired (the existing
        ``lineage_hash_dup`` short-circuits self-pairs upstream).
        """
        # Validate input eagerly. Malformed hashes raise here regardless
        # of index state, matching the behavior of add().
        _parse_fuzzy_hash(fuzzy_hash)
        if not self._known_ids:
            return []
        m = _build_minhash(fuzzy_hash, self._num_perm)
        keys = self._lsh.query(m)
        return [UUID(k) for k in keys]

    # ------------------------------------------------------------------
    # Container protocol
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._known_ids)

    def __contains__(self, curator_id: object) -> bool:
        return curator_id in self._known_ids

    def __repr__(self) -> str:  # pragma: no cover — diagnostic
        return (
            f"<FuzzyIndex threshold={self._threshold} "
            f"num_perm={self._num_perm} size={len(self)}>"
        )


__all__ = [
    "FuzzyIndex",
    "FuzzyIndexError",
    "FuzzyIndexUnavailableError",
    "DEFAULT_THRESHOLD",
    "DEFAULT_NUM_PERM",
    "NGRAM_SIZE",
]
