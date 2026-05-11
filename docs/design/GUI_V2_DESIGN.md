# Curator GUI v2 design — full CLI parity + batch workflows

**Status:** Design captured 2026-05-09. **Not yet implemented.**
**Target version:** v1.7 (foundation) → v1.8 (full parity)
**Companion:** `scripts/workflows/` ships PowerShell wrappers as the interim solution.

---

## Why this exists

The current GUI (v0.34–v0.43, shipping in v1.6.1) has 8 tabs and meaningful
right-click action menus, but **discoverability is poor** and **several CLI
commands have no GUI surface at all**. Jake's working complaint: "the GUI does
not seem very intuitive to use [and lacks] batch command options to harness
the capabilities of the many tools built within it."

Two distinct gaps to close:

1. **CLI parity gap** — every CLI subcommand should have a discoverable GUI
   surface (dialog, button, panel, or menu item).
2. **Batch workflow gap** — common multi-step workflows (scan → group → review
   → apply, full asset cleanup, etc.) should be one-click.

This doc captures both.

---

## Current GUI surface (what exists today, v1.6.1)

### Tabs (8 total)

| Tab | What it shows | Mutation surface |
|---|---|---|
| **Inbox** | Pending review queue | `_build_inbox_tab` (line 154) |
| **Browser** | Every indexed file | Right-click → "Send to Trash" |
| **Bundles** | All bundles + member counts | Right-click → "Dissolve" / "New" / "Edit" |
| **Trash** | Trashed file records | Right-click → "Restore" (often impossible on Windows due to send2trash limitation) |
| **Migrate** | Migration jobs + per-file progress (400-line implementation with `MigrationProgressBridge` signals) | Job list, status detail |
| **Audit Log** | Audit log table | Read-only |
| **Settings** | Config table | Read-only |
| **Lineage Graph** | Visual graph of lineage edges | Read-only viewer (419 lines) |

### Menus (3 total)

| Menu | Items |
|---|---|
| **File** | Refresh, Quit |
| **Edit** | Send to Trash, Restore, Dissolve, New bundle, Edit bundle |
| **Help** | About |

### Dialogs

- `FileInspectDialog` (691 lines) — full file detail with flex attrs + lineage edges
- `BundleEditorDialog` — bundle creation/editing

---

## CLI commands and their GUI gap analysis

Mapping every CLI subcommand to its current GUI status:

| CLI | Has GUI surface? | Gap |
|---|---|---|
| `curator doctor` | ❌ | Need health dashboard panel (could be a Status menu item) |
| `curator sources list/show` | ⚠️ partial (Settings tab shows config; no source list) | Need dedicated Sources tab or dialog |
| `curator sources add/enable/disable/remove` | ❌ | Need source management dialog |
| `curator sources config` | ⚠️ partial (Settings tab is read-only) | Need editable config |
| `curator scan` | ❌ | Need Scan dialog with source picker + folder picker |
| `curator inspect` | ✅ | `FileInspectDialog` covers this |
| `curator group` | ❌ | Need duplicate finder dialog with --keep strategy + apply |
| `curator cleanup empty-dirs/broken-symlinks/junk/duplicates` | ❌ | Need cleanup wizard (4 sub-modes, plan vs apply) |
| `curator trash` | ✅ | Right-click in Browser tab |
| `curator restore` | ⚠️ partial (often raises RestoreImpossibleError on Windows) | Need fallback handling |
| `curator audit` | ✅ | Audit Log tab |
| `curator migrate` (Phase 1) | ❌ | Need migrate-now dialog (the existing Migrate tab is for Phase 2 jobs only) |
| `curator migrate` (Phase 2) | ✅ | Migrate tab |
| `curator organize` | ❌ | Need organize planner panel |
| `curator organize-revert` | ❌ | Need revert button in organize panel |
| `curator lineage` | ✅ | Lineage Graph tab |
| `curator bundles list/show/create/dissolve` | ✅ | Bundles tab + dialogs |
| `curator gdrive paths/status/auth` | ❌ | Need Drive auth flow UI |
| `curator watch` | ❌ | Need live-watch dashboard (real-time event stream) |
| `curator safety check/paths` | ❌ | Need path-safety widget (probably part of organize panel) |
| `curator mcp keys` | ❌ | Need MCP key management dialog (low priority — rare use) |

**Summary:** 6 fully covered, 4 partially covered, **11 fully missing**.

---

## Proposed v1.7 architecture (foundation: parity, then workflows)

### New menu structure

