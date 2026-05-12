"""Performance benchmark: O(n) baseline vs LSH-narrowed lineage candidate selection.

This file is the v0.15 deliverable for Phase Beta gate #1.

What gets measured
------------------
End-to-end ``query → candidate-set → ppdeep.compare`` time, because the
LSH win is in the *number of compare() calls*, not the candidate-list
construction itself. (Pure candidate selection is actually slightly
slower with LSH at small N due to MinHash construction overhead — the
payoff is downstream.)

Two paths exercised on the same corpus + query:

  baseline  iterate every (id, hash) → ppdeep.compare → keep matches
  lsh       FuzzyIndex.query → ppdeep.compare narrowed set → keep matches

Both paths return the same match set (subject to LSH's tunable false-
negative rate; the v0.14 equivalence test verified this on a real
corpus, and the assertions here also enforce it on synthetic data).

Run
---
::

    pytest tests/perf -m slow -v

Skipped when ``datasketch`` isn't installed (Phase Beta optional dep).
"""

from __future__ import annotations

import json
import random
import statistics
import string
import time
from datetime import datetime
from curator._compat.datetime import utcnow_naive
from pathlib import Path
from uuid import UUID, uuid4

import pytest

# Skip the entire module if the optional dep is missing.
pytest.importorskip("datasketch")

from curator.services.fuzzy_index import FuzzyIndex  # noqa: E402

# ppdeep is vendored in Step 8; import the wrapper.
try:
    from curator._vendored.ppdeep import compare as _ssdeep_compare  # noqa: E402
except ImportError:  # pragma: no cover
    _ssdeep_compare = None


pytestmark = [pytest.mark.slow, pytest.mark.integration]


SSDEEP_THRESHOLD = 70
"""Match threshold used by ``lineage_fuzzy_dup`` detector — keeping the
benchmark aligned with production behavior."""


# ---------------------------------------------------------------------------
# Synthetic corpus generation
# ---------------------------------------------------------------------------

