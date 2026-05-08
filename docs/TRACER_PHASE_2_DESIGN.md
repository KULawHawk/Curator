# Tracer Phase 2 Design

**Status:** v0.1 — design proposal, awaiting Jake's ratification of §3 (DM resolutions). Implementation does NOT begin until §3 is ratified.
**Date:** 2026-05-08
**Authority:** Subordinate to Atrium `CONSTITUTION.md`. Implements `DESIGN_PHASE_DELTA.md` §2 (Feature M) Phase 2.
**Companion documents:**
- `DESIGN_PHASE_DELTA.md` — original Feature M spec (§M.1–§M.8)
- `BUILD_TRACKER.md` — v1.1.0a1 entry documents what Phase 1 shipped
- `CHANGELOG.md` — v1.1.0a1 entry lists Phase 2 deferrals
- `Atrium\NAMES.md` — establishes "Tracer" as Curator's Migration capability brand

---

## 1. Scope

### 1.1 What Phase 2 ships

Phase 2 closes Feature M to v1.1.0 stable. It adds, on top of Phase 1's same-source local→local foundation:

1. **Cross-source migration** via the v0.40 `curator_source_write` plugin hook. Local↔gdrive is the headline; the design is source-agnostic so future plugins (Dropbox, OneDrive, S3) inherit the capability.
2. **Resumable migrations.** New `migration_jobs` + `migration_progress` tables persist enough state that an interrupted run picks up exactly where it left off, without re-copying files that already verified.
3. **Concurrent workers.** A bounded thread pool copies files in parallel up to `--workers N` (default 4). Per-file discipline (hash-verify-before-move, audit, index update) is preserved; only the top-level loop is parallelized.
4. **CLI surface completion.** `--resume <job_id>`, `--list`, `--status <job_id>`, `--abort <job_id>` plus the new `--workers N`, `--keep-source`, `--include-caution`, `--include <glob>`, `--exclude <glob>` flags.
5. **GUI Migrate tab** in PySide6 that drives the same MigrationService underneath. Source picker, destination picker, filter inputs, plan preview, live progress, cancel button, recent-job history.
6. **Backward compatibility.** Every Phase 1 invocation continues to produce the same output. Phase 2 is purely additive at the user-facing surface.

### 1.2 What Phase 2 does NOT ship (deferred to Phase 3+)

- **`--delete-source` flag.** Permanent deletion of source data is a MORTAL SIN class operation per Constitution. Phase 1's hardcoded `trash` and Phase 2's added `--keep-source` cover the safe spectrum. Users who want permanent removal can use the OS Recycle Bin themselves *after* verifying the migration. See §3 DM-1 below.
- **`prompt-each-time` source action.** Tedious for bulk operations and not Constitution-aligned (interactive prompts mid-bulk-operation are an anti-pattern). Skip permanently.
- **Full FileQuery filter syntax.** §3 DM-4. Phase 2 exposes ext + path-prefix + glob include/exclude; full FileQuery stays a Python-API-only feature.
- **Conflict resolution beyond "skip."** When the destination file already exists, Phase 2 still skips with `SKIPPED_COLLISION` (Phase 1 behavior). Overwrite-with-backup, rename-with-suffix, and fail-fast modes are useful but not required for v1.1.0; they belong in a v1.2.0 enhancement.
- **`SAME_LOGICAL_FILE` lineage edges between old and new.** Per `DESIGN_PHASE_DELTA.md` §M.7: not needed. The `curator_id` stays the same, so existing edges keep working.
- **Migration of `SourceConfig` rows themselves.** §3 DM-5. Migration moves files; source configs are managed via `curator sources` commands separately.
- **Bandwidth throttling and rate-limit-aware retry.** Useful for cross-source migrations against gdrive's quota, but Phase 2 implements the simpler "retry once on transient error, then mark FAILED." Sophisticated retry is Phase 3+.
- **Migration scheduling / cron-style automation.** Out of scope.

---

## 2. Phase 1 invariants that MUST be preserved

These are non-negotiable. Every Phase 2 change must demonstrably preserve all of them. Phase 2 tests will explicitly re-verify each.

1. **`curator_id` constancy.** Across any migration, the FileEntity row keeps the same `curator_id`. Lineage edges and bundle memberships continue to work transparently. Proven by `test_lineage_edges_survive_move` and `test_bundle_membership_survives_move`. Phase 2 must add the same proofs for cross-source moves.
2. **Hash-Verify-Before-Move (Constitution Principle 2).** Per file: hash src → write to dst → hash dst → verify match → THEN update index → THEN trash src. On any verification failure, src remains intact and dst is removed. Proven by `test_hash_mismatch_leaves_source_intact`.
3. **No Silent Failures (Constitution Principle 4).** Every per-file outcome is captured in the `MigrationReport`. `bytes_moved`, `failed_count`, and `skipped_count` are accurate. With Phase 2 resume, each `migration_progress` row also records its terminal state, so post-mortem analysis works without log scraping.
4. **DB-guard.** The runtime's own DB file (`rt.db.db_path`) is never migrated. Proven by `test_db_guard_skipped`.
5. **Audit per move.** Every successful move writes an audit entry with `actor='curator.migrate'`, `action='migration.move'`, and details `{src_path, dst_path, size, xxhash3_128}`. Proven by `test_audit_entries_written_on_success`. Phase 2 adds audit entries for `migration.job.created`, `migration.job.resumed`, `migration.job.aborted`, `migration.job.completed` to track job-level lifecycle.
6. **Plan/apply two-phase pattern.** No mutations without `--apply`. Phase 2's `--resume` is also gated behind `--apply` (resuming changes the database; you must opt in).
7. **CAUTION/REFUSE skip by default.** Only SAFE files migrate. Phase 2 adds `--include-caution` as opt-in for the "I know what I'm doing, this is a project-marked tree I really do want to relocate" case. REFUSE remains permanently uncrossable.

