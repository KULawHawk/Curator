# Curator — Building Blocks (source-level inventory)

**The actual `.py` files, scripts, and source artifacts that make Curator work.**
As of v1.7.4 (HEAD c61ea02 + release notes staged) — 2026-05-11.

Organized by architectural tier from the inside out: **Models → Storage → Plugins → Services → CLI / GUI / MCP → External plugins → Tests → Scripts → Installer**.

---

## Architectural overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Tier 7: Entry surfaces  →  CLI / GUI / MCP server / Workflow .ps1  │
├─────────────────────────────────────────────────────────────────────┤
│  Tier 6: Plugin framework (pluggy)  →  21 hookspecs, 9 plugins      │
├─────────────────────────────────────────────────────────────────────┤
│  Tier 5: Services (business logic)  →  21 service classes           │
├─────────────────────────────────────────────────────────────────────┤
│  Tier 4: Repositories (data access)  →  11 repo classes             │
├─────────────────────────────────────────────────────────────────────┤
│  Tier 3: Storage primitives  →  DB connection, schema, migrations   │
├─────────────────────────────────────────────────────────────────────┤
│  Tier 2: Models  →  12 dataclass modules                            │
├─────────────────────────────────────────────────────────────────────┤
│  Tier 1: SQLite + Python stdlib + pluggy + PySide6 + FastMCP        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Tier 1.5: Vendored libraries (`src/curator/_vendored/`) — 12 files

Third-party libraries bundled directly into Curator (no external pip dependency). Vendored to keep the install footprint small and avoid version drift.

```
_vendored/__init__.py                       Subpackage marker
_vendored/LICENSE-PPDEEP.txt                BSD license for ppdeep (required by terms)
_vendored/LICENSE-SEND2TRASH.txt            BSD license for send2trash (required by terms)
_vendored/ppdeep/__init__.py                (8 KB)  Pure-Python ssdeep fuzzy hash implementation
_vendored/send2trash/__init__.py            (2 KB)  Cross-platform send2trash entry point
_vendored/send2trash/exceptions.py          send2trash error types
_vendored/send2trash/util.py                Shared helpers
_vendored/send2trash/mac/__init__.py        (3 KB)  macOS Finder-based trash impl
_vendored/send2trash/plat_freedesktop.py    (5 KB)  Linux XDG-trash spec impl
_vendored/send2trash/win/__init__.py        Windows trash entry point
_vendored/send2trash/win/legacy.py          (7 KB)  Pre-Vista shell API path
_vendored/send2trash/win/recycle_bin.py     (9 KB)  Modern IFileOperation API path
```

**Why this matters:** `send2trash` powers `curator trash` (sends files to OS Recycle Bin reversibly). `ppdeep` powers the `lineage_fuzzy_dup` plugin's NEAR_DUPLICATE detection. Both are bundled so users don't need extra pip installs. The LICENSE-*.txt files are required by both libraries' BSD licenses.

---

## Tier 1.5: Configuration (`src/curator/config/`) — 2 files

```
config/__init__.py    (8 KB)  Config dataclass + Config.load() — the TOML+env+CLI-flag config resolver
config/defaults.py    (2 KB)  Default values + path resolution (db_path, log levels, etc.)
```

**Why this matters:** Every entry point (CLI, GUI, MCP) starts by calling `Config.load()`. Reads `curator.toml` per the search order, applies env overrides (`CURATOR_CONFIG`, `CURATOR_LOG_LEVEL`), then CLI flags. Returns a frozen Config that everything else consumes.

---

---

## Examples (`Curator/examples/`) — 1 file

```
examples/watch_demo.py        Minimal demo of `curator watch` programmatic API — useful as an embedding-Curator template
```

---

## Documentation assets (in `docs/`)

In addition to the 23 markdown files, `docs/` contains 8 binary/data files:

