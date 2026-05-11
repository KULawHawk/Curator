# Curator — Complete Inventory of Tools, Utilities, and Assets

**As of Curator v1.7.0 (HEAD a41840d) — 2026-05-11**

Everything you can click, run, or open. Copy any code block as-is. Items grouped by surface (CLI / GUI / MCP / Workflows / Plugins / Files).

---

## 1. CLI Commands (43 total)

The CLI is your primary surface. Activate the venv first, then run any command below.

### Activation

```powershell
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\Activate.ps1
```

### Core file commands

```
curator scan
```
Index files under a folder against a source. Computes hashes, detects lineage, populates the file table.

```
curator inspect
```
Show every field Curator knows about a single file: curator_id, source_id, all hashes, size, mtime, classifications, flex attrs, lineage edges, bundle memberships.

```
curator group
```
Find groups of duplicate files (same xxhash3_128) and optionally trash all but one.

```
curator lineage
```
Show all lineage edges touching a file (fuzzy + hash + filename matches).

```
curator trash
```
Send a file to the OS Recycle Bin while snapshotting its metadata so it's restorable.

```
curator restore
```
Restore a previously-trashed file from the trash registry.

```
curator audit
```
Query the audit log (every mutation Curator has ever made). Filters: `--action`, `--actor`, `--since-hours`, `--json`.

```
curator doctor
```
Run integrity/health checks against the index and environment. Reports DB size, file counts, source counts, plugin count, audit health.

### Source management (group: `curator sources`)

```
curator sources list
```
List all registered sources with type / name / status / file count.

```
curator sources show
```
Show one source's details including config dict.

```
curator sources config
```
View or mutate a source's per-plugin config dict. Example: `curator sources config local --set root --value "C:\new\path"`.

```
curator sources add
```
Register a new source. Example: `curator sources add work_drive --type local --name "Work"`.

```
curator sources enable
```
Re-enable a previously-disabled source.

```
curator sources disable
```
Disable a source (existing files preserved; new scans skip it).

```
curator sources remove
```
Delete a source. Fails if any files still reference it (FK RESTRICT). Use `--apply` to actually delete.

### Bundle management (group: `curator bundles`)

```
curator bundles list
```
List all bundles (manual + auto-detected groupings of related files).

```
curator bundles show
```
Show a bundle and its members.

```
curator bundles create
```
Create a manual bundle from a list of files.

```
curator bundles dissolve
```
Delete a bundle (memberships removed; member files preserved).

### Migration / cross-source moves

```
curator migrate
```
Tracer: relocate files across paths or sources (local → Drive, Drive → local, local A → local B) with index integrity preserved. Phase 2 resumable jobs with `--workers N`.

### Cleanup commands (group: `curator cleanup`)

```
curator cleanup empty-dirs
```
Find empty directories under a root. Plan-mode by default; `--apply` to remove.

```
curator cleanup broken-symlinks
```
Find symlinks under a root whose targets no longer exist.

```
curator cleanup junk
```
Find platform junk files (Thumbs.db, .DS_Store, desktop.ini, ~$\*.tmp, etc.).

```
curator cleanup duplicates
```
Find duplicate files in the index and propose removing all but one per group.

### Organize (file classification / sorting)

```
curator organize
```
Plan-mode preview of an organize operation (music by artist, photos by date, etc.).

```
curator organize-revert
```
Undo a previous `curator organize --stage` operation.

### Watching / continuous indexing

```
curator watch
```
Watch local source roots for filesystem events; auto-index changes. Blocks until interrupted.

### Safety primitives (group: `curator safety`)

```
curator safety check
```
Check whether a path is safe to auto-organize (refuses system paths, app data, etc.).

```
curator safety paths
```
List the app-data + OS-managed path registries on this platform.

### Google Drive auth (group: `curator gdrive`)

```
curator gdrive paths
```
Show where Curator expects this alias's auth files to live.

```
curator gdrive status
```
Report current auth state for an alias (offline; no network call).

```
curator gdrive auth
```
Run the PyDrive2 interactive OAuth flow for an alias.

### MCP server management (group: `curator mcp keys`)

```
curator mcp keys generate
```
Generate a new MCP API key for HTTP Bearer auth.

```
curator mcp keys list
```
List all configured MCP API keys.

```
curator mcp keys revoke
```
Revoke (delete) an MCP API key.

```
curator mcp keys show
```
Show metadata for one MCP API key.

### GUI launch

```
curator gui
```
Launch the PySide6 desktop GUI. 9 tabs, 5 menus, full read/write surface.

---

## 2. MCP Tools (9 tools, exposed via curator-mcp)

