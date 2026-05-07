# Curator Phase Δ+ Roadmap: Migration, Sync, Asset Monitoring, Installation

**Status:** Forward-looking design — not yet implementation-ready
**Date:** 2026-05-07 (with 2026-05-08 ecosystem realignment notes)
**Scope:** Five major feature areas Jake mapped out post-v0.39
**Companion documents:**
- `DESIGN.md` — v1.0 spec (Phase α through γ implementation)
- `Github/CURATOR_RESEARCH_NOTES.md` — research findings + decisions D1-D26
- `BUILD_TRACKER.md` — current implementation state
- **`ECOSYSTEM_DESIGN.md` — supersedes parts of this doc as of 2026-05-08**

## ⚠ Realignment notice (2026-05-08)

After receiving APEX's architecture inventory (`docs/APEX_INFO_RESPONSE.md`), parts of this document are superseded:

1. **Feature A (Asset monitor)** is being spun out as a standalone **Umbrella** project, NOT a Curator feature. The asset-monitoring + auto-Claude-troubleshoot work belongs in its own product so it can monitor any tool (Curator, APEX subsystems, future tools) without being Curator-internal.
2. **Feature I (Installer)** is being spun out as a standalone **Nestegg** project, same reasoning.
3. **Feature M (Migration tool)** stays in Curator. The original §M.1 framing of "Curator vs APEX subAPEX2 (Indexer) overlap" was wrong — APEX's `subAPEX2` (Vampire) is a PDF-to-KB content extractor, not a file inventory tool. The actual file-inventory subsystem is **Synergy (subAPEX12)**, which IS the canonical state-of-disk authority per APEX's Master Scroll v0.4. **The integration question is Synergy/Curator, not Vampire/Curator.** See `ECOSYSTEM_DESIGN.md` §1 for the four resolution options (A/B/C/D) and recommendation.
4. **Feature S (Sync)** stays in Curator. Recommended path remains "wrap rclone" per §S.4.
5. **Feature U (Update protocol)** stays in Curator.

For anything ecosystem-related (SIP definition, APEX integration, multi-product responsibility matrix), `ECOSYSTEM_DESIGN.md` is now canonical. This doc's §A and §I content remains as-is for historical reference but should not drive new work.

## Document purpose

This document captures architecture, key decisions, scope estimates, and an
ordering proposal for five large feature areas Jake described after v0.39
shipped. Each feature is large enough to warrant its own design treatment.

This doc does NOT replace `DESIGN.md`. The original spec covers Phases α
through γ as built. This doc covers the Phase Δ+ expansion that builds on
that foundation.

The format follows the same convention as `CURATOR_RESEARCH_NOTES.md`:
each subsection ends with a numbered **Decisions Pending** list (`DM-N` for
migration, `DS-N` for sync, etc.) so future-Jake can flag what's been
decided and what's still open.

---

## Table of Contents

