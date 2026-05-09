# Curator — User Guide

**Version:** v1.6.1 (with atrium-citation v0.2.0, atrium-safety v0.3.0)
**Audience:** Anyone who has Curator installed and wants to actually use it.
**Companion docs:** [`installer/README.md`](../installer/README.md) for setup • [`DESIGN.md`](../DESIGN.md) for implementation spec • [`docs/lessons/`](lessons/) for postmortems

---

## What Curator is, in one paragraph

Curator gives every file in your indexed directories a stable identity (a `curator_id` derived from content hash), tracks lineage edges between files (e.g., "this is a copy of that," "this was renamed from that"), records every destructive operation in an append-only audit log, and routes every delete through the OS Recycle Bin so nothing is lost permanently without explicit confirmation. It's not a backup tool, not a sync tool, not a search engine — it's an **intelligence layer over files** that other tools (LLMs via MCP, scripts via CLI, your eyes via GUI) can query.

## When to reach for Curator

| Goal | Curator command |
|---|---|
| Find duplicates across folders | `curator group` |
| Move files between drives without losing track | `curator migrate` |
| Find empty dirs / broken symlinks / junk files | `curator cleanup` |
| Track which files came from which | `curator lineage <file>` |
| See history of every action ever taken | `curator audit` |
| Soft-delete a file (recoverable) | `curator trash <file>` |
| Restore something you trashed | `curator restore <id>` |
| Sync a Google Drive folder into the index | `curator scan gdrive` |
| Watch a folder for changes in real time | `curator watch` |
| Plan an organize operation by file type | `curator organize` |

If the answer is "I just want to see what's in the index," that's the **GUI** (`curator gui`) or the **MCP tools** (from any Claude chat). All other actions live in the CLI.

---

## Quick start (5 minutes after install)

Assumes you ran `Install-Curator.bat` successfully and have Claude Desktop restarted.

```powershell
# Activate the Curator venv (so 'curator' is on PATH)
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\Activate.ps1

# 1. Confirm the stack is healthy
curator doctor

# 2. Register a source for a real folder you want to track
curator sources add local "C:\Users\jmlee\Desktop\AL\Curator"

# 3. Scan it (indexes files, computes hashes, detects lineage)
curator scan local "C:\Users\jmlee\Desktop\AL\Curator"

# 4. See what was indexed
curator audit --action scan.complete -n 1
curator inspect "C:\Users\jmlee\Desktop\AL\Curator\README.md"

# 5. Find duplicates (read-only preview)
curator group

# 6. Open the GUI to browse what's there
curator gui
```

Or from any Claude Desktop chat (curator MCP tools are available globally):

> "Add my Curator repo as a local source, scan it, and tell me how many files were indexed and what file types they break down by."

---

## Core concepts

### Sources

A **source** is a logical bucket of files Curator tracks. Each source has:
- a **kind** (`local` for a filesystem path, `gdrive` for a Google Drive folder)
- a **name** (defaults to the kind, but you can have multiple e.g., `local`, `local:vault`, `local:archive`)
- per-source config (e.g., for Drive: `parent_id` to scope to a specific folder)

Sources are registered with `curator sources add` and can be enabled/disabled without losing their indexed files.

### Files & hashing

Every indexed file gets:
- a stable **`curator_id`** (UUID)
- **xxhash3_128** (primary content hash, fast)
- **md5** (secondary, for cross-checks)
- a **fuzzy hash** for text-like files (used for "this is a near-duplicate of that" detection)

Two identical-content files in different paths share the same `xxhash3_128` and get grouped together by `curator group`. The `curator_id` is per-file (per-path), not per-content.

### Bundles

A **bundle** is an explicit grouping of files you (or a plugin) declare belong together — e.g., a draft document and its source images, or all files exported from one project. Bundles don't move files; they just carry a label that survives moves and renames.

### Lineage

A **lineage edge** says "file A was derived from file B" with a confidence score and a reason. Curator detects lineage on scan (e.g., `lineage_hash_dup` plugin notices identical content; `lineage_filename` plugin notices filename similarity; `lineage_fuzzy_dup` notices near-duplicate text). Lineage edges survive moves — if you migrate a file, its edges follow.

### Audit log