---

## 3. DM resolutions (§M.8 from `DESIGN_PHASE_DELTA.md`)

This is the section that needs Jake's explicit ratification before implementation begins.

### DM-1 — Source-file action after successful copy

**Question.** What happens to the source file once the destination is verified? Options: trash (recoverable), delete (permanent), keep (manual cleanup), prompt-each-time.

**Phase 1 status.** Hardcoded `trash` via vendored send2trash. No flag to override.

**Phase 2 recommendation.** Three actions are safe to expose; one is not:

- `--trash-source` (default) — current Phase 1 behavior. Recoverable via OS Recycle Bin. Keep as default.
- `--keep-source` — copy-only mode; do not modify the source. Useful for: (a) cautious users running migrations they want to verify by hand before letting Tracer remove anything, (b) cross-source migrations where the source IS the user's intended retained copy (e.g., "mirror my local Music to gdrive but keep the local copy as primary"), (c) test runs.
- `--delete-source` — **NOT shipping in Phase 2.** Permanent destruction without an undo path is a MORTAL SIN class operation. If a user genuinely wants permanent deletion, the workflow is: migrate with `--trash-source` (default), verify everything is good, manually empty the Recycle Bin. The extra step is the safety. Reconsider in v1.2.0+ if there's a demonstrated need (e.g., bulk migrations large enough that trashing them all overflows the Recycle Bin's quota). Not a Phase 2 concern.
- `prompt-each-time` — never shipping. Interactive prompts mid-bulk-operation are an anti-pattern; users either trust the plan or they don't.

The `--keep-source` flag interacts with the index update: in keep mode, do we still update `FileEntity.source_path` to point at the new location? **No** — in keep mode, we leave the index pointing at the source. The destination becomes an unindexed copy that the next `curator scan` will pick up as a new file. Lineage will then mark them duplicates via the existing duplicate-detection plugins. This is the intuitive semantics ("keep" means everything stays as it was, including the index) and it's the safest fallback.

**Net result for ratification:** add `--keep-source` (default off) and `--trash-source` (default on, mutually exclusive with `--keep-source`). Skip `--delete-source` and `prompt-each-time`.

**RATIFICATION STATUS:** ⏳ pending Jake's confirmation.

### DM-2 — Hash-verify default

**Question.** Should hash-verify-after-copy run by default?

**Phase 1 status.** Default ON. `--verify-hash`/`--no-verify-hash` flag exists; default is `--verify-hash`.

**Phase 2 recommendation.** No change. Default ON is mandated by Constitution Principle 2 (Hash-Verify-Before-Move). The opt-out exists for trusted fast paths and tests but it should never be the default. The cost (re-hashing the destination after copy) is small relative to the I/O cost, and it catches real corruption (silent disk errors, cloud-side rounding bugs, partial writes from interrupted copies that somehow look "complete" to the OS).

**RATIFICATION STATUS:** ⏳ pending Jake's confirmation. Recommendation: ratify and lock — this should not be revisited in future phases.

### DM-3 — Concurrent workers

**Question.** Should migrations run concurrent file copies? If so, how many by default?

**Phase 1 status.** Single-threaded. No `--workers` flag.

**Phase 2 recommendation.** Yes, configurable. **Default 4** (matches scan default).

Rationale for default 4:

- Same-source local→local: bounded by disk I/O. On SSDs, 4 parallel read+write streams saturate without thrashing. On HDDs, 4 is still reasonable; HDD users can lower with `--workers 1` or `--workers 2`.
- Cross-source local→gdrive: bounded by upload throughput and gdrive API rate limits. 4 parallel uploads is gentle on most home connections (~25 Mbps average) and well under gdrive's per-user request limits. Heavy users hitting quota errors can lower; high-bandwidth users wanting to saturate a gigabit uplink can raise to 8 or 16.
- Cross-source gdrive→local: bounded by download throughput. Same analysis.

The simpler alternative — different defaults per migration type — adds complexity for little benefit. One default with documentation around when to override is honest.

**Worker discipline.** Per Constitution: per-file work (hash, copy, hash, verify, update FileEntity, audit, trash) runs ATOMICALLY within one worker. Workers are independent — failure in one worker does not affect another. The audit log handles concurrent inserts via SQLite's serialization (audit appends are already serialized through the AuditRepository today). The FileEntity update path uses the existing repository's atomic `update()` call; no new locking.

