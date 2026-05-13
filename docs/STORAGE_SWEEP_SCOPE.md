# Storage Repositories Sweep — Scope Plan

**Status:** Active arc plan — opened v1.7.126 (Round 2 Tier 3)
**Owner:** Curator engineering doctrine
**Created:** 2026-05-13 (v1.7.126)
**Modules:** 11 modules under `src/curator/storage/` (excluding the two largest, deferred to Tier 4)
**Target:** All 11 modules at 100% line + branch (per apex-accuracy doctrine)

## Why this arc

Round 2 Tier 1 (mop-up) + Tier 2 (Plugins + MCP + Config) closed 19 more modules, bringing the running total to 37 modules at 100% line + branch. The storage layer is the next coherent subsystem: SQLite-backed repositories that own the index. Most are small (3-28 uncovered lines each), and they share the same test pattern: in-memory SQLite + repository instance + CRUD assertions.

Two storage modules are deferred to Tier 4 (opt-in stretch):
- `storage/repositories/lineage_repo.py` (38 lines uncovered, 38.67% coverage)
- `storage/repositories/file_repo.py` (96 lines uncovered, 43.60% coverage — biggest storage repo)

## Baselines (re-measured 2026-05-13 per Lesson #93)

Handoff predictions verified exactly against `pytest --cov=curator.storage` against current HEAD (v1.7.125, 1920 unit tests). No drift.

| Module | Stmts | Misses | Partials | Coverage |
|---|---|---|---|---|
| `storage/connection.py` | 51 | 2 | 1 br | 91.53% |
| `storage/repositories/audit_repo.py` | 62 | 3 | 0 | 96.05% |
| `storage/migrations.py` | 33 | 4 | 0 | 89.19% |
| `storage/repositories/source_repo.py` | 44 | 6 | 0 | 86.96% |
| `storage/repositories/trash_repo.py` | 39 | 6 | 1 br | 82.22% |
| `storage/repositories/migration_job_repo.py` | 106 | 6 | 5 br | 91.54% |
| `storage/exceptions.py` | 9 | 9 | 0 | 0.00% |
| `storage/repositories/job_repo.py` | 41 | 11 | 1 br | 68.89% |
| `storage/repositories/hash_cache_repo.py` | 54 | 14 | 0 | 73.33% |
| `storage/repositories/_helpers.py` | 46 | 16 | 6 br | 57.58% |
| `storage/repositories/bundle_repo.py` | 64 | 28 | 0 | 52.94% |

**Total uncovered:** 105 lines + 14 partial branches across 11 modules. Storage overall: 71.08% → expected close to ~95%+ after Tier 3.

## The 11 sweep targets (ordered by ascending uncovered lines)