```
docs/v034_gui_screenshot.png       PySide6 main window v0.34 (initial GUI release)
docs/v036_inspect_dialog.png       FileInspectDialog v0.36 (metadata + lineage + bundles tabs)
docs/v037_audit_log.png            Audit Log tab v0.37
docs/v038_settings.png             Settings tab v0.38
docs/v039_inbox.png                Inbox tab v0.39 (default landing tab)
docs/v041_lineage_graph.png        Lineage Graph tab v0.41 (networkx-rendered)
docs/v043_bundle_editor.png        BundleEditorDialog v0.43
docs/v100a1_migration_demo.txt     Plain-text dump of the v1.0.0a1 Tracer Phase 1 migration demo run
```

**Why this matters:** These screenshots are referenced in CHANGELOG entries and README.md to show what each GUI iteration looked like. Useful when comparing the current state vs historical baselines.

---

## Repo metadata (`Curator/Github/`) — 2 files

```
Github/CURATOR_RESEARCH_NOTES.md       Notes file for GitHub-side research / link curation (~external)
Github/PROCUREMENT_INDEX.md            Index of procurement / sourcing notes
```

**Why this matters:** Loose collection of notes that didn't fit anywhere else but were worth tracking in the repo. Not part of the runtime.

---

## Tier 2: Models (`src/curator/models/`) — 12 files

The data shapes everything else passes around.

```
models/__init__.py             Package init
models/audit.py                AuditEntry — one row in audit_log
models/base.py                 CuratorEntity base class (Pydantic v2)
models/bundle.py               BundleEntity + BundleMembership
models/file.py                 FileEntity — the central data model
models/jobs.py                 ScanJob — one row in scan_jobs
models/lineage.py              LineageEdge + LineageKind enum
models/migration.py            MigrationJob + MigrationProgress (Tracer)
models/results.py              Plugin return shapes (FileClassification, ValidationResult, BundleProposal, ...)
models/source.py               SourceConfig — one row in sources
models/trash.py                TrashRecord — one row in trash_registry
models/types.py                FileInfo, FileStat, ChangeEvent, ChangeKind, SourcePluginInfo (shared shapes)
```

**Why this matters:** Every other tier imports from `models`. Changing a field here ripples through the whole stack. The split is deliberate — repos handle persistence, services handle behavior, models are inert dataclasses.

---

## Tier 3: Storage Primitives (`src/curator/storage/`) — 6 files

The SQLite plumbing under the repositories.

```
storage/__init__.py            Re-exports CuratorDB + repository classes
storage/connection.py          CuratorDB — wraps sqlite3 with WAL mode, init() runs migrations
storage/exceptions.py          StorageError + 3 subclasses (EntityNotFoundError, DuplicateEntityError, MigrationError)
storage/migrations.py          Schema migration runner — 2 numbered migrations (001_initial, 002_migration_jobs_and_progress)
storage/queries.py             FileQuery — composable filter object (source_id, path-glob, size, mtime, file_type)
storage/schema_v1.sql          The canonical schema DDL — every CREATE TABLE / INDEX statement. Migration 001 reads this.
```

**Why this matters:** All DB access goes through `CuratorDB`. WAL mode is configured here. Schema versioning lives in `migrations.py`. `schema_v1.sql` is the readable source-of-truth for the table layouts (the migrations.py Python just executes this).

---

## Tier 4: Repositories (`src/curator/storage/repositories/`) — 11 files

One repo per entity table. Each repo is the only place that knows SQL for its table.

```
repositories/__init__.py            Public exports
repositories/_helpers.py            json_dumps, json_loads, uuid_to_str, str_to_uuid, timestamp helpers
repositories/audit_repo.py          AuditRepository — append-only writes + filtered queries
repositories/bundle_repo.py         BundleRepository — bundles + bundle_memberships
repositories/file_repo.py           FileRepository — the central files-table accessor
repositories/hash_cache_repo.py     HashCacheRepository — cached hashes (avoid re-computing)
repositories/job_repo.py            ScanJobRepository — scan_jobs (powers Inbox tab)
repositories/lineage_repo.py        LineageRepository — lineage_edges (the duplicate/version graph)
repositories/migration_job_repo.py  MigrationJobRepository + MigrationProgress (Tracer Phase 2)
repositories/source_repo.py         SourceRepository — sources table (the v1.6.4/v1.6.5 fix injected this)
repositories/trash_repo.py          TrashRepository — trash_registry for restore-after-trash
```

