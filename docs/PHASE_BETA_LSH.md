# Phase Beta Plan: LSH-based fuzzy candidate selection

**Status:** scoping → starting (2026-05-06)
**Owner:** Jake Leese
**Tracking:** Phase Beta gate #1 in `BUILD_TRACKER.md`
**Cross-references:** `DESIGN.md` §8.3.2 (NEAR_DUPLICATE detector), §17.4 (perf goals), `Github/CURATOR_RESEARCH_NOTES.md` Round 2 (datasketch decision)

---

## Problem statement

Phase Alpha's NEAR_DUPLICATE detection works correctly but doesn't scale. The current path:

```
LineageService.find_candidates(file)
  → for every other file with a fuzzy_hash:                 ← O(n)
        ppdeep.compare(file.fuzzy_hash, other.fuzzy_hash)   ← O(L²) per pair
        if similarity >= 70: emit NEAR_DUPLICATE edge
```

For Curator's text files, `n` grows with the index. At ~10k files we're still fine (~10k × ~0.5ms = 5s per scan-driven pass, hidden behind the per-file work). At ~100k+, fuzzy lineage becomes the dominant cost.

The fix is well-understood: replace the all-pairs scan with a Locality-Sensitive Hashing (LSH) index. Files whose fuzzy hashes are likely to be similar share an LSH bucket; query time drops from O(n) to roughly O(1) average.

---

## Contract design

Single new module: `src/curator/services/fuzzy_index.py`

```python
class FuzzyIndex:
    """In-memory LSH index keyed on ssdeep fuzzy hashes.

    Purpose: cheap candidate selection for NEAR_DUPLICATE detection.
    Confirms (true positives) and rejects (false positives) are the
    job of the existing ppdeep.compare path — this index just narrows
    the set of pairs that get compared.
    """

    def __init__(self, threshold: float = 0.5, num_perm: int = 128) -> None: ...

    def add(self, curator_id: UUID, fuzzy_hash: str) -> None: ...
    def remove(self, curator_id: UUID) -> None: ...
    def query(self, fuzzy_hash: str) -> list[UUID]: ...
    def clear(self) -> None: ...
    def __len__(self) -> int: ...
```

### Threshold semantics

The constructor's `threshold` is the **Jaccard similarity threshold** the underlying `MinHashLSH` uses on the MinHash signatures we compute from the ssdeep hash. It's NOT the ssdeep `compare()` score directly.

The two scales aren't linearly related, but empirically:

| LSH `threshold` | Approx ssdeep `compare()` floor |
|---|---|
| 0.3 | ~50 (loose; many false positives) |
| **0.5** | **~70 (matches our NEAR_DUPLICATE threshold)** |
| 0.7 | ~85 (tight; misses true near-dupes) |

We default to `0.5` so the candidate set is a slight superset of what `ppdeep.compare(...) >= 70` would catch. The downstream `compare` call still gates the actual edge emission.

### MinHash construction from ssdeep hashes

ssdeep hashes look like `192:rmgy50DvhmsSyaINoxTQ...:Xy8JAxTQtDrQ...` — three colon-separated parts: `<block_size>:<s1>:<s2>`. We build a MinHash over the **3-grams of the concatenation `s1 + s2`** (skipping the block size, which is highly bimodal and would dominate). Three is small enough to capture local structure but large enough to avoid every pair sharing trivial grams.

The 3-gram approach mirrors what spamsum-style dedup tools do and gives MinHash signatures that empirically track ssdeep `compare()` reasonably well at threshold 0.5.

### Memory model

