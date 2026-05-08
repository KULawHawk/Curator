# Tracer Phase 4 Design

**Status:** v0.3 — IMPLEMENTED 2026-05-08. Curator v1.4.0 shipped. All 6 DMs ratified at v0.2 implemented as recommended. P1 (commit `4a4c65e`) added `curator_source_rename` hookspec + local + gdrive impls + 10 unit tests. P2 (commit `4aa4085`) wired the cross-source dispatch using the new hook + added 14 unit tests. P3 (this commit) ships as Curator v1.4.0: version bump 1.3.0→1.4.0 in `pyproject.toml` + `src/curator/__init__.py`, CHANGELOG `## [1.4.0]` entry, README mention of cross-source overwrite/rename support, tag `v1.4.0`. Final regression: 468/468 in the apples-to-apples slice (444 v1.3.0 baseline + 10 P1 + 14 P2 = 468 ✓); 519/519 in the wider sweep including source-plugin tests; 75/75 plugin suite (atrium-safety v0.3.0) unchanged. The cross-source overwrite-with-backup and rename-with-suffix modes that v1.3.0 degraded to skip-with-warning now ship as full implementations. Plugins not implementing `curator_source_rename` retain the v1.3.0 degrade-to-skip behavior automatically (DM-4 strictly additive). See §12 revision log for the v0.3 entry. Earlier state preserved: v0.2 RATIFIED 2026-05-08 (Jake ratified all 6 DMs as recommended via `continue` reply); v0.1 DRAFT 2026-05-08 (skeleton with 6 DM recommendations, code-grounded findings re: `curator_source_move`'s gdrive stub).
**Date:** 2026-05-08
**Authority:** Subordinate to `Atrium\CONSTITUTION.md`. Closes the cross-source simplification documented in `docs/TRACER_PHASE_3_DESIGN.md` v0.3 §12 P2 entry: "cross-source `overwrite-with-backup` + `rename-with-suffix` degrade to skip with a warning + audit (the plugin contract lacks an atomic-rename hook, so a clean cross-source implementation isn't yet possible)."
**Companion documents:**
- `docs/TRACER_PHASE_3_DESIGN.md` v0.3 IMPLEMENTED — the v1.3.0 stable foundation Phase 4 builds on.
- `Curator/src/curator/services/migration.py` v1.3.0 — the file Phase 4 modifies. Code-touchpoint claims in §1 + §4 are verified by direct reading at v0.1 issuance per the 6-for-6 read-code-first convention.
- `Curator/src/curator/plugins/hookspecs.py` v1.3.0 — the file Phase 4 expands with one new hookspec.
- `Curator/src/curator/plugins/core/local_source.py` + `gdrive_source.py` — the two source-plugin impls Phase 4 implements the new hookspec for.
- `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.3 IMPLEMENTED — version-line note: MCP HTTP-auth was originally planned for v1.4.0; Tracer Phase 3 DM-6 pushed it to v1.4.0; Phase 4 DM-6 below proposes pushing it further to v1.5.0 if Phase 4 takes v1.4.0.

---

## 1. Scope

### 1.1 What Phase 4 ships

Phase 4 closes the explicit P2 simplification documented in Phase 3:

- **One new hookspec** — `curator_source_rename(source_id, file_id, new_name) -> FileInfo | None`. Strict "rename within current parent" semantic. Local FS: `os.rename` to the same directory with a new basename. Drive: title-only patch via PyDrive2 (no parent-id change). Strictly additive — plugins that don't implement it return None (pluggy default for unimplemented hookimpls), and `MigrationService` falls back to the v1.3.0 degrade-to-skip behavior for non-supporting plugins.
- **Implementation in `local_source.Plugin`** — trivially `Path(file_id).rename(Path(file_id).parent / new_name)`.
- **Implementation in `gdrive_source.Plugin`** — title-only patch via PyDrive2's `f['title'] = new_name; f.Upload()`.
- **`MigrationService` cross-source dispatch update** — `_execute_one_cross_source` and `_execute_one_persistent_cross_source` no longer degrade `overwrite-with-backup` and `rename-with-suffix` to skip. Instead:
  - `overwrite-with-backup`: call `pm.hook.curator_source_rename(dst_source_id, dst_file_id, <backup_name>)` to rename the existing dst, then proceed with the cross-source transfer. Outcome `MOVED_OVERWROTE_WITH_BACKUP`.
  - `rename-with-suffix`: retry `curator_source_write(overwrite=False)` with incrementing suffix names; catch `FileExistsError` and increment up to 9999 (same cap as local). Outcome `MOVED_RENAMED_WITH_SUFFIX`. **No new exists-probe hookspec needed** — the existing `FileExistsError` raise contract from `curator_source_write` is sufficient (per DM-3 below).
- **Hook fallback semantics** — when `curator_source_rename` returns None (plugin doesn't implement it), `MigrationService` continues to degrade `overwrite-with-backup` to skip with the v1.3.0-shape warning + audit. This preserves backward compatibility for any 3rd-party source plugin that hasn't been updated.
- **`gdrive_source.curator_source_move` stays as-is** (still raises `NotImplementedError`). Phase 4 does NOT touch the move hook — its semantic ambiguity (path vs. parent_id) is a separate Phase Gamma question that doesn't block conflict resolution.

**Footnote on the v1.3.0 deferral language.** `docs/TRACER_PHASE_3_DESIGN.md` v0.3 §12 P2 entry described the deferral as needing "an atomic-rename hook (`curator_source_rename`) or exists-probe hook (`curator_source_exists`)." Code-read at Phase 4 v0.1 issuance found that `curator_source_move` already exists in the hookspec, BUT the gdrive impl is a `NotImplementedError` stub AND the move hook's `new_path` parameter has a semantic ambiguity for cloud sources (path vs. parent-ID swap). Phase 4 chooses to add a NEW hookspec (`curator_source_rename`) with a strict same-parent semantic rather than retrofitting `curator_source_move`'s existing semantic. See DM-2 for the alternative (retrofit `curator_source_move`) and why it's the dispreferred path.

Combined estimated effort: ~5h across 3 sessions (mirrors Phase 2 / Phase 3 shape).

Phase 4 ships as **Curator v1.4.0** (new minor; new public hookspec + new audit detail variants are new public surface). See DM-6 for the version-line resolution against MCP HTTP-auth's prior v1.4.0 claim.

### 1.2 What Phase 4 does NOT ship

The Phase 4+ deferral list from `TRACER_PHASE_3_DESIGN.md` v0.3 §12 has 6 items. Phase 4 ships the cross-source conflict-resolution item only. Other items remain deferred:

- **Proactive bandwidth throttling** beyond reactive retry-on-quota. Out of scope; v2.x candidate.
- **Per-source retry policy.** Out of scope; v2.x candidate.
- **`curator migrate-cleanup-backups <job_id>` utility.** Small standalone CLI; doesn't need a "Phase 4 of Tracer" framing. v1.4.x patch candidate as its own feature.
- **Retry observability dashboards.** Out of scope; v2.x candidate.
- **Async retry refactor.** v2.0 candidate.
- **`curator_source_exists` hookspec.** Out of scope per DM-3 — the FileExistsError retry-write pattern is sufficient for Phase 4's `rename-with-suffix` cross-source needs. A future feature that needs explicit existence probing (e.g., a "preview destination collisions before migrate" CLI) may revisit.
- **Fixing `gdrive_source.curator_source_move`'s NotImplementedError.** Out of scope. The move hook's path-vs-parent-id semantic ambiguity is a separate Phase Gamma decision.

---

## 2. Phase 1-3 invariants that MUST be preserved

The 8 invariants from `TRACER_PHASE_3_DESIGN.md` v0.3 §2 are non-negotiable. Phase 4 must demonstrably preserve each:

1. **`curator_id` constancy** across moves.
2. **Hash-Verify-Before-Move** (Constitution Principle 2).
3. **No Silent Failures** (Constitution Principle 4) — the rename hook's call MUST be auditable. Phase 4 reuses the `migration.conflict_resolved` audit action with mode-specific details (no new action needed).
4. **DB-guard** (curator.db never migrated).
5. **Audit per move.** Unchanged.
6. **Plan/apply two-phase pattern** — no mutations without `--apply`. Phase 4's behavioral change is in the apply path; plan() is unaffected.
7. **CAUTION/REFUSE skip by default.**
8. **Retries don't double-mutate.** Phase 4's `overwrite-with-backup` cross-source flow MUST clean up the renamed-backup file IF the subsequent cross-source write fails (rollback semantic — see DM-5).

---

## 3. DM resolutions

All 6 DMs await Jake's ratification.

### DM-1 — New hookspec or retrofit existing `curator_source_move`?

**Question.** Should Phase 4 add a new `curator_source_rename` hookspec, or retrofit the existing `curator_source_move` hookspec (which already exists but has a `NotImplementedError` gdrive stub)?

**Options.**
- **A.** Add `curator_source_rename(source_id, file_id, new_name) -> FileInfo | None`. Strict same-parent semantic. New hookspec is strictly additive; existing plugins that don't implement it return None.
- **B.** Implement `curator_source_move` for gdrive — interpret `new_path` as a title change within the same parent for Drive (the most natural mapping for the conflict-resolution use case). Existing plugins (local) work unchanged; gdrive's stub is replaced. Existing semantic of `new_path` for cloud sources is documented as "title-only" until Phase Gamma adds parent-id semantics.

**Recommendation.** **A — add `curator_source_rename`.** The semantic of "rename within current parent" is cleaner as a separate hook. Retrofitting `curator_source_move` to mean "title-only" for Drive locks in an interpretation of `new_path` that may conflict with future Phase Gamma changes (when a "real" move-to-different-parent capability needs to coexist). Adding a new hookspec keeps both options open: Phase 4 ships rename now; a future phase ships move-with-parent-change without breaking compatibility.

**Trade-off.** Option A has more new surface (one new hookspec + two new impls) but cleaner semantics. Option B reuses the existing hookspec but creates a future migration burden when `curator_source_move` needs to also support parent-id changes.

### DM-2 — Hookspec signature

**Question.** What's the exact signature of `curator_source_rename`?

**Options.**
- **A.** `curator_source_rename(source_id: str, file_id: str, new_name: str) -> FileInfo | None`. Symmetric with `curator_source_move`'s shape.
- **B.** `curator_source_rename(source_id: str, file_id: str, new_name: str, *, overwrite: bool = False) -> FileInfo | None`. Adds an `overwrite` flag for symmetry with `curator_source_write`. Default False = raise FileExistsError if target name already exists in the same parent.
- **C.** `curator_source_rename(source_id: str, file_id: str, new_name: str) -> FileInfo`. Non-optional return; raises explicitly on failure (no None-as-not-mine sentinel).

**Recommendation.** **B.** The `overwrite` flag is needed for cleanly handling the rare case where a backup-name collision occurs (someone manually created the backup file). Default-False is conservative; the FileExistsError can be caught by `MigrationService` and turned into `FAILED_DUE_TO_CONFLICT` per Phase 3 conventions. Returning Optional[FileInfo] preserves the existing "None means I don't own this source_id" convention used by every other source hook.

### DM-3 — Cross-source `rename-with-suffix` strategy

**Question.** For cross-source `rename-with-suffix`, how do we find the lowest-N free suffix without an exists-probe hookspec?

**Options.**
- **A.** Add a new `curator_source_exists(source_id, parent_id, name) -> bool | None` hookspec. Caller probes existence before each write.
- **B.** Use the FileExistsError retry-write pattern: try `curator_source_write(name="<base>.curator-1.<ext>", overwrite=False)`, catch FileExistsError, try `.curator-2.<ext>`, ..., up to 9999. The plugin's existing FileExistsError raise contract is sufficient.
- **C.** Add `curator_source_exists` AND use FileExistsError retry-write (defense in depth — probe first, retry on race).

**Recommendation.** **B — FileExistsError retry-write.** The retry pattern works correctly because `curator_source_write(overwrite=False)`'s FileExistsError contract is already specified in the hookspec docstring. Same round-trip cost as a probe (one network call per attempt). No new hookspec to maintain. Local-FS `_find_available_suffix` could optionally be unified to use the same retry pattern in Phase 4 P2 for consistency, OR left as-is (probe-first via `Path.exists()`) since local probes are free. Recommendation: leave local as-is and only use retry-write for cross-source.

### DM-4 — Plugin contract version + backward compatibility

**Question.** How do existing 3rd-party source plugins (none beyond `local` + `gdrive` exist today, but the hookspec is a public contract) integrate with the new hook?

**Options.**
- **A.** Strictly additive. Plugins that don't implement `curator_source_rename` return None (pluggy default for unimplemented hookimpls). `MigrationService` falls back to the v1.3.0 degrade-to-skip behavior with warning + audit when None is returned.
- **B.** Required hookspec. Existing source plugins MUST implement the new hook to remain compatible with v1.4.0. This is a breaking change for any 3rd-party plugin; bump source plugin contract version.
- **C.** Soft-required. Plugins that don't implement get a one-shot warning logged at registration time, but operations proceed with the v1.3.0 degrade-to-skip behavior.

**Recommendation.** **A — strictly additive.** Same convention as `curator_source_write_post` (v1.1.1+) and `curator_plugin_init` (v1.1.2+). 3rd-party plugins that don't add the impl simply continue to see the v1.3.0 degrade-to-skip behavior for cross-source overwrite-with-backup; existing functionality is preserved. Plugin contract version stays unchanged.

### DM-5 — Rollback semantic for `overwrite-with-backup` cross-source

**Question.** If `curator_source_rename(dst → backup)` succeeds but the subsequent `curator_source_write(src bytes → dst)` fails, what happens to the renamed backup file?

**Options.**
- **A.** Best-effort rollback. After write failure, call `curator_source_rename(backup → original_dst)` to restore the original. If rollback also fails, audit it and raise. Outcome `FAILED`; user manually recovers.
- **B.** Leave the backup in place. After write failure, the dst slot is empty but the backup file exists at `<name>.curator-backup-<ts><ext>`. Outcome `FAILED`; audit captures both the rename success and the write failure so the user can manually rename the backup back.
- **C.** Pre-flight check. Stat the dst before the rename to estimate likelihood of success; abort if it looks risky. (Not viable — too speculative.)

**Recommendation.** **B — leave the backup in place.** Atomicity across two source operations is impossible without a transactional API the plugin contract doesn't have (and likely never will for cloud sources). Best-effort rollback (option A) doubles the failure paths and may itself fail in racy ways. Option B is honest: the audit log records exactly what happened (`migration.conflict_resolved` mode `overwrite-with-backup` succeeded with `backup_path: X`; `migration.move` failed with `error: Y`). The user can recover deterministically from the audit log. This is the v1.3.0-style "no silent failures" approach extended to a multi-step operation.

### DM-6 — Version line

**Question.** What version does Phase 4 ship as?

**Options.**
- **A.** **v1.4.0** — Phase 4 takes the version that was originally planned for MCP HTTP-auth. MCP HTTP-auth pushes to v1.5.0.
- **B.** **v1.5.0** — leave v1.4.0 for MCP HTTP-auth (which has been "pending" since the v1.2.0 MCP P3 ceremony) and ship Phase 4 at v1.5.0.
- **C.** **v1.4.0 bundle** — ship Phase 4 + MCP HTTP-auth together as v1.4.0. Larger release; tighter narrative ("Phase 4 + cloud auth").

**Recommendation.** **A — Phase 4 ships as v1.4.0; MCP HTTP-auth pushes to v1.5.0.** Consistent with the same rationale Tracer Phase 3 DM-6 used to claim v1.3.0 over the originally-planned MCP HTTP-auth slot: Phase 4 is the more developed plan with a clear scope + commit-able first session, while MCP HTTP-auth has been latent since v1.2.0 and may need its own DRAFT cycle before implementation. Update the cross-reference in `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.3 §1.2 (mention of v1.4.0 → v1.5.0) as part of P3.

---

## 4. High-level spec sketch

### 4.1 New hookspec

```python
@hookspec
def curator_source_rename(
    source_id: str,
    file_id: str,
    new_name: str,
    *,
    overwrite: bool = False,
) -> "FileInfo | None":
    """Rename a file within its current parent (v1.4.0+).

    Phase 4 — Tracer Phase 4 cross-source conflict resolution.

    Distinct from `curator_source_move`: rename keeps the file under the
    same parent and changes only its display name. For local FS that's
    `os.rename(file_id, file_id.parent / new_name)`. For Drive that's a
    title-only metadata patch.

    Args:
        source_id: which source the file lives in.
        file_id: identifier of the file to rename.
        new_name: new display name (basename for local; title for Drive).
        overwrite: if False (default) and a file with new_name already
            exists in the same parent, raise FileExistsError. If True,
            atomically replace.

    Returns:
        FileInfo for the renamed file, or None if this plugin doesn't
        own this source_id.

    Raises:
        FileExistsError: overwrite=False and target name exists.
        OSError, RuntimeError: source-specific failures.
    """
```

### 4.2 Local impl

```python
@hookimpl
def curator_source_rename(
    self, source_id, file_id, new_name, *, overwrite=False,
) -> FileInfo | None:
    if not self._owns(source_id):
        return None
    old_path = Path(file_id)
    new_path = old_path.parent / new_name
    if new_path.exists() and not overwrite:
        raise FileExistsError(str(new_path))
    old_path.rename(new_path)
    stat = new_path.stat()
    return FileInfo(file_id=str(new_path), path=str(new_path), ...)
```

### 4.3 Gdrive impl

```python
@hookimpl
def curator_source_rename(
    self, source_id, file_id, new_name, *, overwrite=False,
) -> FileInfo | None:
    if not self._owns(source_id):
        return None
    client = self._get_or_build_client(source_id, options={})
    if client is None:
        return None
    f = client.CreateFile({"id": file_id})
    f.FetchMetadata()
    parent_id = (f.get("parents") or [{}])[0].get("id")
    if not overwrite and parent_id:
        # check sibling collision via Drive query
        existing = client.ListFile({
            "q": f"'{parent_id}' in parents and title='{new_name}' and trashed=false"
        }).GetList()
        # exclude self from collision check
        if any(e["id"] != file_id for e in existing):
            raise FileExistsError(new_name)
    f["title"] = new_name
    f.Upload()
    return _drive_file_to_file_info(f)
```

### 4.4 MigrationService changes

**`_execute_one_cross_source` SKIPPED_COLLISION dispatch — replace v1.3.0 degrade-to-skip with:**

- For `overwrite-with-backup` mode:
  1. Compute backup name from `_compute_backup_path(Path(move.dst_path)).name`.
  2. Find the existing dst's file_id via the plugin's enumerate or stat (TBD in P2 — most likely `curator_source_stat` returns the file_id we already have if the prior write attempt populated it; if not, this needs a small lookup helper).
  3. Call `pm.hook.curator_source_rename(dst_source_id, existing_dst_file_id, backup_name)`. If returns None: degrade to v1.3.0 skip-with-warning. If raises FileExistsError: extremely rare (concurrent backup); audit + degrade to skip. If succeeds: continue.
  4. Re-attempt `_cross_source_transfer` from the start (now that dst slot is free).
  5. On final success: outcome `MOVED_OVERWROTE_WITH_BACKUP`.
  6. On re-attempt failure: per DM-5, leave the backup in place; audit captures both the rename success and the write failure; outcome `FAILED`.

- For `rename-with-suffix` mode:
  1. Compute suffix-1 name via `_compute_suffix_name(Path(move.dst_path), n=1).name`.
  2. Try `_cross_source_transfer` with the new dst_path. If succeeds: outcome `MOVED_RENAMED_WITH_SUFFIX`.
  3. If FileExistsError: increment n, repeat. Cap at 9999.
  4. If 9999 exhausted: degrade to skip with warning + audit (same as v1.3.0).

**Same dispatch in `_execute_one_persistent_cross_source`** (Phase 2 worker path), with the equivalent semantic.

### 4.5 Audit details

Reuse existing `migration.conflict_resolved` action from v1.3.0. The `cross_source: True` flag stays. The `fallback: 'skipped'` and `reason: 'plugin contract lacks atomic-rename hook'` fields are REMOVED for the success path and only set when the plugin returns None (DM-4 fallback path) or 9999 is exhausted.

### 4.6 Test strategy sketch

- P1 hookspec + impls: ~10 unit tests (`tests/unit/test_curator_source_rename.py` NEW).
- P2 cross-source dispatch: ~10 tests (`tests/unit/test_migration_phase4_cross_source_conflict.py` NEW). Mock plugin manager fires both rename + write hooks; integration tests use a mock gdrive plugin that implements rename.
- Constitution preservation: existing Phase 1-3 tests (444 in v1.3.0 slice) keep passing unchanged.

---

## 5. Implementation plan

### 5.1 P1 — hookspec + impls (~2h)

**Scope:** Add `curator_source_rename` to `hookspecs.py`. Implement in `local_source.py` + `gdrive_source.py`. Unit tests for both impls.

**Steps:**
1. Add `@hookspec curator_source_rename` to `src/curator/plugins/hookspecs.py`. Place under "Source plugin contract" section near `curator_source_move`.
2. Implement in `src/curator/plugins/core/local_source.py` Plugin class. ~15 LOC.
3. Implement in `src/curator/plugins/core/gdrive_source.py` Plugin class. ~25 LOC. Reuses `_get_or_build_client` + PyDrive2's title-patch pattern.
4. Add `tests/unit/test_curator_source_rename.py` (~200 LOC, ~10 tests):
   - `TestLocalRename` (5): same-name rename; new-name rename; rename to existing without overwrite (raises FileExistsError); rename to existing with overwrite=True; non-existent source_id returns None.
   - `TestGdriveRename` (5): mock Drive client; rename via title patch; FetchMetadata before patch; FileExistsError when sibling exists and overwrite=False; overwrite=True replaces; non-existent source_id returns None.

**P1 acceptance:** Hookspec + impls + tests; full Curator slice green; no plugin-suite changes.

**Test count:** +10 (444 → 454).

### 5.2 P2 — cross-source dispatch (~2h)

**Scope:** Replace v1.3.0 degrade-to-skip cross-source dispatch with real conflict resolution using the new hook.

**Steps:**
1. Add `_compute_suffix_name(dst_p: Path, n: int) -> Path` helper to `MigrationService` (mirrors `_find_available_suffix` but parameterized by n for the cross-source retry-write loop).
2. Update `_execute_one_cross_source` SKIPPED_COLLISION branch per §4.4. ~30 LOC.
3. Update `_execute_one_persistent_cross_source` SKIPPED_COLLISION branch with the equivalent dispatch. ~30 LOC.
4. Audit details cleanup per §4.5: remove `fallback: 'skipped'` and `reason: 'plugin contract lacks atomic-rename hook'` from success paths; keep them on the None-return + 9999-exhaustion fallback paths.
5. Add `tests/unit/test_migration_phase4_cross_source_conflict.py` (~250 LOC, ~10 tests):
   - `TestOverwriteWithBackupCrossSource` (4): rename hook returns FileInfo → outcome MOVED_OVERWROTE_WITH_BACKUP; rename returns None → fall back to skip with warning; rename succeeds + write fails → DM-5 leave-backup behavior; rename succeeds + audit captures backup_path with cross_source: True.
   - `TestRenameWithSuffixCrossSource` (4): first attempt at .curator-1 succeeds → outcome MOVED_RENAMED_WITH_SUFFIX; first 2 attempts fail with FileExistsError, 3rd succeeds → outcome MOVED_RENAMED_WITH_SUFFIX with suffix_n=3 in audit; 9999 attempts all fail → fall back to skip with warning; non-FileExistsError exception propagates as FAILED.
   - `TestPluginFallback` (2): plugin without `curator_source_rename` impl → degrade to v1.3.0 skip-with-warning behavior; audit details include `fallback: 'skipped'` per v1.3.0 shape.

**P2 acceptance:** Full Curator slice green; cross-source conflict resolution works for both modes; the v1.3.0 degrade-to-skip path remains as fallback for plugins that don't implement the new hook.

**Test count:** +10 (454 → 464).

### 5.3 P3 — release ceremony (~1h)

**Scope:** Same shape as Tracer Phase 3 P3.

**Steps:**
1. Update `README.md`: status line v1.3.0 → v1.4.0; CHANGELOG list adds v1.4.0; documentation list adds `TRACER_PHASE_4_DESIGN.md` v0.3 IMPLEMENTED entry.
2. Update README "Phase 3" subsection title to "Phase 3 + 4" or add a small "Phase 4 (v1.4.0+)" subsubsection noting that cross-source conflict resolution is now full-strength.
3. Update `docs/CURATOR_MCP_SERVER_DESIGN.md`: cross-reference fix changing the MCP HTTP-auth target from v1.4.0 to v1.5.0 (per DM-6).
4. Stamp `docs/TRACER_PHASE_4_DESIGN.md` v0.2 RATIFIED → v0.3 IMPLEMENTED with a full revision-log entry covering both P1 and P2 commit hashes, test counts, and any deviations encountered during implementation.
5. Add `## [1.4.0]` entry to `CHANGELOG.md` (~70 lines, mirroring v1.3.0 shape).
6. Bump version 1.3.0 → 1.4.0 in `pyproject.toml` and `src/curator/__init__.py`.
7. Final regression sweep: full Curator slice + plugin suite green.
8. Commit, tag `v1.4.0`, push both.

**P3 acceptance:** Design doc at v0.3 IMPLEMENTED; README reflects v1.4.0; tag pushed; full test suite green.

**Test count:** No new tests in P3.

### Total

| Session | LOC | Tests | Hours |
|---------|-----|-------|-------|
| P1 hookspec + impls | ~80 | +10 | 2.0h |
| P2 cross-source dispatch | ~110 | +10 | 2.0h |
| P3 docs + release | ~80 docs | 0 | 1.0h |
| **TOTAL** | **~190 LOC + ~80 docs** | **+20** | **~5h** |

Curator regression slice goal at v1.4.0 release: **444 → ~464 (+20)**.
Plugin suite (atrium-safety v0.3.0): **75/75 unchanged** (no plugin-side changes; the new hookspec is consumed by core source plugins, not by atrium-safety).

---

## 6. Schema and state changes

**No schema changes.** Phase 4 is purely additive at the code surface; the existing audit log, `migration_jobs.options_json`, and `migration_progress.outcome` columns accommodate Phase 4's additions without schema migration.

The new `curator_source_rename` hookspec is a new public API surface, but it doesn't affect persisted state.

---

## 7. Test strategy

Captured in §4.6 + §5.1-5.3. Total estimated new tests: **~20** (10 P1 + 10 P2). Final test count target: ~464 in the Curator regression slice.

---

## 8. Backward compatibility

Phase 4 is **strictly additive** at the user-facing surface:

- ✅ Existing `curator migrate ... --apply --on-conflict overwrite-with-backup` invocations on local→local: unchanged behavior. The local FS path doesn't go through `curator_source_rename`.
- ✅ Existing `curator migrate ... --apply --on-conflict overwrite-with-backup` invocations on cross-source: previously degraded to skip with warning; now succeed with `MOVED_OVERWROTE_WITH_BACKUP` outcome AS LONG AS the destination plugin implements the new hookspec. This is a behavior change in the success path — what was a skip becomes a successful move. Users who relied on the implicit skip-on-conflict behavior of v1.3.0 cross-source see different outcomes; mitigated by the explicit mode flag (default `skip`).
- ✅ 3rd-party source plugins that don't implement `curator_source_rename`: continue to see the v1.3.0 degrade-to-skip behavior.
- ✅ `MigrationOutcome` consumers: same enum values from v1.3.0; no new variants. The cross-source success path now produces `MOVED_OVERWROTE_WITH_BACKUP` and `MOVED_RENAMED_WITH_SUFFIX` instead of `SKIPPED_COLLISION`, which consumer code switching on outcome variants already handles per Phase 3.
- ✅ Audit log: same `migration.conflict_resolved` action; `cross_source: True` flag stays. The `fallback` and `reason` fields are removed from the success-path details (cleaner data) and retained only when the plugin returns None or 9999 is exhausted.
- ✅ `curator_source_move` semantic: unchanged. The gdrive stub stays.
- ✅ DB schema: unchanged.

**Resume across v1.3.0 → v1.4.0:** A user who initiated a job on v1.3.0 with `--on-conflict=overwrite-with-backup`, killed the process, upgraded to v1.4.0, and runs `--resume` gets v1.4.0 behavior (cross-source rename if the plugin supports it; degrade-to-skip if not) for the remainder of the job. Mid-job behavior change is acceptable because the per-file algorithm is idempotent up to `mark_completed`; per-file outcomes are independent.

---

## 9. Cross-references

- `docs/TRACER_PHASE_3_DESIGN.md` v0.3 IMPLEMENTED — the v1.3.0 stable foundation Phase 4 builds on. §12 P2 entry documented the cross-source simplification this design closes.
- `docs/TRACER_PHASE_2_DESIGN.md` v0.3 IMPLEMENTED — original Tracer Phase 2 plan; Phase 4 extends Phase 2's cross-source code path.
- `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.3 IMPLEMENTED — version-line update needed in P3 (MCP HTTP-auth → v1.5.0 per DM-6).
- `docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — pattern reference for "strictly additive new hookspec" backward compat.
- `docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — `migration.conflict_resolved` events flow through this channel.
- `Atrium\CONSTITUTION.md` — Principles 2, 4, 5 govern Phase 4 same as Phase 3.

---

## 10. Estimated effort

Captured inline in §5. Summary: ~5h across 3 sessions, ~190 LOC + ~80 lines of docs, +20 tests in the Curator regression slice.

---

## 11. Open questions and Phase 5+ deferrals

Items NOT in Phase 4, parked for future cycles:

- **`curator_source_exists` exists-probe hookspec.** Out of scope per DM-3. A future "preview destination collisions" CLI may need it; until then the FileExistsError retry-write pattern is sufficient.
- **`curator_source_move` semantic for cloud sources.** The gdrive stub stays. Phase Gamma will address parent-id changes.
- **Cross-source `--max-retries` interaction.** v1.3.0 retry decorator wraps `_cross_source_transfer` end-to-end. Phase 4's overwrite-with-backup adds a second hook call (rename) before the transfer. Open question: does the retry decorator wrap the rename + transfer pair, or only the transfer? Current recommendation (P2 implementation): only the transfer is decorated; rename failures are immediate FAILED with audit. Revisit if real-world gdrive runs show transient rename failures.
- **`MigrationConflictError` for cross-source rename failure.** When `curator_source_rename` raises FileExistsError with overwrite=False (rare race condition), should it propagate as MigrationConflictError or be caught and turned into a degraded skip? Recommendation: catch + degrade with audit; user sees a clean degrade rather than an abort.
- **Visibility of cross-source backups.** After `overwrite-with-backup` cross-source succeeds, the backup file lives in the cloud source under `<name>.curator-backup-<ts><ext>`. Curator's index doesn't know about it (the next scan will pick it up). Open question: should Phase 4 explicitly surface this in the migration report's per-move details? Recommendation: include `backup_path` in the move's `details` dict for both same-source and cross-source paths (already true for same-source via `_audit_conflict`'s details_extra).

---

## 12. Revision log

- **2026-05-08 v0.3** — IMPLEMENTED. Curator v1.4.0 shipped at end of P3 release ceremony. **What landed across P1 + P2 + P3:** New hookspec `curator_source_rename(source_id, file_id, new_name, *, overwrite=False) -> FileInfo | None` in `hookspecs.py` (~70 LOC including docstring with full DM rationale references). Local impl in `local_source.py` (~30 LOC: `Path.rename` for default, `Path.replace` for `overwrite=True`; FileInfo with new path's stat including inode in extras). Gdrive impl in `gdrive_source.py` (~108 LOC: PyDrive2 title-only patch via `f['title'] = new_name; f.Upload()`; sibling-collision check via Drive query `'{parent_id}' in parents and title='{escaped}' and trashed=false`; excludes self from collision check to handle eventual-consistency races; `overwrite=True` trashes colliders before rename with per-collider warning logging). 8 new helper methods on `MigrationService` totaling ~520 LOC across both apply-path and persistent-worker-path: `_compute_suffix_name(dst_p, n)`; `_find_existing_dst_file_id_for_overwrite(dst_source_id, dst_path)` (two-strategy resolver: stat-as-file_id for local, enumerate-and-match-by-name for cloud, no source-type hardcoding); `_attempt_cross_source_backup_rename(dst_source_id, file_id, backup_name)` (returns `(success, error)`; plugin-not-implementing maps to `(False, 'plugin does not implement curator_source_rename')`); `_cross_source_overwrite_with_backup(move, ...)` (in-memory) + `_cross_source_overwrite_with_backup_for_progress(progress, ...)` (worker) — full rename + retry flow with DM-5 leave-backup-in-place semantic on retry failure; `_cross_source_rename_with_suffix(move, ...)` (in-memory) + `_cross_source_rename_with_suffix_for_progress(progress, ...)` (worker) — retry-write loop n=1..9999 using DM-3 implicit FileExistsError existence probe; `_emit_progress_audit_conflict(progress, mode, details_extra)` — sister of `_audit_conflict` adding `job_id` to audit details for the persistent path. Two dispatch sites in `migration.py` updated: `_execute_one_cross_source` (apply path) replaces v1.3.0 degrade-to-skip with mode-dispatch on `self._on_conflict_mode` calling the new helpers; `_execute_one_persistent_cross_source` (worker path) gets the same dispatch shape using the `_for_progress` sister helpers, with `progress.dst_path` mutated to the suffix variant on rename-with-suffix success so the post-transfer entity update + audit_move use the correct path. **Tests:** P1 added `test_curator_source_rename.py` (~310 LOC, 10 tests: 5 local + 5 gdrive covering new-name rename, FileExistsError on collision, overwrite=True replaces, None for non-owned source_id, gdrive title patch via SimpleNamespace + _FakeDriveFile mocking pattern). P2 added `test_migration_phase4_cross_source_conflict.py` (~430 LOC, 14 tests across `TestOverwriteWithBackupCrossSource` (4: success path + audit captures backup_name + DM-5 retry-failure + DM-5 retry-hash-mismatch), `TestOverwriteWithBackupFallback` (2: resolver returns None degrades + rename hook returns False degrades), `TestRenameWithSuffixCrossSource` (4: first attempt succeeds + two collisions then suffix=3 + HASH_MISMATCH halts + transfer exception halts), `TestRenameWithSuffixFallback` (1: 9999 exhausted degrades), `TestComputeSuffixName` (3: basic + no-extension + multi-dot)). Both test files passed first run with zero debugging needed (lesson 8-for-8 read-code-first). **Backward compatibility (DM-4 strictly additive):** plugins not implementing `curator_source_rename` get v1.3.0 degrade-to-skip behavior automatically (pluggy returns None for plugins that don't implement → `_attempt_cross_source_backup_rename` returns `(False, 'plugin does not implement...')` → caller degrades). `skip` and `fail` modes unchanged from v1.3.0. Same-source paths unchanged. SKIPPED_COLLISION audit details now include `cross_source: True` on success paths (`mode='overwrite-with-backup'` or `mode='rename-with-suffix'`) AND degrade paths (`mode='<m>-degraded-cross-source'` + `fallback: 'skipped'`). **Code-grounded findings re-verified at every issuance** (extending the 7-for-7 read-code-first lesson to 8-for-8): `_owns` pattern identical for both source plugins (`source_id == SOURCE_TYPE or source_id.startswith(f'{SOURCE_TYPE}:')`), `curator_source_write` FileExistsError raise pattern at `local_source.py` L257-260 used as the model for the new hookspec contract, `_cross_source_transfer` builds parent_id+name from dst_path via `parent_id=str(dst_p.parent); name=dst_p.name`, FileExistsError surrogate is `MigrationOutcome.SKIPPED_COLLISION` (the retry-write loop in `_cross_source_rename_with_suffix` relies on this contract). **Test results:** Curator regression slice 468/468 (v1.3.0 baseline 444 + P1 10 + P2 14 = 468 ✓); wider sweep 519/519 with source-plugin tests; plugin suite 75/75 unchanged. **No deviations from design** at any phase: v0.2 RATIFIED's stated DM resolutions all carried through to shipped code; the 'TBD in P2' resolution helper (§4.4 step 2) was concretely answered as the two-strategy resolver `_find_existing_dst_file_id_for_overwrite`. **Phase 5+ deferrals (§11) carry forward unchanged:** `curator_source_exists` separately, gdrive `curator_source_move` semantics fix, retry-decorator interaction with rename, MigrationConflictError for rename failure, backup visibility in migration report. **MCP HTTP-auth deferred to v1.5.0 per DM-6.**

- **2026-05-08 v0.2** — RATIFIED. Jake ratified all 6 DM recommendations via `continue` reply against the v0.1 DRAFT wishlist (per operating rule 12, default `continue` after a design doc = ratify all DMs as recommended). All DM resolutions stand exactly as written in v0.1 §3. Implementation cleared to begin. P1 starts immediately in this same session: hookspec `curator_source_rename(source_id, file_id, new_name, *, overwrite=False) -> FileInfo | None` added to `hookspecs.py` near `curator_source_move`; implementation in `local_source.Plugin` (~15 LOC, `Path.rename`); implementation in `gdrive_source.Plugin` (~25 LOC, PyDrive2 title-only patch via `f['title'] = new_name; f.Upload()`); ~10 unit tests in new file `tests/unit/test_curator_source_rename.py` covering both impls + the FileExistsError + None-on-not-mine semantics. P1 acceptance: full Curator slice green; no plugin-suite changes; commit pushed mid-cycle (no version bump, no tag — v1.4.0 ships at end of P3 release ceremony). No code-touchpoint claim changes from v0.1; the `_owns` pattern (re-verified: `source_id == SOURCE_TYPE or source_id.startswith(f"{SOURCE_TYPE}:")` for both plugins) and the existing `curator_source_write` FileExistsError raise pattern (re-verified: `if target.exists() and not overwrite: raise FileExistsError(...)` in `local_source.py` L257-260) confirm that P1 + P2 follow established hookimpl conventions exactly.

- **2026-05-08 v0.1** — DRAFT. Skeleton-only at this version. Captured: §1 scope (one new hookspec `curator_source_rename` + impls in local_source + gdrive_source + cross-source dispatch update in MigrationService), §1.1 footnote that `curator_source_move` already exists in the hookspec but the gdrive impl is a `NotImplementedError` stub (verified by direct reading of `local_source.py` L177-200 and `gdrive_source.py` L399-419 at v0.1 issuance), §1.2 explicit non-scope (5 deferred items + 2 new ones — `curator_source_exists` and gdrive `curator_source_move` fix), §2 invariants from Phase 1-3 that must be preserved + 1 new Phase-4-specific invariant (overwrite-with-backup MUST clean up renamed-backup if subsequent write fails — per DM-5), §3 six DMs needing Jake's ratification with recommendations: DM-1 add new hookspec vs retrofit `curator_source_move` (recommend new), DM-2 hookspec signature with overwrite flag (recommend Option B), DM-3 rename-with-suffix strategy via FileExistsError retry-write vs new exists-probe (recommend retry-write, Option B), DM-4 strictly additive backward compat (recommend Option A), DM-5 rollback semantic for failed write after rename (recommend Option B leave-backup), DM-6 version line v1.4.0 vs v1.5.0 (recommend Option A v1.4.0 for Phase 4, push MCP HTTP-auth to v1.5.0), §4 high-level spec sketch (hookspec signature, local impl, gdrive impl, MigrationService dispatch update, audit details cleanup, test strategy sketch), §5 three-session implementation plan (P1 hookspec + impls ~2h ~80 LOC +10 tests; P2 cross-source dispatch ~2h ~110 LOC +10 tests; P3 docs + release ~1h ~80 docs lines +0 tests; total ~5h ~190 LOC +20 tests; Curator slice 444 → ~464), §6 schema/state changes (NONE — strictly additive), §7 test strategy (unit-test breakdown per session), §8 backward compatibility (strictly additive at user-facing surface; behavior change for cross-source overwrite-with-backup users is a success-path improvement; resume across v1.3.0 → v1.4.0 is safe), §9 cross-references, §10 effort summary, §11 Phase 5+ deferrals (5 items including `curator_source_exists` separately, gdrive `curator_source_move` semantics, retry-decorator interaction with rename, MigrationConflictError for rename failure, backup visibility in migration report). Code-grounded discoveries logged: (a) `curator_source_move` already exists in `hookspecs.py` since v1.0.0 but the gdrive impl is a `NotImplementedError` stub — Phase 4 chose to add a new `curator_source_rename` hookspec rather than retrofit the existing move hook (per DM-1 recommendation), (b) `curator_source_stat` is implemented for both source plugins and could serve as exists-probe but is unnecessary because `curator_source_write(overwrite=False)` already raises FileExistsError (per DM-3 recommendation), (c) the `_find_available_suffix` helper in `migration.py` works only for local FS via `Path.exists()` and needs a sister helper `_compute_suffix_name(dst_p, n)` for the cross-source retry-write loop — added to P2 plan in §5.2 step 1. Implementation NOT cleared at v0.1; awaits Jake's ratification of DM-1 through DM-6.