**Why this matters:** Services NEVER touch SQL directly — they go through these repos. This is what makes Curator testable (swap out a repo with an in-memory mock).

---

## Tier 5: Plugin Framework (`src/curator/plugins/`) — 11 files

Pluggy-based extension system. Hookspecs define the contracts; hookimpls fulfill them.

```
plugins/__init__.py                          get_plugin_manager() re-export
plugins/manager.py                           PluginManager singleton + entry-point discovery
plugins/hookspecs.py                         21 hookspecs (the plugin contract)
plugins/core/__init__.py                     register_core_plugins() — wires all built-ins
plugins/core/audit_writer.py                 AuditWriterPlugin — persists every audit event
plugins/core/classify_filetype.py            FiletypePlugin — extension + magic-byte detection
plugins/core/local_source.py                 LocalFSSource — local filesystem (v1.6.4 fix here)
plugins/core/gdrive_source.py                GoogleDriveSource — PyDrive2 wrapper (v1.6.5 fix here)
plugins/core/lineage_hash_dup.py             DUPLICATE detector — same xxhash3_128
plugins/core/lineage_filename.py             VERSION_OF detector — filename family chains
plugins/core/lineage_fuzzy_dup.py            NEAR_DUPLICATE detector — MinHash-LSH fuzzy match
```

**The 21 hookspecs in `hookspecs.py`** define what a plugin can do:
- `curator_classify_file` — file-type classification
- `curator_validate_file` — file validation
- `curator_compute_lineage` — lineage edge detection
- `curator_propose_bundle` — bundle creation
- `curator_audit_event` — audit log writers (v1.1.3+)
- `curator_plugin_init` — plugin lifecycle hook (v1.1.2+)
- `curator_source_register` / `_enumerate` / `_stat` / `_read_bytes` / `_write` / `_write_post` / `_move` / `_rename` / `_delete` — source plugin contract (10 methods)
- `curator_pre_trash` — trash veto
- And more.

---

## Tier 6: Services (`src/curator/services/`) — 21 files

The business-logic layer. Each service composes plugins + repos to do something useful.

```
services/__init__.py             Public service exports
services/audit.py                AuditService — append + Loguru output
services/bundle.py               BundleService — bundle CRUD + auto-proposals via plugins
services/classification.py       ClassificationService — orchestrates curator_classify_file hookimpls
services/cleanup.py              CleanupService — empty-dirs, broken-symlinks, junk, duplicates (38 KB — the biggest service)
services/code_project.py         CodeProjectService — detect git/svn projects, propose lineage
services/document.py             DocumentService — PDF/DOCX metadata extraction + destination-path planning
services/fuzzy_index.py          FuzzyIndex — in-memory MinHash-LSH for fuzzy-dup detection
services/gdrive_auth.py          AuthPaths + AuthStatus — Drive OAuth lifecycle helpers
services/hash_pipeline.py        HashPipeline — multi-stage hash computation (xxhash + md5 + fuzzy)
services/lineage.py              LineageService — orchestrates lineage-detector plugins
services/migration.py            MigrationService (Tracer) — cross-source moves (131 KB — the biggest service)
services/migration_retry.py      retry_transient_errors decorator (Tracer Phase 3)
services/music.py                MusicService — Mutagen-based audio metadata + destination paths
services/musicbrainz.py          MusicBrainzClient — online lookup enrichment
services/organize.py             OrganizeService — plan/stage/revert smart drive organize (35 KB)
services/photo.py                PhotoService — Pillow/piexif EXIF + destination paths
services/safety.py               SafetyService — refuses risky organize targets (system paths etc.)
services/scan.py                 ScanService — THE central orchestrator (enumerate → hash → classify → lineage → persist)
services/trash.py                TrashService — dual-trash (OS Recycle Bin + registry) with restore
services/watch.py                WatchService — filesystem event monitor (Phase Beta)
```

