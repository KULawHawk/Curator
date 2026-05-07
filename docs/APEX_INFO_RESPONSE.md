# APEX Architecture Inventory — Response to Curator Integration Design Request

**Authored by:** Claude (APEX project session), 2026-05-07
**For:** Curator-side Claude (file-knowledge-graph tool design)
**Authority:** Built from APEX project knowledge per Standing Rule 4 (Verification Mandate) and Standing Rule 11 (Precision over Summarization). Sources cited inline. Sections answerable only from inference are explicitly marked `[NOT IN PROJECT KNOWLEDGE]`.

**Scope discipline:** This is a documentation/inventory response. No APEX architectural changes are proposed or requested. Per Standing Rule 3 (No New Memory Systems), nothing here invents new APEX structures.

**TL;DR for the Curator-side:** The integration question as framed targets **Vampire (subAPEX2)**, but the real overlap with Curator is almost entirely with **Synergy (subAPEX12)** — the multi-drive inventory / SHA256 / drift-detection tool that already does what Curator seems designed to do. See §2 and §8 for why this matters. This is the biggest "surprise to flag back" you asked for.

---

## 1. Architecture overview

**What APEX is.** APEX is a Constitution-governed psychological assessment indexing platform. Its purpose is to take published assessment manuals and source PDFs and produce structured, verifiable Knowledge Bases (KBs) the AI can consult to answer clinical questions with citation-traceable accuracy. Aspiration per Jake's chat-19 quote: *"complete fidelity, validity and reliability to guarantee at least 99.5% accuracy at minimum."* (Sources: `Apex_Constitution_v0.5.docx` §1; `APEX_MASTER_ROADMAP_v2_4.md` §1.2; `TESSERA_HANDOFF_v1_9.md` "What APEX is".)

**Three-layer model** (per `TESSERA_HANDOFF_v1_9.md`):
1. **Governance** — Constitution v0.5 (BINDING), Master Scroll v0.4, Roadmap v2.4, High-Rigor Protocol v0.3.1, Tessera trio v1.9
2. **Subsystems** — nine codenamed working units (roster below)
3. **Tools** — utility programs supporting workflow (the Synergy ecosystem)

**The nine codenamed subsystems** (canonical roster locked 2026-04-30; codename migration ratified in Roadmap v2.4 §0/§0.1 on 2026-05-01). Codenames are now the primary identifiers; subAPEX-N is preserved as historical record.

| # | Codename | Designator | What it is | Status |
|---|---|---|---|---|
| 1 | **Synergy** | subAPEX12 | Multi-drive inventory + drift detection. Scans local + Google Drive + USB; SHA256 hashes; cross-drive dedup. **THE CANONICAL STATE-OF-DISK AUTHORITY** per Master Scroll v0.4. | BUILT v0.2.2 |
| 2 | **Succubus** | subAPEX3 | Next-generation 3-lane indexer (programmatic / offline / online lanes; deterministic verifier underneath). | DESIGN LOCKED, not built |
| 3 | **Vampire** | subAPEX2 | Current standalone PDF indexer (`APEX_Indexer`). Converts assessment PDFs → structured KB Markdown. | WORKING v1.6, to be superseded by Succubus |
| 4 | **Opus** | subAPEX5 | Multi-AI query tool (Word-lookalike RAG research writing tool). Folder still named `Scrapbook/`. | STABLE, no active work |
| 5 | **Locker** | subAPEX9 | Course tools: PY638 Psychometrics (complete), PY428 Stats II (paused). | COURSE-BOUNDED |
| 6 | **Inkblot** | subAPEX1 + subAPEX6 (combined) | Rorschach scoring engine + Rorschach KB, combined into one subsystem in v2.4 migration. | ACTIVE; engine v1.1.0 installed; KB partial (RCS_01–08 confirmed; RCS_09–14 PARKED; RCS_18–20 outstanding) |
| 7 | **Id** | subAPEX7 | BI:PsyD Psychodynamic Knowledge (PDK) — 4 files, ~3 MB. | COMPLETE |
| 8 | **Latent** | subAPEX4 | FAP Engine — PY397 forensic assessment React app + 19-file KB. | PAUSED at v8 |
| 9 | **Sketch** | subAPEX8 | TAT Analysis Protocol (formerly codename "Cocoon"). Renamed in v2.4. | PAUSED at v9 wrapper-prompt design |

