# Curator — `curator_plugin_init` Hookspec Design

**Status:** v0.2 — RATIFIED 2026-05-08. Jake ratified all 4 DMs as recommended ("continue" against the explicit `ratify`-default convention). Implementation cleared to begin. P1 lands as Curator v1.1.2; P2 lands as `curatorplug-atrium-safety` v0.2.0; P3 is regression sweep + doc updates.
**Date:** 2026-05-08
**Authority:** Curator-side design. Provides the plumbing that lets `curatorplug-atrium-safety` (and future Curator plugins like `curatorplug-atrium-reversibility`) deliver headline value that requires a plugin to call OTHER plugins' hooks from inside its own hookimpls.
**Companion documents:**
- `Curator\src\curator\plugins\hookspecs.py` — the file this design adds one hookspec to.
- `Curator\src\curator\plugins\manager.py` — the file that fires the new hook at the right time.
- `curatorplug-atrium-safety\DESIGN.md` v0.2 — the design that motivated this Curator-side amendment. §5 explicitly notes "the plugin re-reads via `curator_source_read_bytes`" as a deferred capability pending exactly this plumbing.
- `Atrium\CONSTITUTION.md` Principle 2 — the invariant whose end-to-end enforcement currently has a gap that this design closes.

---

## 1. Scope

### 1.1 The problem

Pluggy hookimpls receive only the arguments their hookspec declares. They do NOT receive a reference to the plugin manager that invoked them. This is normally fine — most plugins are pure transformers (input → output) with no need to call out to other plugins.

But some plugins are intrinsically **cross-cutting** — they need to react to events fired by Curator's core services AND query other plugins to do their job. The motivating example is `curatorplug-atrium-safety` (v0.1.0, shipped 2026-05-08): when its `curator_source_write_post` hookimpl fires, it WANTS to re-read the destination via `curator_source_read_bytes` to do an independent hash-verification. But its hookimpl doesn't have access to the plugin manager and so cannot fire `curator_source_read_bytes` itself.

The safety plugin v0.1.0 worked around this by shipping with **strict-mode refusal as the only enforcement mechanism** — refusing writes where source-side verify was skipped. That's useful but it's not the headline value the plugin's design imagined. The design's §5 explicitly states:

> the atrium_safety plugin re-reads via `curator_source_read_bytes`, recomputes xxh3, compares.

That capability is currently deferred. This design unblocks it.

### 1.2 The general capability being added

This isn't safety-plugin-specific. The capability is: **let plugins access Curator's plugin manager from inside a hookimpl, so they can call other plugins' hooks.**

Concrete near-term consumers of this capability:

1. **`curatorplug-atrium-safety` v0.2.0** — the headline use case. Re-reads dst via `curator_source_read_bytes` to do independent hash-verify, comparing against `src_xxhash` from the post-write hook. Refuses writes (raises `ComplianceError`) where the re-read hash doesn't match.
2. **A future `curatorplug-atrium-reversibility` plugin** (per Atrium Constitution Principle 1) — would fire `curator_source_stat` to confirm a file is reachable before allowing a destructive operation to proceed.
3. **A future audit-aggregator plugin** — would fire `curator_classify_file` and `curator_compute_lineage` retrospectively against indexed files to rebuild lineage edges from a historical snapshot.

All three need the same primitive: pm-access-from-hookimpl. This design adds it once, generally.

### 1.3 What this is NOT

- **Not** a way to give plugins access to Curator's `CuratorRuntime` object (FileRepository, AuditRepository, Config, etc.). That would have a much larger blast radius. If a plugin needs (say) audit repo access, that should go through a different mechanism — e.g. a `curator_audit_event(actor, action, entity_id, details)` hookspec that ANY plugin can call, with Curator core implementing the hookimpl that actually writes to the repo. Repo access is out of scope for THIS design.
- **Not** a way to inject configuration into plugins. Plugins continue to read config from env vars, files they own, or via future config-injection mechanisms — not via the pm.
- **Not** a way to dynamically swap plugin implementations at runtime. The pm is provided once at startup; subsequent dynamic registrations are handled per DM-4.

