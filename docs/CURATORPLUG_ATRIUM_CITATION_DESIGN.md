# `curatorplug-atrium-citation` — Design

**Status:** v0.1 — DRAFT 2026-05-08. Awaiting Jake's ratification of DMs §3.
**Date:** 2026-05-08
**Authority:** Subordinate to Atrium `CONSTITUTION.md`. Implements Constitution **Principle 3 (Citation Chain Preservation)** as a *cross-cutting verification layer* over Curator's plugin ecosystem, complementing `curatorplug-atrium-safety` (which implements Principle 2: Hash-Verify-Before-Move).
**Companion documents:**
- `Atrium\CONSTITUTION.md` — supreme authority. Article II Principle 3 is the invariant this plugin defends.
- `Atrium\NAMES.md` — establishes the suite's plugin naming convention (`curatorplug-*` distribution name, `curatorplug.*` Python namespace).
- `Curator\src\curator\plugins\hookspecs.py` — plugin hook surface this package consumes (`curator_source_write_post`, `curator_audit_event`, `curator_plugin_init`).
- `Curator\src\curator\models\lineage.py` (and `storage\repositories\lineage_repo.py`) — the lineage-edge data model whose population this plugin verifies.
- `curatorplug-atrium-safety\DESIGN.md` v0.4 IMPLEMENTED — structural template for this design; matches DM ratification flow + soft-enforcement pattern + audit channel pattern.

---

## 1. Scope

### 1.1 What the plugin IS

`curatorplug-atrium-citation` is a Curator plugin package whose single job is to make Atrium Constitution **Principle 3 — Citation Chain Preservation** verifiable across the *entire* Curator runtime, including third-party source plugins that Curator core has no compile-time relationship with.

Constitution Article II Principle 3 reads:

> *Information that originated in a sourced document maintains a chain back to that source through every transformation.*
>
> Operationalization:
> - Conclave outputs include source page references for every extracted claim.
> - Curator's lineage edges preserve "this file derived from that file" relationships.
> - Any cross-product data flow includes provenance metadata.

Today (Curator v1.4.0), Curator's `MigrationService` does record `lineage_edges` rows for cross-source moves and renames. But:

1. There is no enforcement that a *third-party* code path that calls `curator_source_write` directly (bypassing `MigrationService`) creates the corresponding lineage edge. A plugin author could write bytes to a destination, register the resulting `FileEntity` via `file_repo.upsert()`, and never link it to its ancestor.
2. There is no *observability* — the user has no answer to "show me every write that happened in the last week without a corresponding lineage edge." The audit log records writes (`migration.move`, `migration.copy`, `migration.conflict_resolved`) but doesn't surface lineage gaps.
3. There is no *cross-product propagation guarantee* — if Curator hands a file off to APEX or Conclave, there is no constitutional check that the receiving system gets the provenance metadata. (This is a SIP-level concern that the plugin can begin to surface within Curator's boundary.)

This plugin closes those three gaps. It is small, focused, observation-first, and entirely additive to Curator core.

### 1.2 What the plugin is NOT

- **Not** a replacement for Curator's internal lineage-edge creation logic. `MigrationService` continues to create edges; this plugin verifies that creation actually happened.
- **Not** a general-purpose lineage *populator*. The plugin does not attempt to *fix* missing edges (that would require re-deriving provenance from filesystem state, which is fundamentally lossy). It surfaces gaps; it does not paper over them.
- **Not** a mandatory dependency of Curator. Users who don't install it get current behavior. Curator core never imports `curatorplug.atrium_citation` directly; the plugin discovers itself via setuptools entry points.
- **Not** a replacement for Atrium itself. Atrium ratifies the principle; this plugin verifies it within Curator's boundary. The principle's authority comes from `Atrium\CONSTITUTION.md`; the plugin's verification comes from this package.
- **Not** a superset of `curatorplug-atrium-safety`. The two plugins enforce orthogonal principles (Principle 2 vs Principle 3) at different points in the write lifecycle. They can be installed independently or together; they do not share state.

---

## 2. Invariants the plugin must preserve

Every Phase 2-style invariant listed in `Curator\docs\TRACER_PHASE_2_DESIGN.md` §2 stays preserved. Specifically:

1. **`curator_id` constancy** — the plugin must not invalidate file identity by side-effecting the `FileEntity` row.
2. **No Silent Failures** — if the plugin detects a missing-citation gap, it MUST surface it through an explicit channel (audit event, CLI exit code, `curator citation status` summary) rather than logging-and-forgetting. This is itself a Constitution Principle 4 concern, so the plugin honors it transitively.
3. **No Plugin-Side Mutations Without Plan/Apply Gate** — if DM-1 lands as "soft enforcement" mode (refusal), the refusal must happen *before* `MigrationService` updates the index, not after. Practically: the plugin's refusal hook fires before the file_repo write that would orphan the lineage.
4. **Backward Compatibility** — installing this plugin must not break a working Curator installation. Worst case at install time: warnings about plugins that produce un-cited writes; best case: silent operation.
5. **Compatibility with `curatorplug-atrium-safety`** — both plugins register `curator_source_write_post` hookimpls. Pluggy allows multiple hookimpls on the same hook; both run on every write. The two plugins MUST NOT depend on each other's presence (Aim 3: Self-sufficiency); each is independently uninstallable.

---

## 3. Decisions to Make (DMs awaiting ratification)

### DM-1 — Enforcement mode

**Question.** When the plugin detects a write whose corresponding lineage edge is missing, what does it do?

**Options:**
- **(a) Advisory** — emit an `audit.citation_gap` audit entry and log a warning via `loguru`, but allow the write to proceed. The user reads the audit log periodically (or runs `curator citation status`) to discover gaps.
- **(b) Soft enforcement** — refuse the write by raising `CitationError` from the post-write hook. Curator's existing exception-boundary handling in `MigrationService` turns this into a `MigrationOutcome.FAILED` per file. Reversible: uninstall the plugin to bypass.
- **(c) Hybrid** — advisory by default; soft enforcement opt-in via plugin config (`citation.enforcement_mode = 'refuse'`).

**Recommendation: (a) Advisory.**

Rationale: Lineage chain breaks are usually a *symptom* of a deeper integration bug (e.g. a source plugin that doesn't know how to call Curator's lineage API). Refusing the write at runtime turns a debuggability problem into a *user-facing failure*, which violates Principle 4 (No Silent Failures) at the meta level — the user sees "your write failed" without enough context to fix the root cause. Advisory mode preserves the data while making the gap visible, and the user can address the underlying integration issue at their own pace.

Compare to atrium-safety DM-1 (which ratified soft enforcement for hash mismatches): hash mismatch indicates *data corruption in flight*, where letting the write proceed risks silently storing bad bytes. Citation gap indicates *missing metadata* — the bytes are correct, only the provenance trail is incomplete. Refusing doesn't help; documenting does.

(b) Soft enforcement is too aggressive for an observation-first invariant.
(c) Hybrid is overengineering: a config flag we never flip is dead code.

### DM-2 — Scope of "citation requirement"

**Question.** Which writes are expected to produce lineage edges?

**Options:**
- **(a) All writes** — every successful `curator_source_write_post` should have a corresponding lineage edge (either as edge-source or edge-destination) on the resulting `FileEntity`.
- **(b) Cross-source writes only** — same-source writes (typically initial scans of a folder) don't have a parent file in the index, so they can't have a `derived_from` edge. Only cross-source writes (which represent actual derivation) require lineage.
- **(c) Tagged writes only** — Curator core is updated to pass an optional `expected_provenance` flag through `curator_source_write_post`. Plugin checks for lineage only when this flag is set.

**Recommendation: (b) Cross-source writes only.**

Rationale: A scanner ingesting a fresh folder produces hundreds of `curator_source_write` calls, none of which represent derivation — they're just bringing existing-on-disk files into the index. Requiring lineage for these would generate noise. Cross-source writes (`MigrationService._cross_source_transfer` callers) are the actual derivations: file F at src_source moves to dst_source, becoming a new `FileEntity` (or the same one with updated `source_id`/`source_path`). These are exactly where Constitution Principle 3 says the chain must be preserved.

(a) is too broad and would flood the audit log with false positives.
(c) requires Curator core changes to pass the flag, which is out of scope for a v0.1 plugin and shifts the burden onto Curator's API surface.

The plugin can detect "cross-source" by reading `curator_source_write_post`'s `source_id` and comparing to the calling context — but actually, the post-hook only knows the *destination* source_id. To detect cross-source we'd need either (i) Curator core to pass a `cross_source: bool` flag to the post-hook, or (ii) the plugin to maintain a small state machine listening to `migration.*` audit events to know which writes were cross-source. Option (ii) is plugin-internal and doesn't require Curator core changes. **DM-2 ratification implies adding option (ii) state-machine logic in §4.**

### DM-3 — Lineage-edge verification mechanism

**Question.** How does the plugin verify that a lineage edge exists for a given write?

**Options:**
- **(a) Direct DB query** — plugin queries Curator's `lineage_edges` table directly via a passed-in `LineageRepository` reference. Requires plugin to receive the repo on `curator_plugin_init(pm)`.
- **(b) Pluggy-mediated query hook** — a new Curator hookspec `curator_lineage_query(curator_id) -> list[LineageEdge]` is added; the plugin calls it via `pm.hook.curator_lineage_query()`. Cleaner abstraction; requires Curator core change to add the hookspec.
- **(c) Audit-log-only inference** — plugin doesn't query the lineage table; instead listens to `lineage.edge_created` audit events (which it would need Curator core to emit) and maintains its own in-memory map of which `curator_id`s have edges. Decoupled; requires Curator core to start emitting these events.
- **(d) Deferred sweep** — plugin doesn't verify in real-time. A `curator citation sweep` CLI subcommand walks the file_repo + lineage_repo at user request and produces a gap report.

**Recommendation: (d) Deferred sweep, with (a) as a Phase β upgrade once a plugin-friendly LineageRepository handle is part of `curator_plugin_init(pm)`'s contract.**

Rationale: Real-time verification (a/b/c) requires either invasive Curator core changes (b/c) or coupling the plugin to Curator's storage internals (a). A deferred sweep (d) ships immediately with no Curator core changes, gives users the same diagnostic visibility, and runs at user-controlled cadence (weekly cron, pre-release check, post-migration audit). It also matches the Atrium-product convention that introspection commands (`curator status`, `curator audit`, etc.) are user-invoked rather than always-running.

Real-time mode can be added in v0.2+ if the deferred sweep proves insufficient — but the plugin should ship with what's tractable in v0.1.

(a) hard-couples the plugin to LineageRepository internals; future Curator schema migrations break the plugin.
(b/c) require Curator core changes that may not happen on the plugin's timeline.

### DM-4 — Audit channel

**Question.** When the plugin detects a citation gap (during a sweep), where does the record live?

**Options:**
- **(a) Curator's existing audit log** — write `AuditEntry(actor='curatorplug.atrium_citation', action='compliance.citation_gap', ...)`. Same channel as `migration.move`, `trash.send`, `compliance.warned` (from atrium-safety).
- **(b) Plugin-private log table** — new SQLite table `citation_gaps`. Migration-001-style schema add by the plugin.
- **(c) Both** — write to audit log for visibility, also keep a private table for citation-specific queries.

**Recommendation: (a) Curator's existing audit log.**

Rationale: Same reasoning as atrium-safety DM-3 (which also ratified audit-log-only). Audit log entries are already the canonical "something significant happened" channel; adding a parallel log fragments the user's mental model. The audit-log table has no schema obstacle to recording citation events — `details_json` is freeform, and existing audit-log tooling will display them in the same chronological view.

If audit-log volume becomes overwhelming (thousands of citation events from a misconfigured pipeline), DM-4 can be revisited — but the deferred-sweep model (DM-3) writes one summary entry per sweep, not one per gap, so volume should be tiny.

### DM-5 — Plugin registration mechanism

**Question.** How does Curator discover this plugin?

**Options:**
- **(a) Setuptools entry point** under `[project.entry-points.curator]`.
- **(b) Explicit config import** in `curator.toml`.
- **(c) Both, with explicit config taking priority.**

**Recommendation: (a) Setuptools entry point.**

Rationale: Identical to atrium-safety DM-4 (ratified). Standard Python plugin pattern; pluggy + Curator already plumb for it.

### DM-6 — Scope vs Conclave/APEX

**Question.** Does the plugin attempt to verify citation chains *across* Atrium products (e.g., a file imported into APEX must retain its Curator lineage)?

**Options:**
- **(a) Curator-only** — plugin's responsibility ends at the Curator boundary. Cross-product enforcement is a SIP concern, deferred until SIP v1.0.
- **(b) Curator + APEX read** — plugin can read APEX's manifest.json to check that files derived from Curator-indexed sources still reference their Curator `curator_id`.
- **(c) Curator + APEX + Conclave** — full constellation enforcement.

**Recommendation: (a) Curator-only for v0.1.**

Rationale: Constitution Article IV (Cross-Product Governance) §"Cross-product safety" notes that cross-product enforcement happens via SIP-distributed safety plugins, with the SIP itself currently at v0.1. The full mechanism for cross-product invariants ratified in the SIP isn't ready yet. Shipping Curator-only enforcement in v0.1 establishes the pattern; Phase β (after SIP v1.0) can extend.

(b) and (c) couple the plugin to APEX's and Conclave's internal data structures, which violates Aim 5 (Composability — published interfaces only).

---

## 4. Architecture / hookspec usage

### 4.1 Hooks consumed

This plugin registers hookimpls for the following Curator hooks:

```python
@hookimpl
def curator_plugin_init(pm: pluggy.PluginManager) -> None:
    """Capture the pluggy reference so we can call curator_audit_event."""
    self.pm = pm

@hookimpl
def curator_source_write_post(
    source_id: str,
    file_id: str,
    src_xxhash: str | None,
    written_bytes_len: int,
) -> None:
    """Record a write observation in the plugin's in-memory state machine.

    Per DM-2 ratification, the state machine cross-references this with
    audit events to determine whether the write was cross-source. Only
    cross-source writes are flagged for citation requirement.

    Per DM-1 ratification (advisory), this hook never raises -- it just
    records.
    """
```

### 4.2 CLI surface

The plugin contributes a `curator citation` subcommand with two operations:

- `curator citation sweep [--since=<date>] [--source-id=<id>] [--json]` — run the deferred sweep per DM-3. Walks `file_repo` for files registered after `--since`, queries `lineage_repo` for inbound edges, reports gaps. Exit code 0 for no gaps, 1 if any.
- `curator citation status` — summary of the most recent sweep + count of unresolved gaps.

CLI registration uses Curator's existing CLI plugin pattern (TBD — Curator may not have a plugin-extensible CLI yet; if not, the plugin ships its own console script `curator-citation` that imports Curator's runtime).

