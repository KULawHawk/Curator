# Tracer Phase 3 Design — SKELETON

**Status:** v0.1 — DRAFT 2026-05-08. Skeleton-only at this version: identifies the Phase 3 scope candidates against actual `MigrationService` code in `Curator/src/curator/services/migration.py`, recommends a primary scope, surfaces the architectural DMs that need ratification before a full v0.2 design doc is written. Implementation NOT cleared at v0.1; full DM ratification + scope-locking flips this to v0.2 RATIFIED in the next session.
**Date:** 2026-05-08
**Authority:** Subordinate to `Atrium\CONSTITUTION.md`. Implements deferrals listed in `docs/TRACER_PHASE_2_DESIGN.md` §11 and `§1.2 What Phase 2 does NOT ship`.
**Companion documents:**
- `docs/TRACER_PHASE_2_DESIGN.md` — v0.3 IMPLEMENTED. Phase 3 builds on the v1.1.0 stable foundation (resumable jobs, worker pool, cross-source migration, GUI Migrate tab).
- `Curator/src/curator/services/migration.py` (~73 KB) — the file Phase 3 modifies. Code-touchpoints verified by direct reading at v0.1 issuance.
- `CHANGELOG.md` — v1.1.0 entry lists the Phase 3+ deferral set.
- Plus `Atrium\NAMES.md` (Tracer brand), `DESIGN_PHASE_DELTA.md` (original Feature M spec).

---

## 1. Scope candidates

Phase 2's §11 lists 7 deferral items. The code-grounded analysis at this v0.1 skeleton issuance:

