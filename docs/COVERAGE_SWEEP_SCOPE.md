# Coverage Sweep Arc — Scope Plan

**Status:** ✅ **ARC CLOSED at v1.7.106 — all 12 sweep targets at 100% line + branch**
**Owner:** Curator engineering doctrine
**Created:** 2026-05-13 (v1.7.94)
**Closed:** 2026-05-13 (v1.7.106)
**Modules:** 12 services modules below 100% → all now at 100%
**Target:** All 12 modules at 100% line + branch ✅ ACHIEVED

## Why this arc

The Migration Phase Gamma arc (v1.7.88-93b) proved the apex-accuracy doctrine on the **hardest** module in the codebase (`services/migration.py` — 1031 stmts, 3100+ lines, 7-sub-ship arc). That established the patterns; this arc applies them at scale to the **easiest** remaining modules — the ones close enough to 100% that each one is a quick ship.

Why now: the doctrine is mature, the stub vocabulary is built (7 reusable stubs across the test suite), and the 12 target modules collectively have **~110 missing lines** — less than a tenth of what migration.py started with. The cost-per-module is small; the cumulative effect doubles the count of 100%-covered services modules (7 → 19+).

## The 12 sweep targets

Ordered by ascending effort per the handoff doc (`CLAUDE_CODE_HANDOFF_WOW.md`). One module per sub-ship.

| Ship | Module | Estimated effort | Uncovered (handoff baseline) | Notes |
|---|---|---|---|---|
| v1.7.95 | `services/forecast.py` | ~5 min | 1 line | Trivial |
| v1.7.96 | `services/fuzzy_index.py` | ~5 min | 2 lines | Trivial |
| v1.7.97 | `services/watch.py` | ~10 min | 3 lines | Trivial |
| v1.7.98 | `services/audit.py` | ~15 min | 3 lines | Trivial |
| v1.7.99 | `services/pii_scanner.py` | ~15 min | 5 lines | Trivial |
| **v1.7.100** | `services/music.py` | ~20 min | 6 lines | **🎉 100-ship milestone** |
| v1.7.101 | `services/metadata_stripper.py` | ~30 min | 7 lines | Easy (note: 173-line module) |
| v1.7.102 | `services/musicbrainz.py` | ~30 min | 12 lines | Easy |
| v1.7.103 | `services/classification.py` | ~30 min | 12 lines | Small module; lots of branches |
| v1.7.104 | `services/migration_retry.py` | ~45 min | 17 lines | Easy |
| v1.7.105 | `services/code_project.py` | ~45 min | 17 lines | Easy-moderate |
| v1.7.106 | `services/document.py` | ~60 min | 22 lines | Moderate |

**Total handoff-estimated effort:** 5-6 hours of clean execution. **Actual baseline numbers will be confirmed at sub-ship start** (re-measured fresh, not trusted from the handoff doc).

## Explicitly out of scope (deferred to future arcs)

Per the handoff doc, the following modules are NOT part of this sweep — they need their own focused arcs:

- `services/hash_pipeline.py` (~64%, ~52 lines) — moderate; own ship
- `services/organize.py` (~80%, ~56 lines) — moderate; own ship
- `services/cleanup.py` (~88%, ~50 lines) — moderate; own ship
- `services/trash.py` (~25%, ~90 lines) — substantial; defer
- All `plugins/core/*` (~62-96%) — separate plugin arc
- All `gui/*` (0%) — needs GUI test strategy first
- `cli/main.py` (~11%) — standalone arc with `click.testing.CliRunner`
- `mcp/*` (~71-93%) — low priority, minor cleanup

## Per-sub-ship process