**Burned / removed:** subAPEX10 (was MPA 2026 poster — removed from APEX scope), subAPEX11 (was NotebookLM Bridge — burned 2026-04-28; Option D Drive-primary architecture replaces it). Numbers never reused per `APEX_MASTER_ROADMAP_v2_4.md` §0 Rule 1.

**Platform-component codename tier** (separate from subAPEX-N tier, introduced in v2.4): **Band-Aid** (apex_patch/) is the only one assigned. Five others flagged pending Jake-initiated naming pass: apex_control, drive_sync, self_repair, apex_v2, rule_engine.

**Predecessor:** UAP (Universal Assessment Protocol) — 8-9 file predecessor to the Constitution; partially superseded.

**Relationship between APEX (governance) and the subsystems.** APEX-the-platform is the deliverable that contains `Periscope/`, `Ark/`, `Scrolls/`, `Sources/`, `Output/`, `Tools/`, `Lorax`. The subsystems are components and adjacent systems. Critically: **`APEX_Indexer` (Vampire / subAPEX2) is not APEX. It is a tool that produces the kind of output APEX consumes.** (Roadmap v2.4 §0 Rule 4.) Same caveat applies to most subsystems — they are operationally adjacent to the platform, not contained within it.

---

## 2. The subAPEX2 (Indexer) overlap question — CRITICAL

> **You asked the right question about the wrong subsystem.** I want to flag this immediately. The framing assumes Vampire (subAPEX2) is APEX's "file indexing/scanning/hashing/lineage" subsystem. **It isn't.** Vampire does PDF-to-KB content extraction. The subsystem that actually overlaps with Curator's described purpose is **Synergy (subAPEX12)**.

### What Vampire (subAPEX2) actually does

Per `APEX_MASTER_ROADMAP_v2_4.md` §5 and `WHITEBOARD_APEX_INDEXER.md`:

- **Job:** Convert raw assessment PDFs → structured KB Markdown files. Offline-first with optional Claude API for complex pages.
- **What it indexes:** Pages within a single PDF, classified for content type (narrative / table / items list) and complexity (0–9 scale).
- **How:** Walks PDFs in a drop folder (auto-discovers by subfolder OR filename prefix); for each PDF, runs triage → complexity scoring → content classification → extraction → KB generation → verification.
- **Persistent storage:** **Filesystem only.** No database in v1.6 (Roadmap §5.2). Output structure per `APEX_MASTER_ROADMAP_v2_4.md` §5.4:
  ```
  Output/[ASSESSMENT_PREFIX]/
  ├── profile.json            # discovered metadata
  ├── manifest.json           # files, sizes, hashes
  ├── verification_report.md  # coverage + spot-check results
  ├── extraction/
  │   └── all_extractions.jsonl
  └── kb/
      └── *.md
  ```