Every action that touches the index — scan, migrate, trash, restore, group, cleanup, and plugin-emitted compliance events — writes a row to `audit_log`. Each row has:
- `audit_id` (sequential)
- `occurred_at` (timestamp)
- `actor` (which subsystem did it)
- `action` (string like `migration.move`, `compliance.citation_gap`)
- `entity_type` + `entity_id`
- structured `details` (JSON)

Querying the audit log is how you answer "what actually happened to this file" or "what did Curator do at 3am yesterday."

### Trash & restore

`curator trash` does NOT permanently delete. It:
1. Captures the file's full metadata (path, hash, lineage, bundle memberships)
2. Sends the actual file to the OS Recycle Bin (Windows) / `~/.Trash` (macOS)
3. Marks the file row in the index as trashed
4. Writes an audit entry

`curator restore` reverses the above. Until you empty the Recycle Bin, every trash is reversible.

### Plugins (Atrium safety + citation, plus core)

Curator's behavior is extended via plugins (pluggy hookspecs). Three plugin packages ship today:

- **Curator core plugins** (built into curator): local source, gdrive source, file-type classifier, hash-based lineage, filename-based lineage, fuzzy lineage, audit writer.
- **`curatorplug-atrium-citation`** (v0.2.0): tracks citation/attribution gaps when files move across sources. Surfaces in audit log as `compliance.citation_gap`, `compliance.citation_sweep_*`.
- **`curatorplug-atrium-safety`** (v0.3.0): enforces safety invariants on organize/migrate operations (currently Phase Gamma F1 — path safety checks).

`curator doctor` lists all loaded plugins. Currently 9 plugins should be registered.

---

## CLI reference

All commands accept these top-level flags (place them before the subcommand):
- `--config FILE` — path to curator.toml override (default: search standard locations)
- `--db FILE` — DB path override
- `--json` — machine-readable output
- `--quiet` / `-q` — suppress info-level logs
- `--verbose` / `-v` — DEBUG; `-vv` for TRACE
- `--no-color` — disable color output

Run `curator <subcommand> --help` for full options on any command.

### `curator doctor`

Run integrity + health checks against the index and the environment.

```powershell
curator doctor
```

Reports: config source, DB path, log path, registered plugins, vendored dependency status (`ppdeep`, `send2trash`), index stats (file count, audit count), source list with file counts.

**When to use:** any time something seems off, or after install.

### `curator sources`

Source registration and toggling.

```powershell
curator sources list                                    # all registered
curator sources show local                              # detail for one
curator sources add local "C:\path\to\folder"           # register
curator sources add gdrive --config-key parent_id --config-value "<drive-folder-id>"
curator sources config local --get root                 # read one config key
curator sources config local --set root --value "C:\new\path"  # mutate
curator sources enable local                            # re-enable
curator sources disable local                           # disable (keep data)
curator sources remove local                            # delete (fails if files reference it)
```

**When to use:** every time you want to start tracking a new folder or Drive.

### `curator scan`

Scan a source root, hash files, detect lineage.

```powershell
curator scan local "C:\Users\jmlee\Desktop\AL\Curator"
curator scan local "C:\path" --ignore "**/__pycache__/**" --ignore "*.pyc"
```

**When to use:** initial population, or after you know files have changed (or use `curator watch` for live).

### `curator inspect`

Full file details.

```powershell
curator inspect "C:\Users\jmlee\Desktop\AL\Curator\README.md"
curator inspect <curator_id>                # use the UUID
curator inspect "C:\path\prefix"            # path prefix also works
```

Shows: paths, hashes, source, bundle memberships, all lineage edges (in + out), flex attrs.

**When to use:** "what does Curator know about this file?"

### `curator group`

Find duplicate-content groups. Same-content files are grouped by xxhash3_128.

```powershell
curator group                                      # plan only (no changes)
curator group --apply                              # actually trash extras
curator group --apply --keep oldest                # keep oldest, trash newer (default)
curator group --apply --keep newest                # keep newest
curator group --apply --keep shortest_path         # keep file at shortest path
curator group --apply --keep longest_path          # keep file at deepest path
curator group --json > duplicates.json             # machine-readable
```

**When to use:** initial cleanup, periodic dedup. ALWAYS run without `--apply` first to see what would happen.

### `curator cleanup`