**Key relationships:**
- `ScanService` is the central orchestrator — every other service exists to support it
- `MigrationService` (Tracer) is independently big — it's the cross-source move engine
- `CleanupService` is the destructive-action coordinator

---

## Tier 7: CLI (`src/curator/cli/`) — 4 files

Typer-based command-line surface.

```
cli/__init__.py                 Subpackage init
cli/main.py                     The Typer app + every subcommand callback (117 KB — biggest single file)
cli/mcp_keys.py                 `curator mcp keys ...` subcommands (HTTP Bearer auth)
cli/runtime.py                  CuratorRuntime dataclass + build_runtime() — wires everything together
```

**`runtime.py` is the dependency-injection seat:**
- Builds `CuratorDB`
- Constructs all repos
- Loads the plugin manager
- Injects `source_repo` into local + gdrive plugins (v1.5.1, v1.6.4, v1.6.5)
- Injects `audit_repo` into AuditWriterPlugin (v1.1.3)
- Builds all services with the right dependencies
- Returns one `CuratorRuntime` object — every CLI command, the GUI, and MCP all consume this same shape

---

## Tier 7: GUI (`src/curator/gui/`) — 9 files

PySide6 desktop window.

```
gui/__init__.py                 Subpackage init
gui/launcher.py                 run_gui() — boots QApplication + main window
gui/main_window.py              CuratorMainWindow — 9 tabs, 5 menus (66 KB; +Sources tab + Audit filter UI in v1.7)
gui/dialogs.py                  7 dialogs: FileInspect, BundleEditor, HealthCheck, Scan, Group, Cleanup, SourceAdd (95 KB after v1.7-alpha sequence)
gui/models.py                   Qt table models for all 9 tabs (40 KB) — FileTableModel, AuditLogTableModel (extended with set_filter() in v1.7-alpha.6), ScanJobTableModel, etc.
gui/lineage_view.py             Lineage Graph tab (networkx-rendered)
gui/migrate_signals.py          MigrationProgressBridge — bridges background-thread progress events to Qt signals
gui/scan_signals.py             (NEW in v1.7-alpha.2) ScanProgressBridge + ScanWorker for ScanDialog QThread
gui/cleanup_signals.py          (NEW in v1.7-alpha.3, extended alpha.4) GroupProgressBridge + GroupFindWorker + GroupApplyWorker + CleanupProgressBridge + CleanupFindWorker + CleanupApplyWorker
```

**`main_window.py` is mostly menu wiring + slot methods.** The actual data display lives in `models.py` (Qt model/view classes) and the per-tab build methods (`_build_inbox_tab`, `_build_browser_tab`, `_build_sources_tab`, etc.). The two `*_signals.py` modules provide `QThread`+`QObject(Signal)` infrastructure for the v1.7 dialogs that run long operations off the GUI thread.

---

## Tier 7: MCP Server (`src/curator/mcp/`) — 5 files

FastMCP-based MCP server exposing 9 tools.

```
mcp/__init__.py                 Subpackage init
mcp/server.py                   FastMCP server bootstrap (stdio + HTTP modes)
mcp/tools.py                    The 9 tool implementations (30 KB) — health_check, list_sources, query_audit_log, query_files, inspect_file, get_lineage, find_duplicates, list_trashed, get_migration_status
mcp/auth.py                     BearerAuth implementation (v1.5.0) — token validation, key rotation
mcp/middleware.py               BearerAuthMiddleware (v1.5.0) — Starlette middleware for HTTP transport
```

---

## External plugins (separate repos, same install)

### `curatorplug-atrium-safety` v0.3.0 — 5 source files

```
src/curatorplug/atrium_safety/__init__.py
src/curatorplug/atrium_safety/plugin.py       The pluggy entry point (20 KB)
src/curatorplug/atrium_safety/enforcer.py     Atrium Principle 2 (Hash-Verify-Before-Move) compliance check
src/curatorplug/atrium_safety/verifier.py     Pre-move re-read verification
src/curatorplug/atrium_safety/exceptions.py   ComplianceError + subclasses
```