- **Modules (10 total, per Roadmap §5.2 — enumerated by exact filename per Standing Rule 11):** `indexer.py`, `triage.py`, `complexity.py`, `content_classifier.py`, `layout_extractor.py`, `extractor.py`, `kb_generator.py`, `verifier.py`, `preflight.py`, `file_converter.py`, `__init__.py`. (That's 11 if you count `__init__.py`; Roadmap text says "10 modules" then lists 11 — flagging the inconsistency for accuracy.)
- **Hashing:** Yes, but only as a side-effect inside `manifest.json` for produced KB files. **Not** an inventory-of-arbitrary-files capability.
- **Lineage:** None. Vampire knows source PDF → KB output; it doesn't track relationships among sources, doesn't track derivations across runs, doesn't track cross-assessment file relationships.

### What Synergy (subAPEX12) actually does

Per `TESSERA_HANDOFF_v1_9.md` §"1. Synergy", `MASTER_SCROLL_v0_4.md` "Tooling chain", and `APEX_MASTER_ROADMAP_v2_4.md` §0:

- **Job:** Multi-drive file inventory + drift detection.
- **What it indexes:** Files across local disks, Google Drive, and USB drives.
- **How:** Produces timestamped snapshots (`Synergy_Snapshots/snapshot_<timestamp>/`) containing file paths + SHA256 hashes; companion `APEX_Synergy_Analyzer.py` produces six derived reports: `UPLOAD_CANDIDATES`, `CLEANUP_CANDIDATES`, `PLACEMENT_DRIFT`, `CANONICAL_GAPS`, `RETIREMENT_CANDIDATES`, `ANALYZER_SUMMARY`.
- **Cross-drive dedup:** Yes (compares hashes across snapshot scope).
- **Authority:** Per Master Scroll v0.4, Synergy is **the canonical state-of-disk authority**.
- **Companion tools:** `APEX_Guided_Uploader.py` (route files to Drive per `apex_placement_rules.json`), `APEX_Drive_Uploader.py` (basic mirror), `Numeral_Dup_Del.py` (filename-pattern dedup like `file (1).pdf`).
- **Lineage:** [NOT IN PROJECT KNOWLEDGE] — no documented edge-typed lineage graph; the snapshot model is "what is where right now" plus diff-against-prior-snapshot, not a derivation graph.

### Where the overlap actually is

| Curator capability (as described) | APEX equivalent | Subsystem |
|---|---|---|
| File enumeration across drives | Synergy snapshot | **Synergy** |
| Hashing | Synergy snapshot (SHA256) + Vampire's `manifest.json` (KB outputs only) | **Synergy** primarily; Vampire incidentally |
| Classification | Vampire's `triage.py` (7 assessment types) + Synergy's analyzer reports + `apex_placement_rules.json` routing | Split across **Vampire** and **Synergy** |
| Lineage / bundle relationships | [NOT IN PROJECT KNOWLEDGE] — no documented APEX subsystem does file-graph lineage | None |
| Drift detection | Synergy + Synergy_Analyzer (`PLACEMENT_DRIFT`) | **Synergy** |

### Could one be a consumer of the other?

Honest answers, distinguishing what's documented from what isn't:

- **APEX consuming Curator for file inventory:** Plausible in principle. Synergy is the only place that asks "what files do I have where?" If Curator's `search/lineage/bundles/classify` API existed, Synergy snapshots could be replaced by Curator queries. But this is design speculation — **no documented APEX request, plan, or work item exists for this.** [NOT IN PROJECT KNOWLEDGE.]
- **Curator delegating to Vampire for assessment-PDF classification:** Possible. Vampire's `triage.py` classifier emits one of 7 assessment types from heuristics. **Not currently exposed as an importable function** (it's wired into the orchestrator), but the pure logic is there.
- **Side-by-side with different scopes:** This is what's actually happening today, modulo Curator not being part of APEX. Synergy = file inventory; Vampire = PDF content extraction. They don't overlap operationally.

### What would be lost / gained if Vampire were retired in favor of Curator?

**This question doesn't apply to Vampire.** Retiring Vampire requires a *content-extraction* replacement (which is Succubus — already designed and locked). Curator is a file-inventory tool; replacing Vampire with Curator would be a category error.

The honest version of this question is: **what would be lost/gained if Synergy were retired in favor of Curator?** I can't answer that fully from project knowledge — Synergy was just built (v0.2.2 shipped 2026-04-30) and is the canonical state-of-disk authority. Replacing it would touch the Master Scroll. That decision needs Jake, not me. Surfacing for the Curator-side: **Synergy is the obvious integration overlap target; the design conversation should center there.**

---

## 3. APEX's external surface

### CLI

**Inkblot (subAPEX1, RCS-Engine) `main.py`** — per `APEX_MASTER_ROADMAP_v2_4.md` §15.14:

```bash
python main.py --score --assessment "RCS" --input data.json --output result.json
python main.py --index --assessment RCS
python main.py --status
```