Find junk patterns. Has 4 sub-subcommands:

```powershell
curator cleanup empty-dirs "C:\path"               # find empty dirs
curator cleanup broken-symlinks "C:\path"          # find dead symlinks
curator cleanup junk "C:\path"                     # Thumbs.db, .DS_Store, ~$*, etc.
curator cleanup duplicates                         # alternative entry point to group
```

Each defaults to plan-mode; pass `--apply` to actually trash items.

**When to use:** after a scan, to find low-value items safe to remove.

### `curator trash` and `curator restore`

Soft-delete a file (it goes to OS Recycle Bin), or restore one.

```powershell
curator trash "C:\path\to\file.txt"                          # plan only
curator trash "C:\path\to\file.txt" --apply                   # actually trash
curator trash <curator_id> --apply --reason "old draft"

curator restore <curator_id>                                  # plan only
curator restore <curator_id> --apply                          # actually restore
curator restore <curator_id> --apply --to "C:\new\location"   # restore to different path
```

**When to use:** anytime you want to delete something but keep the option to undo.

### `curator audit`

Query the audit log.

```powershell
curator audit                                       # last 50 entries
curator audit -n 200                                # last 200 entries
curator audit --since-hours 24                      # last 24 hours
curator audit --action migration.move               # one action type
curator audit --actor curator.migrate               # one subsystem
curator audit --action compliance.citation_gap      # plugin-emitted events
curator audit --json | ConvertFrom-Json             # for scripts
```

**When to use:** investigating what happened, building reports, debugging plugin behavior.

### `curator migrate`

Relocate files across paths or sources, preserving index integrity. Two execution paths:
- **Phase 1** (default, `--workers 1`): in-memory plan + apply, single-threaded, fast for small jobs
- **Phase 2** (`--workers > 1` or `--resume`): persisted job, worker-pool, resumable on interrupt

```powershell
# Plan only (no moves)
curator migrate local "C:\Music" "D:\Music"

# Phase 1: apply, single-threaded
curator migrate local "C:\Music" "D:\Music" --apply

# Phase 2: 4 workers, persisted (resumable)
curator migrate local "C:\Music" "D:\Music" --apply --workers 4

# Filter by glob
curator migrate local "C:\Music" "D:\Music" --apply --include "**/*.mp3"

# Cross-source local -> Google Drive
curator migrate local "C:\Music" gdrive --apply

# Resume an interrupted Phase 2 job
curator migrate --list                              # find the job_id
curator migrate --status <job_id>                   # progress
curator migrate --resume <job_id> --workers 4       # continue
curator migrate --abort <job_id>                    # cancel
```

Conflict handling (when destination already has a file):
- `--on-conflict skip` (default): leave src unchanged, log skipped
- `--on-conflict fail`: stop the job
- `--on-conflict overwrite-with-backup`: rename existing dst to `<name>.bak`, then move src
- `--on-conflict rename-with-suffix`: write src to `<name>_2.ext`, etc.

Cross-source retry: `--max-retries 3` for transient quota/rate-limit errors.

**When to use:** moving folders between drives, archiving to Drive, consolidating sources.

### `curator organize`

Plan-mode preview of an organize operation. NEVER moves files in current Phase Gamma scope.

```powershell
curator organize local                              # bucket by SAFE/CAUTION/REFUSE
curator organize local --type music --target "D:\Library\Music"
curator organize local --type photo --target "D:\Library\Photos"
curator organize local --type document --target "D:\Library\Docs"
```

For each SAFE-bucket file, computes a proposed destination path:
- music: `<target>/Artist/Album/NN - Title.ext` (via `mutagen` tags)
- photo: `<target>/YYYY/YYYY-MM-DD/<filename>` (via EXIF)
- document: `<target>/YYYY/YYYY-MM/<filename>` (via PDF metadata or filename pattern)

**When to use:** sanity-check what an automated organize would propose before any future apply mode ships.

### `curator lineage`

Show all lineage edges touching a file.

```powershell
curator lineage "C:\path\to\file.txt"
curator lineage <curator_id>
```

Output: incoming edges (other files that became this), outgoing edges (files derived from this), with confidence scores.

**When to use:** "where did this come from / where did this go?"

### `curator bundles`

Explicit groupings of files.

