# Curator — `curator_audit_event` Hookspec Design

**Status:** v0.1 — DRAFT, awaiting Jake's ratification of DM-1 through DM-4. No implementation has begun. Once the DMs are ratified, this doc flips to v0.2 RATIFIED and the implementation work in §5 is cleared to start.
**Date:** 2026-05-08
**Authority:** Curator-side design. Closes the audit-channel gap that `curatorplug-atrium-safety/DESIGN.md` v0.3 §9 explicitly named as "out of scope, requires a separate design+ratification cycle". Lets plugins write structured audit entries via Curator's `AuditRepository` instead of `loguru.warning` calls.
**Companion documents:**
- `Curator\src\curator\plugins\hookspecs.py` — the file this design adds one hookspec to.
- `Curator\src\curator\plugins\manager.py` — the file that registers the new core hookimpl (the writer that persists audit events to the repo).
- `Curator\src\curator\models\audit.py` — `AuditEntry` definition. The hookspec's signature reuses these field names verbatim.
- `Curator\src\curator\storage\repositories\audit_repo.py` — `AuditRepository.insert(entry)` is what the core hookimpl calls.
- `curatorplug-atrium-safety\DESIGN.md` v0.3 §9 ("What's next") — the explicit pointer to this design from the consumer plugin.
- `Curator\docs\PLUGIN_INIT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — sibling design that shipped earlier 2026-05-08 and proves out the "Curator-side hookspec amendment to enable plugin capability" pattern this design follows.

---

## 1. Scope

### 1.1 The problem

Curator core has an `AuditRepository` and a `migration_audit` table that captures every successful `migration.move` and `migration.copy` operation. `MigrationService` writes to it directly via `self.audit.insert(entry)`. That's clean for code that owns a runtime reference; it doesn't work for plugins.

Today (`curatorplug-atrium-safety` v0.2.0), the safety plugin's enforcement decisions are observable only through:

1. `loguru.debug` / `loguru.warning` log lines (filtered out at default verbosity; not queryable).
2. The propagated `ComplianceError` message landing in `MigrationMove.error` — but only for *refusals*. Successful approvals leave no trace.
3. Side effects on the migration outcome — but those don't distinguish "migration succeeded because the plugin approved" from "migration succeeded because no enforcement plugin was installed".

This means a user running `curator audit-log query --action migration.move` sees the migration events but has no way to query "which writes did the safety plugin approve?" or "which writes did it refuse, and on what grounds?". That visibility is exactly what `curatorplug-atrium-safety/DESIGN.md` v0.2 DM-3 ratified — "Use Curator's existing audit log via `actor='curatorplug.atrium_safety'`" — but the implementation can't actually do it because plugins don't have direct repo access.

### 1.2 The general capability being added

This design adds a new hookspec — `curator_audit_event(actor, action, entity_type, entity_id, details)` — that any plugin can call to fire an audit event. Curator core implements one hookimpl that writes the event to `AuditRepository`. Other plugins are free to implement additional hookimpls (e.g., a future SIEM-streaming plugin); they all fire on the same event.

Concrete near-term consumers:

1. **`curatorplug-atrium-safety` v0.3.0** — the headline use case. Replaces `loguru` calls with structured audit events for every enforcement decision (approval, refusal, advisory warning). Emits `actor='curatorplug.atrium_safety'`, `action='compliance.approved'` / `action='compliance.refused'` / `action='compliance.warned'`, `entity_id=file_id`, `details={src_xxhash, written_bytes_len, mode, reason, ...}`.
2. **A future `curatorplug-atrium-reversibility` plugin** — would emit `action='reversibility.checked'` events when verifying a destructive operation is reachable.
3. **A future audit-aggregator plugin** — would implement the hookimpl to *receive* events and stream them to an external system (Splunk, Datadog, etc.) without modifying Curator core.

All three need the same primitive: structured-event-emission-from-plugin. This design adds it once, generally.

### 1.3 What this is NOT

- **Not** a new audit table or schema change. The existing `migration_audit` table (and its `actor`/`action`/`entity_type`/`entity_id`/`details_json` columns) is exactly what we need; this design just adds a write path that doesn't require runtime/repo access.
- **Not** a way to *query* the audit log from a plugin. Read access via `curator audit-log` CLI / future GUI tab is unchanged. Plugins emit; they don't consume.
- **Not** a synchronous-guarantee mechanism. Audit writes are best-effort per Curator's existing convention (`MigrationService._audit_move` already swallows insert failures with a warning log). Plugins must not assume their event was persisted.
- **Not** coupled to PLUGIN_INIT. This hookspec is independent — plugins call it via `pm.hook.curator_audit_event(...)` from inside other hookimpls, exactly like the safety plugin already calls `pm.hook.curator_source_read_bytes(...)` for re-read verification (which DOES require PLUGIN_INIT). A plugin that uses this hookspec but not PLUGIN_INIT would need its own way to get the pm reference, e.g., via `curator_plugin_init` (recommended) or via the future `curator_audit_event` being callable through a different mechanism (out of scope; not designing that here).

---

## 2. Invariants the design must preserve

1. **Existing plugins keep working unchanged.** Strictly additive hookspec.
2. **MigrationService's existing direct-to-repo writes are unaffected.** This design adds a *parallel* write path for plugins; it does NOT replace `MigrationService._audit_move` / `_audit_copy`.
3. **Audit writes remain best-effort.** A failure to persist an audit event must NOT crash the originating action. Plugins emitting events get the same "fire and (probably) forget" semantics that MigrationService already has.
4. **Schema unchanged.** Reuses existing `migration_audit` table (or its successor). `actor` field already accepts arbitrary strings; `details_json` is freeform; `entity_type` is nullable.
5. **No reentrancy hazards.** A plugin's hookimpl for `curator_audit_event` calling `pm.hook.curator_audit_event(...)` could loop. The core hookimpl must NOT itself fire the hook recursively; it does the write and returns.

---

## 3. Decisions Jake needs to make

### DM-1 — Hookspec signature

**Question.** What does the new hookspec look like?

Options:

- (a) **Field-based:** `curator_audit_event(actor: str, action: str, entity_type: str | None, entity_id: str | None, details: dict[str, Any]) -> None`. Plugins pass each field explicitly.
- (b) **Object-based:** `curator_audit_event(entry: AuditEntry) -> None`. Plugins construct an `AuditEntry` and pass it.
- (c) **Both:** ship (a) AND a thin wrapper helper that lets plugins call via either style.

**Recommendation (mine):** (a) — **field-based**.

Rationale: Plugin authors can call the hook without importing `AuditEntry` (which lives in `curator.models.audit` — a deep import for what should be a one-liner). Curator core's hookimpl constructs the `AuditEntry` from the field args before inserting. This decouples plugin code from Curator's internal model layout — if `AuditEntry` gains fields later, the hookspec doesn't have to change as long as the new fields have sensible defaults.

(b) is more "Pythonic" but creates a tighter coupling. (c) is unnecessary complexity for a hookspec we'll fire from at most a few call sites in the safety plugin.

**RATIFICATION STATUS:** ⚠ AWAITING JAKE.

### DM-2 — Where the core hookimpl lives + how it gets the audit repo

**Question.** Curator core needs to register a hookimpl that writes to `AuditRepository`. The hookimpl needs access to the repo. How is that wired?

Options:

- (a) **New core plugin file `curator/plugins/core/audit_writer.py`** with a class `AuditWriterPlugin(audit_repo)` that's instantiated and registered by `_create_plugin_manager` after the audit repo is constructed. The hookimpl uses `self.audit_repo`.
- (b) **Closure-based hookimpl** registered directly in `build_runtime` — `build_runtime` constructs the audit repo, then creates a closure capturing the repo and registers a tiny hookimpl-bearing object on the pm.
- (c) **Module-level singleton** — store the audit repo in a module-level variable that the hookimpl reads. Set it from `build_runtime`.

**Recommendation (mine):** (a) — **new core plugin file**.

Rationale: Consistent with how all other core plugins are structured (each in its own module, constructed by `register_core_plugins`). Easy to test in isolation (instantiate with a mock repo). Easy to extend later (e.g., add a `details_redactor` to scrub PII from `details_json` before insert — that lives in the same file). (b) creates an unnamed plugin with a confusing-to-debug structure. (c) introduces global state that survives test runs and creates ordering bugs.

The implementation needs `register_core_plugins` to receive the audit repo — currently `register_core_plugins(pm)` takes only the pm. Either change the signature to `register_core_plugins(pm, audit_repo)` (small breaking change to internal API; only `_create_plugin_manager` calls it) OR have `_create_plugin_manager` register `AuditWriterPlugin` itself after `register_core_plugins` returns. The latter is cleaner.

**RATIFICATION STATUS:** ⚠ AWAITING JAKE.

### DM-3 — MigrationService: keep direct-to-repo writes or migrate to hook?

**Question.** Should `MigrationService._audit_move` and `_audit_copy` continue writing directly to `audit_repo`, or migrate to firing `curator_audit_event` events?

Options:

- (a) **Keep direct writes.** The hookspec is purely for plugins; core code keeps its existing path. Two write paths coexist.
- (b) **Migrate Migration to use the hookspec.** All audit writes go through the hookspec. Single write path; Migration's writes also become observable to other plugins implementing the hookimpl.
- (c) **Migrate gradually.** Keep direct writes for now; refactor in a future release.

**Recommendation (mine):** (a) — **keep direct writes** for v1.1.3.

Rationale: (b) is technically nicer (single write path) but adds a hop and a dependency: Migration would need pm access (not currently a constructor arg) AND would need to know the hookspec is registered (which requires `curator_plugin_init` plumbing or similar). That's a bigger change than this design wants to commit to. (c) is what (a) becomes naturally — the migration is always available later if a real use case (e.g., the audit-aggregator plugin) emerges.

The cost of (a) is some semantic asymmetry: `MigrationService`'s audit events are NOT visible to plugins implementing `curator_audit_event` hookimpl, while plugin-emitted events ARE. For the headline use case (safety plugin emitting compliance events), this is fine — those events go through the hookspec like the design intends.

**RATIFICATION STATUS:** ⚠ AWAITING JAKE.

### DM-4 — Failure handling in the core hookimpl

**Question.** What if the `AuditRepository.insert(entry)` call inside the core hookimpl fails (DB locked, schema mismatch, disk full)?

Options:

- (a) **Log + swallow.** Match `MigrationService._audit_move`'s existing behavior: log a warning naming the cause, return None, the originating plugin's hookimpl proceeds. Audit entry is lost.
- (b) **Propagate.** Let the exception escape the hookimpl. Pluggy wraps it; the calling plugin sees the failure and can decide what to do. Some plugins might want to refuse the originating action if audit fails.
- (c) **Buffer-and-retry.** On failure, append to a local buffer; flush periodically. Provides eventual consistency.

**Recommendation (mine):** (a) — **log + swallow**.

Rationale: Consistent with Curator's existing audit-write semantics (MigrationService swallows). Audit visibility is best-effort; the originating action's correctness should not depend on the audit write succeeding. A user with a corrupted DB has bigger problems than a missing audit entry. (b) creates a foot-gun where audit failures cascade into business-logic failures. (c) is over-engineered for a single-user tool.

**RATIFICATION STATUS:** ⚠ AWAITING JAKE.

---

## 4. Hookspec specification (assuming DM-1 = a)

Added to `src/curator/plugins/hookspecs.py` under a new "Audit channel" section:

```python
@hookspec
def curator_audit_event(
    actor: str,
    action: str,
    entity_type: str | None,
    entity_id: str | None,
    details: dict[str, Any],
) -> None:
    """Plugin-initiated audit log event (v1.1.3+).

    Plugins call ``pm.hook.curator_audit_event(...)`` to emit a
    structured event. Curator core implements a hookimpl that
    constructs an :class:`~curator.models.audit.AuditEntry` from these
    fields and persists it via ``AuditRepository.insert``.

    Other plugins MAY also implement this hookspec to receive events
    (e.g., a future audit-aggregator plugin streaming to a SIEM).
    Pluggy's default ``firstresult=False`` applies; all hookimpls fire.

    Hook semantics:

    * **Best-effort persistence:** the core hookimpl logs and swallows
      ``AuditRepository.insert`` failures (per DM-4). Plugins MUST NOT
      depend on persistence success.
    * **Strictly additive:** plugins that don't fire this hook are
      unaffected; the existing audit_repo direct-write path used by
      MigrationService is unchanged (per DM-3).
    * **No reentrancy:** the core hookimpl does NOT itself fire the
      hookspec. Plugins' hookimpls SHOULD NOT either — recursive
      firing is undefined.

    Args:
        actor: who emitted the event. Convention: dotted name like
            ``'curator.migrate'`` (core) or ``'curatorplug.atrium_safety'``
            (plugin). Curator's audit-log query CLI filters by actor.
        action: what happened. Convention: dotted verb-phrase like
            ``'migration.move'``, ``'compliance.refused'``,
            ``'reversibility.checked'``. The CLI filters by action.
        entity_type: the type of entity this event is ABOUT (e.g.
            ``'file'``, ``'migration_job'``). Nullable for events that
            don't relate to a specific entity (e.g., startup events).
        entity_id: the entity's identifier (typically a UUID string for
            files, a string ID for migrations). Nullable for the same
            reasons as ``entity_type``.
        details: freeform action-specific data, JSON-serialized when
            persisted. Conventionally includes things like hashes,
            sizes, mode flags, and human-readable reason strings.

    See ``docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md`` v0.2 for the
    design that motivated this hookspec, and
    ``curatorplug-atrium-safety`` v0.3.0+ for the canonical consumer.
    """