```
File           Edit              Tools (NEW)        Workflows (NEW)       Help
├─ Refresh    ├─ Send to Trash  ├─ Scan...         ├─ Initial scan       ├─ About
└─ Quit       ├─ Restore        ├─ Find Duplicates ├─ Find duplicates    └─ User Guide
              ├─ Dissolve       ├─ Cleanup Junk... ├─ Cleanup junk
              ├─ New bundle     ├─ Organize...     ├─ Drive migration
              └─ Edit bundle    ├─ Migrate...      ├─ Audit summary
                                ├─ Sources...      ├─ Health check
                                ├─ Health Check    └─ Custom...
                                ├─ Drive Auth...
                                └─ Settings...
```

### New tab: Sources

Lists all sources with columns: id, kind, status (enabled/disabled), root,
file count, last scan time. Buttons:
- **Add Source...** → dialog with kind picker (local / gdrive)
- **Enable/Disable** → toggle (no confirm)
- **Remove** → confirm dialog (warns if files reference this source)
- **Configure...** → opens config editor

### New tab: Watch (live)

Real-time view of `curator watch` events:
- Top: source picker + start/stop button
- Middle: scrolling event list (ADDED / MODIFIED / DELETED with paths + timestamps)
- Bottom: counters (events/sec, total events, current debounce window)
- Right side: option to enable `--apply` (live incremental scan)

### New dialogs

| Dialog | Purpose | Wraps |
|---|---|---|
| `ScanDialog` | Pick source + folder + ignore globs, click Scan, watch progress | `curator scan` |
| `GroupDialog` | Pick keep strategy, see preview group count, confirm to apply | `curator group --apply` |
| `CleanupDialog` | Pick path + which categories (junk/empty-dirs/symlinks), preview, apply | `curator cleanup` |
| `OrganizeDialog` | Pick source + type (music/photo/document) + target, preview path proposals | `curator organize` |
| `MigrateDialog` | Pick src + dst + workers + conflict mode, kick off Phase 1 or Phase 2 | `curator migrate --apply` |
| `SourceAddDialog` | Pick kind, fill config keys (parent_id for gdrive, etc.) | `curator sources add` |
| `DriveAuthDialog` | Run interactive OAuth, show status, paths | `curator gdrive auth` |
| `HealthCheckDialog` | Live dashboard from `curator doctor` + DB check + MCP probe | `05_health_check.ps1` content |

### Workflows menu (composite operations)

These wrap multi-step CLI sequences as single-click flows. They're modeled
on the PowerShell scripts in `scripts/workflows/` so the same logic ships
on day 1 of v1.7:

| Menu item | Backed by | What it does |
|---|---|---|
| Initial scan | `01_initial_scan.ps1` | Register source if not present + scan + summary |
| Find duplicates | `02_find_duplicates.ps1` | Group --json + render report + confirm + apply |
| Cleanup junk | `03_cleanup_junk.ps1` | All 3 cleanup categories + report + confirm + apply |
| Drive migration | (script #6, not yet shipped) | gdrive auth + register Drive source + plan + apply |
| Audit summary | `04_audit_summary.ps1` | 24h grouped view of audit log |
| Health check | `05_health_check.ps1` | Full stack diagnostic |
| Custom... | (free-form) | User picks: source, action sequence, applies |

Implementation note: workflows can either spawn the existing PowerShell
scripts (simplest) or re-implement them in Python directly inside the GUI
(better integration, but more work). Recommend Phase 1 spawns scripts;
Phase 2 inlines them.

---

## Discoverability fixes (low effort, high value)

These are quick wins that should ship in v1.6.2 or v1.7 alpha:

1. **Add a Tools menu** — even before all the dialogs are built, surface the
   most-used CLI commands as menu items with placeholder dialogs.
2. **Add toolbar buttons** on each tab — Inbox needs "Process," Browser needs
   "Scan/Find Duplicates," Trash needs "Empty all" (with confirm). Currently
   all action lives in right-click menus.
3. **Update the docstring** of `curator gui` — it says "Read-only first ship.
   Three tabs..." which is FALSE. Currently 8 tabs and 5 mutations.
4. **Status bar workflow hints** — the status bar already shows row counts;
   add a contextual hint like "Right-click a row to see actions" or
   "Tools menu → Scan to add files."
5. **First-run tip** — on a freshly-initialized DB (file count = 0), show a
   modal: "No files indexed yet. Click Tools → Scan to start, or use
   Workflows → Initial scan."

---

## Implementation sequencing

### v1.6.2 (patch — discoverability only, ~1 session)

- [ ] Update `curator gui` docstring to reflect actual capability
- [ ] Add Tools menu (placeholder items that say "coming in v1.7")
- [ ] Add Workflows menu that calls into `scripts/workflows/*.bat` via subprocess
- [ ] First-run modal for empty DB
- [ ] Status bar hints

### v1.7.0 (foundation — full parity for high-value commands, ~2-3 sessions)

- [ ] `ScanDialog` — single most-needed dialog
- [ ] `GroupDialog` + apply
- [ ] `CleanupDialog` (4 sub-modes)
- [ ] Sources tab + `SourceAddDialog`
- [ ] `HealthCheckDialog`
- [ ] Audit Log tab: filter UI (since-hours, action, actor)
- [ ] Tools menu populated with real dialogs (not placeholders)

### v1.8.0 (polish + advanced surfaces, ~2 sessions)

- [ ] `OrganizeDialog`
- [ ] `MigrateDialog` (Phase 1 trigger from GUI; Phase 2 already in Migrate tab)
- [ ] `DriveAuthDialog`
- [ ] Watch tab (live event stream)
- [ ] `SafetyCheckDialog`
- [ ] Settings tab: editable config (no longer read-only)

### v1.9.0 (workflows native + lifecycle integration, ~2-3 sessions)

- [ ] Workflows menu items reimplemented in Python (no longer subprocess to PS)
- [ ] Custom workflow builder (drag actions, save as template)
- [ ] Integration with `atrium-reversibility` once that ships (per
  `Atrium/design/LIFECYCLE_GOVERNANCE.md`) — staged actions, rollback UI,
  blocked-version banners

---

## Tech notes

- **PySide6 is sufficient** for everything proposed. No need to switch to
  Tauri/Electron/web stack.
- **Reuse existing models** — `AuditLogTableModel`, `BundleTableModel`,
  `FileTableModel`, etc. already exist in `src/curator/gui/models.py`. New
  dialogs should add new models, not reinvent.
- **Background work via `QThread`** — scans, group computations, migrations
  must not block the UI thread. The existing `MigrationProgressBridge`
  pattern (in `migrate_signals.py`) is the model.
- **Test surface** — `pytest-qt` is already a dev dependency. Every new
  dialog gets a `tests/gui/test_*_dialog.py`.

---

## What this design does NOT cover

- **GUI workflow builder UX research** — drag-and-drop action composition
  is a real UX task; the v1.9 sketch above is rough.
- **Mobile / web access** — out of scope; PySide6 desktop only.
- **Multi-window** — currently one window; future could open multiple
  Browser tabs for different sources.
- **Localization** — English-only for v1.x.

---

## User-flagged improvements (Jake, 2026-05-09)

During the v1.6.4 smoke test, Jake ran `Workflows → Initial scan` and got it working on his 86k-file / 11 GB AL workspace. He flagged three improvements for when the GUI v2 dialogs ship:

1. **Live progress bar + currently-processing-file display** during scans. Curator's `ScanService` already emits progress events internally (the Migrate tab's `MigrationProgressBridge` is the proven pattern); a ScanProgressBridge would surface (i) files-seen / total, (ii) bytes hashed / total, (iii) current file path, (iv) ETA. Must not slow the scan — update at most every 100 ms or every Nth file.

2. **Directory picker dialog** instead of typing the path. Standard `QFileDialog.getExistingDirectory()` — trivial to wire. The CLI keeps the typed-path option for scripting; the GUI should default to the picker.

3. **Window inside the app** instead of separate console window. The current `Workflows → Initial scan` launches a `.bat` via `os.startfile()`, opening a new cmd console. Cleaner long-term: an in-app modal/dock with the same UX (prompt for path, confirm, show progress, show summary). The .bat workflow becomes the fallback when running detached.

All three are part of the v1.7 ScanDialog spec already in this doc; this section just calls them out explicitly so they don't get lost.

## Open questions for next iteration

- Should the Sources tab become the *primary* tab (replacing Inbox as default)?
  Sources are how you start; Inbox is for ongoing triage. New users hit
  Inbox-empty and don't know what to do.
- Should workflows be modal (block UI until done) or asynchronous (run in
  background, queue panel)? Phase 2 migrations are already async; consistency
  argues for async everywhere, but it's more work.
- How does the Watch tab interact with the Browser tab? Should new files
  appear live in Browser when Watch is running with `--apply`?
- Should the Settings tab editing flow auto-restart Curator processes that
  cache the config? Or just warn that changes take effect next launch?