| # | Item | Code touchpoint | Tracer-scoped? | Phase 3 fit |
|---|------|-----------------|----------------|-------------|
| 1 | Quota-aware retry + bandwidth throttling | New: wrap `_cross_source_transfer` and `_execute_one_same_source` in retry decorator. Currently NO retry logic exists in migration.py (verified 2026-05-08 — Phase 2's claim of "retry once on transient error" was deferred without implementation). | ✅ Yes | ⭐ Strong primary candidate |
| 2 | Conflict resolution beyond skip | Extend `MigrationOutcome` enum + branch in `_execute_one` before the SKIPPED_COLLISION return. Add `--on-conflict={overwrite-with-backup,rename-with-suffix,skip,fail}` flag. | ✅ Yes | ⭐ Strong primary candidate |
| 3 | Source config migration (`curator sources migrate <old_id> <new_id>`) | New CLI command, NEW SourceConfig migration logic (credentials, paths, files-as-atomic-unit) | ❌ No — separate beast (Feature S?) | Defer to its own design |
| 4 | Migration scheduling | Out of scope per Phase 2 design | N/A | Permanent skip |
| 5 | Cross-machine migration | Curator-DB-level operation; belongs in Feature U (Update protocol) | ❌ No | Defer to Feature U |
| 6 | `curator sources prune --empty` | Small standalone CLI command querying source_repo + file_repo | ✅ Yes (small) | Could ship alongside Phase 3 as a companion |
| 7 | `curator migrate-cleanup-orphans <job_id>` | Small standalone CLI for un-trashed sources after resume crash | ✅ Yes (small, conditional on real-world need) | Could ship alongside Phase 3 |

### 1.1 Recommended Phase 3 primary scope

**Items 1 + 2.** Both are Tracer-scoped (modify `MigrationService` directly), both have concrete code hooks, both deliver real value to existing v1.1.0 users:

- **Item 1 (retry + throttling)** unblocks reliable cross-source migration against rate-limited cloud sources (gdrive's ~1000 req/100s limit). Without this, multi-thousand-file migrations to gdrive routinely fail on transient 429s.
- **Item 2 (conflict resolution)** unblocks the "I'm migrating into an existing structure with some files already there" workflow. Phase 2's hard-coded skip is correct-but-limiting; users want choice.

Combined estimate: ~5h, ~3 sessions (mirrors Phase 2's session-shape).

### 1.2 Recommended Phase 3 companion scope (optional)

**Item 6** (`curator sources prune --empty`) and/or **Item 7** (`curator migrate-cleanup-orphans`) ship as companion CLI commands if Phase 3 has spare budget. Each is ~30 min of independent work.

### 1.3 What Phase 3 explicitly does NOT ship

- **Item 3** (source config migration). Belongs in Feature S; its own design cycle.
- **Item 5** (cross-machine migration). Belongs in Feature U; its own design cycle.
- **Item 4** (scheduling). Permanent skip per Phase 2.
- **Multi-destination migration** (copy to 2+ dst atomically). Out of scope for v1.x; revisit in v2.0.
- **Migration rollback** (undo a completed job). Out of scope; the audit log + lineage make manual rollback possible if rare.
- **Compression-aware transfer** (compress in-flight to cloud). Out of scope; cloud SDKs handle this internally.

---

## 2. Invariants from Phase 2 that MUST be preserved

Same 7 invariants as `TRACER_PHASE_2_DESIGN.md` §2. Phase 3 must not break any:

1. `curator_id` constancy across moves
2. Hash-Verify-Before-Move (Constitution Principle 2)
3. No Silent Failures (Constitution Principle 4) — every retry attempt + every conflict-resolution outcome must be auditable
4. DB-guard (curator.db never migrated)
5. Audit per move (every successful move logs `migration.move`; Phase 3 ADDS `migration.retry`, `migration.conflict_resolved` actions)
6. Plan/apply two-phase pattern (no mutations without `--apply`)
7. CAUTION/REFUSE skip by default

Plus a new Phase-3-specific invariant:

8. **Retries don't double-mutate.** A retry of a partially-written destination must clean up the partial first (delete the half-written file), THEN re-execute the per-file algorithm. No "retry by appending."

---

## 3. Decisions Jake needs to ratify before v0.2

### DM-1 — Retry primary trigger

**Question.** What conditions trigger a retry attempt?

Options:
- (a) HTTP 4xx-only (rate-limit-aware, gdrive-quota-aware)
- (b) Any IOError / OSError + HTTP 4xx (broader)
- (c) Configurable per-error-class with a default whitelist

**Recommendation:** **(a)** for v0.1 of Phase 3. Concrete error classes: `googleapiclient.errors.HttpError` with `resp.status in (403, 429, 500, 502, 503, 504)`, plus a generic transient-error escape valve for `requests.exceptions.ConnectionError` and `socket.timeout`. Anything else (IOError on local FS, hash mismatch, OS file-handle exhaustion) is a hard failure — no retry. Keeps retry conservative; v0.2 of Phase 3 design can broaden.

### DM-2 — Retry budget

**Question.** How many retries per file before marking FAILED?

Options:
- (a) Fixed N=3 attempts with exponential backoff (1s → 2s → 4s)
- (b) Configurable `--max-retries N` with default 3
- (c) Infinite-with-Retry-After-respect (let the cloud tell us when to stop)

**Recommendation:** **(b)** with default 3 + exponential backoff capped at 60s + Retry-After header parsing when present. Simple, predictable, debuggable. (c) is too easy to footgun (job runs for hours retrying a permanent-failure dst).

### DM-3 — Where the retry loop lives

**Question.** Inside `_cross_source_transfer` (per-file), or in the worker loop wrapping the whole `_execute_one`?

**Recommendation:** **Per-file, inside `_cross_source_transfer`.** Reasons:
1. Keeps the worker-loop simple (one file, one outcome, terminal status)
2. Resume semantics already handle "file failed, retry by re-running the job" at the JOB level — DM-3's per-file retry handles transient failures WITHIN a single attempt
3. Per-file retry doesn't pollute migration_progress with intermediate "in_progress" states
4. The retry decorator can be added as a wrapper around `_cross_source_transfer` without changing any caller. Surgical.

### DM-4 — Conflict resolution semantics

**Question.** What modes does `--on-conflict={...}` support?

Options:
- (a) `skip` (current default), `fail` (abort on first conflict), `overwrite-with-backup` (rename existing dst to `<name>.curator-backup-<timestamp>` before overwrite), `rename-with-suffix` (write new file as `<name>.curator-<n>.<ext>`, preserving existing dst)
- (b) Like (a) but skip `overwrite-with-backup` (too clever; encourages disk-bloat-by-mistake)
- (c) Like (a) plus `merge` for directories (out of scope for file-level migration)

**Recommendation:** **(a)** — all four modes. `overwrite-with-backup` is the headline new capability; users routinely want "replace what's there but don't lose the original until I verify." The backup file lives at the destination indefinitely (user-driven cleanup); MigrationService doesn't auto-trash it. New MigrationOutcome values: `MOVED_OVERWROTE_WITH_BACKUP`, `MOVED_RENAMED_WITH_SUFFIX`, `FAILED_DUE_TO_CONFLICT`. Existing `SKIPPED_COLLISION` stays for `--on-conflict=skip` (the default).

### DM-5 — Companion features

**Question.** Does Phase 3 ship #6 (`sources prune --empty`) and #7 (`migrate-cleanup-orphans`) alongside, or split them out?

**Recommendation:** **Split them out.** Phase 3's primary scope (DM-1 through DM-4) is already ~5h; adding companion CLIs blurs the design's focus. Both are good v1.2.0+ candidates as their own small features.

### DM-6 — Versioning

**Question.** What version line does Phase 3 ship under?

Options:
- (a) v1.2.x patch (additive flags only — `--max-retries`, `--on-conflict`)
- (b) v1.3.0 minor (new public surface — new MigrationOutcome enum values, new audit actions)

**Recommendation:** **(b) v1.3.0 minor.** New MigrationOutcome enum values + new audit actions ARE new public surface (third-party code reading the audit log via `query_audit_log` will see new action strings; libraries depending on MigrationOutcome will see new variants). Honest semver = minor bump. Note: this collides with v1.3.0 currently planned for "MCP HTTP transport authentication" per the MCP design; Phase 3 may want to claim **v1.3.0** and push MCP HTTP-auth to v1.4.0, OR ship as v1.2.1 patch (defensible since enum-additions are arguably non-breaking under most consumer expectations). Worth ratifying explicitly.

---

## 4. Spec sketch (high-level — full spec lives in v0.2)

### 4.1 New MigrationOutcome values
```python
class MigrationOutcome(str, Enum):
    # Phase 1 + 2 (existing)
    MOVED = "moved"
    COPIED = "copied"
    SKIPPED_NOT_SAFE = "skipped_not_safe"
    SKIPPED_COLLISION = "skipped_collision"
    SKIPPED_DB_GUARD = "skipped_db_guard"
    HASH_MISMATCH = "hash_mismatch"
    FAILED = "failed"
    # Phase 3 (NEW)
    MOVED_OVERWROTE_WITH_BACKUP = "moved_overwrote_with_backup"
    MOVED_RENAMED_WITH_SUFFIX = "moved_renamed_with_suffix"
    FAILED_DUE_TO_CONFLICT = "failed_due_to_conflict"
```

### 4.2 New CLI flags
```
curator migrate ...
  --max-retries N           # default 3; 0 = no retries
  --on-conflict MODE        # skip (default) | fail | overwrite-with-backup | rename-with-suffix
```

### 4.3 New audit actions
- `migration.retry` — fires on each retry attempt with `details={attempt: N, error: str, backoff_seconds: float}`
- `migration.conflict_resolved` — fires when a non-skip conflict mode runs with `details={mode: str, original_dst_action: str, backup_path?: str, suffix?: str}`

### 4.4 Code touchpoints (verified by direct reading 2026-05-08)
- `src/curator/services/migration.py:_cross_source_transfer` — wrap retry logic here (per DM-3)
- `src/curator/services/migration.py:_execute_one` — branch on `--on-conflict` before the existing `SKIPPED_COLLISION` return
- `src/curator/services/migration.py:MigrationOutcome` enum — add 3 new values
- `src/curator/cli/main.py:migrate` — add 2 new Typer options
- New: `src/curator/services/migration_retry.py` (~80 LOC) — retry decorator with exponential backoff + Retry-After parsing

---

## 5. Implementation plan sketch (full plan in v0.2)

Three sessions, ~5h total:

| Session | Scope | Output |
|---------|-------|--------|
| **P1 (~2h)** | DM-1, DM-2, DM-3 — retry decorator + integration into `_cross_source_transfer` + `migration.retry` audit + ~10 unit tests | Cross-source migrations against gdrive with realistic transient failures succeed reliably |
| **P2 (~2h)** | DM-4 — conflict resolution: 3 new outcome values + `--on-conflict` flag + branch in `_execute_one` + audit action + ~12 unit tests + ~3 integration tests | All four conflict modes work end-to-end |
| **P3 (~1h)** | Documentation: README "Tracer Phase 3" subsection, `docs/TRACER_PHASE_3_DESIGN.md` v0.2 → v0.3 IMPLEMENTED stamp, CHANGELOG v1.3.0 entry, version bump, tag, push | Released as v1.3.0 |

---

## 6. Cross-references

- `docs/TRACER_PHASE_2_DESIGN.md` v0.3 IMPLEMENTED — the foundation Phase 3 builds on. §1.2 + §11 are the deferral lists this design draws from.
- `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.3 IMPLEMENTED — mentions v1.3.0 for HTTP-auth; DM-6 above flags the version-line collision.
- `Atrium\CONSTITUTION.md` — Principles 2, 4, 5 govern Phase 3 same as Phase 2.
- `DESIGN_PHASE_DELTA.md` §M.1–M.8 — original Feature M spec.

---

## 7. Revision log

- **2026-05-08 v0.1** — DRAFT. Skeleton-only at this version. Captures: §1 the 7 deferral candidates from Phase 2 §11 with code-touchpoint verification (which two are Tracer-scoped + which aren't), §1.1 recommended primary scope (items 1 + 2: quota-aware retry + conflict resolution), §1.2 optional companions (items 6, 7), §1.3 explicit non-scope, §2 invariants from Phase 2 that must be preserved + 1 new Phase-3-specific invariant (retries don't double-mutate), §3 six DMs needing Jake's ratification (retry trigger, retry budget, retry location, conflict modes, companion feature decision, versioning), §4 high-level spec sketch (new MigrationOutcome values, new CLI flags, new audit actions, code touchpoints verified by direct reading 2026-05-08), §5 three-session implementation plan sketch (~5h), §6 cross-references. Implementation NOT cleared at v0.1; awaits Jake's ratification of DM-1 through DM-6, after which v0.2 adds the full per-DM rationale + per-tool spec + acceptance criteria similar in shape to `TRACER_PHASE_2_DESIGN.md`. Code-grounded discoveries logged for future reference: (a) Phase 2 design's claim of "retry once on transient error" was DEFERRED WITHOUT IMPLEMENTATION — there are zero retry/429/exponential references in `migration.py` at v1.2.0; this is a real gap, not just a Phase 3 enhancement, (b) the SKIPPED_COLLISION branch is a single hard-coded return in `_execute_one`, an obvious surgical hook for the new `--on-conflict` flag, (c) the retry decorator can wrap `_cross_source_transfer` without changing any caller — keeps the diff small. Lesson from atrium-reversibility (read code BEFORE writing §1) applied successfully here: every code-touchpoint claim in §4.4 was verified against the actual file at issuance time.