* **v0.13 (this turn):** Pure in-memory. The index is rebuilt from the file table on `FuzzyIndex` construction (caller's responsibility — likely on `CuratorRuntime` startup).
* **v0.14:** Wire into `LineageService.find_candidates`; add an integration test.
* **v0.15+:** Optional persistent backend (datasketch supports SQLite + Redis). Curator stays single-DB by default.

---

## Cut-off plan

This section is a **scope contract** — it says what's in vs. out for each version increment.

### v0.13 — `FuzzyIndex` standalone (THIS TURN)

In:
* `src/curator/services/fuzzy_index.py` with the contract above.
* Lazy import of `datasketch` so missing-dep doesn't break `import curator`.
* `datasketch` added to `[beta]` extras in `pyproject.toml`.
* Unit tests under `tests/unit/test_fuzzy_index.py`:
  * Add + query returns matching `curator_id`s.
  * Identical hash → query returns the added `curator_id`.
  * Unrelated hashes → query returns nothing.
  * Remove → subsequent query doesn't return the removed id.
  * Clear → length 0.
  * Empty query on empty index → empty list.
  * Threshold tuning: at threshold 0.9, slight perturbations don't match; at 0.3 they do.
* Tests use `pytest.importorskip("datasketch")` so they're skipped when the dep isn't installed (matches our "datasketch is in `[beta]`, not core" stance).

Out (deferred to v0.14):
* Wiring `FuzzyIndex` into `LineageService.find_candidates`.
* Index lifecycle inside `ScanService` (build at scan-start, query during pair detection, persist at scan-end).
* Performance benchmark before/after.

### v0.14 — Wire into `LineageService`

In:
* `LineageService.__init__` takes an optional `FuzzyIndex`.
* `find_candidates`: when `fuzzy_index` is set AND the query file has a `fuzzy_hash`, route the fuzzy candidate path through `FuzzyIndex.query` instead of `find_with_fuzzy_hash`.
* `CuratorRuntime` wiring: build a `FuzzyIndex`, populate from existing files, hand to `LineageService`.
* Integration test that proves O(n) and LSH paths produce the same edges on a small corpus.

Out:
* Persistent backend (Phase Gamma).

### v0.15 — Benchmark + cleanup

In:
* `tests/perf/test_lineage_throughput.py` measures `find_candidates` latency at three corpus sizes (100, 1k, 10k synthetic files with random fuzzy hashes).
* Result: documented improvement factor in `BUILD_TRACKER.md`.

---

## Test plan (v0.13 detail)

Synthetic ssdeep-style hashes are easy to generate: pick a block size (3, 6, 12), generate a random 64-char base64 string for `s1`, half-length for `s2`. Helper: `_synth_hash(seed: int) -> str`.

Tests:

| # | Name | Asserts |
|---|---|---|
| 1 | `test_empty_index_query_returns_empty` | `len()` is 0; `query` returns `[]` |
| 2 | `test_add_then_query_finds_self` | adding `(id, h)` then querying `h` returns `id` |
| 3 | `test_add_then_query_finds_close_neighbors` | two hashes built from overlapping 3-grams both surface |
| 4 | `test_unrelated_hashes_dont_match` | wholly random hashes don't trigger spurious matches |
| 5 | `test_remove_eliminates_from_results` | post-`remove`, `query` doesn't return the id |
| 6 | `test_clear_resets_to_empty` | `len()` is 0 after `clear()` |
| 7 | `test_threshold_tightens_with_higher_value` | at threshold 0.9, near-misses drop out |
| 8 | `test_invalid_fuzzy_hash_raises` | malformed input raises `ValueError`, not silently |
| 9 | `test_double_add_overwrites_or_no_ops` | adding the same id twice is idempotent |

Run command: `python -m pytest tests/unit/test_fuzzy_index.py -q`

When `datasketch` isn't installed, all tests skip cleanly with a single descriptive message (per `pytest.importorskip` conventions).

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| 3-gram MinHash from ssdeep hash doesn't track `compare()` well | Medium | Test 3 + test 4 above are calibration tests. If they fail, revisit the grammable construction (try 5-gram, or grams of `s1` only) |
| `datasketch` install fails on Jake's Python (3.13) | Low | datasketch supports 3.8+; main concern is scipy compiling on Windows. If it fails, pip-install pre-built wheel |
| Memory blow-up at 1M+ files | Low for Phase Alpha use-case | Doc the limit; v0.15 benchmark catches it |
| MinHashLSH numbers (b, r) are wrong without scipy `_optimal_param` | Low | We pass `num_perm=128, threshold=0.5` which has well-known optimal (16, 8); datasketch falls back if scipy is missing |

---

## Out of scope for this Phase Beta gate

Adjacent work that won't land here:

* File watcher (Tier 6) — separate gate.
* GUI — separate gate.
* Cloud source plugins — separate gate.
* Cross-platform send2trash / recycle-bin reader — separate gate.
* Switching from ssdeep to `datasketch.MinHash` directly (skipping ppdeep) — out of scope; ppdeep stays the source of truth for hash bytes, MinHash is just an indexing trick.

---

## Revision log

* **2026-05-06** — Doc created. v0.13 starts immediately.
