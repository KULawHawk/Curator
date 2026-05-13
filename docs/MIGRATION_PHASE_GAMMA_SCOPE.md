# Migration Phase Gamma — Scope Plan

> ## 🔴 PRIORITY COMMITMENT (per Jake, 2026-05-12)
>
> **This arc MUST be completed. No abandonment, no indefinite deferral.**
>
> Sub-ships v1.7.90 through v1.7.93 are non-optional follow-through on the apex-accuracy doctrine (Lesson #71) and the no-mid-arc rule (Lesson #88). Leaving migration.py at 68% indefinitely would violate the standard the doctrine was built to enforce.
>
> **Resume protocol when picking back up:**
> 1. Open this document — current sub-ship status is in the tracker below.
> 2. The next pending sub-ship's stubs are already in `tests/unit/test_migration_plan_apply.py` (v1.7.89). Reuse, don't redesign.
> 3. Read the target cluster's source code in `src/curator/services/migration.py` (line ranges in the structural inventory below).
> 4. Write tests, hit 100% on target lines/branches, ship cleanly per Lesson #86 (commit + tag + push, no mid-ship).
> 5. Update the status tracker in this doc. Update revised coverage estimates if actual delta differs.
> 6. Repeat until v1.7.93 lands at 100%.
>
> If picking this up in a new session, the restart prompt is: *"Resume Migration Phase Gamma arc. Open docs/MIGRATION_PHASE_GAMMA_SCOPE.md, next sub-ship per tracker."*

---

**Status:** Active arc plan — sub-ship 1 of 5 closed at v1.7.89
**Owner:** Curator engineering doctrine
**Created:** 2026-05-12 (v1.7.88)
**Module:** `src/curator/services/migration.py`
**Starting coverage:** 66.74% line + branch (319 missing lines, 53 partial branches)
**Current coverage:** 68.18% (after v1.7.89)
**Target:** 100.00% line + branch (per apex-accuracy doctrine, see v1.7.84)

## Why this needs a plan, not a single ship

`migration.py` is 1031 statements / 3032 lines — bigger than `tier.py` + `lineage.py` + `safety.py` + `queries.py` + `scan.py` + `bundle.py` combined (which together total 837 stmts at 100%). A one-shot push would predictably end in a mid-ship state, violating Lesson #86.

The corollary from Lesson #87 (pattern dividends) also applies: when starting a sub-arc, **expect early sub-ships to be more expensive than later ones**. Build stub infrastructure for the Phase 1 main path first; reuse it for Phase 2 persistent jobs and Phase 3 retry/conflict variants.

## Structural inventory

The 319 missing lines fall into seven logical clusters that map cleanly onto the module's actual structure:

| # | Cluster | Missing line ranges | Approx stmts | Surface area |
|---|---|---|---|---|
| 1 | **Plan() edge cases** | 527-599, 719-745, 733-745 | ~30 | Query filtering, plan construction edges |
| 2 | **Apply() retry + DB guard** | 839-840, 874, 881, 895, 912-938, 977 | ~30 | Retry decorator paths, DB-guard branch, post-conditions |
| 3 | **Same-source execution** | 1020-1207, 1218-1227, 1358-1363 | ~50 | `_execute_one_same_source` + conflict-resolution variants |
| 4 | **Cross-source primary** | 1470, 1486-1491, 1530, 1576-1613 | ~45 | `_execute_one_cross_source` orchestration |
| 5 | **Cross-source backup strategy** | 1630-1692, 1713-1786 | ~70 | `_cross_source_overwrite_with_backup` |
| 6 | **Cross-source rename strategy** | 1805-1857, 1892-1956 | ~60 | `_cross_source_rename_with_suffix` + `_auto_strip_metadata` |
| 7 | **Persistent jobs (Phase 2)** | 1966-3324 (scattered) | ~150+ | `run_job`, abort, persistent variants, lifecycle helpers |

## Multi-ship arc plan

### v1.7.89 — Plan + Apply control flow (Clusters 1 + 2)

**Target coverage:** push from 66.74% to ~72-75%
**Stubs needed (new):**
- `StubFileRepository` (reuse + extend from scan/bundle)
- `StubSafetyService` — returns canned `SafetyLevel` per file
- `StubAuditRepository` — captures entries
- Possibly `StubMigrationJobRepository` if we touch run_job paths (defer to v1.7.93)

**Tests to write:**
- Plan() with empty source, all-CAUTION source, mixed SAFE/CAUTION/REFUSE
- Plan() with path-relative-to edge cases (src not under src_root → None return)
- Apply() with retry decorator wrapping `_cross_source_transfer` (mock the decorator path)
- Apply() DB-guard branch — the curator.db file is in source set
- Apply() post-conditions — verify hash mismatches, src untouched after fail
- `MigrationPlan` properties (total_count, safe_count, etc.) — likely covered already; verify

**Risk:** This sub-ship establishes the stub design for the whole arc. Budget 1.5-2x time of a typical Phase Gamma ship.

### v1.7.90 — Same-source execution (Cluster 3)

**Target coverage:** push to ~77-80%

**Tests to write:**
- `_execute_one_same_source` happy path
- Collision handling: `--on-conflict=skip` (default) — exercises lines 1036-1080
- Collision handling: `--on-conflict=fail` — raises `MigrationConflictError` at 1140
- Collision handling: `--on-conflict=overwrite-with-backup` — same-source variant
- Collision handling: `--on-conflict=rename-with-suffix` — same-source variant
- Hash mismatch path — `_xxhash3_128_of_file(dst)` returns different hash from src
- Trash failure that does NOT roll back the index update (lines 1198-1227)

**Risk:** The four `--on-conflict` modes are sub-variants of the same execution path. Each needs its own test.

### v1.7.91 — Cross-source primary (Cluster 4 + 5)

**Target coverage:** push to ~85-88%
**Stubs needed (new):**
- `StubPluginManager` with `curator_source_write` hook (reuse template from scan/bundle)
- `StubSourceRepository` (reuse template from scan)

**Tests to write:**
- `_execute_one_cross_source` happy path
- Cross-source with `FileExistsError` raised → routes to `_cross_source_overwrite_with_backup` or `_rename_with_suffix` based on policy
- `_cross_source_overwrite_with_backup`: backup naming format, backup-then-write, rollback on hash-mismatch
- Retry transient errors — decorator path (mock `retry_transient_errors`)

**Risk:** `_cross_source_overwrite_with_backup` is ~70 stmts and has multiple error rollback paths. Largest single function.

### v1.7.92 — Cross-source rename + auto-strip (Cluster 6)

**Target coverage:** push to ~92-95%
**Stubs needed (new):**
- `StubMetadataStripper` with `.strip_file()` method

**Tests to write:**
- `_cross_source_rename_with_suffix`: naming format (`.curator-1.ext`, `.curator-2.ext`, ...), max-suffix retry, eventual failure
- `_auto_strip_metadata`: SourceConfig with `share_visibility='public'` → strip invoked; with `share_visibility='private'` → skipped
- `_auto_strip_metadata`: stripper raises → outcome captured, source untouched
- Edge: dst_source not registered → graceful skip
- Edge: metadata_stripper is None (legacy code path)

### v1.7.93 — Persistent jobs (Cluster 7)

**Target coverage:** push from ~95% to 100%
**Stubs needed (new):**
- `StubMigrationJobRepository` with full job + progress row APIs

**Tests to write:**
- `create_job`: plan → persistent rows, returns UUID
- `run_job`: worker pool execution, per-file outcome persistence
- `run_job` resume: pick up after interruption (progress rows already exist)
- `abort_job`: signal Event, workers stop between files
- `_execute_one_persistent_same_source` variants — mirror Phase 1 same-source tests but with progress row writes
- `_execute_one_persistent_cross_source` variants — mirror Phase 1 cross-source tests
- `get_job_status`, `list_jobs`, recovery helpers — basic CRUD coverage

**Risk:** This is the biggest sub-ship. Worker-pool tests need thread-safety design — possibly run with `max_workers=1` for determinism.

## Audit-check infrastructure

Per Lesson #76 (doctrine amendments follow stable patterns), this arc warrants an audit test once the arc is complete:

```python
# tests/integration/test_migration_arc_complete.py (added in v1.7.93)
def test_migration_service_at_100_percent_coverage():
    """Verifies migration.py stays at 100% line + branch.

    If this test fires, someone added uncovered code to migration.py.
    Either cover it or add `# pragma: no cover` with a documented
    justification (see PLATFORM_SCOPE.md template).
    """
```

This catches regressions and codifies the standard.

## Estimates

| Sub-ship | Effort estimate | Coverage delta |
|---|---|---|
| v1.7.89 (Plan + Apply) | 1.5-2x typical Phase Gamma | +6-8% |
| v1.7.90 (Same-source exec) | ~1.5x typical | +4-6% |
| v1.7.91 (Cross-source primary) | ~2x typical (largest single function) | +7-9% |
| v1.7.92 (Rename + auto-strip) | ~1.2x typical | +5-7% |
| v1.7.93 (Persistent jobs) | ~2-2.5x typical (biggest sub-ship) | +5-8% to land 100% |

A "typical Phase Gamma ship" for context: scan.py was 23 tests + 8 stubs + 3 passes; bundle.py was 27 tests + 3 stubs + 1 pass.

**Total arc estimate: 5 sub-ships, v1.7.89 through v1.7.93. Possibly v1.7.94 if any sub-ship needs to split.**

## Doctrine notes

This is the **first multi-ship arc planned under the apex-accuracy doctrine** (codified in v1.7.84). The protocol:

1. Each sub-ship MUST close cleanly (committed + tagged + pushed before any session-end). Per Lesson #86, "mid-ship" is not acceptable.
2. Each sub-ship MUST hit 100% on the lines/branches it explicitly targets. Partial coverage of a target cluster is corner-cutting. Per Lessons #71 and #82.
3. Each sub-ship documents which uncovered code remains and which cluster will close it. The progress is transparent across the arc.
4. If a sub-ship's planned scope grows beyond ~1.5x its budget during execution, **split it into two sub-ships** rather than land mid-ship. Re-plan, document the split, ship cleanly.
5. The arc's final ship (v1.7.93 if all goes per plan) lands the 100% audit-check test and amends Doctrine Part V to record migration.py's completion.

## Resume / restart contract

If a session ends between sub-ships:
- The HEAD commit identifies the last completed sub-ship.
- The CHANGELOG entry for that ship documents which cluster(s) closed and the running coverage percentage.
- This document (`MIGRATION_PHASE_GAMMA_SCOPE.md`) is the source of truth for which sub-ship comes next.
- Update this document's status header whenever a sub-ship completes; check off the cluster in the section above; record the new running coverage.

## Status tracker

| Sub-ship | Status | Closed clusters | Coverage after | Date |
|---|---|---|---|---|
| v1.7.88 | ✅ This scope plan | — | 66.74% (unchanged) | 2026-05-12 |
| v1.7.89 | ✅ Plan() edges + Apply() autostrip/conflict-raise + _execute_one dispatch | Clusters 1 + part of 2 | **68.18%** (+1.44%) | 2026-05-12 |
| v1.7.90 | ✅ Same-source execution + 4 on-conflict modes (_resolve_collision) | Cluster 3 + part of Cluster 6 | **70.05%** (+1.87%) | 2026-05-12 |
| v1.7.91 | ⏳ Pending | Clusters 4 + 5 (cross-source primary + backup) | TBD | TBD |
| v1.7.92 | ⏳ Pending | Cluster 6 remainder (rename strategy + auto-strip) | TBD | TBD |
| v1.7.93 | ⏳ Pending | Cluster 7 + audit test + doctrine update | 100.00% | TBD |

### Revised estimates after sub-ship 1 calibration

The original scope plan estimated v1.7.89 at ~72-75% coverage; actual landed at 68.18%. Lesson learned (#89): defensive branches scattered across a 3000+ line file are smaller-grain than the estimate assumed. **Each subsequent sub-ship's estimate is now revised** based on actual line-count of its target clusters:

- v1.7.90 (Cluster 3: same-source execution, ~50 stmts): expect +3-5%, landing ~71-73%
- v1.7.91 (Clusters 4+5: cross-source + backup, ~115 stmts): expect +8-11%, landing ~80-84%
- v1.7.92 (Cluster 6: rename + auto-strip, ~85 stmts): expect +6-8%, landing ~87-92%
- v1.7.93 (Cluster 7: persistent jobs + remainder, ~150+ stmts): expect +8-13% to land 100%

The **arc target (100%) is unchanged**. Only the per-sub-ship delta estimates revised. The arc may still need a 6th sub-ship if v1.7.93's persistent-jobs work proves bigger than estimated.