```powershell
curator bundles list
curator bundles show <bundle_id>
curator bundles create --name "Project X" --files file1 --files file2 --files file3
curator bundles dissolve <bundle_id>
```

**When to use:** marking sets of files as belonging together (e.g., a paper draft + its source images + the BibTeX file).

### `curator gdrive`

Google Drive auth + per-alias credential management.

```powershell
curator gdrive paths gdrive:home              # where credentials live
curator gdrive status gdrive:home             # offline auth check
curator gdrive auth gdrive:home               # interactive OAuth flow
```

Aliases let you have multiple Drive accounts (e.g., `gdrive:personal`, `gdrive:work`).

**When to use:** initial Drive setup, troubleshooting auth. Run `curator gdrive auth` once per alias before scanning a Drive source.

### `curator watch`

Watch local source roots for filesystem events.

```powershell
curator watch local                                 # print events; no DB writes
curator watch local --apply                         # incremental scan on each event
curator watch --debounce-ms 2000                    # coalesce rapid changes
curator watch --json | grep MODIFIED                # pipe-friendly
```

Blocks until Ctrl+C.

**When to use:** keeping the index live during active work without re-running full scans.

### `curator safety`

Safety primitives for organize actions (Phase Gamma F1).

```powershell
curator safety check "C:\path\to\file.ext"        # SAFE / CAUTION / REFUSE + reasons
curator safety paths                               # OS-managed paths Curator never touches
```

**When to use:** before scripting any move/delete to know if Curator considers a path automation-safe.

### `curator mcp keys`

MCP HTTP server auth key management (only needed if running MCP over HTTP, not stdio).

```powershell
curator mcp keys generate
curator mcp keys list
curator mcp keys revoke <key_id>
curator mcp keys show <key_id>
```

**When to use:** rare. Default install uses stdio MCP via Claude Desktop config.

### `curator gui`

Launches the PySide6 desktop window.

```powershell
curator gui
```

See the **GUI guide** section below for what each tab does.

### `curator doctor` (already covered above)

---

## MCP tools reference

When Claude Desktop is running with the curator MCP server attached (per the installer), any chat can call these 9 tools. Phrase requests in natural language; Claude picks the right tool.

| Tool | What it does | Example chat phrasing |
|---|---|---|
| `health_check` | Verify Curator is alive; returns version, plugin count, DB path | "Is Curator working?" |
| `list_sources` | List all registered sources | "What sources is Curator tracking?" |
| `query_files` | Filter the file index | "Show me Python files larger than 10KB in the Curator repo" |
| `query_audit_log` | Query audit log | "What did Curator do in the last hour?" / "Show me all migration events from yesterday" |
| `inspect_file` | Full details for one file | "Tell me everything Curator knows about README.md" |
| `get_lineage` | Lineage edges for a file | "Where did this file come from?" |
| `find_duplicates` | Duplicate groups | "Find all duplicate files in the index" |
| `list_trashed` | Trashed files | "Show me what's in Curator's trash" |
| `get_migration_status` | Phase 2 migration job status | "What's the status of the migration I started yesterday?" |

All MCP tools are **read-only**. Destructive operations (trash, migrate apply, etc.) require the CLI — by design, so an LLM can never accidentally delete files for you.

---

## GUI guide

`curator gui` opens a PySide6 desktop window titled "Curator <version>". **Read-only first ship** — none of the tabs let you trash, migrate, or change anything. They are viewers over the same data the CLI sees.

### Browser tab

Lists every indexed file. Columns include path, source, size, hash, classification.
- **What you do here:** filter, sort, drill into a single file's details
- **What you do elsewhere:** to actually trash/move/etc., go to CLI

### Bundles tab

Lists every bundle and its member counts.
- **What you do here:** see what bundles exist, see what files belong to which bundle
- **What you do elsewhere:** create/dissolve bundles via `curator bundles`

### Trash tab

Lists every file Curator has trashed (still recoverable).
- **What you do here:** see what's in the trash, when it was trashed, why
- **What you do elsewhere:** restore via `curator restore`, or empty Windows Recycle Bin to make permanent

### Why no action buttons in v1.6.1?

The GUI surfaced this functionality intentionally as read-only first because:
1. CLI + MCP cover all destructive actions with audit + reversibility built in
2. A click-to-delete button without confirmation is a footgun
3. Future versions may add staged-action panels (proposed but not built)