These are callable from Claude Desktop or any MCP client connected to `curator-mcp.exe`. They show up as `curator__<tool_name>` in Claude.

```
health_check
```
Confirm the Curator MCP server is alive and able to read its DB.

```
list_sources
```
List every source configured in this Curator instance.

```
query_audit_log
```
Query Curator's audit log for events matching given filters (action, actor, since-hours, limit).

```
query_files
```
Query the file index with simple filters (source_id, path-glob, size, mtime range, file_type).

```
inspect_file
```
Get comprehensive metadata for a single file (the MCP equivalent of `curator inspect`).

```
get_lineage
```
Walk the lineage graph from a starting file to find related files via fuzzy / hash / filename edges.

```
find_duplicates
```
Find files with identical content (matching xxh3_128 hash).

```
list_trashed
```
List files in Curator's trash registry with optional filters.

```
get_migration_status
```
Query Curator's migration jobs (in-progress, completed, failed).

---

## 3. GUI — 9 Tabs

Launch with `curator gui`. The window title shows the current version (1.7.0 at last commit).

```
Inbox
```
Landing tab. Shows recent scans, pending review (ambiguous lineage edges), recent trash. **Currently showing 3 scans (2 completed + 1 failed)**.

```
Browser
```
All-files table. Sort/filter by source, path, size, mtime, extension, file type. Right-click for trash / inspect actions. **Currently showing 86,940 files**.

```
Bundles
```
Bundle list + memberships. Bundles group related files (a song's MP3 + cover art, a project's source + README + lockfile, etc.).

```
Trash
```
Trash registry view: every file Curator has sent to the Recycle Bin with metadata snapshots for restore.

```
Migrate
```
Migration job tracker. Active jobs show progress bars; completed jobs show summary stats.

```
Audit Log
```
Every mutation Curator has ever made: scans, sources adds/removes, trash actions, migrations, bundle ops. **v1.7-alpha.6 added a 6-control filter toolbar** (Since-hours / Actor / Action / Entity type / Entity ID / Apply+Clear) backed by `AuditRepository.query()`. Dropdowns auto-populate from distinct values in the DB.

```
Settings
```
Read-only config view (editable Settings is v1.8 work). Shows current DB path, source roots, log levels, MCP config.