---

## 2. Invariants the design must preserve

1. **Existing plugins keep working unchanged.** Strictly additive.
2. **Failures in the new init hook do not crash Curator startup.** A misbehaving plugin's init hookimpl raising should be logged and the rest of startup should continue (consistent with how `load_setuptools_entrypoints` already handles entry-point discovery failures in `manager.py`).
3. **Init hook fires after all plugins are registered.** A plugin's init hookimpl might fire `pm.hook.<other>()` synchronously and expect other plugins' hookimpls to be present. So initialization order matters.
4. **Init hook fires exactly once per plugin per pm.** Plugins shouldn't have to guard against re-initialization.
5. **No version dance for downstream plugins that don't care.** A plugin that doesn't implement `curator_plugin_init` should be invisibly unaffected by this design.

---

## 3. Decisions Jake needs to make

### DM-1 — Hookspec signature

**Question.** What does the new hookspec look like?

Options:

- (a) **Pm-only:** `curator_plugin_init(pm: pluggy.PluginManager) -> None`. Plugin saves the pm reference.
- (b) **Init-context:** `curator_plugin_init(ctx: PluginInitContext) -> None` where `PluginInitContext` is a small dataclass that wraps `pm` and possibly other future fields (e.g., a `version` string for forward-compat).
- (c) **Runtime-full:** `curator_plugin_init(rt: CuratorRuntime) -> None`. Plugin gets full runtime access.

**Recommendation (mine):** (a) — **pm-only**.

Rationale: Minimal surface area. The motivating use case is calling other plugins' hooks; pm alone is sufficient for that. (b) over-engineers for a hypothetical future need — when that need actually arrives, we can add a NEW hookspec (`curator_plugin_init_v2(ctx)`) without breaking (a). (c) is a much bigger blast radius (plugins could mutate file repo, audit repo, config) which requires a higher trust bar than this design wants to assume.