**Ordering.** With workers, the migration_progress.status field becomes the source of truth for "which files have been moved." There is no global ordering guarantee — a worker may finish file N+1 before file N. This is fine because each row is self-contained.

**RATIFICATION STATUS:** ⏳ pending Jake's confirmation. Default `--workers 4`.

### DM-4 — Filter syntax

**Question.** What expressiveness should the filter language support? Options: extensions only, glob patterns, full FileQuery.

**Phase 1 status.** Comma-separated extension list (`--ext .mp3,.flac`). Case-insensitive, leading-dot optional.

**Phase 2 recommendation.** Add three more axes, each independent and composable:

- `--ext .mp3,.flac` (Phase 1 already shipped) — extension whitelist
- `--include "**/*.lossless.*"` (NEW) — glob include pattern, repeatable; the file must match at least one include if any are specified
- `--exclude "**/draft/**"` (NEW) — glob exclude pattern, repeatable; the file must NOT match any exclude
- `--path-prefix "Pink Floyd"` (NEW) — narrow within `src_root`; equivalent to changing `src_root` but useful when piping job IDs

Globs use the standard `fnmatch` Windows-aware semantics from `pathlib.PurePath.match`. Leading `**/` matches any depth. Trailing `/**` matches any descendants.

**Full FileQuery is NOT exposed** to the CLI. That's a Python-API affordance. CLI ergonomics for FileQuery would require a JSON/YAML embedded language and that's net-negative complexity for a tool that 80% of users will use with `--ext`.

**RATIFICATION STATUS:** ⏳ pending Jake's confirmation.

### DM-5 — `SourceConfig` row after migration

**Question.** When a cross-source migration moves all files from source A to source B, what happens to source A's row in the `sources` table?

**Phase 1 status.** N/A — Phase 1 is same-source.

**Phase 2 recommendation.** Leave it. Migration operates on FILES; source configs are managed by `curator sources` separately. A user who migrates everything off `gdrive:old@example.com` may still want to keep that config row around (perhaps re-enabling it later to verify nothing was missed, or just for historical reference). Removing it is an explicit, separate decision.

**Future enhancement (NOT Phase 2):** a `curator sources prune --empty` command that lists sources with zero active files and offers to remove them. This is useful but not blocking; defer to v1.2.0.

**RATIFICATION STATUS:** ⏳ pending Jake's confirmation.

---

## 4. Schema

Two new tables. Both use SQLite's standard syntax already used by `scan_jobs` and other Phase 1 tables.

### 4.1 `migration_jobs`

```sql
CREATE TABLE migration_jobs (
    job_id TEXT PRIMARY KEY,                      -- UUID4 string
    src_source_id TEXT NOT NULL,
    src_root TEXT NOT NULL,
    dst_source_id TEXT NOT NULL,                  -- equal to src_source_id for same-source jobs
    dst_root TEXT NOT NULL,
    status TEXT NOT NULL,                         -- 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'partial'
    options_json TEXT NOT NULL DEFAULT '{}',      -- JSON-serialized flag values (workers, verify_hash, ext, include, exclude, source_action, include_caution)
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    files_total INTEGER NOT NULL DEFAULT 0,       -- count of moves in plan
    files_copied INTEGER NOT NULL DEFAULT 0,      -- moves with outcome=MOVED
    files_skipped INTEGER NOT NULL DEFAULT 0,     -- moves with outcome=SKIPPED_*
    files_failed INTEGER NOT NULL DEFAULT 0,      -- moves with outcome=FAILED or HASH_MISMATCH
    bytes_copied INTEGER NOT NULL DEFAULT 0,
    error TEXT                                    -- top-level fatal error (e.g., write hook missing)
);

CREATE INDEX idx_migration_jobs_status ON migration_jobs(status);
CREATE INDEX idx_migration_jobs_started_at ON migration_jobs(started_at DESC);
```

