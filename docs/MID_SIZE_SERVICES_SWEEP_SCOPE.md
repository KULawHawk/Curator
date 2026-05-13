# Mid-Size Services Sweep — Scope Plan

**Status:** Active arc plan — opened v1.7.140 (Round 2 Tier 4)
**Owner:** Curator engineering doctrine
**Created:** 2026-05-13 (v1.7.140)
**Modules:** 5 mid-size services modules under `src/curator/services/`
**Target:** All 5 modules at 100% line + branch (per apex-accuracy doctrine)

## Why this arc

Round 2 Tier 1–3 closed 39 modules; Tier 4's first two ships (lineage_repo + file_repo) brought the storage subpackage to 100%. The remaining Tier 4 work is 5 services modules that fell outside Round 1's service sweep. Together they total 259 uncovered lines + 31 partial branches.

This arc is the **second sub-arc within Round 2 Tier 4** (the first being the storage finish at v1.7.138-139).

## Baselines (re-measured 2026-05-13 per Lesson #93)

| Module | Stmts | Misses | Partials | Coverage |
|---|---|---|---|---|
| `services/photo.py` | 134 | 11 | 0 | 93.21% |
| `services/cleanup.py` | 367 | 50 | 9 br | 87.53% |
| `services/organize.py` | 336 | 56 | 10 br | 80.29% |
| `services/hash_pipeline.py` | 158 | 52 | 12 br | 64.42% |
| `services/trash.py` | 130 | 90 | 0 | 24.69% |

**Total uncovered:** 259 lines + 31 partial branches. Combined: 1125 stmts, 75.64% → expected ~100% after the arc.

## The 5 sweep targets (ordered by ascending uncovered lines)

| Ship | Module | Uncovered | Notes |
|---|---|---|---|
| v1.7.140 | scope plan (this doc) | — | doc-only |
| v1.7.141 | `services/photo.py` | 11 lines | Photo-specific operations. |
| v1.7.142 | `services/cleanup.py` | 50 lines + 9 br | Cleanup operations; shares collaborators with organize. |
| v1.7.143 | `services/organize.py` | 56 lines + 10 br | Organize operations; shares collaborators with cleanup. |
| v1.7.144 | `services/hash_pipeline.py` | 52 lines + 12 br | Multi-stage hash pipeline. |
| v1.7.145 | `services/trash.py` | 90 lines | LARGEST. Worst-covered (24.69%). May reveal design issues per handoff watchpoint. |

## Stub/fixture design notes

- Use shared `repos`/`services`/`local_source` fixtures from `tests/conftest.py`.
- These services consume the `Services` namespace (audit, classification, hash_pipeline, etc.) which is already wired in conftest.
- Lesson #84 stub vocabulary still applies for direct unit tests where present.
- Lesson #94 (`_SyncExecutor` shim) applies if any service uses ThreadPoolExecutor.

## Per-sub-ship process

1. **Re-measure baseline** on the target module (Lesson #93) — done at scope plan for all 5, but re-verify per sub-ship in case the test surface changes.
2. Read source thoroughly (Lesson #90).
3. Write focused test file `tests/unit/test_<module>_coverage.py`.
4. Iterate to 100% line + branch.
5. Standard 14-step ship workflow.

## Watchpoints

- **v1.7.145 (trash.py) is the riskiest.** 24.69% baseline + 90 uncovered lines. If complexity blows up (shared state, OS-specific paths, design issues), **split into v1.7.145a + v1.7.145b** per Lesson #88.
- **cleanup.py and organize.py share collaborators** per handoff. Read both before starting v1.7.142 — infrastructure built for cleanup may carry forward to organize (Lesson #87 pattern dividends).
- **hash_pipeline.py 12 partial branches** — careful branch-by-branch test design.

## Lesson capture

Through Tier 1–3 + Tier 4 ships 1–2, Round 2 has captured **zero new lessons**. All pattern dividends carry forward. Honest "no new lesson" entries continue to be the right outcome for doctrine-in-action work.

## Reporting cadence

Per handoff: report briefly after Mid-Size Services Sweep completion. Mid-arc checkpoints only if a sub-ship encounters surprises (Lesson #88: split if scope >1.5x).

## Resume / restart contract

If a session ends between sub-ships:
- HEAD commit identifies the last completed sub-ship.
- CHANGELOG entry documents which module closed and its final coverage.
- This document's status tracker (below) is the source of truth for next sub-ship.

Restart prompt: *"Resume Mid-Size Services Sweep arc. Open docs/MID_SIZE_SERVICES_SWEEP_SCOPE.md, next sub-ship per tracker."*

## Status tracker

| Sub-ship | Status | Module | Final coverage | Date |
|---|---|---|---|---|
| v1.7.140 | ✅ This scope plan | — | n/a (doc-only) | 2026-05-13 |
| v1.7.141 | ✅ Closed | `services/photo.py` | **100.00%** (was 93.21%) | 2026-05-13 |
| v1.7.142 | ✅ Closed | `services/cleanup.py` | **100.00%** (was 87.53%) | 2026-05-13 |
| v1.7.143 | ✅ Closed | `services/organize.py` | **100.00%** (was 80.29%) | 2026-05-13 |
| v1.7.144 | ✅ Closed | `services/hash_pipeline.py` | **100.00%** (was 64.42%) | 2026-05-13 |
| v1.7.145 | ✅ Closed | `services/trash.py` | **100.00%** (was 24.69%) | 2026-05-13 |

**ARC COMPLETE.** All 5 modules at 100% line + branch.

## Arc-level success criteria

When this arc closes:
- **5 more modules at 100% line + branch** (combined w/ Round 1 + Tier 1–3 + Tier 4 storage finish = 55 modules total)
- Curator overall coverage projected at ~75-80% per handoff

## Round 2 Tier 4 close-out (after this arc)

With the Mid-Size Services Sweep complete, Round 2 will close at:
- **55 modules at 100% line + branch**
- All storage modules covered (16)
- All MCP/plugins/config covered (Tier 2)
- All mid-size services covered (this arc)
- 39 ships shipped in Round 2 (v1.7.107–v1.7.145)