For now: **CLI is your control panel; GUI is your dashboard.**

---

## Common workflows (recipes)

### Recipe 1 — Initial index of a new folder

```powershell
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\Activate.ps1
curator sources add local "C:\path\to\folder"
curator scan local "C:\path\to\folder"
curator doctor                                    # confirm files indexed
```

### Recipe 2 — Find and trash duplicates (cautious)

```powershell
# Phase 1: discover
curator group --json > duplicates.json
# Review duplicates.json (open in editor)

# Phase 2: trash extras, keeping the oldest
curator group --apply --keep oldest

# Phase 3: review what was trashed
curator audit --action trash --since-hours 1

# Optional: restore something you didn't mean to trash
curator restore <curator_id> --apply
```

### Recipe 3 — Find junk files in a directory tree

```powershell
curator cleanup junk "C:\Users\jmlee\Downloads"           # plan only
curator cleanup junk "C:\Users\jmlee\Downloads" --apply   # trash them
curator cleanup empty-dirs "C:\Users\jmlee\Downloads" --apply
curator cleanup broken-symlinks "C:\path" --apply
```

### Recipe 4 — Migrate folder local → Drive

```powershell
# One-time: register a Drive alias and authenticate
curator gdrive auth gdrive:home

# Register Drive as a source (point at a specific Drive folder)
curator sources add gdrive --config-key parent_id --config-value "<drive-folder-id>"

# Plan the migration
curator migrate local "C:\to-archive" gdrive

# Apply with 4 workers (resumable Phase 2)
curator migrate local "C:\to-archive" gdrive --apply --workers 4

# Monitor
curator migrate --list
curator migrate --status <job_id>
```

### Recipe 5 — Audit query for the past day

```powershell
curator audit --since-hours 24 -n 200 --json | `
  ConvertFrom-Json | `
  Group-Object action | `
  Sort-Object Count -Descending | `
  Format-Table Name, Count -AutoSize
```

### Recipe 6 — Live scan during active work

```powershell
# In a dedicated terminal, watch a folder; events trigger incremental scans
curator watch local --apply --debounce-ms 2000
```

### Recipe 7 — Generate a duplicate-by-size report

```powershell
curator group --json | `
  ConvertFrom-Json | `
  ForEach-Object {
    $totalSize = ($_.members | Measure-Object size -Sum).Sum
    $extraSize = $totalSize - ($_.members[0].size)
    [PSCustomObject]@{
      Hash = $_.hash.Substring(0, 12)
      Count = $_.members.Count
      ExtraBytes = $extraSize
      Sample = $_.members[0].path
    }
  } | Sort-Object ExtraBytes -Descending | Select-Object -First 20 | Format-Table
```

### Recipe 8 — Make Curator forget a source (without deleting files)

```powershell
curator sources disable <source_id>          # stop scanning, keep data
# OR
# Remove all files from index first, then remove source
curator sources remove <source_id>            # fails if files reference it
```

---

## Configuration (`curator.toml`)

