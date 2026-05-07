# Changelog

All notable changes to Curator are documented here. Format inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with semver
versioning where reasonable.

## [1.0.0rc1] — 2026-05-08 — First release candidate 🎉

**Curator's first release candidate.** Phase α + Phase β are 100% complete.
This is the first version Curator considers itself feature-stable for the
use cases the project was designed to serve. The version bump from 0.43.0
to 1.0.0rc1 marks the milestone; semver-major changes after this point
require deliberation, not just a patch bump.

### What v1.0 IS

A standalone, cross-platform, file-knowledge-graph tool that:

- **Indexes files** from local + Google Drive sources via a pluggable
  source-plugin contract (read + write + delete + stat + enumerate).
- **Hashes** with xxh3_128 (fast) + ssdeep fuzzy + MD5 (compatibility),
  with single-pass file reads.
- **Detects lineage** between files: exact duplicates (xxhash match),
  near-duplicates (ssdeep + MinHash-LSH at scale; 196.7x speedup at
  10k files), version-of and renamed-from heuristics.
- **Bundles** — logical groupings of related files. Manual creation +
  editing via GUI; plugin-driven proposals via the rule engine.
- **Trash + restore** with cross-platform send2trash (Windows Recycle
  Bin v1+v2 metadata parsing; macOS via AppleScript; Linux via
  freedesktop.org Trash spec).
- **Watch + incremental scan** — long-running file watcher with
  debounced events that flow into incremental scans, keeping the
  index live.
- **Safety primitives** — four concern types (open handles, project
  files via VCS markers, app-data prefixes, OS-managed paths) with
  three-level verdicts (SAFE / CAUTION / REFUSE). Foundation of every
  destructive operation.
- **Organize** — four type-specific pipelines (music via mutagen +
  MusicBrainz fallback / photos via EXIF / documents via PDF+OOXML
  metadata / code projects via VCS detection) with plan / stage /
  apply / revert flows. Manifest-based reverts work bidirectionally.
- **Cleanup** — five detectors (junk files / empty directories / broken
  symlinks / exact duplicates / fuzzy near-duplicates) with full
  index-sync on every destructive operation (no phantom-file gap).
- **GUI** — native PySide6 desktop app, 7 tabs (Inbox / Browser /
  Bundles / Trash / Audit Log / Settings / Lineage Graph), 5
  mutations (Trash / Restore / Dissolve / Bundle create / Bundle
  edit), per-file inspect dialog with metadata + lineage edges +
  bundle memberships.
- **CLI** — full Typer-based CLI with `--json` mode for piping; sources
  add/list/show/enable/disable/remove; scan with watch mode; trash +
  restore; bundles list/show/create/dissolve; organize plan/stage/
  apply/revert; cleanup empty-dirs/broken-symlinks/junk/duplicates;
  doctor health check; audit log query; gdrive paths/status/auth.
- **Audit log** — append-only JSONL, every destructive operation
  recorded with timestamp + actor + action + entity + details. Full
  cross-tool compatibility per SIP v0.1.
- **Plugin system** — pluggy-based hookspecs for source plugins,
  classifier plugins, lineage detectors, bundle proposers,
  pre-trash veto. External code can extend Curator without modifying
  the core.

### What v1.0 IS NOT (deferred to v1.x)

- **Migration tool (Feature M)** — unblocked by v0.40 source write hook
  but not yet implemented. ~6-10h. Same-machine local→local first;
  cross-source local↔gdrive after.
- **Sync (Feature S)** — bidirectional source synchronization. Larger;
  design pass needed.
- **Update protocol (Feature U)** — version-and-upgrade ceremony.
- **MCP server** — read APIs as MCP tools. Unblocks Synergy Phase 1 +
  Conclave Phase 1.
- **APEX safety plugin** (`curatorplug-atrium-safety`) —
  operationalizes the MORTAL SIN Constitutional principle as a
  cross-product safety net. ~3-4h.
- **Bundle creation in CLI** — GUI ships in v0.43; CLI parity for
  bundle creation is a v1.x polish item.
- **OneDrive + Dropbox source plugins** — the source-plugin contract
  exists and gdrive uses it; the additional cloud sources are v1.x.

### Test status at v1.0.0rc1

**Default suite: 969 passing, 8 correctly-skipped, 0 failures, ~54s.**
Opt-in suite (slow + perf): 978 total passing.

### Documentation-only release marker

Curator does not yet have a `.git` directory at the project root. The
"tag" at v1.0.0rc1 is therefore documentation-only — the version bump
in `pyproject.toml` + `src/curator/__init__.py` plus this CHANGELOG
entry plus the corresponding BUILD_TRACKER entry collectively mark
the cut point. To make the tag a real Git tag, the user must:

```bash
cd C:/Users/jmlee/Desktop/AL/Curator
git init
# (decisions: .gitignore, squash strategy, remote)
git add -A
git commit -m "Release 1.0.0rc1"
git tag -a v1.0.0rc1 -m "First release candidate"
```

The long-deferred git_init decision is now the highest-priority
outstanding item per BUILD_TRACKER and the Atrium logic-gate inventory's
GATE-PM-013 (surface git/backup risk).

## [0.43.0] — 2026-05-08 — Phase Beta gate 4 polish: bundle creation + editing UI

**Phase β closes at 100%.** Bundle creation and editing now ship in the
GUI — the lone remaining DESIGN.md §15.2 surface from v0.42 is now
complete.

### Added

- New `BundleEditorDialog` in `src/curator/gui/dialogs.py` (~410 lines).
  Modal dialog used for both Create and Edit modes, distinguished by
  the optional `existing_bundle` parameter. Layout: Name + Description
  text fields at top; horizontal splitter with Available files (left)
  | Add→ / ←Remove / Set as ★ Primary buttons (middle) | In bundle
  (right). Each list has a search filter; double-click moves an item.
  The primary member is marked with a `★` prefix; defaults to first
  member if none explicitly chosen. Validation rejects empty name +
  zero-member bundles before the dialog accepts.
- New `BundleEditorResult` dataclass exposing `name`, `description`,
  `member_ids`, `primary_id`, `existing_bundle_id`,
  `initial_member_ids`. `added_member_ids` and `removed_member_ids`
  properties compute the set diff for edit-mode dispatchers.
- Three new GUI flow paths in `src/curator/gui/main_window.py`:
  * `_slot_bundle_new` (Edit menu "&New bundle..." Ctrl+N OR Bundles
    tab right-click "New bundle...") opens the editor in Create mode
    and dispatches to `_perform_bundle_create` on accept.
  * `_slot_bundle_edit_at_row` (Edit menu "&Edit selected bundle..."
    Ctrl+E OR Bundles tab right-click "Edit bundle...") opens the
    editor pre-populated with the bundle's current state and
    dispatches to `_perform_bundle_apply_edits` on accept.
  * `_open_bundle_editor` is the testable seam — tests patch it on
    the window instance to inject a synthetic `BundleEditorResult`
    (or `None` for cancel) without booting the Qt event loop.
- Bundles tab context menu now offers "New bundle..." even when
  right-clicking on empty space. When a row IS selected, the menu
  also offers "Edit bundle..." and "Dissolve bundle...".
- About dialog mentions the new v0.43 capability.

### Tests

- 32 new at `tests/gui/test_gui_bundle_editor.py` covering: dataclass
  set-diff properties (3), `_perform_bundle_create` (4),
  `_perform_bundle_apply_edits` (6), slot wiring with mocked
  `_open_bundle_editor` (4), real dialog construction + validation +
  interaction (12), context menu + Edit menu wiring (3).

### Regression

**Default suite: 937 → 969 passing, 8 correctly-skipped, 0 failures, 60.2s.**
Full GUI suite: 145 → 177 passing.

### Phase status

- Phase β gate 4 (GUI): **100%** — all 7 DESIGN.md §15.2 views shipped
  (Inbox / Browser / Bundles / Trash / Audit Log / Settings / Lineage
  Graph) plus all relevant mutations (Trash / Restore / Dissolve /
  Bundle create + edit) plus per-file inspect dialog plus bundle editor.
- Phase β gate 5 (gdrive): **100%** since v0.42.
- **Phase Beta: 100%.** Eligible for v1.0-rc1 tag at user discretion.
- Curator transitions to Phase Gamma polish + Phase Delta substantive
  features (Migration tool already unblocked by v0.40 write hook).

### Real-world demo

Seeded 12 audio files across 3 albums, pre-created one bundle
("Pink Floyd - The Wall" with 4 members), opened `BundleEditorDialog`
in Create mode, pre-filled name "Radiohead - OK Computer", added the
3 Radiohead tracks, marked "Paranoid Android" as primary, applied a
filter on Available list to "Pink Floyd". Captured a 1000x600
screenshot at `docs/v043_bundle_editor.png` (42KB) showing the
dialog state.

## [Unreleased] — 2026-05-08 (later) — Atrium governance suite + Conclave Lenses v2

Major design milestone: created the Atrium constellation governance
suite (5 documents) and expanded Conclave's Lens roster from 9 to 12
based on 2024-2025 state of art. **No code shipped** — still v0.41.0;
897 tests passing.