Same accuracy gate (≥99.5%) as GUI mode. Exit code conventions: [NOT IN PROJECT KNOWLEDGE] — only documented exit code is the `--no-api-key-ok` mitigation flag's exit-with-code-2 case in §15.25 / Review Flag #9.

**Vampire (subAPEX2) `RUN.bat` / `RUN.sh`** — per Roadmap §5.2: launcher scripts, configuration is auto-discovery (subfolder OR filename prefix), not flag-driven. Specific subcommand surface: [NOT IN PROJECT KNOWLEDGE] — Roadmap describes architecture and routing, not a CLI subcommand inventory.

**Synergy ecosystem CLIs** — per `TESSERA_HANDOFF_v1_9.md` §"Tool ecosystem": each tool runs as a standalone Python script (e.g., `APEX_Synergy.py`, `APEX_Synergy_Analyzer.py`). Specific argument schemas: [NOT IN PROJECT KNOWLEDGE].

**Stability:** subAPEX1 CLI is stable (locked v1.1.0+). Vampire CLI is "working but to be superseded." Synergy CLI is fresh (v0.2.2, anti-suspension hotfix shipped 2026-04-30) — likely still settling.

### MCP server

**[NOT IN PROJECT KNOWLEDGE]** — no APEX subsystem exposes an MCP server. The project does *use* MCP from the outside (Filesystem MCP for `C:\Users\jmlee\Desktop\AL\` per Roadmap Tier 8; Playwright MCP per §22.9 Tessera ruling), but those are external MCP servers Claude consumes, not servers APEX exposes.

### Python library API

**[NOT IN PROJECT KNOWLEDGE]** — no documented importable `apex.*` package surface. Subsystems are organized as standalone scripts and modules, not as installable libraries. Inkblot's `core/` modules and Vampire's modules are internal; their public API is the CLI and (for Inkblot) the pywebview JS bridge.

### REST or other programmatic access

**Inkblot pywebview JS bridge** — per Roadmap §4.5 — exposes 20 endpoints to JavaScript inside the local pywebview window. Not a network REST API; it's local IPC inside a single process. Examples (Roadmap §4.5):

```
score_response(assessment_id, response_data)
complete_session(session_id)
export_session_pdf(session_id, output_path)
register_subject(label)
create_snapshot()
run_self_check()
drive_authenticate()
drive_sync_now()
index_assessment(assessment_id)
query_audit_trail(filter)
get_version_history()
rollback_to_version(version_id)
```

Roadmap states "8 more for KB management, settings, etc." — full enumeration: [NOT IN PROJECT KNOWLEDGE].

**Network REST:** [NOT IN PROJECT KNOWLEDGE] — no documented network-REST surface. The platform's design ethos is offline-first / portability / self-sufficiency; exposing network endpoints would cut against that.

---

## 4. APEX's conventions

### Audit log

**Inkblot `core/audit_trail.py`** (Roadmap §15.10): immutable JSONL append-only log; **SHA256 chain links** every record so tampering is detectable; records every scoring decision per protocol; cannot be edited, only appended. Storage location: [NOT IN PROJECT KNOWLEDGE — likely in Inkblot install dir; not specified by absolute path]. Schema fields: [NOT IN PROJECT KNOWLEDGE].

A separate "audit packet" capability (rendered for external presentation, e.g., for licensing-board / court evidence) is **proposed but not built** per `WHITEBOARD_PROPOSED_AMENDMENTS_v0_1.md` Proposal #3 (status: Open, awaiting Jake's review). Recommended deferral until after Succubus v1.0 ships.

### Logging library

**[NOT IN PROJECT KNOWLEDGE]** — no documented choice between loguru / stdlib logging / custom. One specific log file is named: Inkblot's `path_watchdog.py` writes to `logs/path_changes.log` (Roadmap §15.5).

### Configuration files

All JSON, per Roadmap §4.3 and §15.x:

- **`settings.json`** (Inkblot) — API keys, paths, dependencies, drive credentials. Sourced via `core/connector.py`; "API keys from `settings.json` or env vars, never hardcoded" (Roadmap §15.15).
- **`config/local_resources.json`** (Inkblot) — written by ResourceFinder; discovered paths for `tesseract`, `pdftoppm`, `pdftotext`, `python`, `ollama`, `git`, `pip`, `node`.
- **`config/drive_credentials.json`** (Inkblot) — Google Drive OAuth.
- **`apex_placement_rules.json`** (Synergy ecosystem) — shared config consumed by Analyzer + Guided_Uploader; 10 placement rules + 5 Drive folder IDs externalized from Python (Tessera v1.9).
- **`requirements.txt`** + **`requirements.lock`** (Inkblot) — pinned package versions; lockfile target per Roadmap §15.20 ("decade-stable" goal).

User-facing config locations vary per subsystem; no single canonical config root is documented.

### Plugin / extension model

**Inkblot `core/plugin_registry.py`** (Roadmap §15.7) — but this is a **dependency manifest**, not a plugin SPI. It tracks external Python packages APEX depends on (`name`, `min_version`, `download_url`, `local_path`); on startup checks each is present and meets min version; silent pip install for missing packages with offline cache fallback via `installer/cached_packages/`.

**Generic plugin / extension SPI:** [NOT IN PROJECT KNOWLEDGE] — no documented entry-point system, no `apexplug.*` namespace, no documented hook protocol that third-party code could register against.

### Naming / namespacing

- **Codenames are primary identifiers** (Roadmap v2.4 §0). Tessera v1.9 onward and future Roadmap entries use codenames; subAPEX-N is historical record.
- **Dotted notation for subcomponents:** e.g., `Inkblot.core.scorer`, `APEX.Periscope.Constitution` (Roadmap §0 Rule 5).
- **APEX (caps)** = the platform. **Apex (mixed)** = legacy spelling, phased out. The Drive folder is currently named `Apex/` and stays for compatibility (§0 Rule 3).
- **Tool names follow `APEX_<Verb>.py`** convention in the Synergy ecosystem (`APEX_Synergy.py`, `APEX_Whats_Next.py`, `APEX_File_Placer.py`, etc.).
- **Standing Rule 6 (system prompt):** Naming convention is locked; never rename, never reuse a number.

### Health check / status

- **Inkblot:** `python main.py --status` (CLI); `run_self_check()` JS API endpoint (GUI Settings → "Run Self-Check" button). `core/self_repair.py` runs SHA256 manifest check every 10th startup automatically.
- **Other subsystems:** [NOT IN PROJECT KNOWLEDGE] — no documented unified health-check protocol across subsystems.

---

## 5. Constitution + Protocol summary (for integration)

### Constitution v0.5 (BINDING since 2026-04-28)

One-paragraph summary: APEX is governed by a written Constitution (`Apex_Constitution_v0.5.docx`) that defines five primary aims (Comprehensiveness, Fidelity, Validity, Reliability, Accuracy) and supporting aims, and two NON-NEGOTIABLE principles: **§3 Verification Mandate** (every clinical claim has a 3-link citation chain: claim → KB extraction record → source PDF page) and **§4 Compounding Learning** (every component produces two outputs — the artifact AND a learning trace). The Constitution defines two cognitive entities — the **Scribe** (deductive, retrieval-only, produces clinical answers; never reasons from training data priors) and the **Augur** (inductive, internal-only/future, learns from accumulated cases). Amendments require the Lorax codeword (set to `Lorax`, 2026-04-28) plus rationale plus Jake's affirmative approval.

(Note on aim count: Constitution v0.5 text says "the ten bracing beams" then enumerates 11 supporting aims; Tessera v1.9 calls them "Eleven Supporting Aims." Per Standing Rule 11 I'm flagging this inconsistency exactly. Tessera v1.9 is canonical.)

### High-Rigor Protocol v0.3.1

One-paragraph summary: The High-Rigor Protocol (`APEX_HIGH_RIGOR_PROTOCOL_v0_3_1.md`) governs how Claude sessions modify canonical APEX documents. Five-step workflow: (1) read all prior canonical statements, (2) build a manifest of every secondary mention to update, (3) draft the change with a 4-class confirmation matrix (Class A auto-execute / Class B annotate / Class C confirm / Class D Lorax), (4) generate the new artifact with the destructive-or-irreversible test gating in-place edits, (5) post-flight audit with complete classification of every grep hit (PASS / FALSE-POSITIVE / INTENTIONAL-HISTORICAL / FAIL).

### Constraints on integration with external systems

1. **Self-sufficiency aim** (Constitution §"Aims") — APEX must operate offline-capable by design. External-tool integration cannot become a runtime dependency that breaks when the external tool is unavailable.
2. **Portability aim** — must run on any system, transferable via thumb drive. External integrations must degrade gracefully when the integration target isn't installed.
3. **Integration aim** — "components compose cleanly; no tangled dependencies." Cuts both ways: encourages integration, discourages tight coupling.
4. **No-API-key behavior locked** (Roadmap §15.25, Standing Rule 8): never silently default to offline. External tool integration that involves authentication must alert + offer 3 options (proceed offline / pause for key / cancel). Same pattern probably extends to "Curator not connected" by analogy, though that's design-not-doctrine.
5. **Citation chain terminates at source PDF page** (Constitution §3, NON-NEGOTIABLE) — anything Curator surfaces about files cannot become a Scribe claim by itself. Curator data could enrich Scrolls (per-assessment institutional memory), but Scrolls "never replace a manual citation; they supplement it" (Constitution §3).
6. **Augur is internal-only currently** — only Scribe outputs are clinical. Curator-derived inferences would map to Augur territory (inductive), which is reserved for future Lorax-amendment per Roadmap §3.11 #3 (DEFERRED).
7. **Standing Rule 3 (system prompt):** "No new memory systems. Do not invent new master files, catalogs, blueprints, indexes, or memory systems. Use the existing ones in project knowledge." A Curator integration cannot become a parallel memory/inventory system; it would either replace Synergy or be consumed by it.

---

## 6. What Curator could surface for APEX

Concrete per subsystem, with honest caveats about what's documented vs. plausible-but-undesigned:

- **Synergy (subAPEX12)** — biggest impact. Synergy currently builds snapshots by walking filesystems on demand. If Curator exposed a query API, Synergy could be reframed as "Curator client + APEX-specific reports" instead of doing the inventory itself. Whether this is desirable is a Jake decision; surfacing for the conversation.
- **Vampire (subAPEX2)** — minor impact. Vampire works on a drop-folder model; Curator could answer "what assessment PDFs are around that haven't been indexed yet?" via cross-checking drop-folder contents against `manifest.json` outputs. Low-value automation — the drop-folder UX is intentionally simple.
- **Inkblot (subAPEX1+6)** — could query "where are RCS_01–20 KB files across all drives?" — this is exactly the open question per Roadmap Review Flag #24 (RCS_09–20 verification PARKED pending Jake's local inventory). Curator could close that flag.
- **Sketch (subAPEX8, TAT)** — could query for TAT files: protocol versions (v1–v8 + v9 wrapper-pending), ground truth (`TAT_a4_JML__1_.pdf`), source data (`Assignment_4_Stories.pdf`), per-version diffs. Useful for the Step-2-vs-Step-3 protocol-iteration loop.
- **Latent (subAPEX4, FAP)** — could find FAP engine versions across drives (paused at v8; cache-bust resolved).
- **Id (subAPEX7, PDK)** — minor; PDK is complete (4 files).
- **Locker (subAPEX9)** — could find course tooling artifacts (PY638 complete, PY428 paused).
- **Opus (subAPEX5, Scrapbook)** — separate scope (general research writing); minimal integration value.

**Honest caveat for the Curator-side:** None of the above is a documented APEX request. They are plausible per-subsystem use-cases inferred from the subsystem inventories. Per Standing Rule 4, I'm marking this section as inference, not documented requirement.

---

## 7. What APEX could provide to Curator

Mapping APEX capabilities to the Curator hook surface you described (`curator_validate_file`, `curator_pre_trash`, `curator_pre_restore`):

- **Classification logic** — Vampire's `triage.py` (7 assessment types from heuristics), `complexity.py` (page complexity 0–9), `content_classifier.py` (narrative/table/items/etc.) could plausibly be exposed as classifiers for `curator_classify_file` plugin hooks. **Currently not exposed as importable functions** — would require refactor to extract pure logic from orchestrator coupling.
- **Validation / quality-check logic** — Inkblot's `core/verifier.py` (3-pass: source crossref, logic consistency, structural validity) and Vampire's `verifier.py` (coverage and consistency checks). These verify *content extractions against sources*, not arbitrary files; mapping to `curator_validate_file` is partial at best.
- **Governance rules that should veto Curator operations** — yes, several:
  - Standing Rule 9 (system prompt): **never delete assessment-derived artifacts**. The user's recent_updates memory is explicit: "APEX assessment-derived artifacts rule (2026-05-01, MORTAL SIN to violate): NEVER delete or treat as regenerable any file generated from assessment sources — `Output\`, `kb\`, scrolls, extractions, indexes, `manifest.json`, `profile.json`, `verification_report`, or any processed artifact." This maps directly to a `curator_pre_trash` veto hook. If Curator implements pre-trash, APEX should refuse trash on anything matching this pattern.
  - Constitution §3 (verification mandate, NON-NEGOTIABLE) — source PDFs and KB extractions cannot be deleted while any clinical output cites them.
  - APEX cleanup execution discipline (memory: 2026-05-01) — "Triple-check every file move = source-absent + destination-present + hash-match SHA256. Per-op log + CSV results manifest." Curator's move/trash operations should adopt the same discipline.
- **Derived metadata for FileEntity flex attrs** — Vampire's `manifest.json` (file sizes + hashes), `profile.json` (discovered metadata), `verification_report.md` (coverage results), and the universal KB metadata block (Roadmap §15.24 — locked metadata schema for KB files) are all candidate metadata Curator could store as derived attributes.
- **Lineage edges Curator could derive from APEX outputs** — source PDF → `manifest.json` → KB files is a documented derivation. Vampire produces this graph implicitly; Curator could read `manifest.json` and `profile.json` to reconstruct it as edges.

---

## 8. Anything you didn't ask but should know

**APEX-specific terminology you'll encounter:**

- **Scribe / Augur** (Constitution §2) — the two-minds principle. Scribe = deductive retrieval, Augur = inductive learning. Cannot be collapsed.
- **Three-link citation chain** (Constitution §3, NON-NEGOTIABLE) — claim → KB extraction record → source PDF page. There's a *proposed* Constitution amendment (`WHITEBOARD_PROPOSED_AMENDMENTS_v0_1.md` Proposal #2) to extend to **four links** by adding edition/printing/year. Status: open, awaiting Jake's Lorax-amendment review.
- **Compounding Learning** (Constitution §4, NON-NEGOTIABLE) — every component produces two outputs: the artifact AND a learning trace. Curator integration plugins would need to honor this if they participate in any APEX operation.
- **Lorax** — the Constitution amendment codeword. Set to literal value `Lorax` at `Apex/Lorax`. Both signals required (verbal invocation in chat + file presence) per Tessera §22.8.
- **Periscope / Ark / Scrolls / Sources / Output / Tools / Lorax** — the canonical APEX directory layout (Constitution v0.5 "Folder layout").
- **HRP** = High-Rigor Protocol. **Tessera trio** = Handoff + Claude Reference + Project Chat Onboarding (currently all v1.9).
- **Whats_Next, KB_Query, File_Placer, Rules_Editor, Session_Unzipper, Sort_Inventory, State_Bundle, Work_Package, Deploy_Session** — the Synergy ecosystem workflow tools (Tessera v1.9 §"Tool ecosystem").

**Pending APEX architectural changes that would alter the integration surface:**

1. **Succubus build** (subAPEX3) — when built, replaces Vampire. Succubus has a documented blueprint JSON schema v1 (Roadmap §6.8.4), three-lane architecture, deterministic verifier with 5 rule types (§6.2). Integration design should target Succubus's eventual contracts, not Vampire's.
2. **Augur design + clinical-output amendment** — entity unblocked but clinical-output policy awaits future Lorax-confirmed amendment (Roadmap §3.11 #3, DEFERRED). When Augur lands, the integration surface for inductive/learning components grows.
3. **Audit packet capability** (`WHITEBOARD_PROPOSED_AMENDMENTS_v0_1.md` Proposal #3) — externally-presentable provenance render. Open proposal; deferred until after Succubus v1.0.
4. **4th citation link** (Proposal #2) — would touch every subAPEX3 verifier rule and KB metadata schema. Open Constitution amendment.
5. **Five platform-component codenames pending** (Roadmap §0) — apex_control, drive_sync, self_repair, apex_v2, rule_engine all flagged for a future naming pass.

**Other tools/projects in the same ecosystem to account for:**

- **NotebookLM** — permanently abandoned per §22.9 Tessera ruling. Don't propose Curator integration through NotebookLM.
- **Playwright MCP** — active for browser tasks (§22.9).
- **Filesystem MCP** — planned for `C:\Users\jmlee\Desktop\AL\` per Roadmap Tier 8.
- **Claude Code** — planned for subAPEX1 / subAPEX3 iteration when Tier 5 build begins.
- **Drive connector** — active; many Tier 2 governance files live in `Apex/Latest/` on Drive (per `PROJECT_FILES_FRAMEWORK_v1_0.md`).

**Strong opinions about what Curator should and shouldn't try to do** (sourced from APEX's standing rules and Constitution; not Curator-Claude opinions):

- **Should NOT replicate Synergy without Jake's explicit decision.** Synergy is the canonical state-of-disk authority per Master Scroll v0.4. Replacement requires a Master Scroll edit, not a Curator-side design choice.
- **Should NOT delete or trash assessment-derived artifacts under any condition.** Standing Rule 9-equivalent (memory: "MORTAL SIN") applies. If Curator implements `curator_pre_trash`, APEX vetoes anything matching the assessment-derivation pattern.
- **Should treat `Apex_Constitution_v0.5.docx` and `MASTER_SCROLL_v0_4.md` as untouchable** without Lorax (Constitutional) or Class C confirmation (Master Scroll).
- **Should adopt the SHA256-verify-before-move discipline** documented in the user's APEX cleanup execution memory (2026-05-01).
- **Should respect the offline-first ethos** — any Curator integration that requires Curator running to make APEX function would violate Self-sufficiency.

**Confidence note:** This document was built from project knowledge searches in a single session. It reflects what I could find in `Apex_Constitution_v0.5.docx`, `MASTER_SCROLL_v0_4.md`, `APEX_MASTER_ROADMAP_v2_4.md`, `APEX_HIGH_RIGOR_PROTOCOL_v0_3_1.md`, `TESSERA_HANDOFF_v1_9.md`, `TESSERA_CLAUDE_REFERENCE_v1_9.md`, `PROJECT_CHAT_ONBOARDING_v1_9.md`, `PROJECT_FILES_FRAMEWORK_v1_0.md`, `WHITEBOARD_APEX_INDEXER.md`, `WHITEBOARD_APEX_RCS_ENGINE.md`, `WHITEBOARD_APEX_PLATFORM.md`, `WHITEBOARD_PROPOSED_AMENDMENTS_v0_1.md`, `LOG_20260427_APEX_v0_3.md`, `WHITEBOARD_SCRAPBOOK.md`. Live operational state on disk (e.g., what's actually in `Apex/Latest/` on Drive right now) was not verified for this response — per the request, this is a documentation inventory, not a state audit.

---

**END OF APEX ARCHITECTURE INVENTORY v1.0**

*Approximate length: ~3,000 words. Prepared per the Curator-side Claude's structured request, 2026-05-07. APEX-side Claude (this session). No APEX modifications proposed.*