Why `status='partial'`? When `--resume` is needed, the previous job's status is whatever it was at interruption — `running` if the process was killed, `failed` if a fatal error occurred. After successful resume completes the job, status becomes `completed`. The `partial` value is used when a job ran to completion but had per-file failures: most files moved, some didn't. This distinguishes from `completed` (everything moved) and `failed` (the job couldn't proceed at all).

Why `options_json` rather than columns per option? Forward compatibility. Phase 3+ may add filter axes; we don't want a schema migration each time. Read with `json.loads`, write with `json.dumps`.

### 4.2 `migration_progress`

```sql
CREATE TABLE migration_progress (
    job_id TEXT NOT NULL REFERENCES migration_jobs(job_id) ON DELETE CASCADE,
    curator_id TEXT NOT NULL,
    src_path TEXT NOT NULL,
    dst_path TEXT NOT NULL,
    src_xxhash TEXT,                              -- captured at plan time (or apply time if not cached)
    verified_xxhash TEXT,                         -- captured at apply time after copy (NULL if verify skipped)
    size INTEGER NOT NULL DEFAULT 0,
    safety_level TEXT NOT NULL,                   -- 'safe' | 'caution' | 'refuse'
    status TEXT NOT NULL,                         -- 'pending' | 'in_progress' | 'completed' | 'skipped' | 'failed'
    outcome TEXT,                                 -- MigrationOutcome.value when status is terminal; NULL when pending/in_progress
    error TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    PRIMARY KEY (job_id, curator_id)
);

CREATE INDEX idx_migration_progress_status ON migration_progress(job_id, status);
```

The `(job_id, curator_id)` composite primary key gives us O(1) "have we already moved this file in this job?" lookups during resume. The `idx_migration_progress_status` index supports "give me the next pending row" queries when workers fetch their next assignment.

Note: `outcome` mirrors the existing `MigrationOutcome` enum (MOVED, SKIPPED_NOT_SAFE, SKIPPED_COLLISION, SKIPPED_DB_GUARD, HASH_MISMATCH, FAILED). The two columns (`status` + `outcome`) seem redundant but serve different purposes: `status` is the operational state machine ("can a worker pick this up?"), `outcome` is the result enum ("what happened to this file?"). A row with `status='completed', outcome='moved'` is healthy; `status='completed', outcome='hash_mismatch'` is a verified-and-recorded failure.

### 4.3 New repository: `MigrationJobRepository`

`src/curator/storage/repositories/migration_job_repo.py` (new file). Methods:

- `create_job(job: MigrationJob) -> None`
- `get_job(job_id: UUID) -> MigrationJob | None`
- `list_jobs(*, status: str | None = None, limit: int = 50) -> list[MigrationJob]`
- `update_job_status(job_id: UUID, status: str, *, error: str | None = None) -> None`
- `update_job_counts(job_id: UUID, *, copied: int, skipped: int, failed: int, bytes: int) -> None`
- `seed_progress_rows(job_id: UUID, moves: list[MigrationMove]) -> None` — bulk insert of pending rows from a plan
- `next_pending_progress(job_id: UUID) -> MigrationProgress | None` — atomic claim: returns one row and marks it `in_progress` in the same transaction
- `update_progress(job_id: UUID, curator_id: UUID, *, status: str, outcome: str | None, error: str | None, verified_xxhash: str | None) -> None`
- `query_progress(job_id: UUID, *, status: str | None = None, limit: int | None = None) -> list[MigrationProgress]`

The `next_pending_progress` method is the workhorse for worker concurrency. It uses SQLite's `BEGIN IMMEDIATE` + `UPDATE...WHERE status='pending' RETURNING *` pattern (SQLite 3.35+ supports RETURNING) to atomically claim a row. If two workers race, exactly one wins.

---

## 5. Service architecture

### 5.1 New top-level methods on `MigrationService`

```python
class MigrationService:
    # Existing Phase 1 methods unchanged:
    def plan(...) -> MigrationPlan: ...
    def apply(plan, *, verify_hash=True, db_path_guard=None) -> MigrationReport: ...

    # New Phase 2 methods:
    def create_job(plan: MigrationPlan, *, options: dict) -> UUID:
        """Persist a plan as a migration_jobs row + migration_progress rows.
        Returns the job_id. Does NOT start execution."""

    def run_job(
        job_id: UUID,
        *,
        workers: int = 4,
        verify_hash: bool = True,
        db_path_guard: Path | None = None,
        on_progress: Callable[[MigrationProgress], None] | None = None,
    ) -> MigrationReport:
        """Execute or resume a persisted job. Spawns worker threads;
        each worker pulls pending rows via next_pending_progress.
        Returns the final MigrationReport when all rows reach a
        terminal state (or when the job is aborted)."""

    def abort_job(job_id: UUID) -> None:
        """Signal an in-progress job to stop. Workers finish their
        current file (no mid-file abort to preserve atomicity), then exit.
        Job status becomes 'cancelled'. Already-completed rows are kept."""

    def list_jobs(*, status: str | None = None, limit: int = 50) -> list[MigrationJob]: ...

    def get_job_status(job_id: UUID) -> MigrationJobStatus:
        """Returns counts (pending/in_progress/completed/skipped/failed),
        bytes copied, started_at, last_progress_at, ETA estimate."""
```

### 5.2 Backward compatibility for the Phase 1 path

The Phase 1 `apply()` method continues to work as before — synchronous, single-threaded, no persistence. It is the simple path for one-shot, can-fit-in-memory migrations.

The Phase 2 path (`create_job` + `run_job`) is the durable path. The CLI's default for `curator migrate ... --apply` becomes:

- If the plan has < 1000 files **and** `--workers 1` (the implicit default when not specified by the user), use the Phase 1 path. (Fast, no schema overhead.)
- If the plan has >= 1000 files **OR** `--workers > 1` is specified **OR** `--apply` is combined with `--resume <job_id>`, use the Phase 2 path.

This keeps small migrations cheap and ensures large or resumable migrations get the full state machine. The threshold (1000 files) is a tunable; can be adjusted via `Config` if needed.

### 5.3 Cross-source migration path

For each move, the existing Phase 1 algorithm becomes:

```
1. Read source bytes.
   - same-source local: open(src_path, 'rb')
   - cross-source: pm.hook.curator_source_read(source_id=src_source_id, path=src_path)
                   returns a byte stream
2. Hash source bytes (xxhash3_128).
3. Write to destination.
   - same-source local: shutil.copy2 (preserves mtime).
   - cross-source: pm.hook.curator_source_write(source_id=dst_source_id,
                                                path=dst_path,
                                                content=byte_stream)
4. Read destination bytes.
   - same-source local: re-open from disk.
   - cross-source: pm.hook.curator_source_read(source_id=dst_source_id, path=dst_path)
5. Hash destination bytes; verify match.
6. Update FileEntity.source_id (NEW for cross-source) + source_path.
7. Trash source.
   - same-source local: send2trash.
   - cross-source: pm.hook.curator_source_delete(source_id=src_source_id,
                                                 path=src_path)
                   (We use delete, not trash, for cross-source because cloud
                   sources don't have a Recycle Bin equivalent that's
                   reliably accessible. The cloud's own trash/version-history
                   serves as the safety net.)
8. Audit (with src_source_id + dst_source_id in details).
```

Steps 1 + 4 streaming. For files larger than ~50 MB we don't want to load into memory; the source-plugin contract already specifies `read_bytes` returns a streaming byte iterator (per `DESIGN.md` §6). We hash via xxhash's update() loop on the iterator; the same iterator is consumed by `curator_source_write`. For verification, we re-stream from the destination.

**Memory budget.** Each worker holds at most one open stream at a time. For 4 workers, peak memory is roughly `4 * 64 KB chunk size = 256 KB` for the streaming buffers. The plugin's stream implementations may have their own buffering (gdrive resumable uploads, for example, use 5+ MB chunks); that's plugin business, not Tracer's.

**What if `curator_source_write` is missing for a destination plugin?** Phase 2 must handle this gracefully:

```python
if not pm.has_plugin_for(dst_source_id, 'curator_source_write'):
    raise MigrationDestinationNotWritable(
        f"Source plugin for '{dst_source_id}' does not implement "
        "curator_source_write. Cross-source migration to this destination "
        "is not supported."
    )
```

Detect at `plan()` time, not `apply()` time. Refusing early prevents partial migrations.

### 5.4 Resume semantics

A `--resume <job_id>` invocation:

1. Loads the existing job row. If status is `completed`, refuses with "this job is already complete." If status is `cancelled`, requires `--force` to resume (cancellation was usually intentional). Otherwise proceeds.
2. Updates job status to `running`, sets `started_at` if not already set.
3. Resets any rows where `status='in_progress'` back to `pending` (those are leftovers from a worker that died mid-file; by the atomicity of step 6 below, the index update for those files did not happen, so we can re-execute safely).
4. Spawns workers as if it were a fresh run. Workers pull `pending` rows, execute the per-file algorithm, mark rows `completed` or `failed`.
5. When no `pending` or `in_progress` rows remain, sets job status to `completed` (or `partial` if any rows have `outcome IN ('failed', 'hash_mismatch')`).

**The atomicity guarantee:** between steps 6 (FileEntity update) and 7 (trash source) of the per-file algorithm, the row is marked `status='completed', outcome='moved'`. If the process dies between 6 and 7, the next resume sees the row as `completed` and SKIPS it — but the source file is still on disk un-trashed. That's a benign leak (no data loss; the user can manually clean up). The alternative — marking `completed` only after step 7 — risks a bigger problem: if 6 succeeds but 7 fails to mark completion, resume re-executes step 6, which is a no-op write but causes a duplicate audit entry and risks confusion.

The chosen ordering (mark completed after 6, before 7) trades "occasional un-trashed source on crash" for "no double-update on resume." That's the right trade. We document this in `audit_repo` queries: post-resume, the user may run `curator migrate-cleanup-orphans <job_id>` (a Phase 3 utility) to find any source files that should have been trashed but weren't.

### 5.5 Worker pool implementation

Standard Python `concurrent.futures.ThreadPoolExecutor` with `max_workers=N`. Workers run a loop:

```python
def _worker_loop(svc, job_id, verify_hash, db_path_guard, abort_event):
    while not abort_event.is_set():
        progress = svc._repo.next_pending_progress(job_id)
        if progress is None:
            return  # no more work
        try:
            outcome = svc._execute_one_persistent(progress, verify_hash, db_path_guard)
            svc._repo.update_progress(
                job_id, progress.curator_id,
                status='completed', outcome=outcome.value,
                verified_xxhash=...,
            )
            svc._repo.increment_job_counts(job_id, copied=1, bytes=progress.size)
        except Exception as e:
            svc._repo.update_progress(
                job_id, progress.curator_id,
                status='failed', outcome='failed', error=str(e),
            )
            svc._repo.increment_job_counts(job_id, failed=1)
```

`abort_event` is a `threading.Event` set by `abort_job()`. Workers check it at the top of each loop iteration — between files, never mid-file. This preserves the "atomic per file" invariant.

---

## 6. CLI surface

### 6.1 Full flag matrix

```
curator migrate [<src_source_id> <src_root> <dst_root>]   # positional args required for new jobs
                [--ext .mp3,.flac]                         # ext filter (Phase 1)
                [--include <glob>] [--include <glob>]      # multiple includes (Phase 2)
                [--exclude <glob>] [--exclude <glob>]      # multiple excludes (Phase 2)
                [--path-prefix <prefix>]                   # narrow within src_root (Phase 2)
                [--keep-source | --trash-source]           # default: trash (Phase 2)
                [--include-caution]                        # opt-in CAUTION migration (Phase 2)
                [--verify-hash | --no-verify-hash]         # default: verify (Phase 1)
                [--workers N]                              # default: 4 (Phase 2)
                [--apply]                                  # required for mutations (Phase 1)
                [--dst-source-id <id>]                     # cross-source mode (Phase 2)

# Job lifecycle commands (Phase 2):
curator migrate --resume <job_id>     [--force]            # resume from migration_progress
curator migrate --status <job_id>                          # progress + errors
curator migrate --list                [--limit N] [--all]  # recent jobs (default: last 50, only running/queued)
curator migrate --abort <job_id>                           # cancel a running job (workers finish current file then exit)
```

### 6.2 Examples

Same-source, all defaults (Phase 1 behavior preserved):

```
curator migrate local C:/Music D:/Music --apply
```

Cross-source with verbose filtering:

```
curator migrate local C:/Music gdrive:jake@example.com Music/ \
    --dst-source-id gdrive:jake@example.com \
    --include "**/*.flac" --include "**/*.mp3" \
    --exclude "**/draft/**" \
    --keep-source \
    --workers 2 \
    --apply
```

Resume an interrupted job:

```
curator migrate --resume 7f3a-... --apply
```

Inspect status:

```
curator migrate --status 7f3a-...
# or:
curator --json migrate --status 7f3a-...
```

### 6.3 JSON output schema additions

`migrate.plan` output unchanged from Phase 1 except:

- `options` field captures all flag values (for round-tripping into a job)
- `dst_source_id` no longer always equals `src_source_id`

`migrate.apply` output adds:

- `job_id`: present when the job was persisted (>= 1000 files OR workers > 1 OR --resume was used)
- For Phase 1 path (no persistence), `job_id` is null

New action types:

- `migrate.list` — array of job summaries (job_id, src/dst, status, files_total, files_copied, started_at)
- `migrate.status` — full job state with progress histogram
- `migrate.abort` — confirmation of abort signal sent

---

## 7. GUI Migrate tab

### 7.1 Wireframe

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Tracer (Migrate)                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│ Source                          Destination                              │
│ ┌───────────────────────┐       ┌───────────────────────┐                │
│ │ [v] local             │  →    │ [v] gdrive:jake@…     │                │
│ │ [Browse…] C:/Music    │       │ [Browse…] /Music      │                │
│ └───────────────────────┘       └───────────────────────┘                │
│                                                                          │
│ Filters                                                                  │
│   Extensions: [ .mp3, .flac           ]  [ ] Include CAUTION             │
│   Include globs: [ **/*.lossless.* ] [+]                                 │
│   Exclude globs: [ **/draft/**     ] [+]                                 │
│                                                                          │
│ Options                                                                  │
│   [x] Hash-verify after copy   Workers: [v] 4    Source: (·) Trash ( ) Keep
│                                                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐  │
│ │ [ Plan ]   [ Apply ]   [ Cancel ]                                    │  │
│ └─────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│ Plan preview                                            187 files SAFE    │
│ ┌─────────────────────────────────────────────────────────────────────┐  │
│ │  Source path                       →   Destination                   │  │
│ │  C:/Music/Pink Floyd/01.flac       →   /Music/Pink Floyd/01.flac     │  │
│ │  ...                                                                 │  │
│ └─────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│ Progress (active job 7f3a-…)                                             │
│ ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░ 142 / 187    bytes 4.2 GB / 5.1 GB    eta 2:14    │
│                                                                          │
│ Recent jobs                                                              │
│ ┌─────────────────────────────────────────────────────────────────────┐  │
│ │  job_id     src → dst              status      files     started     │  │
│ │  3a1f-…     local → gdrive:jake    completed   1224/1224  yesterday  │  │
│ │  7f3a-…     local → gdrive:jake    running     142/187    1:42 ago   │  │
│ │  ...                                                                 │  │
│ └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Implementation notes

- New `src/curator/gui/widgets/migrate_tab.py` (~500 LOC).
- Reuses existing `SafetyService` reports for the per-row safety badges.
- The plan preview table is populated from `MigrationPlan.moves` — same data structure the CLI uses.
- Apply runs `MigrationService.create_job` then `run_job` in a `QThread` so the UI stays responsive. Progress callbacks update the bar via `pyqtSignal`.
- Cancel emits the abort event via `MigrationService.abort_job(job_id)`. UI shows "cancelling..." until workers exit, then "cancelled."
- Recent jobs list refreshes on a 5-second poll while the tab is visible.

The visual design matches the v0.43 bundle editor — same color palette, same row densities. Keeps Curator coherent.

### 7.3 Tests

- 1 GUI smoke test (verifies the tab opens, populates source/dest dropdowns, generates a plan against a small temp library).
- Headless (no actual `--apply` from the GUI test); apply paths are covered exhaustively by the service-layer tests.

---

## 8. Test strategy

Total estimated new tests: **~50** (on top of the existing 33 from Phase 1). Final test count target: **~1052 default passing**, plus the 1 new GUI smoke (1011 → 1062 total).

### 8.1 Unit tests (~30 new) — `tests/unit/test_migration_phase2.py`

- `TestMigrationJobRepository` (10 tests)
  - create_job + get_job round-trip
  - list_jobs with status filter
  - update_job_status atomicity
  - seed_progress_rows bulk insert
  - next_pending_progress atomic claim under concurrent access (use threads)
  - query_progress with status filter
  - update_progress mutates correctly
  - foreign key cascade on job delete
  - JSON round-trip of options
  - timestamp population

- `TestRunJob` (12 tests)
  - run_job small plan single worker — same outcome as Phase 1 apply()
  - run_job 4 workers parallel correctness
  - run_job N workers ordering does not affect index integrity
  - run_job interrupt + resume produces identical final state vs uninterrupted
  - run_job abort_job mid-execution stops cleanly
  - run_job with dst_source_id != src_source_id (cross-source mock)
  - run_job MissingWriteHook detected at plan time, not apply time
  - run_job persisted progress row outcomes match in-memory MigrationReport
  - run_job concurrent failure in one worker doesn't block others
  - run_job updates job status to 'partial' when some rows failed
  - run_job updates job status to 'completed' when all rows succeeded
  - run_job re-running a completed job no-ops (with --force-rerun → new job_id)

- `TestKeepSourceFlag` (3 tests)
  - keep_source=True does not trash src
  - keep_source=True does NOT update FileEntity.source_path (index stays on src)
  - keep_source=True still writes audit entry with action='migration.copy' (distinct from migration.move)

- `TestIncludeCautionFlag` (3 tests)
  - default: CAUTION files skip with SKIPPED_NOT_SAFE
  - --include-caution: CAUTION files migrate
  - REFUSE files always skip regardless of flag

- `TestGlobFilters` (2 tests)
  - --include narrows to matching files
  - --exclude removes matching files

### 8.2 Integration tests (~15 new) — `tests/integration/test_cli_migrate_phase2.py`

Each is a CliRunner-driven test against a real seeded DB.

- migrate --list returns recent jobs as JSON
- migrate --status <job_id> returns full state
- migrate --abort <job_id> sets status to cancelled
- migrate --resume picks up where left off
- migrate --resume on already-completed job exits 0 with informational message
- migrate --resume --force on cancelled job re-executes
- migrate with --workers 4 runs to completion
- migrate cross-source plan rejects when dst plugin lacks write hook
- migrate cross-source apply via mock plugin succeeds
- migrate --keep-source preserves src + index points to src
- migrate --include-caution migrates a CAUTION file
- migrate --include glob filter
- migrate --exclude glob filter
- migrate --path-prefix narrowing
- migrate JSON output schema for new actions (list, status, abort)

### 8.3 GUI smoke (~1 new opt-in) — `tests/gui/test_migrate_tab_smoke.py`

- Opens Migrate tab, populates source/dest, generates plan, verifies plan-preview table has rows, exits without --apply.

### 8.4 Constitution preservation tests (re-runs of Phase 1 tests under Phase 2 conditions)

The existing Phase 1 tests in `tests/unit/test_migration.py` keep passing unchanged. New parameterized variants run them through `run_job` with workers=1, workers=4, and via the mock cross-source plugin to prove the invariants hold across all paths.

---

## 9. Phase 1 → Phase 2 schema migration

When a v1.1.0a1 user upgrades to v1.1.0a2 (or v1.1.0):

1. Curator's existing schema migration mechanism (per `DESIGN.md` §11.5) runs `alembic upgrade head` (or whatever the current migration tooling is — verify in v1.1.0a2 implementation).
2. New tables `migration_jobs` and `migration_progress` are created. Empty.
3. Existing FileEntity rows untouched.
4. Existing audit log untouched.

No data migration needed. Phase 2 adds tables; nothing in Phase 1's data model changes.

If a user runs `curator migrate ... --apply` after upgrade with `< 1000` files and no `--workers > 1`, the Phase 1 path runs and no rows are created in `migration_jobs` / `migration_progress`. Both tables stay empty until the user does a "big enough" migration. That's correct.

---

## 10. Estimated effort

| Section | Code | Tests | Hours |
|---------|------|-------|-------|
| §4 schema + `MigrationJobRepository` | ~250 LOC | 10 tests | 1.5h |
| §5.1–5.2 service extensions (create_job, run_job, abort_job, status methods) | ~300 LOC | 12 tests | 1.5h |
| §5.3 cross-source via plugin hook | ~150 LOC | 5 tests | 1.0h |
| §5.4–5.5 resume + worker pool | ~200 LOC | 8 tests | 1.5h |
| §6 CLI surface (--resume, --list, --status, --abort, new flags) | ~250 LOC | 15 tests | 1.0h |
| §7 GUI Migrate tab | ~500 LOC | 1 smoke | 1.5h |
| §8.4 constitution preservation re-runs (parameterization) | minimal | reuse | 0.25h |
| Documentation (CHANGELOG, BUILD_TRACKER, README) | ~50 lines docs | 0 | 0.25h |
| Demo + screenshot for the new GUI | n/a | 0 | 0.25h |
| **TOTAL** | **~1650 LOC** | **~50 tests** | **~8.75h** |

That sits in the upper half of the original 6-10h estimate from `DESIGN_PHASE_DELTA.md` §1, which is honest given that Phase 1 turned out to be ~3-4h of equivalent effort at the lower end of the estimate. Phase 2 has more axes (cross-source + resume + workers + GUI) which is where the budget goes.

This is **not** single-turn work. It splits cleanly into 3 sessions:

- **Session A (~3.5h):** §4 schema + §5.1–5.4 service extensions + §6 CLI core. Test count goes 1011 → ~1045. Net result: `curator migrate --apply --workers 4` works for same-source large jobs with persistent progress + resume. Cross-source still mocked.
- **Session B (~2.5h):** §5.3 cross-source via real `curator_source_write` (gdrive end-to-end), §6 CLI cross-source flags, real-world demo against jake@example.com gdrive. Test count ~1045 → ~1055. Net result: `curator migrate local C:/Music gdrive:jake@... /Music --apply` works end-to-end.
- **Session C (~2.5h):** §7 GUI Migrate tab + final docs + v1.1.0 release ceremony (full regression, screenshot, BUILD_TRACKER write-up, CHANGELOG closure, version bump 1.1.0a1 → 1.1.0). Test count ~1055 → ~1062.

Each session ends with a clean commit. The `1.1.0a1` alpha tag remains the anchor; no intermediate alphas needed (a1 → 1.1.0 final is fine for a feature work cycle).

---

## 11. Open questions and Phase 3+ deferrals

- **Bandwidth throttling and quota-aware retry.** Cross-source migrations against gdrive's free-tier quota (15 GB) and rate limits (~1000 requests / 100 seconds / user) can fail mid-run with retryable errors. Phase 2 implements "retry once, then mark FAILED." Phase 3+: implement exponential backoff with Retry-After header parsing, integrate with gdrive plugin's existing rate-limit awareness.
- **Conflict resolution beyond skip.** When dst exists, Phase 2 skips. Phase 3+: `--on-conflict=overwrite-with-backup | rename-with-suffix | skip | fail`.
- **Migration of source configs as a first-class operation.** Today: out of scope. Phase 3+: a `curator sources migrate <old_id> <new_id>` command that handles credential transfer, path remapping, and bulk file migration as one atomic unit.
- **Migration scheduling.** "Run this migration every Sunday at 3 AM." Out of scope; users can wrap `curator migrate` in their own cron / Task Scheduler.
- **Cross-machine migration.** "Move my Curator index from this laptop to the new one." Distinct from cross-source (which is one Curator instance moving files between sources). Cross-machine is a Curator-DB-level operation, not a Tracer operation. Belongs in Feature U (Update protocol) or its own Feature.
- **`curator sources prune --empty`.** Not Phase 2. v1.2.0 candidate.
- **`curator migrate-cleanup-orphans <job_id>`.** Not Phase 2. v1.2.0 candidate, only if real-world resume runs surface the un-trashed-source edge case enough to justify a tool.

---

## 12. Cross-references

- `Atrium\CONSTITUTION.md` — supreme authority. Principles 2 (Hash-Verify-Before-Move), 4 (No Silent Failures), 5 (Atomic Operations) directly govern Phase 2.
- `Atrium\NAMES.md` — Tracer brand (§3 of NAMES). Phase 2 user-facing copy uses "Tracer."
- `DESIGN.md` — v1.0 spec. §6 (source plugin contract) is the foundation Phase 2 relies on for cross-source. §11 (storage layer) is where the new tables register.
- `DESIGN_PHASE_DELTA.md` — §M.1–M.8 is the Phase 2 feature spec. This document supersedes §M.8 with explicit recommendations.
- `BUILD_TRACKER.md` — v1.1.0a1 entry documents Phase 1 mid-build catches that Phase 2 should remember (LineageEdge.edge_kind not .kind, FileQuery API, %LOCALAPPDATA% safety verdict, Unicode arrows in Typer docstrings, console/err helper pattern).
- `Atrium\INSTALL_PATH_DESIGN.md` — Phase 2 runtime data (job rows, progress rows) lives in the existing curator.db, which lives at `<install_root>\Curator\runtime\curator.db` per the install path design. No new data files outside the DB.

---

## 13. Revision log

- **2026-05-08 v0.1** — first issued. Captures: (1) explicit scope and Phase 3 deferrals, (2) Phase 1 invariants that must be preserved, (3) DM-1 through DM-5 resolutions awaiting Jake's ratification, (4) full schema + repository design, (5) service architecture for cross-source + resume + workers, (6) complete CLI flag matrix, (7) GUI Migrate tab wireframe, (8) test strategy targeting ~1062 total tests, (9) schema migration plan, (10) honest ~8.75h effort estimate split across 3 sessions.