1. [Five-feature overview](#1-five-feature-overview)
2. [Feature M: Migration tool](#2-feature-m-migration-tool)
3. [Feature S: Cloud sync](#3-feature-s-cloud-sync)
4. [Feature A: Asset monitor + auto-troubleshoot](#4-feature-a-asset-monitor--auto-troubleshoot)
5. [Feature I: Standalone installer](#5-feature-i-standalone-installer)
6. [Feature U: Update protocol](#6-feature-u-update-protocol)
7. [Ordering & roadmap](#7-ordering--roadmap)
8. [Consolidated decision register](#8-consolidated-decision-register)

---

## 1. Five-feature overview

| Code | Feature | Honest estimate | Phase | Hard prerequisites |
|------|---------|-----------------|-------|--------------------|
| **M** | Migration tool (drive→drive transfer with index integrity) | 6-10h | γ | LocalFSSource ✅, audit log ✅, source plugin contract ✅ |
| **S** | Cloud sync (1-way mirror first; 2-way later) | 8-15h (rclone wrap) / 30-40h (native) | γ | Gate 5 gdrive plugin, ≥1 cloud source working end-to-end |
| **A** | Asset monitor + auto-Claude-desktop troubleshoot | 6-12h | γ/Δ | Independent; can ship anytime |
| **I** | Standalone single-file installer | 12-20h | Δ+ | Curator feature-complete; Phase β at minimum |
| **U** | Update protocol (DB migrations + smoke verify + rollback) | 4-6h | γ | Migration infrastructure ✅ |

**Headline insight:** Curator already has the right primitives for M and U.
S is the architectural beast. A is novel and worth doing first because it's
self-contained. I is last because it bundles everything else.

---

## 2. Feature M: Migration tool

### M.1 Use cases

- **Drive upgrade.** "I bought a new SSD; move my Music folder from the old
  HDD to the SSD, preserve every Curator ID and lineage edge."
- **Selective transfer.** "Migrate just the photos to the new drive; leave
  the rest."
- **Cross-source migration.** "Move my code projects from local to
  Google Drive (or vice versa)."
- **Cross-account migration.** "Move from `gdrive:jake@old.com` to
  `gdrive:jake@new.com`."

### M.2 Why this fits Curator naturally

Curator's design already separates content (the bytes on disk) from identity
(`curator_id`, the stable UUID). A migration is conceptually: copy bytes
from source A to destination B, then **rewrite `source_id` and `source_path`**
on the existing FileEntity row while keeping `curator_id` constant. Lineage
edges, bundle memberships, audit history — all persist because they reference
`curator_id`, not paths.

The source plugin contract (DESIGN.md §6) already abstracts read/move/delete
per-source. Migration is a thin orchestration layer on top.

### M.3 Architecture

New module: `src/curator/services/migration.py`

```
MigrationService
  ├─ plan(src_id, src_root, dst_id, dst_root, *, filter, opts) -> MigrationPlan
  │      enumerate src files → for each, decide action (copy/skip/conflict)
  │      compute total bytes, time estimate
  │      return immutable plan
  │
  └─ apply(plan, *, on_progress) -> MigrationReport
         per file:
           1. read bytes from src via source plugin
           2. write to dst via source plugin
           3. (optional) hash-verify
           4. update FileEntity: source_id=dst_id, source_path=new_path
           5. (optional) trash/delete source file
           6. audit log entry
         atomically per-file; partial failures leave a resumable state
```

### M.4 New schema

```sql
-- Migration jobs (resumable, like scan_jobs)
CREATE TABLE migration_jobs (
    job_id TEXT PRIMARY KEY,
    src_source_id TEXT NOT NULL,
    src_root TEXT NOT NULL,
    dst_source_id TEXT NOT NULL,
    dst_root TEXT NOT NULL,
    status TEXT NOT NULL,    -- queued/running/completed/failed/cancelled
    options_json TEXT NOT NULL DEFAULT '{}',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    files_total INTEGER NOT NULL DEFAULT 0,
    files_copied INTEGER NOT NULL DEFAULT 0,
    files_skipped INTEGER NOT NULL DEFAULT 0,
    files_failed INTEGER NOT NULL DEFAULT 0,
    bytes_copied INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

-- Per-file progress (so resume works)
CREATE TABLE migration_progress (
    job_id TEXT NOT NULL REFERENCES migration_jobs(job_id) ON DELETE CASCADE,
    curator_id TEXT NOT NULL,
    src_path TEXT NOT NULL,
    dst_path TEXT NOT NULL,
    status TEXT NOT NULL,    -- pending/copying/copied/verified/failed
    error TEXT,
    PRIMARY KEY (job_id, curator_id)
);
```

Resume semantics: if a migration is interrupted, the next `apply()` call with
the same job_id picks up where it left off (resumes only `pending` rows).

### M.5 CLI surface

```
curator migrate <src_id> <src_root> <dst_id> <dst_root> \
    [--filter "ext:.mp3,.flac"]              # subset selectors
    [--keep-source | --trash-source | --delete-source]   # default: trash
    [--verify-hash / --no-verify-hash]       # default: verify
    [--workers N]                            # default: 4
    [--apply]                                # required for actual mutations

curator migrate --resume <job_id>             # resume a partial migration
curator migrate --status <job_id>             # progress + errors
curator migrate --list                        # recent migration jobs
curator migrate --abort <job_id>              # cancel a running job
```

Two-phase pattern (DESIGN.md §1.4): without `--apply` it's a dry-run that
prints the plan and the estimated time/space. With `--apply` it runs.

### M.6 GUI surface (optional, deferred)

A dedicated "Migrate" tab in the GUI would have:
- Source picker (dropdown of configured sources + folder browser)
- Destination picker (same)
- Filter input
- Plan preview table
- Progress bar during apply
- Cancel button
- History of past jobs

Defer to a v0.42+ release. Phase β GUI doesn't need it.

### M.7 Edge cases worth thinking through

- **The Curator DB itself.** If you migrate FROM the drive Curator's DB
  lives on, you must preserve `curator.db` separately (don't migrate it
  through itself). Detect and refuse, or special-case.
- **Cross-source moves are slow.** local→gdrive at 100k files = hours.
  Resume is essential.
- **Conflict at destination.** A file already exists at the target path.
  Default: skip with a warning. Configurable: overwrite (with backup),
  rename-with-suffix, fail-fast.
- **Hash mismatch after copy.** Rare but real (silent data corruption,
  cloud rounding). Default: fail this file, mark `migration_progress.status =
  failed`, don't update the FileEntity. User can retry.
- **Lineage edges between old and new.** Should the migration auto-create a
  `SAME_LOGICAL_FILE` lineage edge from old curator_id to new one? **No —
  the curator_id stays the same.** No new edges needed.

### M.8 Decisions pending

- **DM-1.** Default action for source files after successful copy: trash
  (recoverable), delete (permanent), keep (manual cleanup), or
  prompt-each-time. *Recommendation: trash by default; delete is opt-in via
  flag.*
- **DM-2.** Hash-verify after copy by default? *Recommendation: yes; it's
  cheap relative to the I/O and catches real corruption.*
- **DM-3.** Concurrent file copies allowed? *Recommendation: yes,
  configurable workers, default 4 (matches scan default).*
- **DM-4.** Filter syntax: simple comma-separated extensions, glob patterns,
  or full Curator query language (FileQuery)? *Recommendation: start with
  ext list + path-prefix; add full query later.*
- **DM-5.** What happens to the source `SourceConfig` row after a complete
  migration? *Recommendation: leave it; user explicitly removes via
  `curator sources remove <id>` if they want.*

---

## 3. Feature S: Cloud sync

### S.1 Why this is the architectural beast

Real bidirectional sync is genuinely hard. The list of problems any 2-way
sync system must solve:

1. **State tracking.** What was the last known state of each file on each
   side? (Without this, you can't tell "modified" from "added".)
2. **Tombstones.** A delete on one side must propagate. But you have to
   distinguish "deleted on side A" from "never existed on side A".
3. **Conflict detection.** Both sides modified since the last sync. Whose
   change wins?
4. **Move detection.** A file renamed on one side should propagate as a
   rename, not as a delete + add (that loses lineage / metadata).
5. **Atomicity.** If a sync is interrupted halfway, the next run must not
   double-apply or skip changes.
6. **Rate limiting.** Cloud APIs throttle. Fail gracefully and resume.
7. **Encryption.** End-to-end: optional. Most personal users skip it.
8. **Selective sync.** Sync only certain folders, not everything.

There's a reason Syncthing, Unison, rclone, Resilio, etc. exist as
dedicated projects — not as "feature in some other tool".

### S.2 Recommended approach: build on rclone

[rclone](https://rclone.org/) is mature, supports 70+ cloud backends, has a
robust 1-way sync engine and an experimental bidirectional sync. It exposes
a daemon-mode HTTP API.

**Curator's role:** orchestration + metadata. Curator owns the sync profile
config, the per-file state tracking (`sync_states` table), the GUI, and the
audit log. **rclone's role:** the actual byte movement and the conflict
detection at the file-system level.

This is a 5-10x scope reduction vs. building it native.

The rclone wrap pattern:
```
SyncService
  ├─ profile_create(name, src, dst, mode, conflict_policy)
  ├─ profile_list / profile_delete
  ├─ run(profile_id) -> SyncReport
  │      builds rclone command from profile
  │      invokes rclone via subprocess (or HTTP API in daemon mode)
  │      parses stdout/stderr for per-file changes
  │      updates sync_states table per file
  │      writes audit log entries
  │      handles conflicts per profile.conflict_policy
  └─ schedule(profile_id, cron_expr)
         registers a job with APScheduler (already in [windows] extra)
```

### S.3 Sync modes

| Mode | Direction | Behavior on delete | Use case |
|------|-----------|--------------------|----------|
| **mirror** | A → B | Deletes propagate (A's truth) | "Backup my laptop to cloud" |
| **additive** | A → B | Never delete from B | "Archive everything to cold storage" |
| **bidirectional** | A ↔ B | Deletes propagate both ways via tombstones | "Sync my work between two machines" |
| **selective** | Any | User-controlled inclusion patterns | Subset of any of the above |

### S.4 New schema

```sql
CREATE TABLE sync_profiles (
    profile_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    src_source_id TEXT NOT NULL,
    src_root TEXT NOT NULL,
    dst_source_id TEXT NOT NULL,
    dst_root TEXT NOT NULL,
    mode TEXT NOT NULL,                      -- mirror|additive|bidirectional
    conflict_policy TEXT NOT NULL,           -- newest|src-wins|dst-wins|manual
    schedule TEXT,                           -- cron expression; null = manual only
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_run_at TIMESTAMP,
    last_run_status TEXT,
    options_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE sync_states (
    profile_id TEXT NOT NULL REFERENCES sync_profiles(profile_id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,                  -- path relative to root, identifies the logical file
    src_last_seen_hash TEXT,
    src_last_seen_mtime TIMESTAMP,
    dst_last_seen_hash TEXT,
    dst_last_seen_mtime TIMESTAMP,
    last_synced_at TIMESTAMP,
    last_action TEXT,                        -- copy_to_dst|copy_to_src|delete_src|delete_dst|conflict
    PRIMARY KEY (profile_id, rel_path)
);

CREATE TABLE sync_conflicts (
    conflict_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES sync_profiles(profile_id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    src_state_json TEXT NOT NULL,            -- snapshot of src side
    dst_state_json TEXT NOT NULL,            -- snapshot of dst side
    detected_at TIMESTAMP NOT NULL,
    resolved_at TIMESTAMP,
    resolution TEXT,                         -- src_wins|dst_wins|both_kept|skipped
    notes TEXT
);
```

### S.5 CLI surface

```
curator sync profile create <name> <src> <dst> --mode mirror
curator sync profile list
curator sync profile show <profile_id>
curator sync profile delete <profile_id> --apply

curator sync run <profile_id> [--apply] [--dry-run]
curator sync history <profile_id>
curator sync conflicts list [<profile_id>]
curator sync conflicts resolve <conflict_id> --resolution {src|dst|both} --apply

curator sync schedule <profile_id> "0 2 * * *"   # cron-style
curator sync schedule clear <profile_id>
```

### S.6 GUI surface

Dedicated "Sync" tab with:
- Profile list (one row per configured sync)
- Per-row: Run / Pause / Edit / Delete actions
- Last run status indicator
- Active conflicts count badge
- Conflict resolution dialog (side-by-side diff if files are text)

Estimate: another ~6h on top of the service work.

### S.7 Phasing recommendation

**v1 (cloud sync):**
- Mirror + additive modes only (one-way)
- Manual + scheduled triggers
- Wrap rclone
- No conflict resolution UI (one-way doesn't need it)

**v2 (cloud sync):**
- Bidirectional mode
- Conflict detection + resolution UI
- Real-time watch-and-sync (continuous)

**v3 (cloud sync):**
- More backends (OneDrive, Dropbox, S3, ...) — depends on whether their
  source plugins land in Phase γ
- Encryption-at-rest option
- Quota / bandwidth management

### S.8 Decisions pending

- **DS-1.** Wrap rclone, build native, or hybrid (start wrapped, replace
  bottlenecks)? *Recommendation: wrap rclone. Native is 4-5x more work for
  no clear user benefit.*
- **DS-2.** v1 scope: 1-way only? *Recommendation: yes. 2-way is a
  whole-document architecture problem and shouldn't gate v1.*
- **DS-3.** Schedule mechanism: APScheduler in-process vs. system Task
  Scheduler / cron? *Recommendation: APScheduler when curator-service is
  running; documented manual cron entry as fallback.*
- **DS-4.** Default conflict policy when 2-way ships: newest-wins, manual,
  both-kept-with-suffix? *Recommendation: manual for first-time users;
  configurable per-profile.*
- **DS-5.** Cross-source bundle integrity: when syncing a member of a
  bundle, do we re-create the bundle membership at the destination?
  *Recommendation: bundles live in Curator's index, not in the synced
  source. Bundle memberships persist across sync because they reference
  `curator_id`, not paths. **No special handling needed.***
- **DS-6.** Watch + sync: should the file watcher (Tier 6, already
  scaffolded) auto-trigger sync runs? *Recommendation: yes for
  bidirectional+continuous mode; opt-in via profile config.*

---

## 4. Feature A: Asset monitor + auto-troubleshoot

The novel feature. Two parts: tracking what's installed, and reacting when
an update breaks something.

### A.1 Part A: dependency / asset tracking

What gets tracked:
- **Python dependencies** — every package in `pyproject.toml` plus their
  transitive deps; current installed version vs. latest available on PyPI
- **Curator itself** — `curator/__init__.py:__version__` vs. latest GitHub
  release tag
- **Optional system tools** — Claude desktop, Python interpreter version,
  Node.js (if installed for any future JS-tooling), Git, pip itself
- **Plugin packages** — anything matching `curatorplug.*` namespace

Schema:

```sql
CREATE TABLE assets (
    asset_id TEXT PRIMARY KEY,               -- e.g. "pypi:pydantic", "system:claude-desktop"
    asset_type TEXT NOT NULL,                -- pypi|github|system|plugin
    display_name TEXT NOT NULL,
    installed_version TEXT,
    latest_version TEXT,
    latest_checked_at TIMESTAMP,
    update_available INTEGER GENERATED ALWAYS AS (
        CASE WHEN latest_version IS NOT NULL
              AND installed_version IS NOT NULL
              AND latest_version != installed_version
             THEN 1 ELSE 0 END
    ) VIRTUAL,
    pinned_version TEXT,                     -- if set, suppress update suggestions
    notes TEXT
);

CREATE TABLE asset_check_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    check_type TEXT NOT NULL,                -- scheduled|manual|post-install
    result TEXT NOT NULL,                    -- ok|update_found|error
    details_json TEXT NOT NULL DEFAULT '{}'
);
```

Update sources:
- PyPI: `https://pypi.org/pypi/<name>/json` returns full version metadata
- GitHub: `https://api.github.com/repos/<owner>/<repo>/releases/latest`
- System tools: platform-specific (registry lookup on Windows for installed
  programs; `which <cmd>` + `<cmd> --version` for CLI tools)

CLI:
```
curator assets list [--updates-only]
curator assets check                # query upstream now
curator assets pin <id> <version>
curator assets unpin <id>
curator assets history <id>         # check log for one asset
```

GUI: an "Assets" section could go in the existing Settings tab as a new
table, OR as its own tab. *Recommendation: own tab "Updates" — gives it
visibility, and the asset count in the status bar would prompt action.*

### A.2 Part B: incident logging + auto-Claude-desktop

This is the part where the design gets interesting.

**Trigger conditions:**
1. **Auto-detected.** Post-update health check: after a dep upgrade,
   Curator runs `pytest tests/ -m smoke` (or a designated subset). If it
   fails, an incident is generated.
2. **User-reported.** `curator incident new "describe what went wrong"` —
   captures current state + free-text description.
3. **Scheduled.** Optional periodic smoke-check; failures generate
   incidents.

**Incident payload structure:**

```yaml
# /home/jmlee/AppData/Local/curator/incidents/incident_2026-05-08T14-23-01.md

# Curator Incident Report
incident_id: 2026-05-08T14-23-01
trigger: post-update auto-check
created_at: 2026-05-08T14:23:01Z

## Curator state
curator_version: 0.39.0
curator_db_path: C:\Users\jmlee\AppData\Local\curator\curator.db
db_size_mb: 247

## System
os: Windows 11 Pro 23H2
python: 3.13.1
cpu: AMD Ryzen 7 5800X (8C/16T)
ram_total_gb: 32
ram_free_gb: 18
disk_free_gb: 412 (SSD)

## Dependency changes since last green run
| Package | Was | Is |
|---|---|---|
| pydantic | 2.5.3 | 2.6.0 |
| pluggy | 1.3.0 | 1.4.0 |

## Test failure summary
Failed: 3 tests
  - tests/unit/test_models.py::test_file_entity_validates  -- ValidationError
  - tests/unit/test_models.py::test_bundle_entity_creates  -- AttributeError
  - tests/integration/test_scan.py::test_scan_basic        -- TypeError

## Tracebacks (truncated)
[full tracebacks here, redacted of paths if configured]

## Recent audit entries (last 50)
[as YAML list]

## Curator config
[as YAML]
```

**Auto-launch flow:**

```
1. Generate incident_<ts>.md and write to <user_data>/curator/incidents/
2. Generate prompt template at <user_data>/curator/incidents/prompt_<ts>.txt:
   "Curator's smoke test started failing after a dependency upgrade.
   The incident report at the attached path describes the failure
   and recent dependency changes. Please diagnose and propose a fix
   that restores smoke-test passing without rolling back the upgrades
   if avoidable."
3. Best-effort launch Claude desktop:
   a. Try URI scheme: start "" "claude://prompt?attach=<path>&text=<urlenc-prompt>"
   b. If that fails, try CLI: claude.exe --open "<incident_path>"
   c. If that fails, just `start claude.exe`
4. Show OS notification: "Incident generated. Open Claude desktop, attach
   incident_<ts>.md, paste the prompt from prompt_<ts>.txt"
5. Optionally: copy prompt to clipboard via `clip < prompt_<ts>.txt`
```

**Open question Jake needs to answer / Claude needs to research:** does
Claude desktop currently support any URI scheme, command-line args, or
input automation? If yes, we can fully automate steps 3-4. If no, MVP is
"open the app + clipboard the prompt + open File Explorer at the incident
folder" and the user does the last 2 clicks.

**Even-better MVP using Windows UI Automation (UIA):**
- pywinauto can target Claude desktop's input box by accessibility role
- Paste the prompt
- Use file picker UIA to attach the incident
- Stop short of pressing Send (user-controlled hand-off)

UIA adds a `pywinauto>=0.6.8` dep and ~50 lines of code. Reliability
depends on Claude desktop's Microsoft accessibility tree being well-formed
(it usually is for Electron apps).

### A.3 CLI surface

```
curator incident new ["description"]    # manual incident from current state
curator incident list [--unresolved]
curator incident show <id>
curator incident open <id>              # auto-launch Claude desktop with this incident
curator incident resolve <id> --note "fixed by reverting pluggy"
curator monitor enable / disable        # toggle scheduled smoke-checks
curator monitor status
```

### A.4 GUI surface

A new "Updates" tab combining:
- Asset list with update-available badges
- Incidents list (recent ones, expandable)
- Per-incident: "Open in Claude" button (the auto-launch flow)
- Per-asset: pin / unpin / view history actions

### A.5 Privacy considerations

Incident logs may contain:
- File paths (which leak directory structure, possibly PII)
- Curator config (which may name specific external folders)
- Recent audit entries (which name specific files)

Configurable redaction levels in `curator.toml`:
```toml
[incident.privacy]
redact_paths = "anonymize"      # "anonymize" | "remove" | "keep"
redact_user_home = true         # replace C:\Users\jmlee\ with $HOME
redact_audit_details = false    # leave audit details as-is by default
```

### A.6 Decisions pending

- **DA-1.** Asset scope: pip deps only, or also system tools (Claude
  desktop, Git, etc.)? *Recommendation: pip + GitHub for v1; system tools
  in v2.*
- **DA-2.** Update check trigger: post-install, scheduled (e.g. weekly),
  manual, or all three? *Recommendation: all three; user toggles which.*
- **DA-3.** "Doesn't play well" detection strategy: full pytest, smoke
  subset, custom fast assertion suite? *Recommendation: smoke subset
  (`pytest -m smoke`), expects ~30s; full regression is too slow for a
  post-install check.*
- **DA-4.** Claude desktop integration mechanism: URI scheme (if available),
  command-line args (if accepted), pywinauto UIA, or manual paste?
  *Action item: research what Claude desktop currently supports; may
  inform the v1 mechanism.*
- **DA-5.** Privacy redaction defaults: redact paths by default, or keep
  them by default? *Recommendation: redact `$HOME` by default; keep
  relative paths.*
- **DA-6.** Where do incident reports live: per-user data dir, or a
  configurable path? *Recommendation: `<user_data>/curator/incidents/` by
  default; respects `[incident] path = "..."` override.*
- **DA-7.** Auto-resolve: when does an incident move from open to
  resolved? *Recommendation: when the next post-install smoke check
  passes after a fix; user can also manually mark resolved.*

---

## 5. Feature I: Standalone installer

### I.1 Goals

- Single-file deliverable (one .exe on a USB drive, or one downloadable
  asset from GitHub releases)
- Silent install (no clicks for default-path scenario)
- Online-aware (fetch latest if internet available; fall back to bundled
  version if not)
- System-aware (detect specs, recommend optimal config, warn about
  insufficient resources)
- Versioned (can install older + run upgrade protocol = Feature U)
- Cross-platform (Windows first; macOS/Linux later)

### I.2 Architecture: PyInstaller + Inno Setup wrapper

**Layer 1 — PyInstaller bundle.** Curator + Python interpreter + all deps
into one `curator-app.exe`. ~80-150 MB depending on extras.

**Layer 2 — Installer wrapper (Windows: Inno Setup).** A `.exe` installer
that:
1. Detects system specs via embedded PowerShell script or native API calls
2. Validates: OS version ≥ Win10, free disk ≥ 500 MB, RAM ≥ 4 GB
3. Asks (or skips, if `/SILENT`) for install path; default
   `%LOCALAPPDATA%\Curator`
4. Optionally checks GitHub releases for newer version than bundled
5. If newer is available + online: download newer + use that
6. If older or no internet: extract bundled `curator-app.exe`
7. Copy to install path; create Start Menu shortcut; register uninstaller
8. Run post-install hook: spec-aware config generation; first-time DB init;
   smoke test
9. If smoke test fails: pop a dialog with the error + offer to send to
   Claude (Feature A integration!)
10. On success: notify "Curator is ready. Launch?"

```
   ┌──────────────────────────────────────────┐
   │   curator-installer-0.40.0-win-x64.exe   │
   │   (signed)                               │
   ├──────────────────────────────────────────┤
   │  Inno Setup wrapper                      │
   │   ├─ system spec detector                │
   │   ├─ online-update probe                 │
   │   ├─ payload extractor                   │
   │   └─ post-install hook runner            │
   │                                          │
   │  Bundled payload                         │
   │   └─ curator-app-0.40.0.exe              │
   │       (PyInstaller --onefile)            │
   │                                          │
   │  Optional: portable mode flag            │
   │   └─ /PORTABLE writes to USB itself      │
   └──────────────────────────────────────────┘
```

### I.3 System spec detection

Detect with `psutil` + `platform` + Windows registry queries. Surface as a
report:

```
═══════════════════════════════════════════════════════════
 Curator Installer 0.40.0 — System Detection
═══════════════════════════════════════════════════════════

 OS:          Windows 11 Pro 23H2 (x64)
 CPU:         AMD Ryzen 7 5800X (8 cores, 16 threads, 3.8 GHz)
              SIMD: SSE4.2, AVX, AVX2, BMI2
 Memory:      32 GB total, 18 GB free
 Disk (C:):   1 TB SSD (NVMe), 412 GB free
 GPU:         NVIDIA RTX 3070 (8 GB VRAM)  [not used by Curator currently]
 Python:      Bundled 3.13.1
 Network:     Online (api.github.com reachable)

 Recommended configuration:
   • workers              =  8        (matches CPU cores)
   • hash.fuzzy_for       =  default  (RAM headroom is plenty)
   • organize.batch_size  =  500      (SSD; can handle larger batches)
   • watcher              =  enabled  (8 cores can handle continuous watch)
   • scan.continuous      =  weekly   (SSD wear is minimal at this rate)

 Trade-offs / alternatives:
   • If you want lower CPU usage during scans: workers=4 (50% slower
     but leaves cores free)
   • If you want lower disk wear: scan.continuous = monthly + watcher
     handles between-scan changes
   • If you have <4GB free RAM: disable [beta] datasketch LSH (slower
     fuzzy candidate selection, but cheaper memory)

 [ENTER to install with these recommendations | E to edit | Q to quit]
═══════════════════════════════════════════════════════════
```

The recommendations come from a hardcoded decision table mapping spec ranges
→ config values. v1 is rule-based; v2 could be data-driven from telemetry.

### I.4 Online vs. offline behavior

```
                  ┌─────────────────────────┐
                  │ Installer starts        │
                  └─────────────┬───────────┘
                                ▼
                  ┌─────────────────────────┐
                  │ /OFFLINE flag passed?   │
                  └──┬─────────────────┬────┘
                     │ no              │ yes
                     ▼                 │
       ┌────────────────────┐          │
       │ Probe github.com,  │          │
       │ pypi.org           │          │
       └─┬───────────────┬──┘          │
         │ online        │ no internet │
         ▼               ▼             ▼
  ┌──────────────┐ ┌──────────────────────┐
  │ Latest =     │ │ Use bundled version  │
  │ bundled?     │ │  (= the one shipped  │
  └─┬─────────┬──┘ │  with the installer) │
    │ yes     │ no └──────────┬───────────┘
    │         ▼               │
    │   ┌────────────────┐    │
    │   │ Download latest│    │
    │   │ from GitHub    │    │
    │   │ releases       │    │
    │   └────────┬───────┘    │
    │            ▼            │
    │   ┌────────────────┐    │
    │   │ Newer payload  │    │
    │   │ replaces       │    │
    │   │ bundled        │    │
    │   └────────┬───────┘    │
    │            │            │
    └────────────┴────────────┴───→  Continue install
```

Edge case: bundled version is newer than what's online (e.g. installing
from a fresh USB stick, but the user is on a corp net that lags). Handle:
**always prefer bundled if it's newer than online.** Never silently
downgrade.

### I.5 Versioned install + upgrade protocol (Feature U interlock)

If user passes `/VERSION=0.34.0` (or selects an older version from a
list): installer fetches that version's release asset. Then runs the
upgrade protocol (Feature U §6) which migrates DB schema, re-registers
plugins, etc.

This is useful for:
- Reproducing a bug at an older version
- Staying on a known-good version while a newer one has issues
- Compatibility with externally-shared databases at a specific version

### I.6 Cross-platform considerations

- **macOS:** PyInstaller `.app` bundle + `pkg` installer via `productbuild`.
  Need Apple Developer cert ($99/yr) for distribution outside the App
  Store. Consider Homebrew formula instead for personal use.
- **Linux:** PyInstaller works; package as `.deb` + `.rpm` + AppImage.
  Distribution via `apt` / `dnf` repos requires hosting infrastructure.
  AppImage is the lightweight option.

v1 = Windows only is fine. Cross-platform when there's user demand.

### I.7 Code signing

Without signing, Windows SmartScreen will flag the installer as
"unrecognized" — users have to click through a warning. For personal /
family use this is fine. For wider distribution, an EV certificate is the
gold standard but pricey ($300-500/yr). Standard cert + reputation
building is the affordable path ($75-150/yr).

For Phase Δ first ship: **self-signed is acceptable** with documented
warning-bypass instructions. Real cert when there's distribution demand.

### I.8 Decisions pending

- **DI-1.** Single-file (`--onefile`) vs. directory mode? *Recommendation:
  single-file for the public-facing installer; directory mode for dev
  builds (faster startup).*
- **DI-2.** Online-first or offline-first? *Recommendation: online-first
  with strong offline fallback. Probe with 2s timeout.*
- **DI-3.** Multi-platform from start? *Recommendation: Windows-only for
  v1; document the path to cross-platform but don't gate on it.*
- **DI-4.** Bundled-version policy: always bundle latest stable, or always
  require online? *Recommendation: always bundle the latest stable at
  build time; bundled becomes the offline floor.*
- **DI-5.** Telemetry: opt-in send of system specs to improve future
  recommendations? *Recommendation: skip for v1. Privacy-first stays
  consistent with Curator's positioning.*
- **DI-6.** Spec detection scope: psutil + platform.* + registry, or also
  GPU / VRAM? *Recommendation: include GPU detection (cheap, may matter
  for future ML features).*
- **DI-7.** Code signing: self-signed for first release, real cert later?
  *Recommendation: yes, self-signed first. Document the SmartScreen
  bypass.*
- **DI-8.** Uninstaller: remove user data (DB, config) by default, or
  preserve? *Recommendation: preserve by default; explicit "Also delete
  my data" checkbox.*
- **DI-9.** Per-user vs. per-machine install? *Recommendation: per-user
  (`%LOCALAPPDATA%`); avoids admin elevation; easier to support.*

---

## 6. Feature U: Update protocol

Mostly a primitive that supports Feature I, but worth its own section
because it's interesting on its own.

### U.1 What's already there

- `storage/migrations.py` runs schema migrations on DB init
- `Config.load()` deep-merges user TOML over defaults (resilient to new
  keys appearing)
- `__version__` is in `curator/__init__.py`

### U.2 What's missing

- Pre-startup version check: "is the running Curator code newer than what
  this DB was last touched by?"
- Backup before upgrade: `curator.db` → `curator.db.bak.<old-version>`
- Post-upgrade verification: smoke test (or quick health probe)
- Rollback on verification failure: restore `.bak`, refuse to start

### U.3 Architecture

New module: `src/curator/services/upgrade.py`

```python
class UpgradeService:
    def detect_upgrade_needed(self) -> UpgradeDecision:
        """Compare running code version to last-known version in DB.

        Returns:
            UpgradeDecision with:
              - needed: bool
              - from_version: str | None  (None on fresh install)
              - to_version: str
              - migrations_pending: list[str]
              - estimated_duration_s: float
        """

    def apply(self, decision: UpgradeDecision) -> UpgradeReport:
        """Run the upgrade. Steps:
          1. Backup curator.db -> curator.db.bak.<from_version>
          2. Run pending DB migrations
          3. Refresh plugin registrations
          4. Migrate any deprecated config keys
          5. Run smoke test
          6. If smoke fails: restore .bak, raise UpgradeFailedError
          7. Audit log entry: actor=curator.upgrade, action=version.bump
          8. Update DB metadata: schema_versions += this version's marker
        """

    def rollback(self, target_version: str) -> None:
        """Emergency rollback to a previous backup."""
```

### U.4 New DB metadata

```sql
-- Extend schema_versions to track Curator code version too:
INSERT INTO schema_versions(name) VALUES ('curator_code:0.40.0');
```

The `curator_code:<version>` markers let `detect_upgrade_needed()` compare
running version to the last applied marker.

### U.5 CLI surface

```
curator upgrade status            # is an upgrade pending?
curator upgrade apply             # run it (with safety checks)
curator upgrade rollback <ver>    # emergency rollback
curator upgrade history           # list past upgrades from audit log
```

On Curator startup: if `detect_upgrade_needed().needed`, print a banner
and prompt the user to run `curator upgrade apply`. Don't auto-apply at
startup — that's surprising behavior and can corrupt state if the user
isn't ready.

### U.6 Decisions pending

- **DU-1.** Auto-upgrade on launch, or prompt? *Recommendation: prompt by
  default; `--auto-upgrade` flag for advanced users.*
- **DU-2.** Backup strategy: full DB copy, WAL-only, hash-only manifest?
  *Recommendation: full DB copy. Curator DBs are small (MB to low GB);
  disk space isn't the constraint.*
- **DU-3.** Rollback granularity: per-version or per-migration?
  *Recommendation: per-version. Per-migration is harder and the
  use-case is rare.*
- **DU-4.** Smoke test scope: included in upgrade flow, or separate?
  *Recommendation: included by default; opt-out via `--skip-verify`.*
- **DU-5.** What if upgrade itself crashes mid-flight? *Recommendation:
  always restore from backup on any uncaught exception in `apply()`.*

---

## 7. Ordering & roadmap

### 7.1 Dependency graph

```
                ┌───────────────────────────────────┐
                │  Phase β gate 5 (gdrive plugin)   │
                │  [needed for cross-source any]    │
                └─────────────────┬─────────────────┘
                                  │
                ┌─────────────────┴─────────────────┐
                │                                   │
                ▼                                   ▼
   ┌──────────────────────┐          ┌──────────────────────┐
   │ M: Migration tool    │          │ S: Cloud sync (1-way)│
   │   uses src plugin    │          │   needs ≥1 cloud src │
   │   contract           │          │   working e2e        │
   └──────────────────────┘          └──────────────────────┘
                                                  │
                                                  ▼
                                     ┌──────────────────────┐
                                     │ S: Cloud sync (2-way)│
                                     │ (way later)          │
                                     └──────────────────────┘

   ┌──────────────────────┐
   │ A: Asset monitor     │  ← INDEPENDENT TRACK; no prereqs
   │   (+ auto-Claude)    │
   └──────────────────────┘

   ┌──────────────────────┐
   │ U: Update protocol   │  ← extends migrations infra; cheap
   │   (extends current)  │
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │ I: Standalone        │  ← needs everything else stable +
   │   installer          │     Curator feature-complete
   └──────────────────────┘
```

### 7.2 Recommended order (rationale)

1. **Finish gate 5 (gdrive)** — already scaffolded; finishing unlocks M
   and S. ~3-5 hours.
2. **Feature M (Migration tool)** — biggest near-term win-per-effort.
   Builds on existing primitives. Same-machine case ships first;
   cross-source case follows. ~6-10 hours.
3. **Feature A (Asset monitor)** — independent, novel, high-value.
   Could go first if you'd rather. ~6-12 hours.
4. **Feature U (Update protocol)** — small; ships any time after A.
   ~4-6 hours.
5. **Feature S (Cloud sync, 1-way)** — needs at least gate 5 + M's
   confidence in cross-source operations. ~8-15 hours via rclone.
6. **Feature I (Standalone installer)** — last; bundles all the above.
   ~12-20 hours.
7. **Feature S (Cloud sync, 2-way)** — well after I. Earliest meaningful
   ship is months out.

### 7.3 Why M before A

Both are roughly equal in scope. M gets the nod because:
- M directly affects user data integrity (drive upgrade is a real, common
  user action)
- A is "software hygiene" — important but less urgent
- M is a confidence-builder: "Curator can move my files safely" is a
  trust-establishing milestone before A asks for the ability to launch
  Claude desktop on the user's behalf

If A appeals to you more, do A first — they're independent.

### 7.4 Estimated total

- **Conservative path (1-way sync, Win-only installer):** 39-58h →
  6-8 sessions of this size.
- **Ambitious path (2-way sync, multi-platform installer):** 65-95h →
  10-13 sessions.

The first path gets a real, useful Curator that does everything described
except 2-way sync. The second adds 2-way sync and broader platform
support; the marginal value depends on whether you need bidirectional
between machines.

---

## 8. Consolidated decision register

All `DM-*`, `DS-*`, `DA-*`, `DI-*`, `DU-*` items in one place. Mark each
with status `[OPEN]` / `[DECIDED]` / `[DEFERRED]` as you go.

### Migration (M)
- **DM-1** [OPEN] — Default action for source files post-copy
- **DM-2** [OPEN] — Hash-verify default
- **DM-3** [OPEN] — Concurrent file copies
- **DM-4** [OPEN] — Filter syntax
- **DM-5** [OPEN] — Source SourceConfig disposition

### Sync (S)
- **DS-1** [OPEN] — Wrap rclone vs. native
- **DS-2** [OPEN] — v1 = 1-way only?
- **DS-3** [OPEN] — Schedule mechanism
- **DS-4** [OPEN] — Default conflict policy (when 2-way ships)
- **DS-5** [OPEN] — Cross-source bundle integrity (likely no special handling)
- **DS-6** [OPEN] — Watcher → sync auto-trigger

### Asset monitor (A)
- **DA-1** [OPEN] — Asset scope (pip vs. system tools)
- **DA-2** [OPEN] — Update check trigger(s)
- **DA-3** [OPEN] — "Doesn't play well" detection strategy
- **DA-4** [OPEN] — Claude desktop integration mechanism (RESEARCH NEEDED)
- **DA-5** [OPEN] — Privacy redaction defaults
- **DA-6** [OPEN] — Incident report storage location
- **DA-7** [OPEN] — Auto-resolve trigger

### Installer (I)
- **DI-1** [OPEN] — PyInstaller single-file vs. directory
- **DI-2** [OPEN] — Online-first vs. offline-first
- **DI-3** [OPEN] — Multi-platform from start?
- **DI-4** [OPEN] — Bundled version policy
- **DI-5** [OPEN] — Telemetry
- **DI-6** [OPEN] — Spec detection scope
- **DI-7** [OPEN] — Code signing
- **DI-8** [OPEN] — Uninstaller user-data behavior
- **DI-9** [OPEN] — Per-user vs. per-machine

### Upgrade (U)
- **DU-1** [OPEN] — Auto-upgrade vs. prompt
- **DU-2** [OPEN] — Backup strategy
- **DU-3** [OPEN] — Rollback granularity
- **DU-4** [OPEN] — Smoke test in upgrade flow
- **DU-5** [OPEN] — Mid-flight crash recovery

---

## Appendix: Things that Curator already has that helped this design

Listing these as a reminder of what's reusable:

| Primitive | Where | Reused for |
|---|---|---|
| Source plugin contract | DESIGN.md §6 | M, S |
| Stable `curator_id` independent of path | DESIGN.md §3 | M (no edge fixups needed) |
| Audit log (append-only) | DESIGN.md §17 | M, S, A, U |
| Migration infrastructure | `storage/migrations.py` | U |
| Hash pipeline | DESIGN.md §7 | S (conflict detection), M (verify) |
| Confidence-threshold gating | DESIGN.md §8.2 | (none directly; pattern reused for A's "doesn't play well" definition) |
| Two-phase command pattern (`--apply`) | DESIGN.md §1.4 | M, S, U |
| Plugin framework | DESIGN.md §5 | A (plugin-version tracking) |
| `psutil` already in `[organize]` extra | `pyproject.toml` | I (system spec detection) |
| `APScheduler` already in `[windows]` extra | `pyproject.toml` | S (scheduled), A (scheduled checks) |

The fact that this many primitives are reusable is a sign Phase α-γ
architecture has held up well under expansion pressure. None of these
features needs Curator's core to change shape — they extend it.

---

*End of `DESIGN_PHASE_DELTA.md`. Next iteration of this document should
flag decided/deferred items, add architecture details for the feature
that's tackled next, and append research notes (e.g. Claude desktop's
input mechanism research for DA-4).*