### 4.3 Audit emission

Per DM-4 ratification, all citation events go to Curator's existing audit log via `curator_audit_event`:

| Action | When emitted | Details |
|---|---|---|
| `compliance.citation_sweep_started` | At start of `citation sweep` | sweep_id (UUID), since, source_id |
| `compliance.citation_gap` | One per gap found in sweep | sweep_id, curator_id, source_id, source_path, expected_parent_id (None if unknown) |
| `compliance.citation_sweep_completed` | At end of sweep | sweep_id, files_scanned, gaps_found, duration_seconds |

Each is a single audit entry with `actor='curatorplug.atrium_citation'`.

---

## 5. Implementation walkthrough sketch

(Filled in during P1 / P2 / P3 cycles after DM ratification, matching atrium-safety's structure.)

**P1: Plugin scaffolding + state machine + sweep skeleton.** ~3h.
- `pyproject.toml` with entry-point declaration.
- `src/curatorplug/atrium_citation/__init__.py` exposing the Plugin class.
- `src/curatorplug/atrium_citation/plugin.py` with `curator_plugin_init` + `curator_source_write_post` hookimpls.
- In-memory `WriteObservation` state machine listening to migration.* audit events.
- Skeleton `citation_sweep.py` module.
- Initial test pass: 10-15 unit tests covering hook registration, observation recording, and sweep over a populated test database.

**P2: CLI subcommand + audit emission.** ~3h.
- `console_scripts` entry for `curator-citation` (or Curator-CLI plugin if framework available).
- Argparse subcommand wiring.
- Audit emission via `pm.hook.curator_audit_event()`.
- Integration tests covering full sweep → audit emission → CLI exit code path.

**P3: Release ceremony.** ~1h.
- `CHANGELOG.md` `## [0.1.0]` entry.
- `README.md` install + usage section.
- Tag `v0.1.0` + push.
- Update `Atrium\CONSTITUTION.md` Principle 3 operationalization to reference the new plugin.
- Cross-link from `curatorplug-atrium-safety\README.md` and `Curator\README.md`.

---

## 6. Out of scope for v0.1

Captured here so future revisions know what was deliberately deferred:

- **Real-time enforcement.** DM-3's options (a/b/c) are all v0.2+ work pending Curator core API exposure.
- **Cross-product enforcement.** DM-6's options (b/c) wait for SIP v1.0.
- **Automatic gap repair.** The plugin reports gaps; it does not attempt to populate missing lineage edges (would require re-deriving provenance from filesystem state, which is fundamentally lossy).
- **Conclave-style provenance metadata format.** A plugin-defined provenance schema (file → page → claim chain) is a Conclave concern and ships separately.
- **Bidirectional verification.** The plugin checks `did this write produce a lineage edge?` It does NOT check `does every lineage edge correspond to a real file?` (i.e. orphaned edges). That's a different invariant and may warrant a separate plugin or sweep mode.
- **Performance.** First sweep over a large corpus may be slow; pagination / incremental sweep is v0.2+.

---

## 7. Document log

* **2026-05-08 v0.1 — DRAFT.** Initial design authored after Curator v1.4.0 release + Constitution v0.1 reading. Addresses Principle 3 (Citation Chain Preservation). Companion to `curatorplug-atrium-safety\DESIGN.md` v0.4 IMPLEMENTED (Principle 2). Six DMs raised for ratification (DM-1 through DM-6). Recommended decisions are observation-first / advisory / Curator-only, deliberately conservative for v0.1. Repo bootstrap (separate from this DESIGN doc) deferred until DM ratification.

---

*Awaiting Jake's affirmative ratification of DMs §3 to clear repo bootstrap + P1 implementation.*