```
Sources
```
**(NEW in v1.7-alpha.5.)** Live table of all registered sources (id / type / display name / enabled / # files / created). Top toolbar has `+ Add source...` (opens `SourceAddDialog`) and `Refresh`. Right-click any row for Enable / Disable / Remove. Remove gracefully refuses if files still reference the source (SQL ON DELETE RESTRICT) and suggests Disable instead.

```
Lineage Graph
```
networkx-rendered graph of lineage edges. Empty if no edges yet.

---

## 4. GUI — Menu Bar (5 menus)

### File menu

```
File → Refresh    (F5)
File → Quit       (Ctrl+Q)
```

### Edit menu

```
Edit → Send selected file to Trash    (Ctrl+T)
Edit → Restore selected trash record  (Ctrl+R)
Edit → Dissolve selected bundle       (Ctrl+D)
Edit → New bundle                     (Ctrl+N)
Edit → Edit selected bundle           (Ctrl+E)
```

### Tools menu

**As of v1.7.0: all 5 items are real dialogs/tabs. Zero placeholders.**

```
Tools → Scan folder...                       → ScanDialog          (v1.7-alpha.2)
Tools → Find duplicates...                   → GroupDialog         (v1.7-alpha.3)
Tools → Cleanup junk / empty / symlinks...   → CleanupDialog       (v1.7-alpha.4)
Tools → Sources manager...                   → Sources tab pivot   (v1.7-alpha.5)
Tools → Health check                         → HealthCheckDialog   (v1.7-alpha.1)
```

- **`ScanDialog`** — folder + source picker → background QThread runs `runtime.scan.scan()` → renders ScanReport (counts + errors + timings).
- **`GroupDialog`** — 2-phase duplicate finder: configure (source, keep strategy, match kind) → Find (background) → review groups with keepers highlighted → Apply (background, trash duplicates).
- **`CleanupDialog`** — 3-mode picker (junk files / empty dirs / broken symlinks) backed by `CleanupFindWorker` + `CleanupApplyWorker`. Duplicates mode delegates to GroupDialog via a shortcut button.
- **`Sources tab pivot`** — switches to the new Sources tab rather than opening a modal, so the user sees existing sources first.
- **`HealthCheckDialog`** — 8-section diagnostic running 22 checks in ~4 seconds. Refresh + Copy-to-clipboard buttons.

### Workflows menu

```
Workflows → Initial scan...           (01_initial_scan.bat)
Workflows → Find duplicates...        (02_find_duplicates.bat)
Workflows → Cleanup junk...           (03_cleanup_junk.bat)
Workflows → Audit summary (24h)       (04_audit_summary.bat)
Workflows → Health check              (05_health_check.bat)
```

Each launches a separate PowerShell console window via `os.startfile`.

### Help menu

```
Help → About Curator
```

---

## 5. Workflow Scripts (5 click-to-run batch files)

Located at `C:\Users\jmlee\Desktop\AL\Curator\scripts\workflows\`. Double-click any `.bat` or launch via GUI Workflows menu.

```
01_initial_scan.bat
```
Cautious initial scan of a chosen folder. Pre-flight file count + size, explicit confirmation, full summary after. Wraps `curator scan local <path>`.

```
02_find_duplicates.bat
```
Discover duplicate-content groups, sample each group, confirm before trashing all-but-one per group. Uses `curator --json group` for reliable parsing.

```
03_cleanup_junk.bat
```
Find junk files + empty dirs + broken symlinks under a folder. Plan-mode preview, confirm, apply via Recycle Bin. Uses `curator --json cleanup <subcommand>`.

```
04_audit_summary.bat
```
Read-only 24-hour audit report grouped by action, actor, hour. Saves full JSON to %TEMP%.

```
05_health_check.bat
```
8-section green/red diagnostic dashboard: filesystem layout, Python+venv, package versions, GUI deps, DB integrity, plugins, MCP config, real MCP probe.

Each script also has matching `.ps1` (the actual logic) and shares `_common.ps1` helpers (`Invoke-Curator`, `Read-Confirmation`, `Show-Banner`, etc.).

---

## 6. Built-in Plugins (9 registered)

All registered automatically; not user-toggleable in v1.6.

### Source plugins (file system adapters)

```
curator.core.local_source
```
Local filesystem source. Claims any `source_id` with `source_type='local'` (v1.6.4+).

```
curator.core.gdrive_source
```
Google Drive source via PyDrive2. Claims any `source_id` with `source_type='gdrive'` (v1.6.5+). Multi-account ready.

### Hash + classification pipeline

```
curator.core.audit_writer
```
Audit-log persistence (writes to `audit_log` table for every Curator mutation).

```
curator.core.classify_filetype
```
File-type classification (extension + magic-byte sniffing → `file_type` field).

### Lineage detection (three strategies)

```
curator.core.lineage_hash_dup
```
Hash-duplicate detector. Same xxhash3_128 across two paths → high-confidence lineage edge.

```
curator.core.lineage_filename
```
Filename-similarity detector. `report_v1.pdf` ↔ `report_v2.pdf` → "filename family" edge.

```
curator.core.lineage_fuzzy_dup
```
Fuzzy-content detector via MinHash-LSH (requires `datasketch` from `[beta]` extras). Detects near-duplicates with edits.

### External plugins (Atrium constitutional compliance)

```
atrium_safety
```
Enforces Atrium Principle 2 (Hash-Verify-Before-Move) via `curator_source_write_post` hook. Refuses non-compliant writes.

```
atrium_citation
```
Enforces Atrium Principle 3 (Citation Chain Preservation). v0.2 cross-source filter, sweep CLI.

---

## 7. Installer (one-click setup)

Located at `C:\Users\jmlee\Desktop\AL\Curator\installer\`.

```
Install-Curator.bat
```
Double-click entry point. Spawns elevated PowerShell, runs `Install-Curator.ps1`.

```
Install-Curator.ps1
```
The 10-step installer (32 KB). Detects Python, creates venv, installs `curator[gui,mcp,organize]`, builds default config, wires Claude Desktop's MCP config, **Step 9 = real MCP probe verification gate** with auto-rollback if probe fails.

```
README.md
```
Installer usage + troubleshooting (7 KB).

---

## 8. Configuration Files

```
C:\Users\jmlee\Desktop\AL\.curator\curator.toml
```
Canonical Curator config. Set as `CURATOR_CONFIG` env var. Points at the canonical DB.

```
C:\Users\jmlee\Desktop\AL\.curator\curator.db
```
Canonical SQLite DB. Currently **86,940 indexed files**, 14 audit entries, 1 source enabled.

```
%APPDATA%\Claude\claude_desktop_config.json
```
Claude Desktop's MCP server config. Has the `curator` entry pointing at `curator-mcp.exe` with `CURATOR_CONFIG` env var.

---

## 9. Documentation Files

Located at `C:\Users\jmlee\Desktop\AL\Curator\docs\`. Open any in your editor or GitHub.

### User-facing

```
docs/USER_GUIDE.md
```
**Read this first.** Comprehensive 700-line guide: every CLI subcommand, every MCP tool, every GUI tab, config reference, 8 recipes, troubleshooting.

```
docs/NEXT_SESSION_CHECKLIST.md
```
4-test smoke checklist to verify the stack after a reboot or update.

```
docs/AD_ASTRA_CONSTELLATION.md
```
Workspace-level umbrella doc. Where Curator sits in the Ad Astra constellation.

### Design docs (engineering reference)

```
docs/ROADMAP.md
```
Where Curator is going (23 KB).

```
docs/design/GUI_V2_DESIGN.md
```
Full v1.7 / v1.8 / v1.9 GUI architecture (12 KB). v1.7 portion is now SHIPPED — all 5 dialogs and the Sources tab landed in the v1.7-alpha.1..6 sequence. v1.8 / v1.9 portions remain spec-only.

```
docs/CURATOR_MCP_SERVER_DESIGN.md
```
How the MCP layer is structured (27 KB).

```
docs/CURATOR_MCP_HTTP_AUTH_DESIGN.md
```
HTTP Bearer auth design (25 KB, v1.5.0+).

```
docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md
```
The `curator_audit_event` hookspec (26 KB, v1.1.3).

```
docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md
```
The `curator_plugin_init` lifecycle hook (23 KB, v1.1.2).

```
docs/CURATORPLUG_ATRIUM_CITATION_DESIGN.md
```
atrium-citation plugin internals (21 KB).

### Tracer (migration engine) design

```
docs/TRACER_PHASE_2_DESIGN.md
```
Resumable jobs + workers (51 KB).

```
docs/TRACER_PHASE_3_DESIGN.md
```
Retry + 4-mode conflict resolution (48 KB).

```
docs/TRACER_PHASE_4_DESIGN.md
```
Cross-source overwrite-with-backup + rename-with-suffix (40 KB).

```
docs/TRACER_SESSION_B_RUNBOOK.md
```
End-to-end Drive migration runbook (10 KB).

### Phase Beta features

```
docs/PHASE_BETA_LSH.md
```
MinHash-LSH fuzzy-duplicate index (7 KB).

```
docs/PHASE_BETA_WATCH.md
```
Watch mode (filesystem event monitoring) (8 KB).

### Cross-pillar coordination

```
docs/APEX_INFO_REQUEST.md
```
What APEX needs from Curator (6 KB).

```
docs/APEX_INFO_RESPONSE.md
```
Curator's response to APEX's request (30 KB).

```
docs/CONCLAVE_PROPOSAL.md
```
Conclave (multi-pillar coordination) proposal (29 KB).

```
docs/CONCLAVE_LENSES_v2.md
```
Conclave query lens spec (15 KB).

### Lessons learned

```
docs/lessons/2026-05-09_install_mcp_session.md
```
9 numbered lessons from the install + MCP debug saga (11 KB).

---

## 10. Repository Links

```
https://github.com/KULawHawk/Curator
```
Main Curator repo (HEAD = v1.7.0 tag at commit `a41840d`).

```
https://github.com/KULawHawk/curatorplug-atrium-safety
```
atrium-safety plugin (v0.3.0).

```
https://github.com/KULawHawk/curatorplug-atrium-citation
```
atrium-citation plugin (v0.2.0).

```
https://github.com/KULawHawk/curatorplug-atrium-reversibility
```
atrium-reversibility plugin (DESIGN deferred — see `Atrium/design/LIFECYCLE_GOVERNANCE.md` for the new design).

---

## 11. Quick-start one-liners

```powershell
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\Activate.ps1; curator doctor
```
Activate venv + health check.

```powershell
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\Activate.ps1; curator gui
```
Launch the GUI.

```powershell
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\Activate.ps1; curator scan local "C:\path\to\index"
```
Index a folder.

```powershell
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\Activate.ps1; curator group --json | ConvertFrom-Json
```
Find duplicates (JSON output).

```powershell
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\Activate.ps1; curator audit --since-hours 24 -n 50
```
See the last day's activity.

---

**Total surface count (v1.7.0):** 43 CLI commands + 9 MCP tools + 9 GUI tabs + 14 GUI menu actions + 5 workflow scripts + 9 plugins + 24 doc files + 3 installer files + 3 config files + 4 GitHub repos = **123 discrete clickable items**.

**Delta v1.6.5 → v1.7.0:** +1 GUI tab (Sources), +0 menu actions (5 placeholders graduated to real dialogs), +3 doc files (FEATURE_TODO, BUILDING_BLOCKS, ALL_FILES), +0 new CLI commands or MCP tools.