If a plugin needs runtime-level access (e.g., the safety plugin's DM-3 desire to write to the audit log), the right answer is a separate, narrowly-scoped hookspec like `curator_audit_event(actor, action, entity_id, details)` that Curator core implements. That's a different design doc; not blocked by this one.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. Hookspec is `curator_plugin_init(pm: pluggy.PluginManager) -> None`. Pm-only; minimal surface area; future runtime/repo access via separate narrow hookspecs.

### DM-2 — When the hook fires

**Question.** At what point in `_create_plugin_manager` does the init hook fire?

Options:

- (a) **After all plugins registered** — fire `pm.hook.curator_plugin_init(pm=pm)` as the last step in `_create_plugin_manager`, after `register_core_plugins(pm)` and `pm.load_setuptools_entrypoints("curator")` have both completed.
- (b) **After each plugin registered** — fire the init hook immediately when each individual plugin is registered. Init hookimpls of earlier-registered plugins might run before later plugins are registered.
- (c) **Lazy** — fire the init hook the first time any plugin tries to access the pm (would need a dedicated accessor like `get_my_pm()` rather than the standard hook firing pattern).

**Recommendation (mine):** (a) — **after all plugins registered**.

Rationale: A plugin's init hookimpl might want to query the pm for what other plugins are present, or might want to fire a hook synchronously inside init to do setup work. (b) breaks this — a plugin registered first wouldn't see plugins registered later. (c) adds a non-standard mechanism that doesn't compose well with the rest of pluggy.

The cost of (a) is one extra invariant: the init hook MUST be the LAST thing `_create_plugin_manager` does before returning. Easy to enforce.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. Fires AFTER all plugins (core + entry-point-discovered) are registered. Init hookimpls can see all siblings via `pm.list_name_plugin()`.

### DM-3 — Behavior when a plugin's init hookimpl raises

**Question.** What happens if a plugin's `curator_plugin_init` raises an exception?

Options:

- (a) **Log + continue** — Curator catches the exception, logs a warning naming the offending plugin, and continues startup. The misbehaving plugin remains registered but its init failed; subsequent hookimpls may behave oddly because the plugin's setup didn't complete.
- (b) **Log + de-register** — Curator catches the exception, logs a warning, AND unregisters the plugin from the pm. The plugin is effectively "removed" because its setup failed.
- (c) **Propagate** — Curator lets the exception propagate, which crashes startup. User has to fix the offending plugin (e.g., uninstall it) before Curator runs at all.

**Recommendation (mine):** (a) — **log + continue**.

Rationale: This is consistent with how `_create_plugin_manager` already handles `load_setuptools_entrypoints` failures (logs a warning, continues). It's also consistent with Atrium Principle 1 (Reversibility) at the operational level — a plugin breaking shouldn't render Curator unusable. (b) is more aggressive than (a) and could surprise users (a misbehaving plugin silently disappears from the pm). (c) is too brittle — one bad plugin breaks the whole tool.

The downside of (a) is that a plugin whose init failed might still try to do work in subsequent hookimpls and produce confusing errors. That's acceptable; the warning at startup signals the issue clearly.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. Log + continue. Misbehaving plugin remains registered (consistent with existing `load_setuptools_entrypoints` failure handling and Atrium Principle 1 Reversibility at the operational level).

### DM-4 — Re-fire on dynamic plugin registration

**Question.** When a plugin is registered dynamically AFTER startup (e.g., a test fixture that calls `pm.register(SomePlugin())` mid-test), should the init hook fire for that plugin?

Options:

- (a) **No re-fire** — init hook fires exactly once per pm, at startup. Plugins registered later don't get the hook. They have to use a different mechanism (e.g., constructor argument) to get the pm if they need it.
- (b) **Re-fire per dynamic registration** — Curator wraps `pm.register` to also fire `curator_plugin_init` for the newly-registered plugin. Mid-test plugin registrations get the same treatment as startup ones.
- (c) **Document the gap** — keep current `pm.register` behavior; document that test fixtures and other dynamic-registration code paths must call the init hookimpl manually if needed.

**Recommendation (mine):** (a) — **no re-fire** for v1.1.2.

Rationale: Dynamic plugin registration is a niche pattern (mostly tests). The two known use cases for this hookspec — `curatorplug-atrium-safety` v0.2.0 and the future plugins listed in §1.2 — are all installed via setuptools entry points and discovered at startup; none of them rely on dynamic registration. Tests that DO register plugins dynamically (the recorder pattern in Curator's `tests/unit/test_migration_cross_source.py`) don't currently need pm access in their hookimpls.

If a future use case emerges where dynamic registration needs init, we can revisit. (b) requires wrapping `pm.register`, which is a more invasive change to pluggy's interface. (c) is what (a) becomes naturally; just document it.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. No re-fire on dynamic registration in v1.1.2. Documented as a known gap; revisit if a real use case appears.

---

## 4. Hookspec specification (DM-1 ratified = a)

Added to `src/curator/plugins/hookspecs.py`:

```python
@hookspec
def curator_plugin_init(pm: "pluggy.PluginManager") -> None:
    """One-time initialization notification for plugins (v1.1.2+).

    Fired exactly once per ``pm``, after all plugins (core +
    entry-point-discovered) have been registered. Plugins that need
    to call OTHER plugins' hooks from inside their own hookimpls
    implement this hookspec to receive a reference to the plugin
    manager and save it for later use.

    Hook semantics:

    * **One-shot:** fired once per pm at the end of
      ``_create_plugin_manager``. Plugins registered dynamically after
      startup do NOT receive this hook (per DM-4 of
      docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md v0.2).
    * **Multi-plugin:** all plugins implementing this hookspec are
      invoked. Pluggy's default ``firstresult=False`` applies; results
      (typically ``None``) are not consumed.
    * **Failure isolation:** a plugin's init hookimpl raising an
      exception is logged but does NOT abort startup or de-register the
      plugin (per DM-3). Subsequent hookimpls of the misbehaving plugin
      may behave oddly; that's the plugin author's problem to surface.
    * **Strictly additive:** plugins that don't implement this hookspec
      are unaffected.

    Args:
        pm: the plugin manager that holds this plugin and all its
            siblings. Plugins typically save it as ``self.pm`` and
            use ``self.pm.hook.<other_hook>(...)`` from inside other
            hookimpls.

    See ``docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md`` for the design that
    motivated this hookspec, and ``curatorplug-atrium-safety`` v0.2.0
    for the canonical consumer (independent re-read verification of
    cross-source migration writes).
    """
```

And in `src/curator/plugins/manager.py`, the hookspec firing:

```python
def _create_plugin_manager() -> pluggy.PluginManager:
    pm = pluggy.PluginManager("curator")
    pm.add_hookspecs(hookspecs)

    # Built-in plugins (always registered).
    from curator.plugins.core import register_core_plugins
    register_core_plugins(pm)

    # External plugins via setuptools entry points.
    try:
        loaded = pm.load_setuptools_entrypoints("curator")
        if loaded:
            logger.debug("Loaded {n} external Curator plugin(s)", n=loaded)
    except Exception as e:
        logger.warning("Entry-point plugin discovery failed: {err}", err=e)

    # NEW (v1.1.2): fire one-shot init hook so plugins that need pm
    # access can save the reference. Per DM-3, exceptions from
    # individual plugins' init hookimpls are caught and logged but do
    # not abort startup.
    try:
        pm.hook.curator_plugin_init(pm=pm)
    except Exception as e:
        logger.warning(
            "curator_plugin_init: at least one plugin's init hookimpl "
            "raised; check earlier log lines for the specific plugin. "
            "Misbehaving plugin remains registered but may behave "
            "oddly in subsequent hookimpls. Cause: {err}", err=e,
        )

    return pm
```

(The outer `try`/`except` catches the case where pluggy aggregates per-plugin failures into a single exception. Pluggy's behavior here depends on version; the `try` is cheap insurance.)

---

## 5. Implementation plan

Three sessions, ~2.0h total:

### P1 (this design's Curator side) — ~30 min

* Add `curator_plugin_init` hookspec to `src/curator/plugins/hookspecs.py`.
* Wire it into `_create_plugin_manager` per §4.
* Add 3 unit tests in `tests/unit/test_plugin_manager.py`:
  - hook fires exactly once per pm
  - hook fires AFTER all plugins are registered (a plugin's init can see its siblings via `pm.list_name_plugin()`)
  - a plugin's init raising doesn't crash `_create_plugin_manager`
* Update CHANGELOG.md `[1.1.2]` entry.
* Bump version 1.1.1 → 1.1.2 (patch — strictly additive, no behavior change for plugins that don't opt in).
* Commit, tag `v1.1.2`, push to GitHub.

### P2 (`curatorplug-atrium-safety` v0.2.0) — ~1.0h

* Add `curator_plugin_init` hookimpl to `AtriumSafetyPlugin` that saves `self.pm = pm`.
* Update `curator_source_write_post` hookimpl to perform independent re-read verification when:
  - `self.pm is not None`
  - `src_xxhash is not None` (verify was done at source side; we have a hash to compare against)
* The re-read flow:
  1. Loop calling `self.pm.hook.curator_source_read_bytes(source_id, file_id, offset, 65536)` to assemble dst bytes
  2. Compute xxh3_128 of assembled bytes via `compute_xxh3` (already in `verifier.py`)
  3. If hash differs from `src_xxhash`, return `EnforcementVerdict.REFUSE` with a message that distinguishes this from the "skipped verify" refusal (so users see WHICH compliance condition tripped)
  4. If hash matches, return OK (the write is independently verified)
* Add 3-4 integration tests:
  - Independent re-read confirms a compliant write (passes through)
  - Independent re-read catches a transport corruption that source-side verify missed (refused)
  - Independent re-read in lax mode just LOGS the mismatch and lets the migration proceed (advisory mode for non-strict)
* Update CHANGELOG `[0.2.0]` and bump to v0.2.0.
* Commit, tag, push.

### P3 (regression sweep + documentation) — ~30 min

* Run Curator's full slice with plugin v0.2.0 auto-discovered: confirm 342/342 still pass.
* Run plugin's full suite: 53+ tests passing.
* Update `curatorplug-atrium-safety/DESIGN.md` v0.2 → v0.3 marking §5's "deferred re-read verification" as implemented in v0.2.0.
* Update `curatorplug-atrium-safety/README.md` to describe the new strict-mode re-read flow.

---

## 6. Backward compatibility analysis

**v1.1.1 → v1.1.2 is a patch bump.**

- ✅ Existing plugins (Curator core's `LocalPlugin`, `GoogleDriveSourcePlugin`, plus `curatorplug-atrium-safety` v0.1.0) work unchanged. They don't implement `curator_plugin_init`; pluggy doesn't invoke it on them.
- ✅ Existing CLI invocations are identical. The new init hook is a startup-time event with no user-visible effect for users who don't install plugins consuming it.
- ✅ Existing test suites pass without modification. Will be verified at P1 commit time.
- ✅ No new dependencies. `pluggy` already covers the mechanism.
- ✅ Schema unchanged. No new tables, no new columns.

**v0.1.0 (`curatorplug-atrium-safety`) → v0.2.0 is a minor bump** because the plugin's behavior gains a feature (independent re-read verification in strict mode). Patch (0.1.1) wouldn't be honest because users WILL see different refusal-rates after upgrade — strict mode now refuses MORE cases (transport corruption, not just skipped-verify).

---

## 7. Cross-references

- `curatorplug-atrium-safety\DESIGN.md` v0.2 §5 — "the atrium_safety plugin re-reads via `curator_source_read_bytes`" — the original design assumption that this Curator-side amendment unblocks.
- `Curator\docs\TRACER_PHASE_2_DESIGN.md` v0.3 — `_cross_source_transfer` is the call site that fires `curator_source_write_post` (Curator v1.1.1+); this design adds the plumbing for plugins consuming that hook to do meaningful follow-up work.
- `Atrium\CONSTITUTION.md` Principle 2 — Hash-Verify-Before-Move; the invariant whose end-to-end enforcement gains a new layer of defense once P2 of this plan ships in plugin v0.2.0.

---

## 8. Revision log

- **2026-05-08 v0.1** — first issued. Captures: §1 scope (the problem of plugins not having pm access; the general capability being added; what this is NOT), §2 invariants (additive, failure-tolerant, init-after-all-registered, once-per-plugin, invisible-to-non-opt-in), §3 four DMs (signature, timing, failure handling, dynamic registration) with recommendations awaiting Jake's ratification, §4 hookspec specification (assuming DM-1=a), §5 three-session implementation plan (~2.0h total: P1 Curator v1.1.2, P2 plugin v0.2.0, P3 regression + docs), §6 backward compatibility analysis (v1.1.1→v1.1.2 = patch; v0.1.0→v0.2.0 = minor for the plugin's behavior change), §7 cross-references. No code has been written; no commits have landed. Next step: Jake reviews DMs → ratifies (or modifies) → doc flips to v0.2 RATIFIED → P1 (Curator-side hookspec addition) → P2 (plugin v0.2.0) → P3 (regression + docs) lands.
- **2026-05-08 v0.2** — RATIFIED. Jake ratified all 4 DM recommendations (DM-1 through DM-4) as written without modification (replied "continue" against the explicit `ratify`-default convention). Doc status flips from "design proposal" to "approved spec"; P1 implementation cleared to begin. No structural changes to the design — only ratification-status flips on each DM and this revision-log entry.