Resolution order:
1. `--config <path>` CLI flag (highest)
2. `$CURATOR_CONFIG` env var (this is what Claude Desktop's curator-mcp uses)
3. `./curator.toml` (current directory)
4. `<platformdirs.user_config_dir('curator')>/curator.toml`
5. Built-in defaults (always merged as base layer)

**Canonical install location** (per `Install-Curator.ps1`):
`C:\Users\jmlee\Desktop\AL\.curator\curator.toml`

### Schema (most-used keys)

```toml
[curator]
db_path  = "C:\\Users\\jmlee\\Desktop\\AL\\.curator\\curator.db"
log_path = "auto"   # "auto" -> platformdirs.user_log_dir
log_level = "INFO"  # DEBUG | INFO | WARNING | ERROR

[hash]
primary = "xxh3_128"
secondary = "md5"
fuzzy_for = [".py", ".md", ".txt", ".json", ".csv", ".log", ".html", ".css", ".js", ".ts"]
prefix_bytes = 4096
suffix_bytes = 4096

[trash]
provider = "os_recycle_bin"   # only supported in Phase Alpha
restore_metadata = true
purge_older_than_days = 30    # null = never auto-purge from registry

[lineage]
fuzzy_threshold = 70           # 0–100; below this, no edge stored
auto_confirm_threshold = 0.95
escalate_threshold = 0.70

[group]
default_keep_strategy = "oldest"   # oldest | newest | shortest_path | longest_path

[plugins]
disabled = []   # plugin names to skip loading
```

See [`src/curator/config/defaults.py`](../src/curator/config/defaults.py) for the full schema.

---

## Filesystem layout (where things live)

```
C:\Users\jmlee\Desktop\AL\
├── .curator\
│   ├── curator.db                   # canonical DB (CURATOR_CONFIG points here)
│   └── curator.toml                 # canonical config
├── Curator\
│   ├── .venv\                       # editable-install venv
│   ├── installer\                   # the one-click installer
│   ├── docs\                        # this guide + design docs
│   └── src\curator\                 # source tree
├── curatorplug-atrium-citation\
├── curatorplug-atrium-safety\
└── session_b_real\                  # preserved test artifact (249 KB)

%APPDATA%\Claude\
├── claude_desktop_config.json       # has curator MCP entry
└── logs\
    ├── mcp-server-curator.log       # Curator MCP launch + JSON-RPC trace
    └── main.log                     # Claude Desktop's MCP orchestration

%LOCALAPPDATA%\curator\curator\
├── corrupt_backup_20260509-161838\  # forensic artifact (do not touch)
└── Logs\curator.log                 # CLI runtime log
```

---

## Troubleshooting

### Curator MCP tools don't show up in chat

1. Quit Claude Desktop fully (system tray → Quit)
2. Re-run `Install-Curator.bat` (idempotent; will re-validate Step 9 MCP probe)
3. Restart Claude Desktop
4. If still broken: read `%APPDATA%\Claude\logs\mcp-server-curator.log` last 30 lines for the actual error

### `WinError 32: file used by another process` during pip install

Claude Desktop has `curator-mcp.exe` open. Quit Claude Desktop fully and re-run.

### "Database disk image is malformed"

The default DB at `%LOCALAPPDATA%\curator\curator\curator.db` got corrupted. The installer's canonical DB at `$RepoRoot\.curator\curator.db` is independent and won't be affected. If using the canonical DB and it still corrupts, open an issue with `curator.db.corrupt-backup-*` files attached.

### `curator gui` crashes with "No module named PySide6"

Re-run the installer. Recent installer versions install with `curator[gui,mcp]` extras to bring PySide6 in. If you installed before the fix:
```powershell
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\python.exe -m pip install PySide6
```

### Plugin not loading

```powershell
curator doctor                          # see what's registered
curator audit --action plugin.load -n 20  # see load events
```

If a plugin is listed as installed but not in `plugins:` block, look at log for the import error.

### "Cannot attach to server curator" in Claude Desktop UI

The MCP server tried to launch and crashed. Read `mcp-server-curator.log` last 20 lines. Most common cause: stale config with bad args (e.g., older `--db` instead of `CURATOR_CONFIG` env var). Re-running the installer fixes this.

---

## See also

- **`Install-Curator.ps1`** — the one-click installer with built-in real-MCP-probe verification
- **`docs/lessons/2026-05-09_install_mcp_session.md`** — postmortem of the install/MCP debug session that hardened the installer
- **`Atrium/design/LIFECYCLE_GOVERNANCE.md`** — design (UNIMPLEMENTED) for asset classification + universal update-rollback that future Curator versions will adopt
- **`docs/CURATOR_MCP_SERVER_DESIGN.md`** — implementation spec for the MCP server
- **`docs/TRACER_PHASE_*_DESIGN.md`** — Migration tool design notes (v1.1 → v1.4)

---

## What this guide does NOT yet cover (followups)

- **GUI walkthrough with screenshots for v1.6** — current screenshots in `docs/` are from older versions (v0.34–v0.43)
- **Plugin authoring** — separate guide for writing your own curatorplug-* package
- **HTTP MCP transport** — `curator-mcp --http` mode for remote LLM clients
- **Performance tuning** — large-corpus indexing, fuzzy hash threshold tuning
- **Cross-platform notes** — this guide assumes Windows; macOS/Linux have minor differences (path separators, OS trash provider)