Each sub-ship is a **trimmed-ceremony ship** (memory edit #5). Re-measure baseline at sub-ship start (Lesson #93).

| Ship | Module | Uncovered | Notes |
|---|---|---|---|
| v1.7.126 | scope plan (this doc) | — | doc-only |
| v1.7.127 | `storage/connection.py` | 2 lines + 1 br | Smallest. Likely defensive boundaries. |
| v1.7.128 | `storage/repositories/audit_repo.py` | 3 lines | Audit event persistence. |
| v1.7.129 | `storage/migrations.py` | 4 lines | Schema migration runner. |
| v1.7.130 | `storage/repositories/source_repo.py` | 6 lines | SourceConfig CRUD. |
| v1.7.131 | `storage/repositories/trash_repo.py` | 6 lines + 1 br | Trashed-file index. |
| v1.7.132 | `storage/repositories/migration_job_repo.py` | 6 lines + 5 br | Already 91.54%; mostly branch closures. |
| v1.7.133 | `storage/exceptions.py` | 9 lines (0% currently) | Pure exception classes — likely just constructors + reprs. |
| v1.7.134 | `storage/repositories/job_repo.py` | 11 lines + 1 br | Generic job CRUD. |
| v1.7.135 | `storage/repositories/hash_cache_repo.py` | 14 lines | Hash cache for dedup. |
| v1.7.136 | `storage/repositories/_helpers.py` | 16 lines + 6 br | Shared helper functions used across repos. |
| v1.7.137 | `storage/repositories/bundle_repo.py` | 28 lines | Biggest of this tier. |

**Total predicted effort:** 4-6 hours of focused execution.

## Stub/fixture design notes

### In-memory SQLite

The storage layer requires a real SQLite connection (it uses real schemas + queries, not abstract collaborators). The convention should be:

1. **Per-test fixture**: pytest fixture creates `sqlite3.connect(":memory:")`, runs `curator.storage.migrations.run()` against it, yields the connection, tears down.
2. **Reuse existing fixtures** if any tests/unit/ files already establish this pattern — search for `:memory:` and `run_migrations` first.
3. Most repositories take a `connection` arg in `__init__`; pass the in-memory connection directly.

### Exception module testing

`storage/exceptions.py` at 0% — likely all pure class definitions. Tests should construct each exception with representative args, verify the `__str__` output, and confirm chained `__cause__` if any. Expect very short test files (one test per exception class).

### Helpers module testing

`storage/repositories/_helpers.py` at 57.58% — shared utilities (row → entity, entity → row, query building). 16 lines + 6 partial branches suggests a few input-shape branches. Read the source thoroughly first (Lesson #90).

### Bundle repo

`bundle_repo.py` is the biggest of this tier at 28 uncovered lines. Likely needs the most test scaffolding. Estimate 12-18 tests.

## Per-sub-ship process

1. **Re-measure baseline** on the target module (Lesson #93)
2. If actual ≠ predicted: report the delta briefly and adjust scaffolding plan
3. Read source thoroughly (Lesson #90)
4. Write focused test file `tests/unit/test_<module>_coverage.py` covering only uncovered lines
5. Iterate to 100% line + branch
6. Standard 14-step ship workflow

## Watchpoints

- **v1.7.137 (bundle_repo) is the biggest.** If scope grows beyond 1.5x typical, **split into 137a (infrastructure) + 137b (test surface)** per Lesson #88.
- **v1.7.136 (_helpers) is shared infrastructure.** If `_helpers` tests reveal it's used in non-trivial ways by other repos, expect carry-forward dividends (Lesson #87 pattern dividends).
- **Lesson #93 re-measure** at each ship — predictions verified at scope plan, but per-module drift is still possible.

## Lesson capture

This arc may or may not yield fresh lessons. Through Tier 2 (9 sub-ships), Round 2 captured **zero new lessons** — all settled doctrine. Honest "no new lesson" entries continue to be the right outcome for doctrine-in-action work.

Likely candidates for new lessons (only if they surface a genuine principle):
- In-memory SQLite fixture lifecycle pattern (if it becomes a per-tier reusable utility)
- Exception class testing pattern (if it generalizes beyond storage/exceptions.py)

## Reporting cadence

Per handoff: report briefly after Tier 3 completion. Mid-tier checkpoints only if a sub-ship encounters surprises (Lesson #88: split if scope >1.5x).

## Resume / restart contract

If a session ends between sub-ships:
- HEAD commit identifies the last completed sub-ship.
- CHANGELOG entry documents which module closed and its final coverage.
- This document's status tracker (below) is the source of truth for next sub-ship.

Restart prompt: *"Resume Storage Repositories Sweep arc. Open docs/STORAGE_SWEEP_SCOPE.md, next sub-ship per tracker."*

## Status tracker

| Sub-ship | Status | Module | Final coverage | Date |
|---|---|---|---|---|
| v1.7.126 | ✅ This scope plan | — | n/a (doc-only) | 2026-05-13 |
| v1.7.127 | ✅ Closed | `storage/connection.py` | **100.00%** (was 91.53%) | 2026-05-13 |
| v1.7.128 | ✅ Closed | `storage/repositories/audit_repo.py` | **100.00%** (was 96.05%) | 2026-05-13 |
| v1.7.129 | ✅ Closed | `storage/migrations.py` | **100.00%** (was 89.19%) | 2026-05-13 |
| v1.7.130 | ✅ Closed | `storage/repositories/source_repo.py` | **100.00%** (was 86.96%) | 2026-05-13 |
| v1.7.131 | ✅ Closed | `storage/repositories/trash_repo.py` | **100.00%** (was 82.22%) | 2026-05-13 |
| v1.7.132 | ⏳ Pending | `storage/repositories/migration_job_repo.py` | TBD | TBD |
| v1.7.133 | ⏳ Pending | `storage/exceptions.py` | TBD | TBD |
| v1.7.134 | ⏳ Pending | `storage/repositories/job_repo.py` | TBD | TBD |
| v1.7.135 | ⏳ Pending | `storage/repositories/hash_cache_repo.py` | TBD | TBD |
| v1.7.136 | ⏳ Pending | `storage/repositories/_helpers.py` | TBD | TBD |
| v1.7.137 | ⏳ Pending | `storage/repositories/bundle_repo.py` | TBD | TBD |

## Arc-level success criteria

When this arc closes:
- **11 more modules at 100% line + branch** (combined w/ Round 1 + Tier 1 + Tier 2 = 48 modules total)
- Storage layer overall coverage ~95%+ (currently 71.08%)
- Curator overall coverage ~65-70% per handoff projection

## Tier 4 deferral

The remaining storage modules (`lineage_repo.py`, `file_repo.py`) are explicitly deferred to Tier 4 (opt-in stretch). Tier 4 requires Jake's explicit go-ahead per Round 2 handoff. Do not preempt that decision.
