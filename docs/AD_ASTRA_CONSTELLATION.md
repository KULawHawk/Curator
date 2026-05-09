# Ad Astra Constellation

**Status:** v0.1 — RATIFIED 2026-05-08. Workspace-level umbrella reference.
**Authority:** Subordinate to `Atrium\CONSTITUTION.md`. This doc names and locates the constellation; the Constitution governs how its components must behave.
**Owner:** Jake Leese.

---

## What Ad Astra is

**Ad Astra** is the overarching brand and umbrella name for Jake's entire suite of tools and utilities. Individual repositories, applications, and protocols sit *under* the Ad Astra umbrella as named pillars (or "subAPEX" components, in older nomenclature carried forward from APEX-specific naming conventions).

**Important framing distinction:** the GitHub repo named `Ad-Astra` is **one element within the constellation**, not the whole brand. The constellation predates and supersedes any single repository.

The Latin phrase *ad astra* — "to the stars" — captures the organizing intent: every tool below contributes to a long-term arc of cumulative capability. No tool exists in isolation; each is a star in the same sky.

---

## Constellation map (as of 2026-05-08)

### Governance pillar

| Pillar | Status | Location | Primary doc |
|---|---|---|---|
| **Atrium** | **v0.2 RATIFIED** ✅ (2026-05-08) | `C:\Users\jmlee\Desktop\AL\Atrium\` (HEAD `adabcb7`) | `CONSTITUTION.md` v0.2 (Ten Aims + Five Non-Negotiable Principles); ratified by Jake Leese 2026-05-08. |

Atrium is the constitutional layer. Every other tool below ultimately answers to Atrium's principles via pluggy hookspecs (in Curator's case) or by direct convention (everything else). The Constitution was ratified at v0.2 by Jake Leese on 2026-05-08, making it the binding governance document for all constellation work.

### Infrastructure pillars

| Pillar | Status | Location | What it does |
|---|---|---|---|
| **Curator** | **v1.4.0 stable** ✓ (released 2026-05-08) | `C:\Users\jmlee\Desktop\AL\Curator\` (HEAD `710445f`, tag `v1.4.0`) | Content-aware artifact intelligence layer. SQLite index + plugin framework + lineage tracking + Tracer migration engine + MCP server + PySide6 GUI. The most complete pillar by lines of code and test coverage. |
| **curatorplug-atrium-safety** | **v0.3.0** ✓ | `C:\Users\jmlee\Desktop\AL\curatorplug-atrium-safety\` (HEAD `8399318`) | Constitutional plugin enforcing Atrium **Principle 2 (Hash-Verify-Before-Move)** via the `curator_source_write_post` hook. Soft-enforcement model: refuses non-compliant writes by raising `ComplianceError`, which Curator turns into `MigrationOutcome.FAILED`. |
| **curatorplug-atrium-reversibility** | DESIGN-only ✅ (now on GitHub) | `C:\Users\jmlee\Desktop\AL\curatorplug-atrium-reversibility\` (HEAD `84ee978`, repo `https://github.com/KULawHawk/curatorplug-atrium-reversibility`) | Constitutional plugin for Atrium **Aim 2 (Reversibility)**. Architectural skeleton in `DESIGN.md` (~28 KB); implementation paused awaiting Jake's prioritization vs other ecosystem work. |
| **curatorplug-atrium-citation** | **DESIGN v0.1 DRAFT** 📄 | `C:\Users\jmlee\Desktop\AL\curatorplug-atrium-citation-DESIGN.md` (workspace-level draft, not yet a separate repo) | Constitutional plugin for Atrium **Principle 3 (Citation Chain Preservation)**. Verifies that cross-source writes produce corresponding `lineage_edges`. Six DMs awaiting Jake's ratification; recommended decisions are observation-first / advisory / Curator-only for v0.1. |
| **Synergy** | v0.2.2 stable | `C:\Users\jmlee\Desktop\AL\Synergy\` | Multi-drive inventory + drift detection. Scans local + Google Drive + USB drives; produces timestamped snapshots with SHA256 hashes; detects cross-drive duplicates. Per Master Scroll v0.4, Synergy is the canonical state-of-disk authority. |
| **Tracer** | shipped *as part of Curator* | `Curator\src\curator\services\migration.py` (~2200 LOC) | Cross-source migration engine. Phase 2 (resumable jobs + workers), Phase 3 (retry + 4-mode conflict resolution), Phase 4 (cross-source overwrite-with-backup + rename-with-suffix via `curator_source_rename`). |

### Indexer pillar (multi-indexer architecture)

The "indexers working together" concept lives here. Two indexers exist concurrently — current and next-generation — with the design that they will eventually swap.

| Pillar | Status | Location | Role |
|---|---|---|---|
| **Vampire** | working, single-pass indexer | `Apex\APEX_Indexer\indexer.py` + `core/` | Current production indexer. Has processed BAI, BASC3, BDI II, MMPI3, MMPI3 ES, PAI, RBANS Update, Rorschach, TAT Resources, Vineland 3, WAIS5, WAISIV, WISCV. |
| **Succubus** | DESIGN LOCKED, NOT BUILT | `Apex\apex_v2\architecture\INDEXER_ARCHITECTURE.md` (23 KB, 2026-04-25) | Next-generation 3-lane indexer: text-extraction lane + structure-detection lane + cross-reference lane. Lanes cross-validate to push toward 99.5% accuracy. Will supersede Vampire when built. |
| **APEX integrated design** | spec'd | `Apex\APEX_INTEGRATED_INDEXER_GUI_SCORING_DESIGN_v1_0.md` (60 KB, 2026-05-06) | Integrates indexer + GUI + scoring engine. Most current evolution of the platform-level vision. |
| **APEX↔Curator interface** | spec'd | `Apex\APEX_ARCHITECTURE_INVENTORY_for_Curator_v1_0.md` (31 KB, 2026-05-07) | How APEX consumes Curator's index + lineage + audit log. Defines the seam between the two pillars. |

### Assessment pillars (clinical/forensic instruments)

| Pillar | Status | Codename origin | Scope |
|---|---|---|---|
| **Inkblot** | v1.1.0 LIVE (deterministic R-PAS/CS scoring) | Rorschach inkblot test | Rorschach scoring engine + KB combined per Master Scroll §22.10 (engine + KB version together; serve a single operational purpose). RCS_01-08 indexed; RCS_09-14 PARKED; RCS_18-20 outstanding. Per Constitution §9, classified PROJECTIVE_RULE_CODED. |
| **Latent** | active development | "latent traits/risks" — forensic clinical resonance | FAP Engine (Forensic Assessment Protocol Engine, v1-v3). Cross-instrument forensic assessment framework. Covers all PY397 instruments. 16 KB files. |
| **Id** | COMPLETE (4 indexed files) | Freudian terminology | Psychodynamic Diagnostic Knowledge. PDM-2, OPD reference base. |
| **Cocoon** | established session-continuity prompt | metaphor for emergence/continuity | Named session-resumption prompt for Jake's projects (alongside "Relay"). |
| **Locker** | PY638 complete; PY428 paused | "academic locker holding course materials" | Course tools. PY638 (Psychometrics) work is COMPLETE. PY428 (Statistics) work PAUSED. Holds whiteboards: `WHITEBOARD_PY638_PSYCHOMETRICS.md`, `WHITEBOARD_PY428_STATS.md`. **Promotion flag:** if psychometrics grows into a shared resource other subsystems consume, it may be promoted out of Locker into its own codenamed subsystem. |

### Knowledge & infrastructure pillars

| Pillar | Status | Location | What it does |
|---|---|---|---|
| **RCS Knowledge Base** | active, build protocol v3.0 | Google Drive `1py38t20LyDJB84uIeaPlD14Je3AeRL8g` (23+ .md files + 4 source PDFs) | Forensic-grade Rorschach Comprehensive System coding reference. Targeting ≥99.5% accuracy. Recently pivoted to Topic-By-Card Index architecture. 31 F-codes, three-class source structure (Type A/B/C), trap codes F29-F31. |
| **UAP** | files 01-09 complete | per project notes | Universal Assessment Protocol. Cross-instrument AI assessment framework. Three-lane indexer (Programmatic / Offline / Online). Includes Cartegraph (extraction protocol), Skiptrace (full-protocol trigger), Collage (file-splitting for platform size limits). |
| **Opus / Scrapbook** | stable | `C:\Users\jmlee\Desktop\AL\Scrapbook\` (folder name retained for compatibility) | Multi-AI query tool. Queries Gemini 2.5, Claude (Haiku/Sonnet/Opus), Ollama (phi3/mistral) against Jake's local topic library. Python/pywebview. "Scrapbook" is now an alias for the new codename Opus. |
| **Automaton** | active | `C:\Users\jmlee\Desktop\AL\Automaton\` | Automation utilities (24 files). |
| **gristle** | early | `C:\Users\jmlee\Desktop\AL\gristle\` | (5 files; details TBD) |
| **Isnt it Iconic** | active | `C:\Users\jmlee\Desktop\AL\Isnt it Iconic\` | (5 files; details TBD) |

---

## Naming conventions

* **Supernatural-themed indexer family:** Vampire, Succubus (extractor entities). The theme is "draws content out of source PDFs." Discussed and ratified in past Apex hand-off sessions.
* **subAPEX numbering:** legacy nomenclature where each pillar had a `subAPEX{N}` index alongside its codename (e.g., subAPEX1=Inkblot, subAPEX2=Vampire, subAPEX3=Succubus, subAPEX4=Latent, subAPEX5=Opus, subAPEX7=Id, subAPEX8=Cocoon, subAPEX9=Locker, subAPEX12=Synergy). Numbering was preserved across rename events. **Current direction:** Ad Astra umbrella naming supersedes APEX-specific subAPEX numbering for cross-cutting docs; pillar codenames remain in active use.
* **Versioning across pillars:** semver where it makes sense (Curator 1.4.0, atrium-safety 0.3.0, Synergy 0.2.2). Some pillars use date-stamped versions (RCS Build Protocol v3.0). No global version is imposed.

---

## Atrium Constitution structure (v0.2 ratified 2026-05-08)

Atrium's `CONSTITUTION.md` v0.2 distinguishes between two governance categories:

**Article I — Ten Aims** (ordinary-amendment authority):
1. Accuracy
2. Reversibility → mapped to `curatorplug-atrium-reversibility`
3. Self-sufficiency
4. Auditability
5. Composability
6. Portability
7. Comprehensiveness *(added in Constitution v0.2, 2026-05-08)*
8. Validity *(added in Constitution v0.2; psychometric construct)*
9. Reliability *(added in Constitution v0.2; psychometric construct)*
10. Fidelity *(added in Constitution v0.2; cross-cutting with Principle 3 Citation Chain)*

**Article II — Five Non-Negotiable Principles** (reinforced-amendment authority — require 7-day waiting period + amendment codeword):
1. The MORTAL SIN Rule (no deletion of assessment-derived artifacts)
2. Hash-Verify-Before-Move → enforced by `curatorplug-atrium-safety` v0.3.0
3. Citation Chain Preservation → design drafted as `curatorplug-atrium-citation` (v0.1 DRAFT)
4. No Silent Failures (behavioral / cross-cutting; not currently a separate plugin)
5. Atomic Operations Where Atomicity Is Claimed (per-tool implementation discipline; not currently a separate plugin)

Principles are stricter than Aims: amending a Principle requires the amendment codeword (proposed: `Keystone`) AND a 7-day waiting period AND written rationale. Aims are merely strongly normative and amend by ordinary process.

---

## Cross-pillar relationships

```
                         Atrium (governance)
                              |
           ┌──────────────────┼─────────────────────────┐
           |                  |                         |
       Curator            Synergy                APEX (indexer suite)
           |                  |                         |
   ┌───────┴───────┐          |                  ┌──────┴──────┐
   |               |          |                  |             |
 atrium-safety  Tracer     drift-detect      Vampire      Succubus (next-gen)
                 |
         ┌───────┼────────┐
         |       |        |
   Phase 2   Phase 3   Phase 4
  (workers) (retry+   (rename
            conflict)  hook)