### Added (constellation governance)

New directory `C:\Users\jmlee\Desktop\AL\Atrium\` (peer to Curator,
Apex, future Conclave/Umbrella/Nestegg):

- `Atrium/CONSTITUTION.md` (~3000 words) — binding governance with
  Six Aims (Accuracy, Reversibility, Self-sufficiency, Auditability,
  Composability, Portability), Five Non-Negotiable Principles
  (MORTAL SIN, Hash-Verify-Before-Move, Citation Chain, No Silent
  Failures, Atomic Operations), six Articles. Awaiting Jake's
  ratification.
- `Atrium/CHARTER.md` (~2000 words) — operational elaboration of
  Constitution Articles III + IV. Constellation pattern, membership
  criteria, retirement criteria, cross-product authority, APEX peer
  relationship.
- `Atrium/CONTRIBUTOR_PROTOCOL.md` (~2500 words) — codifies all 11
  operating rules from this conversation, mid-session repair
  patterns, things to never do, standard restart prompt.
- `Atrium/GLOSSARY.md` (~1500 words) — ~50 terms with cross-references
  and APEX attribution markers.
- `Atrium/ONBOARDING.md` (~2000 words) — fresh-session ramp-up guide,
  reading order, current state snapshot, common tasks, success
  criteria.

### Added (Conclave roster)

- `docs/CONCLAVE_LENSES_v2.md` (~3000 words) — Lens roster expanded
  from 9 to 12 with explicit distinctness criterion (distinct method
  + distinct failure mode + distinct cost profile). Four genuine
  additions: GotOcrUnified (Stepfun GOT-OCR 2.0), NougatScience (Meta
  scientific paper specialist), MinerU (Shanghai AI Lab comprehensive
  pipeline, AGPL-3 license watch item), ColPaliVerify (late-interaction
  retrieval verifier in verification role, not extraction). Updated
  configurable presets: Triage 3 / Cheap-and-fast 5 / Balanced 7 /
  Full 12. Lens evaluation methodology added with quantitative
  acceptance criteria. Honest exclusions documented (Mistral OCR API,
  Reducto, Aryn, Surya, Donut, LayoutLMv3, EasyOCR, generic VLMs).
  Re-validation calendar specified. Four new open questions OQ-9
  through OQ-12.

### What's now waiting on Jake

- Ratification of Atrium Constitution
- Selection of Constitutional amendment codeword (proposed: `Keystone`)
- Confirmation of "Atrium" as constellation name (alternatives in
  `Charter`)
- OQ-9 through OQ-12 in CONCLAVE_LENSES_v2.md
- DE-1 through DE-13 in `ECOSYSTEM_DESIGN.md` (still open from
  earlier today)
- OQ-1 through OQ-8 in CONCLAVE_PROPOSAL.md


## [Unreleased] — 2026-05-08 (mid) — Conclave proposal + Synergy phased recommendation


### Added

- `docs/CONCLAVE_PROPOSAL.md` (~30 KB, ~3000 words) — full proposal
  for **Conclave**, a multi-Lens ensemble indexer for assessment
  knowledge bases. 5-9 independent extractors run on the same source,
  produce candidate KB outputs, then collectively vote section-by-
  section. Mathematical premise: ensemble voting with uncorrelated
  errors collapses error rates multiplicatively past 99% (matches
  APEX Constitution §1's ≥99.5% accuracy aim).
  - Nine proposed Lenses: PdfText (pdfplumber), OcrFlow (Tesseract),
    OcrPaddle (PaddleOCR), MarkerPdf (Marker), TableSurgeon (Camelot
    + table-transformer), VisionClaude (Claude API), VisionLocal
    (Qwen-VL or Llama-3.2-Vision), StructuredHeuristic (regex
    patterns), CitationGraph (anystyle/GROBID).
  - Configurable subset presets: cheap-and-fast (3) / balanced (5) /
    full (9) / custom.
  - Five-stage pipeline: source prep → parallel Lens execution →
    alignment → voting → synthesis emitting APEX KB format.
  - Logic gates and decision trees as the organizing primitive.
  - Standalone constellation product; integrates with Curator (MCP),
    APEX (KB format), Umbrella (monitoring), Nestegg (model bundling).
  - Phased rollout proposal: ~120h to v1.0, best built parallel with
    Curator Phase Δ work, not before.
  - 8 open questions (OQ-1 through OQ-8); 10 Conclave-specific ideas;
  - 3 explicit anti-patterns.

### Changed

- `ECOSYSTEM_DESIGN.md` §1: Synergy resolution updated from "Option B
  forever" to **"phased B → A"**. Per Jake's framing that Synergy is
  effectively Curator's alpha, the optimal path is opt-in Curator MCP
  consumption (Phase 1) → organic scope narrowing (Phase 2) →
  Constitutional retirement via Master Scroll edit (Phase 3). Trigger
  is event-driven, not calendar-driven. Honors offline-first via
  fallback paths; honors APEX authority structure via explicit
  Constitutional moment.
- `ECOSYSTEM_DESIGN.md` §4: Per-product responsibility matrix gains
  **Conclave** row as fifth constellation product (proposed).
- `ECOSYSTEM_DESIGN.md` §8: Ideas log gains `[IDEA-00] Conclave`
  reference pointing to `docs/CONCLAVE_PROPOSAL.md`.

### What this enables

- Concrete forward-looking proposal for the assessment-indexing
  bottleneck: Vampire's single-method approach is the current
  ceiling; Conclave is the architectural answer.
- Clean Synergy phase-out path that doesn't force a Master Scroll
  edit until the moment is genuinely warranted.

## [Unreleased] — 2026-05-08 (earlier) — Ecosystem-design milestone

Received and integrated APEX architecture inventory response. Synthesized
into a forward-looking ecosystem design document.

### Added

- `docs/APEX_INFO_RESPONSE.md` (~30 KB) — the canonical APEX architecture
  inventory verbatim from the APEX session, with full citations and
  `[NOT IN PROJECT KNOWLEDGE]` markers per APEX's Standing Rule 11.
  Complete subsystem roster (9 codenamed: Synergy / Succubus / Vampire /
  Opus / Locker / Inkblot / Id / Latent / Sketch).
- `ECOSYSTEM_DESIGN.md` (~36 KB, ~1000 lines) — full integration design
  in 8 sections covering: Synergy/Curator overlap (4 resolution options +
  recommendation), hard constraints from APEX Constitution translated to
  Curator requirements, Suite Integration Protocol (SIP) v0.1, per-product
  responsibility matrix, 3 first-integration milestone candidates
  (recommended: APEX safety plugin, ~3-4h), 13 enumerated open decisions,
  ideas log with 14 IDEA items + 3 NOT-IDEA anti-patterns.

### Changed

- Banner added to `DESIGN_PHASE_DELTA.md` realigning per ecosystem
  understanding: Feature A (asset monitor) → Umbrella standalone project,
  Feature I (installer) → Nestegg standalone project, Feature M
  (migration) framing realigned to Synergy/Curator (not Vampire/Curator),
  Features S + U stay in Curator.

### Critical finding

The original Phase Δ framing assumed APEX's `subAPEX2` (Vampire) was the
file inventory subsystem. **It isn't.** Vampire is a PDF-to-KB content
extractor. The actual file-inventory subsystem is **Synergy (subAPEX12)**
— the canonical state-of-disk authority per APEX's Master Scroll v0.4
(built v0.2.2, shipped 2026-04-30). Synergy directly overlaps with
Curator's role; recommended resolution is Option B: Synergy becomes a
Curator client (preserves APEX interfaces, no Master Scroll edit needed).

### Hard constraints surfaced

- **MORTAL SIN rule** (Standing Rule 9): never delete assessment-derived
  artifacts. Maps to required `curator_pre_trash` veto plugin.
- **Self-sufficiency**: APEX must work offline. Any Curator dependency
  must have graceful fallback.
- **Citation chain** (Constitution §3, NON-NEGOTIABLE): Curator data is
  enrichment, never Scribe authority.
- **Standing Rule 3** (No new memory systems): formally rules out
  side-by-side Curator+Synergy as long-term state.
- **SHA256 universal hash**: APEX uses SHA256 throughout; recommend
  adding SHA256 as Curator secondary hash.
- **Hash-verify-before-move discipline**: triple-check (source-absent +
  destination-present + hash-match SHA256) before declaring move
  successful.

### Recommended first integration milestone

APEX safety plugin for Curator — separately distributable
`curatorplug-apex-safety` package registering a `curator_pre_trash`
veto hook. Checks paths against APEX assessment-derivation patterns;
blocks trash with reason citing Standing Rule 9. ~3-4h. Validates
Curator's plugin system works for external code, validates APEX
governance can be enforced by Curator hooks, prevents real data loss.

## [0.41.0] — 2026-05-08 — "Phase Beta gate 4 complete: Lineage Graph view"

Seventh and final GUI tab from DESIGN.md §15.2. Renders the full
lineage edge graph as a 2D node+edge visualization with type-colored
edges and confidence labels. **Phase β gate 4 (GUI) is now 100%.**
Combined with gate 5 (gdrive plugin) also complete, **Phase β is
~98%.** Remaining items are bundle creation/editing UI and
`curator gdrive auth` helper — Phase γ polish.
**897 default-suite tests passing in ~53s, 0 failures.**

### Added

- New module `src/curator/gui/lineage_view.py` (~360 lines):
  `LineageGraphBuilder` (pure-Python facade over file_repo +
  lineage_repo) + `LineageGraphView` (`QGraphicsView` with networkx
  layout). Build modes: full graph (all files with edges) or
  focus-graph (BFS from a curator_id outward to N hops).
- New 7th tab "Lineage Graph" with edge-kind legend bar.
- Color-coded edges: magenta (duplicate), orange (near_duplicate),
  blue (version_of), green (derived_from), yellow (renamed_from);
  unknown kinds get neutral gray.
- 26 unit tests at `tests/gui/test_gui_lineage.py`.
- Real-world screenshot at `docs/v041_lineage_graph.png`.
- Companion design doc at `docs/APEX_INFO_REQUEST.md` — a prompt to
  paste into the APEX project chat for ecosystem integration design.

### Changed

- New dependency in `[gui]` extras: `networkx>=3.0`. MIT license,
  ~5MB pure Python; provides graph algorithms (spring/kamada_kawai/
  circular/shell layouts).
- Tab order is now: Inbox / Browser / Bundles / Trash / Audit Log /
  Settings / Lineage Graph (was: ... without Lineage Graph).
- `refresh_all()` (F5) now also refreshes the lineage view when present.

### Fixed

- `test_gui_inbox.py::test_inbox_tab_at_index_0` updated for new
  tab count (6 -> 7).
- `test_gui_settings.py::test_settings_tab_exists_at_index_4`
  updated similarly.

### What this closes

- Phase β gate 4 (GUI) is **100% complete**: all 7 DESIGN.md §15.2
  canonical views shipped (Inbox, Browser, Bundles, Trash, Audit Log,
  Settings, Lineage Graph) plus the per-file inspect dialog.
- Phase β overall: **~98%.** Remaining is polish, not architecture.

### Not yet (deferred)

- Focus-mode: select a file in the graph to filter to its N-hop
  neighborhood. Builder supports this; the picker UI is a v0.42 item.
- Bundle creation + membership editing UI (Phase γ polish).
- `curator gdrive auth <alias>` interactive helper (Phase γ polish).

## [0.40.0] — 2026-05-08 — "Phase Beta gate 5: source write hook"

Source plugin contract extended with `curator_source_write` for
create-new-file operations. Both `local_source` and `gdrive_source`
implement it. **This is the foundational primitive for cross-source
migration (Phase Δ Feature M) and cloud sync (Feature S)** — without
it, the source contract was read-only-plus-delete.
**871 default-suite tests passing in ~49s, 0 failures.**

### Added

- New hookspec `curator_source_write(source_id, parent_id, name,
  data: bytes, *, mtime, overwrite) -> FileInfo | None` in
  `src/curator/plugins/hookspecs.py`.
- New `SourcePluginInfo.supports_write: bool = False` field; both core
  plugins now advertise `supports_write=True`.
- Local implementation: atomic write via `tempfile.mkstemp` in same
  directory + `os.replace`. Auto-creates parent directories. Optional
  mtime preservation. Tempfile cleanup on any exception.
- Google Drive implementation: PyDrive2 CreateFile + content as BytesIO
  + Upload + FetchMetadata. Pre-flight existence check (Drive permits
  duplicate titles, so overwrite=False must check explicitly). When
  overwrite=True, trashes existing files with the same title first.
- 24 unit tests at `tests/unit/test_source_write_hook.py`.

### Changed

- `gdrive_source.py` docstring updated: status changed from
  "scaffolding" to "v0.40 implements register / enumerate / stat /
  read_bytes / write / delete".
- Phase β gate 5 status: COMPLETE for the core read+write+delete
  contract. Move and watch remain explicit Phase γ items.

### What this unblocks

- **Feature M (Migration tool)** can now be designed against a
  complete source contract — migration TO local OR TO gdrive works
  through the same code path.
- **Feature S (Cloud sync)** v1 can wrap rclone for sync while using
  `curator_source_write` for any non-rclone-handled cases.
- Phase β is now ~95% complete; only the Lineage Graph view (gate 4)
  remains.

### Not yet (deferred)

- `curator gdrive auth` interactive CLI helper (Phase γ).
- Streaming write variant for >500 MB files (Phase γ).
- `curator_source_move` for gdrive (Phase γ — Drive moves are
  parent-id swaps, need higher-level API).
- `curator_source_watch` for gdrive (Phase γ — push notifications).

## [0.39.0] — 2026-05-07 — "Inbox view"

Sixth GUI tab landing the canonical landing-tab of DESIGN.md §15.2.
Three-section dashboard: Recent scans / Pending review / Recent trash.
**847 default-suite tests passing in ~49s, 0 failures.**

### Added

- New `ScanJobTableModel` over `ScanJobRepository.list_recent` (~70
  lines). Columns: Status / Source / Root / Files / Started / Completed.
- New `PendingReviewTableModel` over lineage edges with confidence
  in the `[escalate, auto_confirm)` ambiguous middle band (~110
  lines). Resolves file paths via `file_repo.get` with a per-instance
  cache; falls back to `(<uuid>)` when a file row is missing.
- New 1st tab "Inbox" composing three QGroupBox sections, each with
  row count in the title and an empty-state hint label below when 0.
  Inbox is the canonical landing tab per DESIGN.md §15.2 ordering.
- New public method on `LineageRepository`: `query_by_confidence(*,
  min_confidence, max_confidence, limit)` returns edges in `[min, max)`.
  Replaces an earlier draft that crossed the public/private boundary
  by reaching into `_row_to_edge` from the model.
- `TrashTableModel` extended to accept an optional `limit` kwarg.
- 25 unit tests at `tests/gui/test_gui_inbox.py`.
- Real-world screenshot at `docs/v039_inbox.png`.

### Changed

- Tab order is now: Inbox / Browser / Bundles / Trash / Audit Log /
  Settings (was: Browser / Bundles / Trash / Audit Log / Settings).
  This matches DESIGN.md §15.2.
- `_make_inbox_section(title, view, model, *, empty_hint)` factored
  out so each section's QGroupBox composition is consistent.
- `_build_inbox_tab` reads `lineage.escalate_threshold` /
  `auto_confirm_threshold` from the runtime Config, so the band is
  configurable per-deployment via curator.toml. The active band
  appears in the section title (e.g. `[0.70, 0.95)`).
- `refresh_all()` (F5) now also refreshes the three Inbox models.

### Fixed

- `test_audit_tab_exists_with_title` updated for the new tab order:
  count >= 5, tabText(4) == "Audit Log".
- `test_settings_tab_exists_at_index_4` updated: count == 6,
  tabText(5) == "Settings".

### Not yet (deferred to v0.40+)

- Lineage Graph view (1 of 7 DESIGN.md views remaining).
- Bundle creation + membership editing UI.

## [0.38.0] — 2026-05-07 — "Settings view"

Fifth GUI tab landing the second of the remaining DESIGN.md §15.2
core views. Read-only display of the active `curator.toml` config
plus a Reload-from-disk button for verifying TOML edits.
**822 default-suite tests passing in ~45s, 0 failures.**

### Added

- New `ConfigTableModel` in `src/curator/gui/models.py` (~135 lines).
  Two columns: Setting (dotted path) / Value. Lists JSON-formatted;
  primitives stringified; tooltip on Value column shows untruncated.
- New 5th tab "Settings" with header label showing source TOML path,
  table view, Reload button, and help text below.
- Reload button re-parses the source TOML and updates the *display*
  only; the live runtime keeps using its original config until
  Curator is restarted.
- 26 unit tests at `tests/gui/test_gui_settings.py`.
- Real-world screenshot at `docs/v038_settings.png`.

### Architecture

- The Settings tab is the first GUI view that's NOT just a wrapped
  table — it composes a header label + table + button row + help text
  in a vertical layout.
- Reload logic factored into `_perform_settings_reload()` returning
  `(success, message, fresh_config | None)`, never raises. Slot calls
  it then either shows QMessageBox.warning on failure or updates the
  model + header + status bar on success.
- `refresh_all()` (F5) does NOT refresh Settings; the explicit Reload
  button is the only path for that. Comment in the code explains why.

### Fixed

- v0.37 audit tab test was asserting `count() == 4`; updated to
  `count() >= 4` so adding tabs in subsequent versions doesn't break it.

### Not yet (deferred to v0.39+)

- Inbox view, Lineage Graph view (2 of 7 DESIGN.md views).
- Bundle creation + membership editing UI.

## [0.37.0] — 2026-05-07 — "Audit Log view"

Fourth GUI tab landing the first of the remaining DESIGN.md §15.2
core views. Read-only tabular view over the append-only audit log.
**796 default-suite tests passing in ~44s, 0 failures.**

### Added

- New `AuditLogTableModel` in `src/curator/gui/models.py` (~155 lines).
  Five columns: When / Actor / Action / Entity / Details (JSON-truncated).
  ToolTipRole on the Details column shows the full untruncated JSON.
- New 4th tab "Audit Log" in the main window. Read-only by design —
  the audit log is intentionally append-only at the storage layer.
- Status bar gains an "Audit: N" count alongside the existing
  Files / Bundles / Trash counts.
- `refresh_all()` (F5) now refreshes the audit model too.
- 23 unit tests at `tests/gui/test_gui_audit.py`.
- Real-world screenshot at `docs/v037_audit_log.png`.

### Architecture

- The `AuditLogTableModel` caps results at 1000 rows newest-first
  (matches `AuditRepository.query()` default). For larger forensic
  histories, users should filter via the CLI — the GUI is for
  at-a-glance "what just happened".
- UUID-shaped entity IDs are truncated to 8 chars + "..." in the
  Entity column for table compactness; full IDs are still in the
  underlying AuditEntry rows accessible via `entry_at(row)`.

### Not yet (deferred to v0.38+)

- Inbox / Lineage Graph / Settings views (3 of 7 DESIGN.md views).
- Bundle creation + membership editing UI.

## [0.36.0] — 2026-05-07 — "Per-file inspect dialog"

Double-click any row in the Browser tab to open a modal showing
everything Curator knows about that file. **773 default-suite tests
passing in ~43s, 0 failures.**

### Added

- New `FileInspectDialog` in `src/curator/gui/dialogs.py` (~210 lines).
- Three tabs: Metadata (every fixed-schema field + flex attrs), Lineage
  Edges (every edge with the other-file path resolved + direction
  arrow), Bundle Memberships (every bundle with role + confidence).
- Browser tab gains a "Inspect..." action above "Send to Trash..." in
  the right-click context menu.
- Header label shows path bolded with size / mtime / source; if the
  file is deleted (`deleted_at` set), shows a red "DELETED (timestamp)"
  segment.
- 14 unit tests at `tests/gui/test_gui_inspect.py`.
- Real-world screenshot at `docs/v036_inspect_dialog.png`.

### Architecture

- The dialog construction is factored as a testable seam:
  `_open_inspect_dialog(file)` on the main window can be patched in
  tests to capture the call without entering `dlg.exec()`.

## [0.35.0] — 2026-05-07 — "GUI mutations: Trash / Restore / Dissolve"

The v0.34 read-only GUI gains its first three destructive operations.
Each is gated through a confirmation dialog with Cancel as default.
**759 default-suite tests passing in ~44s, 0 failures.**

### Added

- Browser tab right-click → "Send to Trash..." → confirm →
  `TrashService.send_to_trash`. The file moves to the OS Recycle Bin
  via `send2trash`, the FileEntity is soft-deleted, and a TrashRecord
  is created.
- Trash tab right-click → "Restore..." → confirm →
  `TrashService.restore`. On Windows this typically returns a
  friendly "please restore manually" message because `send2trash`
  doesn't record the OS trash location.
- Bundles tab right-click → "Dissolve bundle..." → confirm →
  `BundleService.dissolve`. Member files are preserved.
- New Edit menu with the same three actions (Ctrl+T trash, Ctrl+R
  restore, Ctrl+D dissolve) for keyboard users.
- 11 unit tests for the mutation paths (`tests/gui/test_gui_mutations.py`).
  All use real `build_runtime` against a temp DB; none requires pytest-qt
  event loop driving.

### Architecture

- Mutation logic factored into `_perform_*` methods that NEVER raise
  and return `(success, message)`. Slots show the message in a dialog.
  This makes the methods unit-testable without booting Qt.
- Slot dispatch from Edit menu vs context menu shares the same
  `_slot_*_at_row(row)` core; only the row-resolution path differs.

### Not yet (deferred to v0.36+)

- Per-file inspect dialog (Browser double-click).
- Bundle creation + membership editing.
- Lineage Graph / Inbox / Audit Log / Settings views.

## [0.34.0] — 2026-05-07 — "PySide6 desktop GUI, read-only first ship"

First visual interface. Native PySide6 Qt window with three read-only
tabs (Browser, Bundles, Trash) over the existing Curator runtime.
Launched via `curator gui`. **748 default-suite tests passing in ~70s,
0 failures.**

### Added

- New package `src/curator/gui/` with three Qt table models, main
  window, and launcher (~550 lines total).
- New CLI subcommand `curator gui` with friendly install hint when
  PySide6 isn't available.
- New `[gui]` extra in `pyproject.toml` (`PySide6>=6.6`).
- `pytest-qt>=4.2` added to the `[dev]` extra.
- 19 unit tests for the Qt table models (`tests/gui/test_gui_models.py`)
  + 1 slow-marked smoke test for the actual QMainWindow.
- Real-world screenshot at `docs/v034_gui_screenshot.png`.

### Fixed

- `cli/runtime.py:build_runtime` was missing the `code: CodeProjectService`
  collaborator that v0.31 added to OrganizeService. Fixed; the runtime
  now passes `code=CodeProjectService()` explicitly.

### Not yet in v0.34 (deferred to v0.35+)

- Mutations from the GUI (trash a file, dissolve a bundle, restore from
  trash). The visual layer ships first; HITL escalation comes next.
- The remaining 4 of DESIGN.md §15.2's 7 core views: Inbox, Lineage
  Graph, Audit Log, Settings.
- Per-file inspect dialog (double-click on a Browser row).

## [0.33.0] — 2026-05-07 — "Twelve-feature Phase Gamma block"

The first stable release. Every type-specific organize pipeline, every
cleanup detector, and full bidirectional index sync are feature-complete
and tested. **728 default-suite tests passing in 39.5s, 0 failures, 0
warnings.** No manual rescan is needed after any destructive operation —
the index always reflects on-disk truth.

### Highlights

- **Four organize pipelines feature-complete**: music + photo + document
  + code (VCS-marked projects). Each has its own metadata reader,
  destination templating, and CLI integration.
- **Five cleanup detectors feature-complete**: junk files, empty dirs,
  broken symlinks, exact duplicates (xxhash3_128), fuzzy near-duplicates
  (LSH MinHash + ssdeep). All routed through `send2trash` with full
  audit logging.
- **Bidirectional index sync** on every destructive operation: cleanup
  deletes mark `FileEntity.deleted_at`; organize moves update
  `FileEntity.source_path` to follow the file. No phantom files.
- **Optional MusicBrainz enrichment** for music files where the filename
  heuristic produced only artist + title — fills missing album / year /
  track from canonical MB data without overwriting any existing real
  tags.

### Phase Gamma versions rolled into this release

| Version | What landed |
|---|---|
| v0.20 | Phase Gamma kickoff: SafetyService + OrganizeService.plan |
| v0.21 | F2 Music: MusicService (mutagen-backed) + plan integration |
| v0.22 | Stage / revert mode for organize |
| v0.23 | Apply mode + collision handling |
| v0.24 | F3 Photos: PhotoService (EXIF date hierarchy) |
| v0.25 | F6 Cleanup: junk + empty_dirs + broken_symlinks |
| v0.26 | F4 Documents: DocumentService (PDF + DOCX dates) |
| v0.27 | Music filename heuristic + MusicBrainz client (un-wired) |
| v0.28 | F7 Dedup-aware cleanup (exact, via xxhash3_128) |
| v0.29 | F8 Index sync for cleanup (mark_deleted on apply) |
| v0.30 | F9 Fuzzy near-duplicate detection via LSH |
| v0.31 | F5 Code project organization (VCS markers + language inference) |
| v0.32 | MB auto-enrichment wiring (`--enrich-mb`) |
| v0.33 | Organize index sync for stage / apply / revert |

### Test counts

- Phase Alpha closed at **149 passing**
- Phase Beta gates 1, 2, 3 (LSH + cross-platform send2trash + watchfiles) added ~150
- Phase Gamma block added the rest
- **Total: 728 default-suite + 8 opt-in passing, 7 correctly skipped, 0 failures**
- Plus 4 LSH perf benchmarks available via `pytest tests/perf -m slow`

### Known gaps (intentionally deferred)

- **Phase Beta gate 4 (GUI)**: 0% — Windows app shell still pending
- **Phase Beta gate 5 (Google Drive)**: scaffolded — OAuth flow + remote
  source plugin not yet completed
- **MB enrichment stretch goals**: `--enrich-mb-limit N`, progress
  counter, audit-table caching
- **Code organize stretch goals**: non-VCS project detection
  (pyproject.toml / package.json / Cargo.toml), submodule recognition
- **Pillow 12 deprecation**: a quiet warning in pytest output

---

## [0.1.0a1] — Phase Alpha (closed)

Foundational release. Storage layer (CuratorDB + repositories), scan
service with xxhash3_128 indexing, source plugin scaffolding,
classification service, audit log, full CLI shell, 149 passing tests.
See `BUILD_TRACKER.md` for the chronological log.
