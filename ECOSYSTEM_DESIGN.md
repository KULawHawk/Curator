# Curator Ecosystem Design

**Status:** v0.1 — first pass synthesizing APEX architecture inventory (2026-05-07) into concrete integration design
**Date:** 2026-05-08
**Scope:** Curator + APEX integration architecture; Suite Integration Protocol (SIP) v0.1 definition; first-milestone selection
**Companion documents:**
- `docs/APEX_INFO_REQUEST.md` — the prompt sent to APEX
- `docs/APEX_INFO_RESPONSE.md` — APEX's full inventory response (canonical reference)
- `DESIGN_PHASE_DELTA.md` — Phase Δ+ roadmap (this doc supersedes some assumptions there)
- `DESIGN.md` — Curator v1.0 spec

## Document purpose

The APEX architecture inventory revealed a fundamental design surprise that
changes the ecosystem framing. This document captures the realigned
understanding and turns it into actionable architecture: where products
overlap, where they don't, what shared protocol they need, and the
smallest first-integration milestone we can ship to prove the wiring
works.

Per Jake's earlier request to "keep a record of suggestions," this also
serves as the accumulating ideas log — sections marked with **[IDEA]**
are forward-looking suggestions for him to triage, not commitments.

---

## Table of contents

1. [The Synergy/Curator overlap (THE BIG QUESTION)](#1-the-synergy-curator-overlap-the-big-question)
2. [Hard constraints from APEX's Constitution](#2-hard-constraints-from-apexs-constitution)
3. [Suite Integration Protocol (SIP) v0.1](#3-suite-integration-protocol-sip-v01)
4. [Per-product responsibility matrix (revised)](#4-per-product-responsibility-matrix-revised)
5. [First-integration milestone proposals](#5-first-integration-milestone-proposals)
6. [Realignments to existing Curator docs](#6-realignments-to-existing-curator-docs)
7. [Open decisions Jake needs to make](#7-open-decisions-jake-needs-to-make)
8. [Ideas log (accumulating)](#8-ideas-log-accumulating)

---

## 1. The Synergy/Curator overlap (THE BIG QUESTION)

### What the APEX session surfaced

The original APEX prompt anchored on **subAPEX2 (Vampire)** as APEX's
"file indexing/scanning/hashing/lineage" subsystem. **It isn't.** Vampire
is a PDF-to-KB content extractor — it indexes *pages within an
assessment PDF* for content type and complexity, not files-on-disk.

The actual file-inventory subsystem is **Synergy (subAPEX12)**. Per
APEX's Master Scroll v0.4, Synergy is "**the canonical state-of-disk
authority**." It does:

- Multi-drive file inventory (local + Google Drive + USB)
- SHA256 hashing of every file
- Cross-drive dedup via hash comparison
- Drift detection (snapshot diffs)
- Six derived analyzer reports: `UPLOAD_CANDIDATES`, `CLEANUP_CANDIDATES`,
  `PLACEMENT_DRIFT`, `CANONICAL_GAPS`, `RETIREMENT_CANDIDATES`,
  `ANALYZER_SUMMARY`
- Companion tools: `APEX_Guided_Uploader.py` (Drive routing per
  `apex_placement_rules.json`), `APEX_Drive_Uploader.py`, `Numeral_Dup_Del.py`

**This is exactly Curator's job.** The overlap is direct, not partial.
Both products do file enumeration + hashing + cross-drive dedup;
Curator goes further with lineage edges, bundles, classification, GUI,
audit log, and a plugin system.

### Why this matters

If both products run on Jake's machine, they walk the same files
twice, hash them twice, and store overlapping metadata in two
different schemas. That's wasted work AND a divergence risk: over
time, their views drift because they're updated by different processes
on different schedules.

This isn't "they collaborate" territory. It's "one of them needs to
either replace the other or become a client of the other." The APEX
session was explicit about this:

> "Synergy is the obvious integration overlap target; the design
> conversation should center there."

### The four resolution options

I see four genuinely viable paths:

#### Option A: Curator absorbs Synergy's role

**What it means:** Curator becomes the canonical state-of-disk
authority. Synergy is retired. Synergy's six analyzer reports are
re-implemented as Curator queries.

**What APEX would have to do:**
- Replace Synergy snapshot calls with Curator queries
- The six analyzer reports become CLI subcommands or MCP tools on Curator
- `apex_placement_rules.json` becomes config that Curator loads when
  the APEX integration is active
- Master Scroll v0.4 needs an edit: "canonical state-of-disk authority"
  reassigns from Synergy to Curator

**Pros:**
- Curator is more capable (lineage, bundles, plugins, GUI, audit log)
- Single source of truth
- No double-walking
- Existing Curator development continues unblocked

**Cons:**
- Master Scroll edit required (per Constitution authority structure)
- Synergy v0.2.2 just shipped 2026-04-30; retiring it shortly after is
  cognitive overhead Jake didn't sign up for
- APEX-side rework: every place that called Synergy now calls Curator
- Potential portability concern: APEX can't run on a system without
  Curator installed (violates Self-sufficiency aim)

#### Option B: Synergy becomes a Curator client

**What it means:** Synergy keeps its name, its CLI surface, and its six
analyzer reports. But internally, the snapshot walk is replaced by
Curator queries. Synergy.snapshot becomes "render Curator's current
state in snapshot format."

**What APEX would have to do:**
- Refactor `APEX_Synergy.py` to call Curator instead of walking
  filesystems directly
- Keep `apex_placement_rules.json` as Synergy's APEX-aware logic
- Keep all six analyzer reports unchanged from APEX's user POV
- Master Scroll text potentially unchanged ("Synergy is canonical
  state-of-disk authority" stays true; *how* Synergy gets that state
  is implementation detail)

**Pros:**
- Lowest disruption to APEX's interfaces
- No Master Scroll edit needed
- Synergy retains its codename, identity, and APEX-specific reports
- Synergy's APEX-aware logic (`apex_placement_rules.json`,
  `PLACEMENT_DRIFT`) stays in APEX where it belongs
- Curator and APEX both stay happy: Curator owns the state, Synergy
  owns the APEX-specific interpretation

**Cons:**
- Synergy gains a hard dependency on Curator → violates Self-sufficiency
  unless we add a fallback (Synergy walks files itself when Curator
  isn't available)
- Refactor effort on the APEX side (small but real)
- Two snapshot formats temporarily coexist (Curator's internal model +
  Synergy's snapshot output) until Synergy's snapshot is reframed as
  "render this Curator query as Synergy snapshot format"

#### Option C: Side-by-side with different scopes

**What it means:** Both products walk the same files. They happen to
agree because they're SHA256-based (Synergy) and xxh3+MD5 (Curator) —
the *facts* about files are the same regardless of hash algorithm.
Each has its own UI, its own CLI, its own audit log, its own opinions.

**Pros:**
- Zero immediate work
- Both products evolve independently
- Maximum portability (each fully standalone)

**Cons:**
- Wasteful: double walking, double hashing, double storage
- Drift risk over time: Curator detects a duplicate that Synergy
  doesn't (or vice versa) because they use different algorithms or
  different scoping rules
- **Violates APEX's Standing Rule 3** ("No new memory systems") —
  Curator-on-Jake's-machine *is* a parallel memory system to Synergy
  by definition. The APEX session flagged this.

**Honest read: this option is formally ruled out by APEX's own rules.**
Surfacing it for completeness, not as a real choice.

#### Option D: Scope-bounded coexistence

**What it means:** Synergy is for **assessment-derived files only**.
Curator handles general file inventory (everything else: code, music,
photos, documents, etc.). Synergy's `apex_placement_rules.json` only
applies to APEX's scope.

**Pros:**
- Clean conceptual boundary
- Synergy stays APEX-internal
- Curator stays general-purpose

**Cons:**
- Still wasteful where APEX assessment files and Curator's general
  scope overlap (which they will — the assessment PDFs live on the
  same drives Curator walks)
- The boundary is fuzzy: is `Apex_Constitution_v0.5.docx` an
  assessment-derived file? It's APEX-internal but not output of an
  assessment
- Still violates Standing Rule 3 if both walk the same files

### My recommendation: Phased B → A (UPDATED 2026-05-08)

Jake's framing 2026-05-08: "Synergy may be an early alpha of Curator."
This changes the recommendation from "B forever" to "B now, A eventually."

**Phase 1 (now → ~1 month):** Curator stays standalone. Ship the
Curator MCP server (Candidate 2, §5). Synergy gains an opt-in "use
Curator if available" code path with fallback to its native walk for
self-sufficiency. Synergy keeps everything else identical. **Zero APEX
disruption.**

**Phase 2 (1-3 months):** APEX subsystems start using Curator MCP
directly for non-Synergy queries — Inkblot's "where are RCS_01-20?",
Sketch's TAT version finder, Latent's FAP version locator. Synergy's
scope organically narrows to "the APEX-specific drift/placement
reports" (the six analyzer outputs).

**Phase 3 (when Synergy's only remaining unique value is the six
APEX-aware analyzer reports):** Those become a thin Curator plugin —
`curatorplug-apex-reports` — that runs against Curator's index and
emits the six reports in Synergy's format. Synergy is formally retired
in a Master Scroll edit. **Curator becomes the canonical state-of-disk
authority** per a clean Constitutional update.

The trigger for each phase is event-driven, not calendar-driven: "when
there's nothing Synergy does that Curator can't, AND APEX has migrated
its callers." Could be 2 months, could be 6.

This honors Jake's "operate independently as well as enhance one
another" principle: each phase keeps both products fully functional
standalone; the integration is opt-in and gracefully degrading.

### Original reasoning (preserved for context)

1. **Lowest disruption to APEX's interfaces.** No Master Scroll edit
   needed in Phase 1 or 2.
2. **Honors APEX's authority structure.** The "Synergy is canonical
   state-of-disk authority" claim stays true through Phase 2; the
   eventual transfer in Phase 3 is the explicit Constitutional moment.
3. **Preserves APEX's offline-first ethos** — fallback path to native
   walk in Phase 1 means Synergy stays portable; Curator becomes the
   upgrade path, not a hard dependency.
4. **Plays to each product's strengths.** Curator owns the indexing
   substrate; Synergy owns the APEX-specific drift/placement logic
   until that logic also moves to a Curator plugin in Phase 3.
5. **Most aligned with the constellation architecture.** Each product
   is fully functional standalone; they compose when both present;
   Synergy's Constitutional retirement is the explicit, deliberate
   moment, not an accidental drift.

### Where I'm not sure

Whether the Synergy refactor effort is something Jake wants to take on
soon (Option B requires APEX-side work) vs. defer (in which case
Option C is the de-facto state until then).

If Option B is the right destination but not the right *next* step,
that's fine — we can do Option C as a temporary state with the
explicit understanding that it's transitional. But that should be a
deliberate decision, not a default.

### Decisions pending

- **DE-1.** Resolution path: A / B / C / D? **Recommend B.**
- **DE-2.** Timeline: do this before or after Curator's Phase Δ
  features (Migration tool, Sync, etc.)? **Recommend after.**
  Migration tool is independently useful; tackling it first builds
  Curator confidence with APEX's discipline (SHA256 verify, hash-match
  before move) before reshaping Synergy.
- **DE-3.** Master Scroll edit needed? **Only if Option A.**

---

## 2. Hard constraints from APEX's Constitution

These are non-negotiable for any Curator/APEX integration. They
translate to concrete code/design requirements on the Curator side.

### 2.1 The MORTAL SIN rule

**APEX Standing Rule 9 / user memory (2026-05-01):**

> "NEVER delete or treat as regenerable any file generated from
> assessment sources — `Output\`, `kb\`, scrolls, extractions, indexes,
> `manifest.json`, `profile.json`, `verification_report`, or any
> processed artifact."

**Curator-side requirement:** When the APEX integration is active,
Curator's `curator_pre_trash` hook MUST veto trash operations on
files matching APEX's assessment-derivation pattern.

**Concrete plugin design:** A new plugin package, separately
distributable, that registers a `curator_pre_trash` hook. It checks
each candidate file against:

1. Path patterns: `**/Output/**`, `**/kb/**`, `**/Scrolls/**`,
   `**/Sources/**` (per APEX's canonical directory layout)
2. Filename patterns: `manifest.json`, `profile.json`,
   `verification_report.md`, `*_extractions.jsonl`
3. Optional: a configurable list of APEX folder roots from
   `apex_placement_rules.json` (if accessible)

If matched: returns `ConfirmationResult(allow=False, reason="APEX
Standing Rule 9 / MORTAL SIN: assessment-derived artifact cannot be
trashed")`. Curator's mutation slot already shows this as a friendly
dialog (v0.35 `_perform_send_to_trash`).

**This is one of the most concrete first-integration milestones
available. See §5.**

### 2.2 Self-sufficiency

APEX must operate offline-capable by design (Constitution Aims).
External-tool integration cannot become a runtime dependency that
breaks when the external tool is unavailable.

**Curator-side requirement:** Any Curator code APEX consumes (Option
B's "Synergy as Curator client") must have a graceful-degradation
path. If Curator isn't installed/running, APEX must fall back to
Synergy's native walk. **This is built into Option B by construction**
but worth stating explicitly.

### 2.3 Citation chain (Constitution §3, NON-NEGOTIABLE)

Every clinical claim has a 3-link chain: claim → KB extraction record
→ source PDF page. A *proposed* amendment extends this to 4 links
(adding edition/printing/year). Status: open.

**Curator-side requirement:** Curator-derived metadata can enrich
Scrolls (per-assessment institutional memory) but **cannot become a
Scribe claim by itself.** This affects what APEX can use Curator data
for: it's enrichment, not authority.

**Practical implication:** If APEX's TAT subAPEX (Sketch) queries
Curator for "all my TAT files modified this week," the Curator
response is a *retrieval helper*, not a clinical claim. Sketch still
generates the Scribe claim from the canonical KB sources.

### 2.4 No new memory systems (Standing Rule 3)

> "Do not invent new master files, catalogs, blueprints, indexes, or
> memory systems. Use the existing ones in project knowledge."

**Curator-side implication:** This rule formally rules out Option C
(side-by-side). Curator running on Jake's machine *is* a parallel
memory system to Synergy by definition. The honest paths are A or B
(or D for narrow scopes).

### 2.5 Hash discipline divergence

APEX uses **SHA256 throughout**: Synergy snapshots, Inkblot's
audit_trail.py for tamper-detection chains, the user's APEX cleanup
discipline ("Triple-check every file move = source-absent +
destination-present + hash-match SHA256").

Curator uses **xxh3_128 + MD5 secondary** (per `DESIGN.md` §7.2).

**Reconciliation options:**
1. Curator gains a SHA256 plugin (cheap; xxhash3 is for speed,
   SHA256 is for cross-tool compatibility) and computes both per file.
2. Curator emits SHA256-on-demand when APEX queries, computing it from
   bytes at query time (slow on first query, cached after).
3. SIP defines hash-algorithm-as-metadata: Curator reports its
   primary hash algorithm in query results, APEX adapts.

**Recommend (1):** add SHA256 to Curator's hash pipeline as a
secondary (alongside xxh3 and MD5). Cost: a few minutes per scan for
large libraries; one-time. Benefit: any APEX tool can take Curator's
SHA256 at face value, no recomputation.

### 2.6 Compounding Learning (Constitution §4, NON-NEGOTIABLE)

Every component produces TWO outputs: the artifact AND a learning
trace.

**Curator-side requirement:** APEX-facing Curator plugins should
emit learning traces. Concretely, the `curator_pre_trash` veto plugin
should log to APEX's audit trail (or a Curator-side trace that APEX
can ingest) every veto event with: file path, the rule invoked, why
it matched.

**Implementation:** Curator already has an audit log. The APEX-facing
plugin writes a richer audit entry on veto: `actor="apex-safety",
action="pre_trash.veto", details={"path": ..., "rule":
"standing_rule_9", "match_pattern": ..., "trace": "explanation"}`.
Compounding Learning satisfied by construction.

### 2.7 Hash-verify-before-move (APEX cleanup discipline)

User's APEX cleanup execution memory (2026-05-01):
> "Triple-check every file move = source-absent + destination-present
> + hash-match SHA256. Per-op log + CSV results manifest."

**Curator-side requirement:** Curator's Migration tool (Phase Δ
Feature M) must adopt this discipline. The migration_jobs and
migration_progress schema in `DESIGN_PHASE_DELTA.md` §M.4 should:
1. Record SHA256 of source before copy
2. Record SHA256 of destination after copy
3. Verify match before declaring success
4. Refuse to mark source for trash unless the hash-match passes

**This was already in §M.7 of `DESIGN_PHASE_DELTA.md` as a default;
APEX's discipline confirms it should be non-negotiable, not optional.**

---

## 3. Suite Integration Protocol (SIP) v0.1

The SIP is the contract every tool in the ecosystem follows. Tools
that follow SIP get cross-product capabilities (cross-tool audit
viewer, unified health monitor, plugin discovery, etc.) for free.
Tools that don't, work fine standalone — they just don't get the
suite-level benefits.

This section defines SIP v0.1 in concrete terms now that we know what
APEX's actual conventions are.

### 3.1 Health check protocol (REQUIRED)

Every SIP-compliant tool exposes a `<tool> health [--json]` CLI command
returning structured status:

```json
{
  "tool": "curator",
  "version": "0.41.0",
  "status": "ok",
  "checks": [
    {"name": "db_accessible", "status": "ok"},
    {"name": "schema_version_current", "status": "ok"},
    {"name": "plugins_registered", "status": "ok", "count": 7}
  ],
  "warnings": [],
  "errors": [],
  "metrics": {
    "files_indexed": 12453,
    "lineage_edges": 387
  }
}
```

**Curator side:** Already has `curator status` CLI command per Phase α.
Need to add `--json` output mode + standardize the schema. ~1h.

**APEX side:** Inkblot has `python main.py --status` and a JS API
`run_self_check()`. Aligning these to the SIP shape would require
adding `--json` output. Other APEX subsystems don't have a unified
health check — that's a SIP-driven future addition.

### 3.2 Audit log format (REQUIRED for SIP-aware tools)

JSON Lines format with required fields:

```json
{"timestamp": "2026-05-08T14:23:01Z", "tool": "curator", "version": "0.41.0", "actor": "user", "action": "trash.send", "entity_type": "file", "entity_id": "550e8400-...", "details": {...}}
```

Required fields: `timestamp`, `tool`, `version`, `actor`, `action`,
`entity_type`, `entity_id`, `details`.

Optional fields: `previous_hash` (for chained logs), `learning_trace`
(per APEX Constitution §4 Compounding Learning).

**Curator side:** Audit log already conforms to most of this.
Additions needed: `tool` field (currently implicit), `version` field
(missing). ~1h.

**APEX side (Inkblot):** Inkblot's `core/audit_trail.py` is
SHA256-chained JSONL append-only. Schema fields not documented in
APEX response — would benefit from explicit alignment to SIP fields
above. Cross-tool audit viewer becomes possible once both align.

**[IDEA]:** A standalone `audit-viewer` tool that reads SIP-compliant
audit logs from any directory and produces a unified timeline. Goes
in the Umbrella project (it's monitoring; same conceptual home).

### 3.3 SHA256 as universal hash (REQUIRED for SIP integrations)

Every SIP-compliant tool reports SHA256 hashes for files in its query
responses. Tools may use other hashes internally for speed (Curator
uses xxh3 primary), but cross-tool exchange uses SHA256.

**Curator side:** Add SHA256 as secondary hash in the pipeline.
~2-3h. See §2.5 above.

### 3.4 Configuration conventions

**The SIP recommendation is TOML; APEX uses JSON.** Both are
acceptable; the SIP's actual rule is:

- Configuration is file-based (no env-var-only config)
- Files live under `<user_data_dir>/<tool>/` per platformdirs
- Dotted-path access in code (`config.get("section.key")`)
- A `[<tool>]` top-level section is reserved for the tool's own
  settings; other sections are domain-specific
- Format may be TOML or JSON (each tool's choice)

**Curator side:** TOML, conforms.
**APEX side:** JSON, conforms.

The SIP doesn't try to force a single format — file-based +
dotted-path is enough for cross-tool readability.

### 3.5 Plugin discovery via entry points

Pip entry points namespace: `<tool>plug.<category>`.

For Curator: `curatorplug.classify`, `curatorplug.lineage`,
`curatorplug.source`, `curatorplug.safety` (for pre-trash/pre-restore
plugins).

For APEX (future, since APEX doesn't currently have a plugin SPI):
TBD when APEX adds one. Per the APEX response, no documented
entry-point system or `apexplug.*` namespace exists.

**[IDEA]:** Curator's pluggy-based hook system is mature; APEX could
adopt the same pattern for `curator_pre_trash`-style governance
hooks. This is a forward-looking suggestion; doesn't gate anything.

### 3.6 MCP server convention

When a tool exposes an MCP server, tools follow the naming pattern:

`<tool>__<verb>` — e.g., `curator__search`, `curator__inspect`,
`apex__query`, `umbrella__status`.

Tools register via standard MCP server publication; Claude (or any
MCP client) discovers via the registry. This is the SIP's most
powerful integration vector.

**Curator side:** Curator does NOT currently expose an MCP server.
Adding one is a Phase Δ candidate (would unblock APEX→Curator queries
without process-launching). ~6-10h to ship a v1.

**APEX side:** APEX exposes none currently. APEX *uses* MCP from the
outside (Filesystem MCP, Playwright MCP). APEX adding an MCP server
would be a Phase Δ-equivalent decision on their side.

### 3.7 CLI conventions

Standard flags:
- `--json` for structured output
- `--config <path>` for explicit config file
- `--verbose` / `-v`, repeatable for verbosity level
- Standard exit codes: 0=ok, 1=user error, 2=tool error, 3=remote/network error

**Curator side:** Mostly conforms; some commands lack `--json`. Audit
sweep + fix. ~2h.

**APEX side:** Mixed. Inkblot has `--no-api-key-ok` (custom exit
code 2). SIP alignment is desirable but not breaking.

### 3.8 Version and compatibility

Tools follow semver. Major version changes require:
- Explicit upgrade migration (per the Phase Δ Feature U "Update
  protocol")
- Cross-tool compatibility matrix in the suite docs

### 3.9 Naming conventions

- **Codenames are primary identifiers** when present (matches APEX's
  Standing Rule 6).
- Dotted notation for subcomponents: `Curator.gui.lineage_view`,
  `Inkblot.core.scorer`.
- Tool names follow `<Codename>_<Verb>` for utility scripts (matches
  APEX's `APEX_<Verb>.py` pattern).
- Curator is the package name `curator`; lowercase.

### 3.10 Compounding Learning (OPTIONAL but recommended)

Per APEX Constitution §4: every component produces an artifact AND a
learning trace.

For Curator: every plugin call optionally emits a learning trace via
`audit.append(actor="<plugin>", action="trace", details=...)`. Not
strictly required for non-APEX integrations but recommended for
ecosystem hygiene.

### 3.11 What SIP does NOT prescribe

- A specific UI framework
- A specific database schema
- A specific test framework
- Brand identity (each tool has its own visual language)

The SIP is about **interfaces between tools**, not internals.

---

## 4. Per-product responsibility matrix (revised)

Updated with APEX context + 2026-05-08 Conclave addition:

| Product | Domain | Standalone? | When ecosystem present |
|---|---|---|---|
| **Curator** | File knowledge graph + lineage + bundles + sync + migration. **Becomes the canonical state-of-disk authority** in Phase 3 of Synergy realignment. | ✅ | APEX's Synergy queries Curator (Phase 1+); APEX safety plugins veto Curator's pre_trash; Curator surfaces TAT/RCS/FAP files for APEX subsystems |
| **APEX** | Constitution-governed psychological assessment indexing platform. Nine subsystems each with their own scope. | ✅ | Consumes Curator for file-inventory questions; provides governance rules to Curator's safety hooks; Vampire/Succubus output flows back as Curator-readable manifests |
| **Umbrella** | Health monitoring + dep tracking + auto-Claude-troubleshoot. | ✅ | Monitors any SIP-compliant tool (Curator, APEX subsystems, Conclave, future) |
| **Nestegg** | Installer generator + system-spec-aware install + upgrade orchestration. | ✅ | Builds installers for any tool with a Nestegg manifest |
| **Conclave** (proposed 2026-05-08) | Multi-Lens ensemble indexer for assessment KBs. 5-9 indexers vote section-by-section on best output. See `docs/CONCLAVE_PROPOSAL.md`. | ✅ (planned) | Queries Curator for source files; emits APEX KB format; Umbrella monitors Lens progress; Nestegg bundles model assets |

**Removed from earlier framing:**

- "Curator vs APEX subAPEX2 (Indexer)" overlap question — *this was the
  wrong framing.* Vampire is not a file inventory tool. The real
  overlap is Synergy.

**Newly explicit:**

- **APEX subsystems that benefit from Curator integration:** Synergy
  (most), Inkblot (RCS file location across drives), Sketch (TAT file
  versions), Latent (FAP versions). Other subsystems: minimal benefit.
- **APEX subsystems Curator should integrate with for governance:** all
  of them transitively, via the safety plugin pattern (§2.1).

---

## 5. First-integration milestone proposals

The smallest shippable thing that proves the ecosystem works. Three
candidates ranked by leverage-to-effort ratio:

### Candidate 1: APEX safety plugin for Curator ⭐ RECOMMENDED

**What it is:** A separately distributable Python package
(`curatorplug-apex-safety` or similar) that registers Curator's
`curator_pre_trash` hook and vetoes trash operations on
APEX-assessment-derived files.

**What it requires:**
- New repo or subfolder under `C:\Users\jmlee\Desktop\AL\` (e.g.
  `apex-safety-plugin/`)
- Single Python file: `apex_safety.py`
- Plugin entry point in pyproject.toml: `curatorplug.safety:apex`
- Path-pattern matcher: configurable list of APEX root paths +
  filename patterns from `apex_placement_rules.json`
- Test suite: ~10 tests
- Documentation: a README explaining what the plugin does and how to
  install

**Estimated effort:** 3-4 hours.

**What this proves:**
- Curator's plugin system works for external code (not just built-in
  plugins) — validates a Phase α design assumption
- APEX governance rules can be enforced by Curator's hooks without
  Curator depending on APEX
- The two products can share rules without sharing dependencies
- The MORTAL SIN rule is enforceable (currently it's documented but
  not technically prevented)

**Why it's the best candidate:**
- Tiny scope, big symbolic value
- Solves a real problem (the MORTAL SIN rule is enforced by humans
  today; one mistake = catastrophic data loss)
- Doesn't require any APEX-side changes
- Doesn't require any decisions on Synergy/Curator overlap (that's
  a much bigger question — see §1)
- Could ship this week

**Decisions to make before starting:**
- Repo location: standalone `apex-safety-plugin/` or under `Apex/`?
- Path pattern config: hardcoded vs. read from `apex_placement_rules.json`?
- Plugin name: `curatorplug-apex-safety`, `apex-curator-safety`,
  `curatorplug.apex`?

### Candidate 2: Curator MCP server (read-only `query` tool)

**What it is:** A minimal MCP server exposing Curator's read APIs
(search, lineage, bundles, classify) as MCP tools that any client
(including APEX subsystems) can call.

**What it requires:**
- New module `src/curator/mcp/server.py`
- Implementation using the official `mcp` Python SDK
- Endpoints: `curator__search(filter)`, `curator__lineage(file_id)`,
  `curator__bundles(file_id)`, `curator__inspect(file_id)`,
  `curator__classify(path)`
- Test suite: ~15 tests
- Documentation: how to register the server with Claude and other clients

**Estimated effort:** 6-10 hours.

**What this proves:**
- Curator can be a service, not just a tool
- The SIP MCP convention works
- APEX subsystems can query Curator without process-launching

**Why it's a strong candidate:**
- Unblocks the Synergy-as-Curator-client refactor (Option B) by
  giving Synergy something concrete to query
- Forward-looking: Umbrella will need to query tools too; same MCP
  pattern applies
- Resolves the "how does APEX get Curator data" question once for all
  subsystems

**Why it's not first:**
- 2-3x the effort of Candidate 1
- Doesn't have the immediate "prevent data loss" payoff
- More design decisions involved (auth? rate limiting? caching?)

### Candidate 3: SHA256 secondary hash in Curator

**What it is:** Add SHA256 as a secondary hash alongside xxh3 in
Curator's hash pipeline. Surface in `FileEntity.hash_sha256` field.

**What it requires:**
- Update hash service to compute both
- New schema column on `files` table
- Migration to add the column (existing files get `NULL` until
  re-hashed)
- Update CLI/GUI to surface the new hash where relevant

**Estimated effort:** 3-5 hours (plus a re-hash pass for existing
files, which is bounded by I/O).

**What this proves:**
- Cross-tool hash compatibility
- Curator's schema can evolve cleanly

**Why it's a candidate but not the first:**
- Useful but doesn't directly enable any new integration
- Better paired with Candidate 1 or 2

### Recommendation: Candidate 1 first, then Candidate 2

**Path:**
1. Ship the APEX safety plugin (3-4h). Validates the plugin
   architecture, prevents real data loss, no APEX changes needed.
2. After v0.42 work or after one more Curator increment: Curator MCP
   server (6-10h). Sets up Synergy-as-Curator-client option.
3. SHA256 secondary hash (3-5h) bundled with Migration tool work
   (since migration is where hash discipline matters most).

Total ecosystem-integration effort to "first real win banked":
**~3-4 hours**. Beautiful leverage.

---

## 6. Realignments to existing Curator docs

### 6.1 `DESIGN_PHASE_DELTA.md`

The original Phase Δ doc had Feature A (asset monitor) and Feature I
(installer) as Curator features. After Jake's ecosystem realignment
(2026-05-08 chat), these become Umbrella and Nestegg as standalone
products. **The Phase Δ doc needs a brief banner pointing readers
here.** Do this in a small edit; don't rewrite.

The Phase Δ doc's framing of "Curator's role vs APEX subAPEX2
overlap" is now superseded by §1 of this document. Add a `[REVISED:
see ECOSYSTEM_DESIGN.md §1]` note to Feature M's intro.

### 6.2 `DESIGN.md` (the v1.0 spec)

DESIGN.md doesn't mention APEX at all (correctly — APEX context
wasn't available when it was written). Keep it that way. The
ecosystem integration belongs in this doc, not in the v1.0 spec.

What COULD update in DESIGN.md:
- §7.2 (hash policy): mention SHA256 as upcoming secondary hash for
  cross-tool compatibility (when Candidate 3 ships)
- §15.2 (GUI views): mention that an "APEX" tab might appear when the
  apex-safety plugin is active, showing recently-vetoed operations
  (this is a future feature; flag in the doc but don't build yet)

### 6.3 `BUILD_TRACKER.md`

No edits. The build tracker is an as-built history; ecosystem design
is forward-looking and lives in design docs.

### 6.4 New doc proposal

This doc itself (`ECOSYSTEM_DESIGN.md`) becomes the canonical
ecosystem reference. Update as decisions are made (mark `[OPEN]`
items as `[DECIDED]` etc.).

---

## 7. Open decisions Jake needs to make

Aggregating all `[OPEN]` items from above. Mark each as you decide.

### Synergy/Curator overlap (§1)
- **DE-1.** Resolution path: A (Curator absorbs Synergy) / B (Synergy
  becomes Curator client) / C (side-by-side, ruled out by APEX rules) /
  D (scope-bounded). **Recommended: B.**
- **DE-2.** Timeline for whichever option wins: now / after Phase Δ /
  later. **Recommended: after.**
- **DE-3.** Master Scroll edit needed (only if Option A): yes / no.

### Hard constraints (§2)
- **DE-4.** SHA256 reconciliation: add to Curator pipeline (recommended)
  / on-demand at query time / SIP metadata only.
- **DE-5.** Hash-verify-before-move discipline for Migration tool:
  non-negotiable (recommended) / configurable / advisory.

### SIP definition (§3)
- **DE-6.** SIP version 0.1 acceptable as-is, or revise before
  formalizing? (Suggested: ship as v0.1, iterate per real integration
  experience.)
- **DE-7.** Curator's MCP server: which subset of read API to ship in
  v1? (Suggested: search + lineage + bundles + inspect + classify.)
- **DE-8.** SIP audit log alignment: Curator add `tool` and `version`
  fields (recommended). Inkblot publish its schema fields for SIP
  alignment.

### First-integration milestone (§5)
- **DE-9.** Which milestone first: Candidate 1 (APEX safety plugin) /
  Candidate 2 (Curator MCP server) / Candidate 3 (SHA256). **Recommended:
  Candidate 1.**
- **DE-10.** Repo location for APEX safety plugin: standalone
  `apex-safety-plugin/` / under existing `Apex/` / under `Curator/`.
  (Suggested: standalone, since it depends on neither codebase but
  bridges them.)
- **DE-11.** Plugin name: `curatorplug-apex-safety` /
  `curatorplug.apex` / something else.

### Realignments (§6)
- **DE-12.** Update `DESIGN_PHASE_DELTA.md` with banner pointing to
  this doc: yes / no. (Recommended: yes.)
- **DE-13.** Promote Umbrella + Nestegg discussions out of
  `DESIGN_PHASE_DELTA.md` entirely (per the ecosystem realignment) and
  into their own design docs when those projects start: yes / no.
  (Recommended: yes, when ready.)

---

## 8. Ideas log (accumulating)

Per Jake's request to "keep a record of suggestions" — these are
forward-looking ideas, NOT commitments. Triage at your leisure.

### Cross-product capabilities

- **[IDEA-00]** **Conclave** — multi-Lens ensemble indexer. Full
  proposal at `docs/CONCLAVE_PROPOSAL.md` (Jake-requested 2026-05-08).
  Standalone constellation product. ~120h to v1.0; phased.
- **[IDEA-01]** A standalone `audit-viewer` tool that reads
  SIP-compliant audit logs from any directory and produces a unified
  timeline. Lives in the Umbrella project. ~6-10h.
- **[IDEA-02]** A `suite-doctor` CLI: runs SIP-compliant `health`
  commands against every installed tool and reports aggregate status.
  Foundation for Umbrella. ~3-5h.
- **[IDEA-03]** Cross-tool config sync: when both APEX and Curator
  are installed, sync `apex_placement_rules.json` paths into
  Curator's source registry automatically (Curator becomes "aware"
  of APEX folders). ~2-3h. Risk: tight coupling; only if Option B
  resolution is chosen.
- **[IDEA-04]** Shared `<tool> version` registry: each tool publishes
  its version + dependency manifest to a known location;
  Umbrella consumes for version compatibility checking.

### Curator-side plugins from APEX integration

- **[IDEA-05]** `curatorplug-apex-safety` — see §5 Candidate 1. The
  first concrete plugin.
- **[IDEA-06]** `curatorplug-apex-classify` — exposes Vampire's
  `triage.py` (7 assessment types) as a Curator classifier. Requires
  refactoring Vampire's pure logic out of orchestrator coupling.
  Defers until Vampire→Succubus migration is closer.
- **[IDEA-07]** `curatorplug-apex-lineage` — reads Vampire's
  `manifest.json` outputs and emits LineageEdge entries connecting
  source PDFs to KB outputs. Curator gains free PDF→KB lineage when
  APEX has run. ~3-4h.
- **[IDEA-08]** `curatorplug-apex-validate` — Inkblot's
  `core/verifier.py` exposed as a Curator validator. Same caveat as
  IDEA-06 about pure-logic extraction.

### APEX-side adoptions

- **[IDEA-09]** Synergy refactor to Curator client (Option B from §1).
  Estimated 8-12h on APEX side.
- **[IDEA-10]** Inkblot adopts SIP audit log fields (`tool`,
  `version`). Trivial.
- **[IDEA-11]** APEX adopts pluggy for governance hooks (similar to
  Curator's `curator_pre_trash` pattern) so that other tools can
  register vetoes against APEX operations. Forward-looking; doesn't
  gate anything.

### Suite-level structural

- **[IDEA-12]** Pick a suite name. Earlier I proposed: Atrium,
  Conservatory, Lattice, Constellation, Refuge, Workshop, Beacon.
  After APEX context: maybe one of these aligns better, e.g.,
  **Atrium** (ties together separate buildings, central hall),
  **Lattice** (interconnected without being merged), **Beacon** (the
  health-and-status motif fits Umbrella). Not blocking; flag.
- **[IDEA-13]** Cross-tool `<suite> install` script (e.g., `atrium
  install curator umbrella nestegg`): one command pulls in the whole
  suite from a central registry. Sits under Nestegg's responsibility
  when Nestegg ships.
- **[IDEA-14]** Shared docs site (`<suite>.dev`): one place for
  cross-product user-facing documentation. Per-product detailed docs
  remain in their own repos.

### Honest "this isn't worth doing" notes

- **NOT-IDEA-01:** A unified GUI shell that combines APEX UI + Curator
  UI in one window. **Don't do this.** Each product's UI is purpose-built;
  combining them just creates a worse version of both. Cross-tool
  integration happens at the data layer, not the UI layer.
- **NOT-IDEA-02:** A single suite-wide database. **Don't do this.**
  Each product's data model serves its purpose; a shared DB just
  forces compromises that hurt all of them. SIP defines exchange
  protocols, not shared storage.
- **NOT-IDEA-03:** Auto-installing one product when another is
  installed. **Don't do this.** Independent products = independent
  install decisions. Suggestion-with-link is fine; auto-install is
  not.

---

*End of `ECOSYSTEM_DESIGN.md` v0.1. Update as decisions are made and
new APEX context arrives. Triage `[IDEA-NN]` items into your roadmap
at your pace.*