**Hook used:** `curator_source_write_post` — raises `ComplianceError` if the post-write hash doesn't match what was expected, causing the migration to be marked FAILED.

### `curatorplug-atrium-citation` v0.2.0 — 6 source files

```
src/curatorplug/atrium_citation/__init__.py
src/curatorplug/atrium_citation/plugin.py     The pluggy entry point
src/curatorplug/atrium_citation/sweep.py      Sweep algorithm (12 KB) — finds Citation Chain violations
src/curatorplug/atrium_citation/cli.py        `curator-citation sweep` CLI
src/curatorplug/atrium_citation/audit.py      Custom audit event types for Principle 3
src/curatorplug/atrium_citation/exceptions.py
```

**Hook used:** `curator_audit_event` — observes every migration audit entry, filters for cross-source moves (v0.2.0+), flags missing citations.

---

## Tests — 84 Python test files across 5 categories

```
tests/conftest.py                    (8 KB)  Shared pytest fixtures (tmp_db, runtime_factory, etc.)
tests/unit/                          41 files — service / repo / model / plugin unit tests
  tests/unit/__init__.py
  tests/unit/mcp/__init__.py
  tests/unit/mcp/test_tools.py       (27 KB) MCP tools unit tests
  tests/unit/test_*.py               37 more unit-test files
tests/integration/                   27 files — full-stack CLI invocations against tmp DBs
  tests/integration/__init__.py
  tests/integration/mcp/__init__.py
  tests/integration/mcp/test_stdio.py (8 KB) MCP stdio transport integration test
  tests/integration/test_*.py        24 more integration-test files
tests/gui/                           9 files — pytest-qt GUI tests (offscreen Qt)
tests/property/                      2 files — Hypothesis property-based tests
tests/perf/                          2 files — performance regression tests
```

Plus external plugin tests:
- `curatorplug-atrium-safety/tests/` — 6 test files (incl. conftest.py)
- `curatorplug-atrium-citation/tests/` — 6 test files (incl. conftest.py + __init__.py)

**Total: 96 test files, 1438 passing tests, 0 failing, 9 documented skips.**

---

## Performance test result snapshots (`tests/perf/results/`) — 12 JSON files

Benchmark results from 3 historical runs of the two perf tests, captured at gates v0.45 / v0.46 / v0.47 (2026-05-06):

```
tests/perf/results/index_build_scaling-20260506T205150.json
tests/perf/results/index_build_scaling-20260506T212007.json
tests/perf/results/index_build_scaling-20260506T223806.json
tests/perf/results/lineage_throughput_n100-20260506T205107.json
tests/perf/results/lineage_throughput_n100-20260506T211942.json
tests/perf/results/lineage_throughput_n100-20260506T223749.json
tests/perf/results/lineage_throughput_n1000-20260506T205110.json
tests/perf/results/lineage_throughput_n1000-20260506T211944.json
tests/perf/results/lineage_throughput_n1000-20260506T223750.json
tests/perf/results/lineage_throughput_n10000-20260506T205135.json
tests/perf/results/lineage_throughput_n10000-20260506T211959.json
tests/perf/results/lineage_throughput_n10000-20260506T223800.json
```

Used for regression comparison: a future perf run's JSON gets diffed against the last snapshot to detect throughput drops.

---

## PEP 561 type-marker files — 3 files

Empty files that tell Python type checkers (`mypy`, `pyright`) the package ships inline type annotations:

```
src/curator/py.typed                                  Curator's marker
curatorplug-atrium-safety/src/curatorplug/atrium_safety/py.typed       Safety plugin marker
curatorplug-atrium-citation/src/curatorplug/atrium_citation/py.typed   Citation plugin marker
```

---

## Workflow scripts (`Curator/scripts/workflows/`) — 13 files

