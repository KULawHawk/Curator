# Curator Feature TODO

**Updated 2026-05-11. Captures the feature backlog discussed during the v1.7-alpha + inventory-accuracy sessions, plus the brainstorm Jake delivered after the ScanDialog work.**

Every item has:
- **ID** (stable reference, e.g. `T-A01`)
- **Status** (proposed / approved / in-progress / shipped / declined / deferred)
- **Tier** (A = already 70-100% built, B = small additions, C = medium, D = Conclave/Nestegg-dependent, E = declined)
- **Effort** (rough size: S = afternoon, M = 1-2 sessions, L = 3-5 sessions, XL = multi-week)
- **Depends on** (other features or external work)
- **Notes** (Claude's evaluation + rationale)

---

## Tier A — Already 70-100% built (immediate wins)

The heavy machinery exists. Work is wiring + a small UI/glue layer.

### `T-A01` — Fuzzy-Match Version Stacking
- **Status:** **shipped (read-only viewer) v1.7.1** (Apply semantics deferred to v1.8)
- **Effort:** M
- **Depends on:** none (uses existing `lineage_fuzzy_dup.py`)
- **What:** Collapse NEAR_DUPLICATE chains into a single stack widget. ("Draft_1", "Draft_Final", "Draft_FINAL_v2" → one stack with expander.)
- **Why high priority:** Directly relevant to Jake's RCS workflow where 31+ F-codes and 3+ card revisions per session produce exactly this pattern.
- **v1.7.1 delivery:** `LineageService.find_version_stacks()` + `VersionStackDialog` (Tools menu). Read-only — no Apply action yet. Apply (keep newest / mark canonical / bundle) deferred to v1.8 pending atrium-reversibility v0.1.
- **Notes:** False positives below 0.85 confidence happen — the dialog defaults to 0.70 (matches plugin threshold) but the user can dial up.

### `T-A02` — Visual Lineage Graphing (time-machine UI)
- **Status:** **shipped v1.7.5**
- **Effort:** M
- **Depends on:** none (uses existing `gui/lineage_view.py` + `LineageEdge.detected_at`)
- **What:** Add a time-slider to the Lineage Graph tab that filters edges by creation date, so you can replay how a file lineage evolved.
- **v1.7.5 delivery:** `LineageGraphBuilder.build_full_graph(max_detected_at=...)` filters edges by SQL `detected_at <= cutoff`. New `get_time_range()` returns DB MIN/MAX for slider bounds. `LineageGraphView.refresh(max_detected_at=...)` accepts the filter; persists state for argument-less calls. Tab adds a QSlider (0–100 → linear interp over time range), Play/Pause button (5 steps/sec), Show-all reset.
- **Why high priority:** Same RCS use case — see "Card VIII v1 → v2 → v3.1 → final" as an animated graph.

### `T-A03` — Watchdog Daemon Mode
- **Status:** proposed
- **Effort:** M
- **Depends on:** none (uses existing `services/watch.py`)
- **What:** Transform `curator watch` from a foreground command into a Windows Service / scheduled-task wrapper that runs continuously.
- **Notes:** Needs "Pause when on battery" toggle (use `psutil.sensors_battery()`). Without it, the laptop will overheat on long sessions.

### `T-A04` — Bit-Rot Auto-Healing
- **Status:** proposed
- **Effort:** L
- **Depends on:** `T-C01` (atrium-reversibility v0.1)
- **What:** Schedule periodic re-reads of archived files, compare against stored hashes. On mismatch: optionally auto-fetch the canonical copy from Drive to overwrite corruption.
- **Notes:** Initial ship should be **detect-only** (alert, don't overwrite). Auto-overwrite is destructive — needs rollback. That's what atrium-reversibility provides, hence the dependency. The `curatorplug-atrium-safety/verifier.py` already does the re-read primitive.

### `T-A05` — Audit-Feedback Loop ("learn from corrections")
- **Status:** deferred
- **Effort:** L
- **Depends on:** A specific decision point that needs to consume the weights
- **What:** Read audit history, produce preference signals ("user trashes .tmp files 90% of the time"), feed back into organize decisions.
- **Notes:** Defer until there's a clear consumer. Without one, this is data exhaust. Reconsider when organize/migration starts making automated decisions worth biasing.

---

## Tier B — Small additions (1-2 sessions)

New code, but slots cleanly into existing services.

### `T-B01` — Heuristic Space Forecasting
- **Status:** **shipped v1.7.2**
- **Effort:** S
- **Depends on:** none (uses existing `file_repo` + psutil)
- **What:** `curator forecast` subcommand + Tools menu dialog that linear-regresses scan history to predict when local drives hit capacity.
- **v1.7.2 delivery:** `ForecastService` with `compute_disk_forecast()` + `compute_all_drives()`. CLI command + ForecastDialog (Tools menu). 5 status states including `past_95pct` / `past_99pct` for already-over-threshold drives.
- **Why:** Zero risk, useful, ~210 lines. Caught the canonical-DB 99.8%-full signal immediately on first run.

### `T-B02` — Compliance Retention Enforcement
- **Status:** **shipped v1.7.4 (Curator-side bump) + atrium-safety v0.4.0**
- **Effort:** M
- **Depends on:** T-C02 (status column on files table) — NOW SATISFIED via v1.7.3
- **What:** `atrium-safety` vetoes trash/delete when `status='vital'`. Optional retention horizon via `expires_at`: veto lifts after horizon passes.
- **v0.4.0 (atrium-safety) delivery:** New `curator_pre_trash` hookimpl. Emits structured audit events (`compliance.retention_veto`, `compliance.retention_allow`). Graceful degradation against pre-v1.7.3 Curator (no `.status` attribute = treated as active = no veto). 11-test unit suite.
- **Override paths:** Re-classify via `curator status set <path> active`, OR set `expires_at` to a past date via `curator status set <path> vital --expires-in-days -1`. Each override is auto-audit-logged via `cli.status`.
- **Why:** Real value for forensic / IRB / HIPAA work. RCS knowledge base + assessment records should never accidentally land in trash.

### `T-B03` — Heuristic Ransomware Quarantine
- **Status:** proposed
- **Effort:** M
- **Depends on:** `T-A03` (watch.py daemon mode), `safety.py`
- **What:** Anomaly detector on watch.py events: high write-rate, extension churn (`.docx → .docx.locked`), content entropy spikes. On trigger: pause all migration jobs + alert.
- **Notes:** False positives are the killer (npm install ≠ ransomware). First ship is **alert-only**; opt-in auto-pause comes later.

### `T-B04` — PII & Sensitivity Scanning (regex baseline + Conclave hookspec)
- **Status:** **shipped v1.7.6** (regex baseline; Conclave/semantic version deferred to T-D02)
- **Effort:** M
- **Depends on:** none for regex; `T-D02` for semantic version
- **What:** Regex detector for SSN, MRN, case numbers. Organize-service routing rule: "if matches sensitive pattern, never select for migration to a destination flagged 'public'."
- **v1.7.6 delivery:** `services/pii_scanner.py` with `PIIScanner`, `PIIMatch`, `PIISeverity` (HIGH=ssn/credit_card, MEDIUM=phone_us/email), `PIIScanReport`. Methods: `scan_text(text)`, `scan_file(path)` (2 MB cap, configurable), `scan_directory(dir, recursive, extensions)`. CLI: `curator scan-pii <path> [--ext .txt --high-only --show-matches --head-bytes N]` with redacted output (last-4 chars visible). Wired into `CuratorRuntime.pii_scanner` for downstream consumers. Detect-only; routing/quarantine hooks deferred until FP/FN rate is measured against real data.
- **Why:** Forensic psych work demands it. The regex baseline gets you 90% of the value in 30 lines. Conclave will plug into the hookspec later for semantic detection.

### `T-B05` — Tiered Storage Manager (`curator tier`)
- **Status:** **shipped v1.7.8** (detect-only baseline; one-step `--apply --target` deferred to v1.8)
- **Effort:** M
- **Depends on:** existing migration + organize + safety; T-C02 status taxonomy
- **What:** `curator tier` subcommand. Scan files matching age/access criteria, propose migration plan to a "cold" source, apply on confirmation. The user-supplied `tiered_storage_manager.py` sketch is the right intent, wrong API — real version is ~300 lines using `ScanService` + `MigrationService.run_job()`.
- **v1.7.8 delivery:** `services/tier.py` with `TierService`, `TierCriteria`, `TierCandidate`, `TierReport`, `TierRecipe` enum (3 recipes). CLI: `curator tier <recipe> [--min-age-days N --source-id X --root PREFIX --show-files --limit M --json]`. Recipes: **cold** (provisional + stale >90d), **expired** (expires_at < now), **archive** (vital + stale >365d). Detect-only baseline; emits `tier.suggest` audit events. One-step `--apply --target <dst>` (which would chain into MigrationService) deferred to v1.8 — for now, users run `curator tier <recipe> --json` and feed the curator_ids into a `curator migrate` invocation.
- **Notes:** Don't generalize to a full rule engine yet; ship as a single-purpose command first. If it gets used heavily, then consider generalizing (`T-C02`).

### `T-B06` — Background OCR via pytesseract (with Conclave hookspec placeholder)
- **Status:** proposed
- **Effort:** M
- **Depends on:** Tesseract binary present on system
- **What:** Detect image-only PDFs and screenshots via `classify_filetype`. Run pytesseract in a background worker. Inject extracted text into the file's metadata and into the SQLite FTS index (when that ships).
- **Notes:** Mark the *semantic indexing* layer as a Conclave hookspec (`curator_index_semantic`). Curator ships the OCR-to-text part; Conclave later replaces the dumb keyword index with an embedding index.

### `T-B07` — Metadata-Stripping Export Pipelines
- **Status:** **shipped v1.7.7** (standalone `curator export-clean` CLI; per-source policy gating deferred to v1.8)
- **Effort:** S-M
- **Depends on:** `services/organize.py` staging
- **What:** When destination source is flagged "shareable," strip EXIF from photos (Pillow/piexif — already installed) and DOCX author metadata before staging the move.
- **v1.7.7 delivery:** `services/metadata_stripper.py` with `MetadataStripper`, `StripResult`, `StripReport`, `StripOutcome`. Handles images (EXIF/XMP/IPTC/PNG-text via Pillow re-save), DOCX (docProps/core.xml + app.xml stub-replacement via stdlib zipfile), PDF (metadata-dict clear via pypdf re-emit), and passthrough for unknown types. CLI: `curator export-clean <src> <dst> [--ext .jpg --drop-icc --show-files --json]`. Source files never modified; ICC profiles kept by default (color rendering).
- **Notes:** Per-source policy gating (`SourceConfig.strip_metadata: bool` or `share_visibility: 'private' | 'team' | 'public'`) is the v1.8 follow-up; v1.7.7 ships the stripper as a standalone CLI command so it's usable today without touching the migration/organize integration surface.

### `T-B08` — Smart OS-Level Deduplication (hardlinks)
- **Status:** proposed (cautious)
- **Effort:** S
- **Depends on:** `services/cleanup.py`
- **What:** Add `--strategy=hardlink` to cleanup. Use `mklink /H` instead of trash. Files stay visible in all original locations but share one inode.
- **Caveats:** Windows hardlinks are **same-volume only**. Office apps "break" hardlinks on save by writing a new file. Only safe for **archival** directories where files won't be edited.
- **Notes:** Useful but narrow. v1.9 candidate.

### `T-B09` — Cross-Platform Metadata Translation
- **Status:** proposed
- **Effort:** M
- **Depends on:** `gdrive_source.py`
- **What:** When Windows-illegal-on-Linux names move to Drive, write a `.curator-meta.json` sidecar preserving the original name + alternate data streams. Restore on download.
- **Notes:** Sidecar files create their own lineage edges — design needs care to avoid noise.

### `T-B10` — Bandwidth-Aware Sync Profiling
- **Status:** deferred
- **Effort:** S
- **Depends on:** `migration.py`
- **What:** `--schedule` option that defers migrations to a configurable off-peak window.
- **Notes:** Trivial to add. Defer until Jake actually hits bandwidth pain. Low priority.

---

## Tier C — Medium additions (3-5 sessions)

Real new infrastructure.

### `T-C01` — atrium-reversibility v0.1
- **Status:** designed (`Atrium/design/LIFECYCLE_GOVERNANCE.md`), not built
- **Effort:** XL (3-6 sessions per original estimate)
- **Depends on:** Atrium constitution
- **What:** Universal lifecycle library. State machine `PROPOSED → STAGED → FINALIZED | ROLLED_BACK`, filesystem snapshots via robocopy, blocked-versions registry at `~/.atrium/blocked_versions.json`, 7-day burn-in default.
- **Why critical:** Several Tier A/B features (`T-A04`, `T-C04`, `T-C05`) depend on reversibility. Build this *before* any auto-destructive feature.

### `T-C02` — Asset Classification Taxonomy (status: vital/active/provisional/junk)
- **Status:** **shipped v1.7.3 (foundation)**
- **Effort:** M-L
- **Depends on:** Schema migration
- **What:** Add `status`, `supersedes_id`, `expires_at` columns to `files` table. New `curator status set/get/report` subcommands. Matching MCP tool.
- **v1.7.3 delivery:** Migration 003 + FileEntity fields + 4 new FileRepository methods + 3 CLI subcommands. Applied transparently to canonical 86,943-file DB on first run. GUI/MCP integration deferred.
- **Notes:** This is the foundation for `T-B02`, `T-B05`, `T-A05`, `T-C03`. Worth building early.

### `T-C03` — Virtual Project Overlays
- **Status:** proposed
- **Effort:** L
- **Depends on:** `T-C02` (classification taxonomy makes filters more useful)
- **What:** Saved-query bundles. Aggregate files across multiple sources into a virtual view without moving them. Browser tab gets a "Virtual" filter dropdown.
- **Notes:** Start with **saved filter bookmarks** (simple). Don't build a query language unless that's not enough.

### `T-C04` — State-Based Automation / Desired-State Model
- **Status:** deferred until `T-B05` ships and proves the need
- **Effort:** XL
- **Depends on:** `T-C01` (rollback)
- **What:** Rule engine. Define constraints ("folder X never exceeds 10GB"), system auto-triggers cleanup/migration when violated.
- **Notes:** Easy to overbuild. Recommend shipping `T-B05` as a hardcoded one-off first.

### `T-C05` — Authenticated Lineage Signing
- **Status:** proposed
- **Effort:** L
- **Depends on:** `T-C01`
- **What:** HMAC or Ed25519 signature on every audit entry + lineage edge. Per-install keypair. Chain-of-custody verification.
- **Notes:** Real value for forensic work. Plan crypto primitives shared with atrium-reversibility.

### `T-C06` — Shadow-Copy Snapshotting
- **Status:** **collapse into `T-C01`**
- **What was proposed:** Hourly local delta snapshots.
- **Why collapsed:** atrium-reversibility's filesystem snapshot mechanism (per `LIFECYCLE_GOVERNANCE.md`) covers this. Don't build separately.

### `T-C07` — Predictive Tiering (ML version)
- **Status:** deferred
- **Effort:** L
- **Depends on:** `last_accessed_at` column + access-pattern tracking
- **What:** Decision-tree model predicting "project is winding down" → pre-cache tomorrow's files + queue cold candidates.
- **Notes:** Start with simple rule ("90 days unmodified → suggest cold tier"). Probably 80% of the value with 5% of the work. Don't build ML unless the rule is insufficient.

---

## Tier D — Conclave/Nestegg-dependent (define hookspec now, baseline impl, full impl later)

The pattern: **extend `plugins/hookspecs.py`** with new hooks. Ship Curator's default impl (no-op or simple baseline). When Conclave lands, it ships `curatorplug-conclave` registering real hookimpls. Curator code never changes.

### `T-D01` — `curator_classify_semantic` hookspec
- **Status:** placeholder needed
- **Depends on:** Conclave (build but expose hookspec now)
- **What:** New hook `curator_classify_semantic(file: FileEntity) -> SemanticCluster | None`. Default impl: returns None. Conclave impl: embeds file content, clusters via local model.
- **Use case:** Semantic Content Clustering — "legal brief + psych assessment both relate to 'Forensic Testimony'."

### `T-D02` — `curator_pii_scan` hookspec
- **Status:** **build regex baseline now**, mark Conclave seam
- **Depends on:** none for baseline
- **What:** Regex impl ships with Curator (`T-B04`). Conclave later registers an embedding-based detector that catches contextual PII the regex misses.

### `T-D03` — `curator_extract_citations` hookspec
- **Status:** **build regex baseline (recommended)**, Conclave for fuzzy
- **Depends on:** none for regex; PubMed/CrossRef API for cross-reference
- **What:** Bluebook + APA regex extractors (~500 lines). Cross-reference local PDF library for hit/miss. Conclave later does fuzzy citation resolution (recognize a citation paraphrased rather than properly formatted).

### `T-D04` — `curator_ocr_extract` hookspec
- **Status:** **build pytesseract impl now** (`T-B06`)
- **Depends on:** Tesseract binary
- **What:** Default impl calls pytesseract. Conclave later registers a layout-aware OCR (e.g. detecting tables, figures).

### `T-D05` — `curator_evaluate_rule` hookspec (Natural Language Logic Gates)
- **Status:** placeholder needed
- **Depends on:** Conclave (no baseline possible)
- **What:** Hook accepts a natural-language rule + a file, returns a decision. "If signed but not in 'Executed' folder within 48h, alert and tag." Default impl: returns Indeterminate. Conclave impl: invokes local LLM.

### `T-D06` — `curator_find_orphaned_assets` hookspec (Semantic Asset Sweeper)
- **Status:** **build code-parsing baseline (recommended)**, Conclave for semantic
- **Depends on:** none for code parsing
- **What:** Parse imports / `<img src>` / file path strings out of code files. Find local assets not referenced anywhere. Queue for review. Conclave later: semantic match ("this PNG is the diagram described in README §3").

### `T-D07` — Cross-Source Conflict Merging (with assisted merge)
- **Status:** designed not built; baseline 3-way merge buildable now
- **Effort:** L (baseline), XL (Conclave-assisted)
- **What:** When two machines modify same file: don't create "Conflict Copy," show diff in Curator UI, allow surgical merge. Baseline: git-style 3-way merge. Conclave: LLM-suggested resolution.

### `T-D08` — Automated Transcription Pipelines
- **Status:** placeholder needed (Conclave/Nestegg territory)
- **Depends on:** Nestegg (per Jake's note)
- **What:** Drop-zone watch + Whisper. New hookspec `curator_transcribe_media(file) -> str | None`. Default impl: returns None. Nestegg plugin: routes to local Whisper.

### `T-D09` — `curator_index_semantic` hookspec
- **Status:** placeholder needed
- **Depends on:** Conclave (Curator can ship FTS5 keyword index as default)
- **What:** Semantic search layer. Default impl: SQLite FTS5 keyword search. Conclave impl: embedding index + ANN search.

---

## Tier E — Declined or skipped

### `T-E01` — Delta-Block Synchronization
- **Status:** declined
- **Why:** PyDrive2 already does delta sync via resumable upload + revision history. Don't reinvent. If specific bandwidth pain appears, profile first.

### `T-E02` — Hardware-Aware Job Scheduling (full scheduler)
- **Status:** declined; replace with simple throttle
- **Why:** 90% of value from a 20-line check in `hash_pipeline.py` ("if CPU > 80% or unplugged, sleep 30s"). Don't build a scheduler.

### `T-E03` — Dependency Conflict Resolver (Python deps)
- **Status:** declined
- **Why:** Out of Curator's scope. uv/pip already solve this. Wrong problem in wrong place.

### `T-E04` — Zero-Trust File Sandboxing for downloads
- **Status:** declined
- **Why:** OS-level concern. Windows Defender + SmartScreen handle this. Curator can't intercept at kernel without driver work. Extend `safety.py`'s rule set instead if specific extension blocking is needed.

### `T-E05` — Geographic Data Pinning (as separate feature)
- **Status:** **merged into `safety.py` extension** (not a new feature)
- **Why:** It's a special case of extension-based migration veto, which `safety.py` already handles. Add the extension list, don't build a feature.

### `T-E06` — Bit-Level Data Scrubbing (quarterly full read)
- **Status:** merged into `T-A04`
- **Why:** Overlaps with auto-healing. Schedule the auto-healing on a periodic basis instead.

---

## Recommended priority order (next 8 features)

If shipping linearly across v1.7 → v1.9:

| # | ID | Feature | Tier | Effort |
|---|---|---|---|---|
| 1 | `T-A01` | Fuzzy-Match Version Stacking | A | M |
| 2 | `T-A02` | Visual Lineage Time-Machine | A | M |
| 3 | `T-A03` | Watchdog Daemon Mode | A | M |
| 4 | `T-B02` | Compliance Retention Enforcement | B | M |
| 5 | `T-B01` | Heuristic Space Forecasting | B | S |
| 6 | `T-B04` | PII Regex Scanner + Conclave hookspec | B+D | M |
| 7 | `T-B06` | Background OCR + Conclave hookspec | B+D | M |
| 8 | `T-B05` | Tiered Storage Manager | B | M |

**Parallel track (foundation work that unlocks Tier A/C destructive features):**
- `T-C01` — atrium-reversibility v0.1 (unblocks `T-A04`, `T-C04`, `T-C05`)
- `T-C02` — Asset classification taxonomy (unblocks `T-B05`, `T-A05`, `T-C03`)

**v1.7 alpha pieces all shipped:**
- HealthCheckDialog (v1.7-alpha.1, committed `34c1483`)
- ScanDialog (v1.7-alpha.2, committed `e7c46ce`)
- GroupDialog (v1.7-alpha.3, committed `0ce5d8a`)
- CleanupDialog (v1.7-alpha.4, committed `6b9212a`)
- SourceAddDialog + Sources tab (v1.7-alpha.5, committed `1ac40e8`)
- Audit Log filter UI (v1.7-alpha.6, this session)

**Status:** All 6 v1.7-alpha pieces complete. Tagging **v1.7.0** in this commit.

---

## How to use this doc

- **Adding a new feature idea:** assign next `T-???` ID, drop in the appropriate tier, fill in fields.
- **Starting work:** mark Status: in-progress, link to the working branch.
- **Shipping:** mark Status: shipped, link to the commit + CHANGELOG entry.
- **Killing an idea:** move to Tier E with reasoning.
- **Reprioritizing:** edit the "Recommended priority order" table.

This doc is the **single source of truth for the Curator feature backlog**. Other docs (USER_GUIDE, DESIGN, CHANGELOG) link to specific T-IDs rather than restating intent.