Each sub-ship is a **trimmed-ceremony ship** (memory edit #5). Process:

1. **Re-measure baseline** on the target module via `pytest --cov=curator.services.<module> --cov-report=term-missing --cov-branch` (numbers from this doc are handoff-era; confirm before committing to scope).
2. **Read the uncovered lines** in source.
3. **Apply doctrine items 1-13** — especially #84 (stub reuse), #87 (pattern dividends), #90 (data-flow tracing), #91 (defensive boundaries), #93 (coverage diff for rewrites).
4. **Write a focused test file** at `tests/unit/test_<module>_coverage.py` covering ONLY the uncovered lines (don't duplicate existing tests).
5. **Iterate to pass.** Expected 1 iteration pass for trivial modules; 1-2 for easy modules.
6. **Confirm 100.00% line + branch** on the target module.
7. **Standard ship workflow** (CLAUDE.md steps 6-14): scope plan tracker update, CHANGELOG entry (trimmed, with lesson section if anything new emerges), release notes, commit msg, stage, commit, tag, push, cleanup, CLAUDE.md update.

## Lesson capture

The Migration Phase Gamma arc captured 8 lessons (#88-95) across 7 sub-ships — roughly one per ship. This arc may or may not yield fresh lessons; the patterns are now mature. **Honest "no new lesson" entries are fine** when work is doctrine-in-action (precedent: v1.7.93a captured no fresh lesson and honestly logged that).

If a fresh lesson DOES emerge, capture it richly in the CHANGELOG (per memory edit #5 — lessons-learned content stays rich even in trimmed-ceremony ships) and add it to CLAUDE.md doctrine.

## Reporting cadence

Per handoff: **report back to Jake after every 3 sub-ships** with a status line. If anything blocks (e.g. a module turns out to need substantial mocking that wasn't predicted), **STOP and ask Jake** before proceeding.

At **v1.7.100 (music.py)**: pause for celebration. **100 ships is a milestone.** Add a brief reflection to `docs/releases/v1.7.100.md` on the journey from v1.0 to v1.7.100.

## Resume / restart contract

If a session ends between sub-ships:
- The HEAD commit identifies the last completed sub-ship.
- The CHANGELOG entry documents which module closed and its final coverage.
- This document's status tracker (below) is the source of truth for which sub-ship comes next.
- Pick up at the next pending module. Re-measure baseline before locking in any per-module test plan (the prior session may have shifted coverage on adjacent modules).

If picking this up in a new session, the restart prompt is: *"Resume Coverage Sweep arc. Open docs/COVERAGE_SWEEP_SCOPE.md, next sub-ship per tracker."*

## Status tracker

| Sub-ship | Status | Module | Final coverage | Date |
|---|---|---|---|---|
| v1.7.94 | ✅ This scope plan | — | n/a (doc-only) | 2026-05-13 |
| v1.7.95 | ✅ Closed | `services/forecast.py` | **100.00%** (was 98.45%) | 2026-05-13 |
| v1.7.96 | ✅ Closed | `services/fuzzy_index.py` | **100.00%** (was 98.02%) | 2026-05-13 |
| v1.7.97 | ✅ Closed | `services/watch.py` | **100.00%** (was 97.77%) | 2026-05-13 |
| v1.7.98 | ✅ Closed | `services/audit.py` | **100.00%** (was 89.66%) | 2026-05-13 |
| v1.7.99 | ✅ Closed | `services/pii_scanner.py` | **100.00%** (was 97.94%) | 2026-05-13 |
| v1.7.100 | ✅ Closed — 🎉 MILESTONE | `services/music.py` | **100.00%** (was 94.55%) | 2026-05-13 |
| v1.7.101 | ✅ Closed | `services/metadata_stripper.py` | **100.00%** (was 94.17%) | 2026-05-13 |
| v1.7.102 | ✅ Closed | `services/musicbrainz.py` | **100.00%** (was 88.34%) | 2026-05-13 |
| v1.7.103 | ✅ Closed | `services/classification.py` | **100.00%** (was 91.89%) | 2026-05-13 |
| v1.7.104 | ✅ Closed | `services/migration_retry.py` | **100.00%** (was 77.78%) | 2026-05-13 |
| v1.7.105 | ✅ Closed | `services/code_project.py` | **100.00%** (was 89.45%) | 2026-05-13 |
| v1.7.106 | ✅ **ARC CLOSURE** | `services/document.py` | **100.00%** (was 89.66%) | 2026-05-13 |

## Arc-level success criteria

When this arc closes:
- **15+ services modules at 100% line + branch** (up from 7 after Migration Phase Gamma).
- **104+ ships** in the Curator repo (up from 94 after v1.7.93b; 94 + this scope plan + 12 module sweeps = 107 if no splits needed).
- Demonstrates that the apex-accuracy doctrine scales beyond focused arcs to **systematic coverage closure**.
- The Coverage Sweep pattern (per-module trimmed-ceremony ships ordered by effort) becomes a reusable arc template for future cleanup work.

## ✅ Arc closure — final stats (2026-05-13)

All criteria met or exceeded:

- **19 services modules at 100%** (7 Phase Gamma + 12 Coverage Sweep targets), up from 6 at session start
- **108 ships total** (107 = the predicted-no-split count, +1 for the v1.7.93 split into 93a/93b which happened earlier in the session)
- **Apex-accuracy doctrine scaled** across both intense (Migration Phase Gamma) and breadth (Coverage Sweep) arcs in a single session
- Coverage Sweep pattern is now a documented template (this doc) — reusable for future `plugins/` and `mcp/` sweeps

### Sweep ship trajectory

| Ship | Module | Coverage delta | Tests |
|---|---|---|---|
| v1.7.95 | forecast.py | 98.45 → 100.00 | 1 |
| v1.7.96 | fuzzy_index.py | 98.02 → 100.00 | 1 |
| v1.7.97 | watch.py | 97.77 → 100.00 | 2 |
| v1.7.98 | audit.py | 89.66 → 100.00 | 1 |
| v1.7.99 | pii_scanner.py | 97.94 → 100.00 | 2 |
| **v1.7.100** | music.py | 94.55 → 100.00 | 6 |
| v1.7.101 | metadata_stripper.py | 94.17 → 100.00 | 6 |
| v1.7.102 | musicbrainz.py | 88.34 → 100.00 | 10 |
| v1.7.103 | classification.py | 91.89 → 100.00 | 4 |
| v1.7.104 | migration_retry.py | 77.78 → 100.00 | 9 |
| v1.7.105 | code_project.py | 89.45 → 100.00 | 11 |
| v1.7.106 | document.py | 89.66 → 100.00 | 15 |
| **TOTAL** | **12 modules** | **avg +6.6% per module** | **68 tests** |

### Source-level refactors performed during the arc

Three modules had provably-unreachable defensive code removed (per doctrine item 1):

- v1.7.95 `forecast.py`: `if n else 0.0` ternary in `_linear_fit`'s denom==0 path
- v1.7.99 `pii_scanner.py`: try/except around `bytes.decode("utf-8", errors="replace")` (documented total per Python spec)
- v1.7.104 `migration_retry.py`: end-of-loop defensive re-raise after for-loop that always exits via internal return/raise

One `# pragma: no branch` was added (per Lesson #91 — defensive boundaries that the language produces but the logic can't traverse):

- v1.7.104 `migration_retry.py`: `for attempt in range(max_retries + 1)` natural-exit branch is unreachable