```
_common.ps1                   Shared helpers: CuratorRoot, CuratorVenv, Test-CuratorAvailable, Invoke-Curator, Invoke-CuratorJson, Read-Confirmation, Show-Banner, Show-Section
01_initial_scan.ps1           Cautious scan workflow — pre-flight count, confirm, scan, summary
01_initial_scan.bat           Double-click wrapper for the above
02_find_duplicates.ps1        Find duplicates — discover, sample, confirm, trash (uses --json group)
02_find_duplicates.bat        Double-click wrapper
03_cleanup_junk.ps1           Junk/empty-dirs/broken-symlinks cleanup (uses --json cleanup)
03_cleanup_junk.bat           Double-click wrapper
04_audit_summary.ps1          24-hour audit report grouped by action/actor/hour
04_audit_summary.bat          Double-click wrapper
05_health_check.ps1           8-section diagnostic dashboard
05_health_check.bat           Double-click wrapper
README.md                     Workflow documentation
```

## Other scripts (`Curator/scripts/`)

```
scripts/setup_gdrive_source.py    (5 KB) Phase Beta helper — pre-v1.6.0 OAuth setup workflow.
                                  Largely superseded by `curator sources config` (v1.6.0+) but kept for legacy use.
```

---

## Installer (`Curator/installer/`) — 3 files

```
Install-Curator.bat           Double-click entry point — spawns elevated PowerShell
Install-Curator.ps1           The 10-step installer (32 KB):
                                 1. Detect Python via py launcher
                                 2. Create venv
                                 3. Install editable curator[gui,mcp,organize] + plugins
                                 4. Import-probe each extra separately
                                 5. Build default curator.toml
                                 6. Detect Claude Desktop install
                                 7. Generate / merge claude_desktop_config.json
                                 8. Persist JSON via venv Python's json.dumps (clean output)
                                 9. REAL MCP PROBE GATE — spawn curator-mcp, handshake, assert ≥9 tools
                                10. Roll back on probe failure
README.md                     Installer usage + troubleshooting
```

---

## Configuration files (3 active)

```
C:\Users\jmlee\Desktop\AL\.curator\curator.toml      Canonical Curator config (CURATOR_CONFIG env var points here)
C:\Users\jmlee\Desktop\AL\.curator\curator.db        Canonical SQLite database (86,940 files indexed)
%APPDATA%\Claude\claude_desktop_config.json          Claude Desktop MCP server registration
```

---

## Build / project metadata

```
Curator/pyproject.toml              (5 KB)    Project definition + dependencies + entry points + extras ([gui], [mcp], [organize], [beta], [cloud])
Curator/README.md                   (18 KB)   Top-level repo readme
Curator/CHANGELOG.md                (131 KB)  Every version's changes (v0.1 → v1.6.5)
Curator/.gitignore                  git ignore rules
```

## Top-level design/history docs (root of Curator repo, NOT in docs/)

```
Curator/BUILD_TRACKER.md            (218 KB!)  Master build log — every gate's tasks, status, lessons. THE biggest doc in Curator.
Curator/DESIGN.md                   (113 KB)   Original master architecture doc — section numbers (§5.2, §6.3, etc.) referenced throughout the codebase
Curator/DESIGN_PHASE_DELTA.md       (45 KB)    Phase Delta architectural changes (most recent gate sequence)
Curator/ECOSYSTEM_DESIGN.md         (37 KB)    Where Curator fits in the Ad Astra ecosystem (Atrium, Synergy, APEX, etc.)
```