Assessment pillars (Inkblot, Latent, Id, Locker) consume Curator's index
+ APEX's indexers + RCS knowledge base. Opus/Scrapbook is the human-facing
multi-AI query interface that surfaces results from any of the above.

UAP (Universal Assessment Protocol) is the methodology layer that
governs how AI sessions interact with assessment instruments. UAP's
three-lane indexer concept influenced Succubus's 3-lane design.

Cocoon and Relay are session-continuity primitives — named prompts that
let work resume across context window boundaries.
```

---

## Active deferrals (as of 2026-05-08)

These are explicit "not now" items rather than forgotten tasks:

1. ~~**Atrium Constitution ratification**~~ — ✅ RATIFIED 2026-05-08 at v0.2.
2. **curatorplug-atrium-reversibility implementation** — GitHub repo created + DESIGN.md pushed at `84ee978`; P1 implementation paused awaiting Jake's prioritization.
3. **Filesystem migration to** `C:\Users\jmlee\AdAstra\` — currently everything lives at `Desktop\AL\`. The move to a dedicated `AdAstra\` folder structure under the user profile is on the wishlist but not scheduled.
4. **Succubus build** — design locked at `INDEXER_ARCHITECTURE.md` 2026-04-25; no implementation started.
5. **MCP HTTP-auth** — deferred to Curator v1.5.0 per Tracer Phase 4 v0.2 RATIFIED DM-6.
6. **Tracer Phase 2 Session B real-world demo** — Jake-required. v1.4.0 surface complete; needs gdrive OAuth + actual files to validate against real Drive responses (vs. our test mocks). Runbook: `Curator/docs/TRACER_SESSION_B_RUNBOOK.md`.
7. **`curatorplug-atrium-citation` repo bootstrap** — DMs ratified by Jake 2026-05-08; awaiting GitHub repo creation at `https://github.com/new` with name `curatorplug-atrium-citation`. Once created, P1 implementation can begin.

---

## Provenance & maintenance

* **This doc lives at:** `C:\Users\jmlee\Desktop\AL\AD_ASTRA_CONSTELLATION.md`
* **Created:** 2026-05-08, immediately after Curator v1.4.0 release ceremony, in response to Jake's clarification that "Ad Astra" names the umbrella, not just the GitHub repo.
* **Update cadence:** revise on each pillar version bump, codename change, or Constitution amendment. The doc should always reflect the *current* state — historical revisions of pillars belong in their own CHANGELOGs.
* **Authority hierarchy:** Atrium Constitution > this constellation map > individual pillar docs. If this doc and a pillar's own README disagree, defer to the pillar's README and update this doc.

---

## Revision log

* **2026-05-08 v0.1 — RATIFIED.** Initial workspace-level constellation map. Created in response to Jake's framing clarification ("the constellation suite of tools and utilities is Ad Astra"). Reflects state at Curator v1.4.0 release: Curator/atrium-safety/Synergy stable, APEX indexer suite per `INDEXER_ARCHITECTURE.md` 2026-04-25 + `APEX_INTEGRATED_INDEXER_GUI_SCORING_DESIGN_v1_0.md` 2026-05-06, assessment pillars per past Apex hand-off session, Atrium pre-ratification.
