# Tracer Phase 3 Design

**Status:** v0.2 — RATIFIED 2026-05-08. Jake ratified all 6 DMs as recommended (`1` reply against the v0.1 DRAFT skeleton's wishlist; same `ratify`-default convention used by all prior atrium-* / PLUGIN_INIT / CURATOR_AUDIT_EVENT / MCP_SERVER plans). Implementation cleared to begin. P1 lands as Curator v1.3.0 with the retry decorator + integration; P2 adds conflict resolution; P3 is documentation + release ceremony. See §5 for the implementation plan and §12 revision log for the v0.2 entry. Earlier state preserved: v0.1 DRAFT 2026-05-08 (skeleton with 6 DM recommendations).
**Date:** 2026-05-08
**Authority:** Subordinate to `Atrium\CONSTITUTION.md`. Implements `docs/TRACER_PHASE_2_DESIGN.md` §11 deferral items 1 (quota-aware retry) + 2 (conflict resolution beyond skip).
**Companion documents:**
- `docs/TRACER_PHASE_2_DESIGN.md` v0.3 IMPLEMENTED — the v1.1.0 stable foundation Phase 3 builds on.
- `Curator/src/curator/services/migration.py` — the file Phase 3 modifies (~73 KB at v1.2.0). All code-touchpoint claims in §4 are verified by direct reading at v0.2 issuance.
- `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.3 IMPLEMENTED — version-line collision (originally claimed v1.3.0 for HTTP-auth) resolved by DM-6: Phase 3 claims v1.3.0, MCP HTTP-auth pushes to v1.4.0. Cross-reference updated in MCP design.
- `Atrium\NAMES.md` — Tracer brand.
- `CHANGELOG.md` — v1.1.0 entry lists the Phase 3+ deferral set this design closes.

---

## 1. Scope

### 1.1 What Phase 3 ships

Phase 3 closes Tracer Phase 2's two highest-value deferrals against actual `MigrationService` code:

1. **Quota-aware retry with exponential backoff.** v1.2.0 has *no retry logic at all* (verified 2026-05-08 — zero `retry`/`429`/`exponential` references in `migration.py`). Cross-source migrations against gdrive's rate limits (~1000 req/100s) fail on the first transient 429. Phase 3 adds a retry decorator wrapping `_cross_source_transfer` (and `_execute_one_same_source` for local-FS transient errors), with `--max-retries N` CLI flag (default 3), exponential backoff capped at 60s, and Retry-After header parsing.
2. **Conflict resolution beyond skip.** v1.2.0's hard-coded `SKIPPED_COLLISION` branch in `_execute_one` is correct-but-limiting. Phase 3 adds three new conflict modes via `--on-conflict={skip,fail,overwrite-with-backup,rename-with-suffix}`, three new `MigrationOutcome` enum values, and a `migration.conflict_resolved` audit action.

Combined estimated effort: ~5h across 3 sessions (mirrors Phase 2's 3-session shape).

Phase 3 ships as **Curator v1.3.0** (new minor; new `MigrationOutcome` enum values + new audit actions are new public surface).

### 1.2 What Phase 3 does NOT ship

The full deferral list from `TRACER_PHASE_2_DESIGN.md` §11 contains 7 items. Phase 3 ships items 1 and 2 (above). Other items remain deferred:

- **Source config migration** (`curator sources migrate <old_id> <new_id>`). Different beast — needs new CLI command + credential transfer + path remapping. Belongs in Feature S; its own design cycle.
- **Cross-machine migration.** Curator-DB-level operation, not a Tracer operation. Belongs in Feature U (Update protocol).
- **Migration scheduling** (cron-style automation). Permanent skip per Phase 2 design; users wrap `curator migrate` in their own cron / Task Scheduler.
- **`curator sources prune --empty`.** Small standalone CLI; doesn't need a "Phase 3 of Tracer" framing. v1.2.0+ candidate as its own feature.
- **`curator migrate-cleanup-orphans <job_id>`.** Conditional ("only if real-world resume runs surface the un-trashed-source edge case enough"). v1.2.0+ candidate.
- **Multi-destination migration** (copy to 2+ dst atomically). Out of scope for v1.x; revisit in v2.0.
- **Migration rollback** (undo a completed job). Out of scope; the audit log + lineage make manual rollback possible if rare.
- **Compression-aware transfer** (compress in-flight to cloud). Out of scope; cloud SDKs handle this internally.

---

## 2. Phase 2 invariants that MUST be preserved

The 7 invariants from `TRACER_PHASE_2_DESIGN.md` §2 are non-negotiable. Phase 3 must demonstrably preserve each:

1. **`curator_id` constancy** across moves.
2. **Hash-Verify-Before-Move** (Constitution Principle 2).
3. **No Silent Failures** (Constitution Principle 4) — every retry attempt + every conflict-resolution outcome must be auditable.
4. **DB-guard** (curator.db never migrated).
5. **Audit per move.** Phase 3 ADDS `migration.retry` and `migration.conflict_resolved` actions to the existing `migration.move` / `migration.copy` set.
6. **Plan/apply two-phase pattern** — no mutations without `--apply`. Phase 3's new flags (`--max-retries`, `--on-conflict`) all gate behind `--apply`.
7. **CAUTION/REFUSE skip by default.**

Plus a new Phase-3-specific invariant:

8. **Retries don't double-mutate.** A retry of a partially-written destination must clean up the partial first (delete the half-written file via `pm.hook.curator_source_delete` for cross-source, `Path.unlink` for same-source), THEN re-execute the per-file algorithm. No "retry by appending."

---

## 3. DM resolutions

All 6 DMs ratified by Jake on 2026-05-08 against the v0.1 DRAFT recommendations.

### DM-1 — Retry primary trigger

**Question.** What conditions trigger a retry attempt?

**Resolution.** **HTTP 4xx-aware conservative retry.** Retryable error classes:

- `googleapiclient.errors.HttpError` with `resp.status in (403, 429, 500, 502, 503, 504)`. The 403 inclusion handles gdrive's "User Rate Limit Exceeded" which surfaces as 403 (not 429) for some endpoints.
- `requests.exceptions.ConnectionError` (transient TCP failures).
- `requests.exceptions.Timeout`, `socket.timeout` (read/connect timeouts).
- `urllib3.exceptions.ProtocolError` (mid-connection drops, common on gdrive).

Non-retryable (fail-fast):
- `OSError` / `IOError` on the local FS — these indicate disk full, permission denied, or corruption. Retrying won't help; user intervention is needed.
- `HashMismatchError` — verification failed. Can't be transient. Constitution Principle 2 requires immediate FAILED.
- `MigrationDestinationNotWritable` — plugin-level rejection. Plan-time error; should never surface during apply.
- Any other Exception subclass — fail-fast keeps the retry loop conservative.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake.

### DM-2 — Retry budget

**Question.** How many retries per file before marking FAILED?

**Resolution.** **`--max-retries=3` default, exponential backoff capped at 60s, Retry-After header respected when present.**

Backoff algorithm:
```
attempt 1: try; on retryable error: sleep min(60, base * 2^0) = 1s, then retry
attempt 2: try; on retryable error: sleep min(60, base * 2^1) = 2s, then retry
attempt 3: try; on retryable error: sleep min(60, base * 2^2) = 4s, then retry
attempt 4 (final): try; on retryable error: mark FAILED with err message
```

If the response includes a `Retry-After` header (gdrive sometimes provides one with 429s), the backoff sleep becomes `max(exponential_backoff, retry_after_seconds)` capped at 60s. This handles the common case where the server explicitly tells us to wait longer than our default schedule would.

`--max-retries=0` disables retry (immediate FAILED on first error). Useful for fast feedback during testing.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake.

### DM-3 — Where the retry loop lives

**Question.** Inside `_cross_source_transfer` (per-file), or in the worker loop wrapping the whole `_execute_one`?

**Resolution.** **Per-file, inside the transfer function.** Implemented as a decorator wrapping `_cross_source_transfer` AND `_execute_one_same_source`'s I/O-bearing inner block (the actual `shutil.copy2` call + the post-copy hash re-read). The retry decorator is in a new module `src/curator/services/migration_retry.py` so the retry logic is testable in isolation.

Reasons not chosen:
- **Worker-loop retry** would re-execute the entire `_execute_one` (plan validation, safety check, hash, copy, verify, index update, audit). Retrying steps that aren't I/O-bound (the safety check, the index update) wastes time and pollutes the audit log with duplicate `migration.move` entries on partial success.
- **Job-level retry** (resume an aborted job) already exists and handles longer-timescale failures. Per-file retry handles transient mid-call failures without bloating job state.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake.

### DM-4 — Conflict resolution semantics

**Question.** What modes does `--on-conflict={...}` support?

**Resolution.** **Four modes:**

- **`skip` (default).** Existing v1.2.0 behavior. Move marked `SKIPPED_COLLISION`. No bytes written.
- **`fail`.** First conflict aborts the job with a fatal error. Useful for "I expect a clean destination; if anything's there, something's wrong." Job status becomes `failed` with `error="conflict at <dst_path>"`. Per-file outcome `FAILED_DUE_TO_CONFLICT`.
- **`overwrite-with-backup`.** Existing dst is renamed to `<name>.curator-backup-<UTC-iso8601-timestamp><ext>` before the new copy is written. Backup files persist indefinitely (user-driven cleanup; MigrationService never auto-trashes them). Per-file outcome `MOVED_OVERWROTE_WITH_BACKUP`. Audit details include `backup_path`.
- **`rename-with-suffix`.** New file is written as `<name>.curator-<n><ext>` where `n` is the lowest available positive integer such that `<name>.curator-<n><ext>` doesn't exist. Existing dst is preserved unchanged. Per-file outcome `MOVED_RENAMED_WITH_SUFFIX`. Audit details include `final_dst_path` and `suffix_n`.

Three new `MigrationOutcome` enum values: `MOVED_OVERWROTE_WITH_BACKUP`, `MOVED_RENAMED_WITH_SUFFIX`, `FAILED_DUE_TO_CONFLICT`.

Conflict resolution applies at the same point in the per-file algorithm as the v1.2.0 SKIPPED_COLLISION check (right after dst-existence is detected, before the copy starts). The mode determines which branch executes.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake.

### DM-5 — Companion features

**Question.** Does Phase 3 ship #6 (`sources prune --empty`) and #7 (`migrate-cleanup-orphans`) alongside?

**Resolution.** **Split them out.** Phase 3 stays focused on retry + conflict resolution. Both companion features remain v1.2.0+ candidates for separate small-feature releases.

Rationale: Phase 3's primary scope (DM-1 through DM-4) is already ~5h; adding companions blurs focus and inflates testing burden. `sources prune --empty` and `migrate-cleanup-orphans` are each ~30 min of independent work — a single shipping window each, separately.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake.

### DM-6 — Versioning

**Question.** Phase 3 ships under what version line?

**Resolution.** **Curator v1.2.0 → v1.3.0 (minor bump).**

New public surface:
- 3 new `MigrationOutcome` enum values (`MOVED_OVERWROTE_WITH_BACKUP`, `MOVED_RENAMED_WITH_SUFFIX`, `FAILED_DUE_TO_CONFLICT`). Third-party code consuming `MigrationOutcome` (e.g., the GUI Migrate tab's outcome display) sees new variants.
- 2 new audit log actions (`migration.retry`, `migration.conflict_resolved`). Third-party code reading the audit log via `query_audit_log` sees new action strings.
- 2 new CLI flags (`--max-retries`, `--on-conflict`).

These are additive-but-meaningful changes. Honest semver = minor bump.

**Version-line collision resolution:** `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.3 IMPLEMENTED previously claimed v1.3.0 for HTTP transport authentication. Phase 3 claims v1.3.0; **MCP HTTP-auth pushes to v1.4.0**. The MCP design doc gets a small cross-reference update as part of P3.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake.

---

## 4. Spec

### 4.1 New `MigrationOutcome` enum values

```python
class MigrationOutcome(str, Enum):
    # Phase 1 + 2 (existing, unchanged):
    MOVED = "moved"
    COPIED = "copied"
    SKIPPED_NOT_SAFE = "skipped_not_safe"
    SKIPPED_COLLISION = "skipped_collision"
    SKIPPED_DB_GUARD = "skipped_db_guard"
    HASH_MISMATCH = "hash_mismatch"
    FAILED = "failed"
    # Phase 3 (NEW):
    MOVED_OVERWROTE_WITH_BACKUP = "moved_overwrote_with_backup"
    MOVED_RENAMED_WITH_SUFFIX = "moved_renamed_with_suffix"
    FAILED_DUE_TO_CONFLICT = "failed_due_to_conflict"
```

`MigrationReport.moved_count()` is updated to count `MOVED_OVERWROTE_WITH_BACKUP` and `MOVED_RENAMED_WITH_SUFFIX` alongside `MOVED` and `COPIED` (these are all "successfully migrated" outcomes from the user's perspective). `failed_count()` is updated to include `FAILED_DUE_TO_CONFLICT` alongside `FAILED` and `HASH_MISMATCH`.

### 4.2 New CLI flags

```
curator migrate ...
  --max-retries N           # default 3; 0 = no retries; max 10 (sanity cap)
  --on-conflict MODE        # skip (default) | fail | overwrite-with-backup | rename-with-suffix
```

Both flags are stored in the persisted job's `options_json` (the existing forward-compatibility column), so resumes pick up the original mode automatically.

### 4.3 New audit actions

**`migration.retry`** — fires on each retry attempt. Details schema:
```json
{
  "src_path": "...",
  "dst_path": "...",
  "attempt": 2,
  "max_retries": 3,
  "error_class": "HttpError",
  "error_message": "...",
  "backoff_seconds": 2.0,
  "retry_after_header": null
}
```

**`migration.conflict_resolved`** — fires when a non-`skip` conflict mode runs (i.e., when conflict mode would have changed v1.2.0's behavior). Details schema:
```json
{
  "src_path": "...",
  "original_dst_path": "...",
  "mode": "overwrite-with-backup",
  "backup_path": "/.../foo.curator-backup-2026-05-08T15-30-00.txt",
  "final_dst_path": "/.../foo.txt"
}
```

For `rename-with-suffix`:
```json
{
  "src_path": "...",
  "original_dst_path": "...",
  "mode": "rename-with-suffix",
  "suffix_n": 1,
  "final_dst_path": "/.../foo.curator-1.txt"
}
```

For `fail`:
```json
{
  "src_path": "...",
  "original_dst_path": "...",
  "mode": "fail",
  "outcome": "FAILED_DUE_TO_CONFLICT"
}
```

### 4.4 Code touchpoints (verified by direct reading 2026-05-08)

| Location | Phase 3 change |
|----------|----------------|
| `src/curator/services/migration.py` `MigrationOutcome` enum | Add 3 new values per §4.1 |
| `src/curator/services/migration.py` `MigrationReport.moved_count` | Update to count new MOVED_* variants |
| `src/curator/services/migration.py` `MigrationReport.failed_count` | Update to count FAILED_DUE_TO_CONFLICT |
| `src/curator/services/migration.py` `_execute_one` | Add conflict-resolution branch BEFORE the existing SKIPPED_COLLISION return |
| `src/curator/services/migration.py` `_cross_source_transfer` | Wrap with `@retry_transient_errors` decorator from new module |
| `src/curator/services/migration.py` `_execute_one_same_source` I/O block | Wrap the `shutil.copy2` + `Path.read_bytes` calls with `@retry_transient_errors` |
| `src/curator/services/migration_retry.py` | NEW module (~120 LOC) — see §4.5 |
| `src/curator/cli/main.py` `migrate` command | Add `--max-retries N` and `--on-conflict MODE` Typer options |

### 4.5 New module: `src/curator/services/migration_retry.py`

```python
"""Retry decorator for Tracer Phase 3 transient-error recovery.

See docs/TRACER_PHASE_3_DESIGN.md v0.2 §3 DM-1, DM-2, DM-3.

Wraps I/O-bearing inner functions in `_cross_source_transfer` and
`_execute_one_same_source` with exponential-backoff retry against a
conservative error whitelist (HTTP 4xx/5xx for cloud sources;
ConnectionError/Timeout for local). Emits `migration.retry` audit
events on each retry attempt. Honors Retry-After headers when present.
"""

from __future__ import annotations

import socket
import time
from datetime import datetime
from functools import wraps
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from curator.services.migration import MigrationService

# Retryable error classes -- conservative whitelist per DM-1
_RETRYABLE_HTTP_STATUSES = frozenset({403, 429, 500, 502, 503, 504})


def _is_retryable(exc: Exception) -> tuple[bool, float | None]:
    """Returns (is_retryable, retry_after_seconds_if_any).

    The Retry-After value, if present, is parsed and returned for the
    caller to incorporate into backoff.
    """
    # gdrive / generic googleapiclient HttpError
    try:
        from googleapiclient.errors import HttpError  # type: ignore
        if isinstance(exc, HttpError):
            status = getattr(exc.resp, "status", 0)
            if status in _RETRYABLE_HTTP_STATUSES:
                # Try to parse Retry-After
                retry_after = exc.resp.get("retry-after") if exc.resp else None
                if retry_after:
                    try:
                        return True, float(retry_after)
                    except (ValueError, TypeError):
                        pass
                return True, None
            return False, None
    except ImportError:
        pass

    # Generic transient errors
    try:
        import requests
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True, None
    except ImportError:
        pass

    if isinstance(exc, socket.timeout):
        return True, None

    try:
        import urllib3.exceptions
        if isinstance(exc, urllib3.exceptions.ProtocolError):
            return True, None
    except ImportError:
        pass

    return False, None


def retry_transient_errors(
    *,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    backoff_cap: float = 60.0,
) -> Callable:
    """Decorator: retry the wrapped function on retryable errors.

    Args:
        max_retries: Number of retry ATTEMPTS (so total attempts is
            max_retries + 1: one initial + max_retries retries).
            0 disables retry. Capped at 10 by Tracer's CLI.
        backoff_base: Base of exponential backoff (seconds).
            Sleep duration on attempt N is min(backoff_cap, base * 2^(N-1))
            unless a Retry-After header overrides.
        backoff_cap: Maximum backoff sleep regardless of formula or header.

    The decorated function MUST be a method on MigrationService (or a
    callable accepting `self` as first arg) so the audit_repo is
    reachable for emitting `migration.retry` events.
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(self: "MigrationService", *args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(self, *args, **kwargs)
                except Exception as e:  # noqa: BLE001
                    is_retryable, retry_after = _is_retryable(e)
                    if not is_retryable:
                        raise  # fail-fast for non-retryable errors
                    last_exc = e
                    if attempt >= max_retries:
                        # Out of retries; re-raise to mark FAILED
                        raise
                    # Compute backoff
                    exponential = min(backoff_cap, backoff_base * (2 ** attempt))
                    sleep_for = max(exponential, retry_after or 0.0)
                    sleep_for = min(sleep_for, backoff_cap)
                    # Audit the retry attempt
                    try:
                        self.audit_repo.log(
                            actor="curator.migrate",
                            action="migration.retry",
                            details={
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "error_class": type(e).__name__,
                                "error_message": str(e)[:500],
                                "backoff_seconds": sleep_for,
                                "retry_after_header": retry_after,
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass  # never let audit failure block retry
                    time.sleep(sleep_for)
            # Should be unreachable; raise the last exception defensively
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("retry_transient_errors: unreachable")
        return wrapper
    return decorator
```

### 4.6 Conflict resolution in `_execute_one`

The current v1.2.0 `_execute_one` checks `dst_path.exists()` and returns `(MigrationOutcome.SKIPPED_COLLISION, dst_path, None)` if so. Phase 3 replaces this single branch with a mode-dispatched decision tree:

```python
# (inside _execute_one, just after dst-existence check)
if dst_path.exists():
    mode = self._on_conflict_mode  # populated from options at job creation
    if mode == "skip":
        return (MigrationOutcome.SKIPPED_COLLISION, dst_path, None)
    elif mode == "fail":
        self._audit_conflict(src, dst_path, mode, outcome="failed")
        raise MigrationConflictError(
            f"Destination exists: {dst_path}. on-conflict=fail."
        )
    elif mode == "overwrite-with-backup":
        backup_path = self._compute_backup_path(dst_path)
        dst_path.rename(backup_path)  # atomic on same FS
        self._audit_conflict(src, dst_path, mode,
                             backup_path=backup_path,
                             final_dst_path=dst_path)
        # fall through to normal copy logic; outcome upgraded later
        self._this_move_outcome_override = MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP
    elif mode == "rename-with-suffix":
        original_dst = dst_path
        dst_path, suffix_n = self._find_available_suffix(dst_path)
        self._audit_conflict(src, original_dst, mode,
                             suffix_n=suffix_n,
                             final_dst_path=dst_path)
        self._this_move_outcome_override = MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX
    else:
        raise ValueError(f"unknown on-conflict mode: {mode}")
# (continue to the existing copy logic)
```

Helper functions:

```python
def _compute_backup_path(dst_path: Path) -> Path:
    """Generate <name>.curator-backup-<UTC-iso8601><ext>."""
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    stem = dst_path.stem
    suffix = dst_path.suffix
    return dst_path.with_name(f"{stem}.curator-backup-{ts}{suffix}")


def _find_available_suffix(dst_path: Path) -> tuple[Path, int]:
    """Find the lowest n such that <name>.curator-<n><ext> doesn't exist."""
    stem = dst_path.stem
    suffix = dst_path.suffix
    parent = dst_path.parent
    n = 1
    while True:
        candidate = parent / f"{stem}.curator-{n}{suffix}"
        if not candidate.exists():
            return candidate, n
        n += 1
        if n > 9999:
            raise MigrationConflictError(
                f"Exhausted suffix range for {dst_path}; "
                f"too many existing curator-N variants."
            )
```

`_audit_conflict()` emits the `migration.conflict_resolved` audit event with the appropriate details schema from §4.3.

### 4.7 Cross-source path retry integration

`_cross_source_transfer` is decorated:

```python
@retry_transient_errors(max_retries=3, backoff_cap=60.0)  # defaults; runtime values come from job options
def _cross_source_transfer(
    self,
    src_source_id: str,
    src_path: str,
    dst_source_id: str,
    dst_path: str,
    src_xxhash: str,
) -> tuple[bytes, str]:
    """Existing v1.2.0 implementation; retry now wraps the whole thing."""
    ...
```

For per-job customization (when `--max-retries` is set on the CLI), the decorator's defaults are overridden via a runtime parameter pulled from `self._max_retries` (set at job creation from `options_json`). Implementation detail: the decorator accepts a callable for `max_retries` so it can read from `self` lazily:

```python
@retry_transient_errors(max_retries=lambda self: self._max_retries, ...)
def _cross_source_transfer(self, ...):
    ...
```

The lazy form is mildly more complex but keeps the retry policy job-scoped instead of process-scoped. v0.2 ships the lazy form; v0.1 of the design used the static form for simplicity in the skeleton.

---

## 5. Implementation plan

Three sessions, ~5h total.

### P1 — Curator v1.2.0 + retry decorator (~2h)

**Scope:** §4.5 new module + §4.4 wrapping of `_cross_source_transfer` and `_execute_one_same_source`'s I/O block + §4.3 `migration.retry` audit emission + ~10 unit tests.

**Steps:**

1. Create `src/curator/services/migration_retry.py` with the `retry_transient_errors` decorator + `_is_retryable` helper.
2. Wrap `_cross_source_transfer` in `migration.py` with the decorator. Lazy `max_retries` form so per-job options work.
3. Wrap the I/O-bearing inner block of `_execute_one_same_source` similarly.
4. Add `_max_retries` instance attribute to `MigrationService`; populate from `options_json` at `run_job` start.
5. Add `--max-retries N` Typer option to `curator migrate` CLI.
6. Add new tests in `tests/unit/test_migration_phase3_retry.py`:
   - `test_retry_decorator_no_failure_passes_through` (no retry path)
   - `test_retry_decorator_retryable_error_then_success` (1 retry succeeds)
   - `test_retry_decorator_max_retries_then_fail` (3 retries exhausted → raises)
   - `test_retry_decorator_non_retryable_error_immediate_fail` (no retry on OSError)
   - `test_retry_decorator_respects_retry_after_header` (Retry-After overrides backoff)
   - `test_retry_decorator_audit_logs_each_attempt` (3 retries → 3 audit entries)
   - `test_max_retries_zero_disables_retry`
   - `test_max_retries_capped_at_10`
   - `test_cross_source_transfer_retries_on_429` (integration with mock plugin)
   - `test_same_source_retries_on_connection_error` (integration with mocked I/O)

**P1 acceptance:** Cross-source migrations against a flaky source plugin (mock with 429s every 3rd request) reliably complete; audit log shows the retry trail.

**Test count:** Curator slice +10 (~414 → ~424).

### P2 — Conflict resolution (~2h)

**Scope:** §4.1 new enum values + §4.2 `--on-conflict` CLI flag + §4.6 conflict-resolution branch in `_execute_one` + §4.3 `migration.conflict_resolved` audit emission + ~12 unit tests + ~3 integration tests.

**Steps:**

1. Add 3 new `MigrationOutcome` enum values per §4.1.
2. Update `MigrationReport.moved_count` and `failed_count` to include the new variants.
3. Add `_on_conflict_mode` instance attribute; populate from `options_json` at `run_job` start.
4. Implement `_compute_backup_path` and `_find_available_suffix` helpers.
5. Implement `_audit_conflict` helper.
6. Replace the `SKIPPED_COLLISION` branch in `_execute_one` with the dispatch tree from §4.6.
7. Define `MigrationConflictError` exception class.
8. Add `--on-conflict MODE` Typer option (with Click choice validation: `["skip", "fail", "overwrite-with-backup", "rename-with-suffix"]`).
9. Add new tests in `tests/unit/test_migration_phase3_conflict.py`:
   - `test_skip_mode_preserves_v1_2_0_behavior` (default unchanged)
   - `test_fail_mode_raises_on_first_conflict`
   - `test_fail_mode_records_FAILED_DUE_TO_CONFLICT_outcome`
   - `test_overwrite_with_backup_creates_backup_file`
   - `test_overwrite_with_backup_filename_format` (timestamp + extension preservation)
   - `test_overwrite_with_backup_proceeds_with_normal_copy`
   - `test_rename_with_suffix_finds_lowest_available_n`
   - `test_rename_with_suffix_preserves_existing_dst`
   - `test_rename_with_suffix_increments_past_existing_curator_n_files`
   - `test_audit_conflict_emits_correct_details_for_each_mode`
   - `test_audit_conflict_includes_backup_path_for_overwrite_mode`
   - `test_audit_conflict_includes_suffix_n_for_rename_mode`
10. Add integration tests in `tests/integration/test_cli_migrate_phase3.py`:
    - `test_cli_on_conflict_overwrite_with_backup_end_to_end`
    - `test_cli_on_conflict_rename_with_suffix_end_to_end`
    - `test_cli_on_conflict_fail_aborts_job`

**P2 acceptance:** All four conflict modes work end-to-end against a seeded DB with collisions; audit log records mode-specific events.

**Test count:** Curator slice +15 (~424 → ~439).

### P3 — Documentation + release ceremony (~1h)

**Scope:** README "Tracer Phase 3" subsection + `docs/TRACER_PHASE_3_DESIGN.md` v0.2 → v0.3 IMPLEMENTED stamp + `docs/CURATOR_MCP_SERVER_DESIGN.md` v1.3.0→v1.4.0 cross-reference update + CHANGELOG v1.3.0 entry + version bump + tag + push.

**Steps:**

1. Update `README.md`:
   - Status line: v1.2.0 → v1.3.0; mention retry + conflict resolution.
   - CHANGELOG list: add v1.3.0.
   - Documentation list: add `TRACER_PHASE_3_DESIGN.md` v0.3 IMPLEMENTED.
   - In the "Migration (Tracer)" section (or a new subsection): document `--max-retries` and `--on-conflict` flags with examples.
2. Update `docs/CURATOR_MCP_SERVER_DESIGN.md`:
   - Single-line cross-reference fix: change "MCP HTTP-auth was also planned for v1.3.0" → "MCP HTTP-auth was originally planned for v1.3.0; pushed to v1.4.0 by Tracer Phase 3 ratification 2026-05-08."
3. Stamp `docs/TRACER_PHASE_3_DESIGN.md` v0.2 → v0.3 IMPLEMENTED with full revision-log entry covering both P1 and P2 commit hashes, test counts, and any signature-mismatch lessons.
4. Add `## [1.3.0]` entry to `CHANGELOG.md` (~50 lines).
5. Bump version 1.2.0 → 1.3.0 in `pyproject.toml` and `src/curator/__init__.py`.
6. Final regression sweep: full Curator slice + plugin suite green.
7. Commit, tag `v1.3.0`, push both.

**P3 acceptance:** Design doc at v0.3 IMPLEMENTED; README reflects v1.3.0; tag pushed; full test suite green.

**Test count:** No new tests in P3.

### Total

| Session | LOC | Tests | Hours |
|---------|-----|-------|-------|
| P1 retry | ~250 | +10 | 2.0h |
| P2 conflict | ~280 | +15 | 2.0h |
| P3 docs + release | ~80 docs | 0 | 1.0h |
| **TOTAL** | **~610 LOC + ~80 docs** | **+25** | **~5h** |

Curator regression slice goal at v1.3.0 release: **414 → ~439** (+25).
Plugin suite (atrium-safety v0.3.0): **75/75 unchanged** (no plugin-side changes).

---

## 6. Schema and state changes

**No schema changes.** Phase 3 is purely additive at the code surface; the existing `migration_jobs.options_json` column already accommodates new flag values (per Phase 2's forward-compat design).

The new `MigrationOutcome` enum values are stored as strings in the existing `migration_progress.outcome` column. Existing rows with old values continue to deserialize correctly. New rows can store any of the 10 enum values.

The `migration.retry` and `migration.conflict_resolved` audit actions are new strings in the existing `audit_log.action` column. No schema change.

---

## 7. Test strategy

Total estimated new tests: **~25** (10 P1 + 15 P2). Final test count target: ~439 in the Curator regression slice.

### 7.1 Retry tests (P1, ~10)

Located in `tests/unit/test_migration_phase3_retry.py`. The `_is_retryable` helper is tested independently against a matrix of error classes; the `retry_transient_errors` decorator is tested with synthetic functions that raise N times before succeeding.

Real cross-source retry behavior is tested via a mock source plugin that fails with synthetic `HttpError(resp=Resp(status=429))` on the first 2 attempts, succeeds on the 3rd. End-to-end: a migration of 5 files completes with 5 successful moves and 10 retry audit entries (2 retries × 5 files).

### 7.2 Conflict tests (P2, ~15)

Located in `tests/unit/test_migration_phase3_conflict.py` (unit) and `tests/integration/test_cli_migrate_phase3.py` (integration). Per-mode tests seed a DB with files at the destination and exercise each conflict mode against same-source local→local migrations (where the test can directly inspect the FS state post-apply).

Edge cases:
- `rename-with-suffix` with existing `<name>.curator-1.txt`, `<name>.curator-2.txt` → suffix=3.
- `overwrite-with-backup` preserves backup file's mtime as the original's mtime (for archival purposes).
- `fail` mode aborts the JOB (sets job status to `failed`), not just the per-file outcome — verified by querying `migration_jobs` after.

### 7.3 Constitution preservation tests

Existing Phase 1 + Phase 2 tests in `tests/unit/test_migration.py`, `test_migration_phase2.py`, `test_migration_cross_source.py` keep passing unchanged. The retry decorator is a transparent wrapper for the no-failure case; the conflict dispatch defaults to `skip` (existing behavior).

---

## 8. Backward compatibility

Phase 3 is **strictly additive** at the user-facing surface:

- ✅ Existing `curator migrate ... --apply` invocations: unchanged behavior. `--max-retries` defaults to 3 (was effectively 0 before; this is a behavior change in the failure path, but ALL retries trigger only on errors that previously caused FAILED, so no successful-migration outcome changes).
- ✅ Existing `MigrationOutcome` consumers: see new enum values they didn't previously enumerate. Code that switch-cases on outcome values needs to handle the new variants OR fall through to a default. Reasonable consumer code defaults the unknown to "completed/successful" when the value starts with `MOVED_*`. The GUI Migrate tab is updated as part of P2 to display the new outcomes.
- ✅ Existing audit log readers: see new action strings. Code filtering by exact action match (`action='migration.move'`) is unaffected. Code wildcard-matching `migration.*` sees the new `migration.retry` and `migration.conflict_resolved` events; this is the intended behavior.
- ✅ `migration_jobs` and `migration_progress` schemas: unchanged.
- ✅ `options_json` field: new keys (`max_retries`, `on_conflict_mode`) are added at job creation; old jobs without these keys default to `max_retries=3` and `on_conflict_mode='skip'` at resume.
- ✅ Cross-source plugin contract: unchanged. Plugins don't need to know about retry — the decorator wraps caller-side.

**Resume behavior across the Phase 2 → Phase 3 upgrade:** A v1.2.0 user who initiated a job on v1.2.0, killed the process, upgraded to v1.3.0, and runs `--resume` gets the v1.3.0 default behavior (retry=3, conflict=skip) for the remainder of the job. No partial-migration corruption — the per-file algorithm is idempotent up to `mark_completed`.

---

## 9. Cross-references

- `docs/TRACER_PHASE_2_DESIGN.md` v0.3 IMPLEMENTED — the foundation. §1.2 + §11 are the deferral lists Phase 3 closes.
- `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.3 IMPLEMENTED — version-line collision resolved in DM-6.
- `docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — sibling Curator-side plumbing design.
- `docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — `migration.retry` and `migration.conflict_resolved` events use the audit channel established here.
- `Atrium\CONSTITUTION.md` — Principles 2, 4, 5 govern Phase 3 same as Phase 2.
- `DESIGN_PHASE_DELTA.md` §M.1–M.8 — original Feature M spec.

---

## 10. Estimated effort

Captured inline in §5. Summary: ~5h across 3 sessions, ~610 LOC + ~80 lines of docs, +25 tests in the Curator regression slice.

---

## 11. Open questions and Phase 4+ deferrals

Items NOT in Phase 3, parked for future cycles:

- **Bandwidth throttling beyond retry-on-quota.** Phase 3's retry-on-429 handles rate-limit responses reactively. A proactive bandwidth limiter (e.g., `--max-bandwidth 5MB/s`) is a future enhancement for users on metered connections. Phase 4+ candidate.
- **Per-source retry policy.** Currently `--max-retries` applies globally to a job. A user migrating from gdrive (high failure rate) to onedrive (low failure rate) might want different retry budgets per leg. Out of scope for Phase 3.
- **Conflict resolution at scale.** `overwrite-with-backup` accumulates backup files indefinitely. A future `curator migrate-cleanup-backups <job_id>` utility could trash backup files older than N days. v1.2.0+ candidate, separate small feature.
- **Retry observability.** `query_audit_log(action='migration.retry')` works but doesn't aggregate. A future "migration retry summary" report (per-job retry distribution, longest backoff seen, etc.) would be useful. Out of scope.
- **Async retry.** Current retry is synchronous (worker thread blocks). For very long backoffs (cap=60s) on many failed files, this could slow throughput. Async/await refactor is a future v2.0 candidate.

---

## 12. Revision log

- **2026-05-08 v0.1** — DRAFT. Skeleton-only at this version. Captured: §1 the 7 deferral candidates from Phase 2 §11 with code-touchpoint verification (which two are Tracer-scoped + which aren't), §1.1 recommended primary scope (items 1 + 2: quota-aware retry + conflict resolution), §1.2 optional companions (items 6, 7), §1.3 explicit non-scope, §2 invariants from Phase 2 that must be preserved + 1 new Phase-3-specific invariant (retries don't double-mutate), §3 six DMs needing Jake's ratification with recommendations, §4 high-level spec sketch, §5 three-session implementation plan sketch, §6 cross-references. Implementation NOT cleared at v0.1; awaited Jake's ratification of DM-1 through DM-6. Code-grounded discoveries logged: (a) Phase 2 design's claim of "retry once on transient error" was DEFERRED WITHOUT IMPLEMENTATION — zero retry/429/exponential references in `migration.py` at v1.2.0; this is a real gap, not just a Phase 3 enhancement, (b) the SKIPPED_COLLISION branch is a single hard-coded return in `_execute_one`, an obvious surgical hook for the new `--on-conflict` flag, (c) the retry decorator can wrap `_cross_source_transfer` without changing any caller. Skeleton ratified as commit `27b9838` (3 commits past `v1.2.0`).

- **2026-05-08 v0.2** — RATIFIED. Jake ratified all 6 DM recommendations (`1` reply against the v0.1 skeleton's wishlist; same `ratify`-default convention as all prior atrium-* / PLUGIN_INIT / CURATOR_AUDIT_EVENT / MCP_SERVER plans). Doc expanded from skeleton (~195 lines) to full design (~600 lines). Adds: §3 detailed per-DM resolution with implementation notes covering retryable error classes (HttpError 403/429/5xx + ConnectionError + Timeout + ProtocolError, fail-fast OSError/HashMismatchError/MigrationDestinationNotWritable), backoff algorithm (exponential capped at 60s + Retry-After parsing), retry-loop location (per-file decorator wrapping `_cross_source_transfer` and `_execute_one_same_source`'s I/O block, in new module `src/curator/services/migration_retry.py`), conflict modes (4 modes — skip/fail/overwrite-with-backup/rename-with-suffix), companion features deferred (items 6+7 split out), version-line resolution (Phase 3 = v1.3.0; MCP HTTP-auth pushed to v1.4.0 with single-line cross-reference fix in MCP design as part of P3), §4 full spec (3 new MigrationOutcome enum values, 2 new CLI flags, 2 new audit actions with details schemas, code touchpoints with specific function names verified by direct reading at v0.1 issuance, full pseudocode for `migration_retry.py` module ~120 LOC, conflict-resolution dispatch tree in `_execute_one` ~30 LOC, `_compute_backup_path` and `_find_available_suffix` helpers, lazy `max_retries` form for job-scoped policy), §5 three-session implementation plan with per-session steps, acceptance criteria, and test-count targets (P1 ~2h ~250 LOC +10 tests; P2 ~2h ~280 LOC +15 tests; P3 ~1h ~80 docs lines +0 tests; total ~5h ~610 LOC +25 tests; Curator slice 414 → ~439), §6 schema/state changes (NONE — strictly additive per Phase 2's forward-compat options_json design), §7 test strategy (unit + integration breakdown per session; Constitution preservation tests preserved; edge cases enumerated for `rename-with-suffix` increment logic + `overwrite-with-backup` filename format + `fail` mode job-level abort), §8 backward compatibility (strictly additive at user-facing surface; new MigrationOutcome enum variants require consumer code to handle or fall through; cross-source plugin contract unchanged; resume behavior across v1.2.0→v1.3.0 upgrade is safe via per-file algorithm idempotency up to mark_completed), §9 cross-references (including MCP design's version-line update), §10 effort summary, §11 Phase 4+ deferrals (proactive bandwidth throttling, per-source retry policy, backup-file cleanup utility, retry observability dashboards, async retry refactor — all explicit non-scope). No code has been written; v0.2 RATIFIED state means P1 implementation is cleared to begin in the next session. Next step: P1 lands as Curator v1.2.0 → v1.3.0 (P3) with retry decorator + integration; P2 adds conflict resolution; P3 stamps doc to v0.3 IMPLEMENTED.