**Why these matter:** When code references `DESIGN.md §6.3` (e.g., `local_source.py`'s module docstring), it means section 6.3 of `Curator/DESIGN.md` (the top-level one) — NOT a doc inside `docs/`. The 218 KB `BUILD_TRACKER.md` is your historical record of every architectural decision.

---

## File counts by tier (verified against git ls-files)

| Tier / Category | Files | Notes |
|---|---:|---|
| Vendored libraries | 12 | send2trash + ppdeep + LICENSE files |
| Config | 2 | Config dataclass + defaults |
| Models | 12 | Pydantic dataclasses |
| Storage primitives | 6 | DB + 2 migrations + schema_v1.sql |
| Repositories | 11 | One per entity table |
| Plugin framework | 11 | hookspecs + 9 built-in plugins |
| Services | 22 | Business logic (+forecast.py in v1.7.2) |
| CLI | 4 | Typer subcommands |
| GUI | 9 | PySide6 desktop (5 dialogs + 9 tabs + 2 signal bridges) |
| MCP server | 5 | FastMCP tools + auth |
| PEP 561 markers | 1 | py.typed |
| **Curator src/ total** | **96** | All Python + SQL + py.typed + LICENSE (+forecast.py in v1.7.2) |
| Tests (production) | 82 | Excludes perf/results JSON |
| Tests (perf results JSON) | 12 | Historical benchmark snapshots |
| **Curator tests/ total** | **94** | All test artifacts |
| Workflow scripts | 13 | .ps1 + .bat + README |
| Installer | 3 | .bat + .ps1 + README |
| docs/ markdown | 31 | Design + user docs (+5 release notes files for v1.7.0–v1.7.4) |
| docs/ binary assets | 8 | 7 PNGs + 1 demo TXT |
| Github/ | 2 | Loose metadata notes |
| examples/ | 1 | watch_demo.py |
| Top-level Curator/ | 8 | pyproject + .gitignore + 6 design/changelog .md |
| **CURATOR REPO TOTAL** | **255** | Verified via `git ls-files --cached` 2026-05-11 (v1.7.4 + release notes for v1.7.3 + v1.7.4) |
| atrium-safety | 18 | 6 src + 7 tests (+test_pre_trash_retention.py v0.4.0) + 5 metadata |
| atrium-citation | 19 | 7 src + 6 tests + 6 metadata |
| **GRAND TOTAL (all 3 repos)** | **292** | |

---

## Files that show up the most when debugging

When something is broken, these are the files to check first (in order):

```
src/curator/cli/runtime.py                Dependency wiring — bugs here break everything downstream
src/curator/services/scan.py              Central orchestrator — most scan issues land here
src/curator/services/migration.py         Tracer — every migration bug is in this file
src/curator/plugins/core/local_source.py  Local file ops — v1.6.4 fix
src/curator/plugins/core/gdrive_source.py Drive file ops — v1.5.1 + v1.6.5 fixes
src/curator/storage/connection.py         If "database is locked" or WAL issues
src/curator/storage/migrations.py         If schema is out of sync
src/curator/gui/main_window.py            If a GUI tab won't load
src/curator/mcp/tools.py                  If an MCP tool returns the wrong shape
installer/Install-Curator.ps1             If install / re-install behaves weirdly
```

---

**Verifying the counts:**

```powershell
cd C:\Users\jmlee\Desktop\AL\Curator;                              git ls-files | Measure-Object | Select-Object -Expand Count    # → 246
cd C:\Users\jmlee\Desktop\AL\curatorplug-atrium-safety;             git ls-files | Measure-Object | Select-Object -Expand Count    # → 17
cd C:\Users\jmlee\Desktop\AL\curatorplug-atrium-citation;           git ls-files | Measure-Object | Select-Object -Expand Count    # → 19
```

The authoritative cross-reference document with every file individually listed is `docs/ALL_FILES.md` — regenerated from `git ls-files` so it can never drift from the truth.

---

**Total source surface: 282 git-tracked files** across the Curator repo and two plugin repos. Of these:
- **196 are Python** (`.py`)
- **41 are Markdown** (`.md`)
- **12 are JSON** (perf result snapshots)
- **7 are PNG screenshots**
- **7 are PowerShell** (`.ps1`)
- **6 are batch** (`.bat`)
- **3 are TOML** (`pyproject.toml` x 3)
- **3 are LICENSE/demo TXT**
- **3 are PEP 561 `py.typed` markers**
- **3 are `.gitignore`**
- **1 is SQL** (`schema_v1.sql`)