```

And in `src/curator/plugins/core/audit_writer.py` (new file):

```python
"""Core audit-event writer plugin.

Implements ``curator_audit_event`` hookimpl that persists plugin-
emitted audit events to the AuditRepository. Registered by
``_create_plugin_manager`` after construction with the runtime's
audit repo.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from curator.models.audit import AuditEntry
from curator.plugins import hookimpl
from curator.storage.repositories.audit_repo import AuditRepository


class AuditWriterPlugin:
    """Persists plugin-emitted ``curator_audit_event`` events to repo."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo

    @hookimpl
    def curator_audit_event(
        self,
        actor: str,
        action: str,
        entity_type: str | None,
        entity_id: str | None,
        details: dict[str, Any],
    ) -> None:
        """Construct an AuditEntry and insert. Best-effort; logs on failure."""
        try:
            entry = AuditEntry(
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
            )
            self.audit_repo.insert(entry)
        except Exception as e:  # noqa: BLE001 -- best-effort write
            logger.warning(
                "AuditWriterPlugin: failed to persist audit event "
                "actor={a}, action={ac}: {err}",
                a=actor, ac=action, err=e,
            )
```

And in `src/curator/cli/runtime.py` (modification to `build_runtime`):

```python
# After audit_repo is constructed but before the pm is finalized:
from curator.plugins.core.audit_writer import AuditWriterPlugin
audit_writer = AuditWriterPlugin(audit_repo=audit_repo)
pm.register(audit_writer, name="curator.core.audit_writer")
# ^ registered before _create_plugin_manager fires curator_plugin_init
# (per PLUGIN_INIT design DM-2, init fires AFTER all plugins registered).
```

---

## 5. Implementation plan

Three sessions, ~1.75h total:

### P1 (Curator side) — ~30 min

* Add `curator_audit_event` hookspec to `src/curator/plugins/hookspecs.py` under a new "Audit channel" section.
* Create `src/curator/plugins/core/audit_writer.py` with `AuditWriterPlugin`.
* Modify `build_runtime` to instantiate `AuditWriterPlugin(audit_repo)` and register it on the pm (before `_create_plugin_manager` fires `curator_plugin_init` so the writer is registered when init runs).
* Add 3-4 unit tests in `tests/unit/test_audit_writer.py`:
  - hookimpl persists a valid entry to the repo
  - hookimpl swallows DB errors and logs (per DM-4)
  - the hookspec is reachable via `pm.hook.curator_audit_event(...)` after build_runtime
  - existing core audit writes (MigrationService direct path) still work (per DM-3 invariant)
* Update `CHANGELOG.md` `[1.1.3]` entry.
* Bump version 1.1.2 → 1.1.3 (patch — strictly additive, no behavior change for plugins that don't fire the hook).
* Commit, tag `v1.1.3`, push.

### P2 (`curatorplug-atrium-safety` v0.3.0) — ~45 min

* Replace the `logger.debug` / `logger.warning` calls in `plugin.py`'s `curator_source_write_post` with structured `curator_audit_event` calls. Three actions:
  - `compliance.approved` — fired on every successful enforcement decision (lax mode observation OR strict mode pass). `details` includes `mode`, `src_xxhash` (truncated), `written_bytes_len`.
  - `compliance.refused` — fired when the plugin raises ComplianceError. `details` includes `mode`, `phase` ('decide' or 're-read'), `reason`, `expected_xxhash`, `actual_xxhash` (for re-read mismatches).
  - `compliance.warned` — fired when the plugin sees a re-read mismatch in lax mode (advisory; doesn't refuse).
* Add 3-4 integration tests verifying the audit entries are actually persisted by tracing through the AuditRepository:
  - `test_compliance_approved_audit_entry_in_strict_compliant_migration`
  - `test_compliance_refused_audit_entry_in_strict_skipped_verify`
  - `test_compliance_refused_audit_entry_in_strict_re_read_mismatch`
  - `test_compliance_warned_audit_entry_in_lax_re_read_mismatch`
* The plugin will need pm access to fire the hook — already has it via `curator_plugin_init` (PLUGIN_INIT plan, shipped 2026-05-08). No new plumbing needed.
* Update plugin's `CHANGELOG.md` `[0.3.0]` entry.
* Bump plugin version 0.2.0 → 0.3.0 (minor — observable behavior change: audit log gets new entries that didn't exist before).
* Commit, tag `v0.3.0`, push.

### P3 (regression sweep + doc stamps) — ~30 min

* Run Curator's full slice with plugin v0.3.0: confirm 348/348 still pass.
* Run plugin's full suite: 71+ tests passing (53 from v0.1.0 + 18 from v0.2.0 + 4 new in v0.3.0 = ~75).
* Update `curatorplug-atrium-safety/DESIGN.md` v0.3 → v0.4 marking DM-3's audit-log integration as IMPLEMENTED.
* Update this doc v0.2 → v0.3 IMPLEMENTED with the v0.3 revision-log entry.
* Final regression sweep + doc commits + push.

---

## 6. Backward compatibility analysis

**v1.1.2 → v1.1.3 is a patch bump.**

- ✅ Existing plugins (Curator core's `LocalPlugin`, `GoogleDriveSourcePlugin`, `curatorplug-atrium-safety` v0.1.0/v0.2.0) work unchanged. They don't fire `curator_audit_event` so the new path is invisible to them.
- ✅ Existing `MigrationService` audit writes are unaffected (DM-3 keeps direct-to-repo path).
- ✅ Existing CLI invocations identical.
- ✅ Existing test suites pass.
- ✅ No new dependencies; no schema change.

**v0.2.0 (`curatorplug-atrium-safety`) → v0.3.0 is a minor bump** because the audit log gains new entries (`compliance.approved`, `compliance.refused`, `compliance.warned`) that didn't exist before. Users running `curator audit-log query` will see new rows; users grepping audit log by `actor='curatorplug.atrium_safety'` will find data where there was none. That's the intended observable change.

---

## 7. Cross-references

- `curatorplug-atrium-safety\DESIGN.md` v0.3 §9 ("What's next") — explicit pointer to this design as the natural follow-on.
- `curatorplug-atrium-safety\DESIGN.md` v0.2 DM-3 — ratified `actor='curatorplug.atrium_safety'` for safety plugin's audit emissions; this design provides the mechanism to honor that ratification.
- `Curator\docs\PLUGIN_INIT_HOOKSPEC_DESIGN.md` v0.3 IMPLEMENTED — sibling Curator-side hookspec design that established the "small additive hookspec to enable plugin capability" pattern this design follows.
- `Atrium\CONSTITUTION.md` Principle 4 (No Silent Failures) — the constitutional grounding for this design. Plugin enforcement decisions that aren't auditable are *silent*; this design makes them queryable.

---

## 8. Revision log

- **2026-05-08 v0.1** — first issued. Captures: §1 scope (the gap atrium-safety v0.3 §9 named; what the hookspec enables; what it's NOT), §2 invariants (additive, parallel-not-replacement, best-effort, no schema change, no reentrancy), §3 four DMs (signature, location/wiring, MigrationService migration, failure handling) with recommendations awaiting Jake's ratification, §4 hookspec specification (assuming DM-1=a) with full contract docstring + AuditWriterPlugin sketch, §5 three-session implementation plan (~1.75h: P1 Curator v1.1.3, P2 plugin v0.3.0, P3 regression + docs), §6 backward compatibility (v1.1.2→v1.1.3 patch; v0.2.0→v0.3.0 minor), §7 cross-references. No code has been written; no commits have landed. Next step: Jake reviews DMs → ratifies → doc flips to v0.2 RATIFIED → P1 → P2 → P3 lands.