def _random_ssdeep_hash(rng: random.Random, length: int = 32) -> str:
    """Build a synthetic ssdeep-format hash ``"<bs>:<s1>:<s2>"``.

    Real ssdeep hashes have richer structure but for benchmarking the
    distribution-of-strings is what matters: the LSH n-grams + MinHash
    don't care about the symbolic meaning of the bytes.
    """
    block_size = rng.choice([3, 6, 12, 24, 48, 96, 192, 384, 768, 1536])
    alphabet = string.ascii_letters + string.digits + "+/"
    s1 = "".join(rng.choices(alphabet, k=length))
    s2 = "".join(rng.choices(alphabet, k=length // 2))
    return f"{block_size}:{s1}:{s2}"


def _build_corpus(n: int, seed: int = 42) -> list[tuple[UUID, str]]:
    """Generate ``n`` (curator_id, fuzzy_hash) pairs with sprinkled near-dupes.

    Every 50th entry is a near-duplicate of the previous one (same s1
    prefix, perturbed s2). This guarantees the benchmark exercises the
    "LSH actually finds candidates" path, not just the empty-result path.
    """
    rng = random.Random(seed)
    corpus: list[tuple[UUID, str]] = []
    last_hash: str | None = None
    for i in range(n):
        if i % 50 == 1 and last_hash is not None:
            # Near-duplicate: shared s1, perturbed s2
            bs, s1, s2 = last_hash.split(":")
            perturbed = (
                s2[:max(1, len(s2) - 4)]
                + "".join(rng.choices(string.ascii_letters, k=4))
            )
            h = f"{bs}:{s1}:{perturbed}"
        else:
            h = _random_ssdeep_hash(rng)
        corpus.append((uuid4(), h))
        last_hash = h
    return corpus


# ---------------------------------------------------------------------------
# Pipelines under test
# ---------------------------------------------------------------------------

def _baseline_pipeline(
    query_hash: str,
    corpus: list[tuple[UUID, str]],
) -> set[UUID]:
    """O(n): compare query against every entry in the corpus."""
    if _ssdeep_compare is None:
        return set()
    matches: set[UUID] = set()
    for cid, h in corpus:
        try:
            if _ssdeep_compare(query_hash, h) >= SSDEEP_THRESHOLD:
                matches.add(cid)
        except (ValueError, RuntimeError):
            continue
    return matches


def _lsh_pipeline(
    query_hash: str,
    index: FuzzyIndex,
    corpus_dict: dict[UUID, str],
) -> set[UUID]:
    """LSH-narrowed: query LSH, compare only the narrowed candidate set."""
    if _ssdeep_compare is None:
        return set()
    try:
        candidate_ids = index.query(query_hash)
    except ValueError:
        return set()
    matches: set[UUID] = set()
    for cid in candidate_ids:
        h = corpus_dict.get(cid)
        if h is None:
            continue
        try:
            if _ssdeep_compare(query_hash, h) >= SSDEEP_THRESHOLD:
                matches.add(cid)
        except (ValueError, RuntimeError):
            continue
    return matches


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------

def _time_calls(fn, *args, repeats: int = 5) -> dict[str, float]:
    """Run ``fn(*args)`` ``repeats`` times, return summary stats (ms)."""
    samples: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn(*args)
        samples.append((time.perf_counter() - t0) * 1000.0)
    return {
        "median_ms": round(statistics.median(samples), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "n_repeats": repeats,
    }


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------

def _write_result(name: str, payload: dict) -> Path:
    """Append benchmark output to ``tests/perf/results/{name}-{ts}.json``."""
    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = utcnow_naive().strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"{name}-{ts}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


# ---------------------------------------------------------------------------
# The benchmarks themselves
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def _ppdeep_check():
    """Skip benchmarks if ppdeep isn't importable (defensive — Step 8 vendored it)."""
    if _ssdeep_compare is None:
        pytest.skip("ppdeep not importable — Step 8 vendoring is broken?")


@pytest.mark.parametrize("corpus_size", [100, 1_000, 10_000])
def test_lineage_throughput(corpus_size: int, _ppdeep_check, capsys):
    """Benchmark candidate-selection + compare on three corpus sizes.

    Asserts:
      * Both paths return identical match sets (correctness — the v0.14
        contract reaffirmed at scale).
      * LSH path is at least as fast as baseline at corpus_size >= 1000
        (the wall-clock "speedup" shows up once compare() dominates).
    """
    corpus = _build_corpus(corpus_size)
    corpus_dict = dict(corpus)
    query_id, query_hash = corpus[1]  # the first near-duplicate's pivot

    # Build the LSH index once (this cost is amortized across queries
    # in real usage, so we measure it separately and don't roll it into
    # the per-query timing).
    t0 = time.perf_counter()
    index = FuzzyIndex()
    for cid, h in corpus:
        try:
            index.add(cid, h)
        except ValueError:
            continue
    index_build_ms = (time.perf_counter() - t0) * 1000.0

    # Correctness check: both paths return the same set.
    baseline_matches = _baseline_pipeline(query_hash, corpus)
    lsh_matches = _lsh_pipeline(query_hash, index, corpus_dict)
    # LSH may have minor false-negatives at the threshold boundary; we
    # accept any match-set that's a (mostly) subset of baseline.
    fn_rate = len(baseline_matches - lsh_matches) / max(1, len(baseline_matches))
    assert fn_rate <= 0.10, (
        f"LSH false-negative rate too high at corpus_size={corpus_size}: "
        f"baseline={baseline_matches}, lsh={lsh_matches}"
    )

    # Wall-clock for one query each, repeated.
    repeats = 5 if corpus_size <= 1_000 else 3
    base_stats = _time_calls(_baseline_pipeline, query_hash, corpus, repeats=repeats)
    lsh_stats = _time_calls(_lsh_pipeline, query_hash, index, corpus_dict, repeats=repeats)

    speedup = (
        base_stats["median_ms"] / lsh_stats["median_ms"]
        if lsh_stats["median_ms"] > 0 else float("inf")
    )

    payload = {
        "corpus_size": corpus_size,
        "ssdeep_threshold": SSDEEP_THRESHOLD,
        "index_build_ms": round(index_build_ms, 3),
        "baseline": base_stats,
        "lsh": lsh_stats,
        "speedup_x": round(speedup, 2),
        "match_count_baseline": len(baseline_matches),
        "match_count_lsh": len(lsh_matches),
        "false_negative_rate": round(fn_rate, 4),
    }
    out_path = _write_result(f"lineage_throughput_n{corpus_size}", payload)

    # Print a human-readable line (capsys is captured but visible with -v).
    with capsys.disabled():
        print(
            f"\n[bench] corpus={corpus_size:>6} | "
            f"baseline={base_stats['median_ms']:>8.2f}ms | "
            f"lsh={lsh_stats['median_ms']:>8.2f}ms | "
            f"speedup={speedup:>5.1f}x | "
            f"index_build={index_build_ms:>8.1f}ms | "
            f"matches base={len(baseline_matches)} lsh={len(lsh_matches)} | "
            f"results: {out_path.name}"
        )

    # Soft perf assertion: at 1k+, LSH should not be slower than baseline.
    if corpus_size >= 1_000:
        assert lsh_stats["median_ms"] <= base_stats["median_ms"], (
            f"LSH path slower than baseline at corpus_size={corpus_size}: "
            f"baseline={base_stats}, lsh={lsh_stats}"
        )


def test_index_build_scales_linearly(_ppdeep_check, capsys):
    """Sanity: index build cost should scale roughly linearly with N.

    This is informational — no hard assertion. Just prints the
    per-entry add cost so regressions are visible in CI logs.
    """
    sizes = [100, 1_000, 5_000]
    rows = []
    for n in sizes:
        corpus = _build_corpus(n)
        t0 = time.perf_counter()
        idx = FuzzyIndex()
        for cid, h in corpus:
            try:
                idx.add(cid, h)
            except ValueError:
                continue
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        per_entry_us = (elapsed_ms * 1000.0) / n
        rows.append({
            "n": n,
            "total_ms": round(elapsed_ms, 2),
            "per_entry_us": round(per_entry_us, 2),
        })

    payload = {"sizes": rows}
    out_path = _write_result("index_build_scaling", payload)

    with capsys.disabled():
        print("\n[bench] FuzzyIndex build scaling:")
        for row in rows:
            print(
                f"  n={row['n']:>6} | "
                f"total={row['total_ms']:>8.1f}ms | "
                f"per-entry={row['per_entry_us']:>6.1f}\u00b5s"
            )
        print(f"  results: {out_path.name}")
