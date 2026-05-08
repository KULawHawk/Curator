# Curator

A content-aware artifact intelligence layer for files.

**Status:** v1.3.0 stable (released 2026-05-08). v1.0.0rc1 was the stability anchor; v1.1.0 shipped the Migration tool ("Tracer") with persistent resumable jobs, worker-pool concurrency, cross-source migration, and a PySide6 Migrate tab. v1.1.1 → v1.1.2 → v1.1.3 added the plugin ecosystem hookspecs that let third-party plugins enforce constitutional invariants (Atrium Principles 2 & 4) over Curator's plugin surface. v1.2.0 added an optional `[mcp]` extra exposing a Model Context Protocol server (`curator-mcp`) so LLM clients (Claude Desktop, Claude Code, third-party agents) can query Curator's index, audit log, and lineage programmatically. v1.3.0 closes Tracer's two highest-value Phase 2 deferrals: quota-aware retry with exponential backoff for cross-source transient errors (`--max-retries`) and four-mode destination-collision handling (`--on-conflict={skip,fail,overwrite-with-backup,rename-with-suffix}`). See [`CHANGELOG.md`](CHANGELOG.md) for the full release history.

Curator gives every file a stable identity, tracks relationships and lineage between files with confidence scores, knows where files belong, and makes every destructive operation reversible.

## Documentation

- [`CHANGELOG.md`](CHANGELOG.md) — release history (v1.0.0rc1, v1.1.0a1, v1.1.0, v1.1.1, v1.1.2, v1.1.3, v1.2.0, v1.3.0)
- [`DESIGN.md`](DESIGN.md) — implementation specification (21 sections)
- [`docs/TRACER_PHASE_2_DESIGN.md`](docs/TRACER_PHASE_2_DESIGN.md) — Migration tool (Tracer) Phase 2 design + implementation evidence
- [`docs/TRACER_PHASE_3_DESIGN.md`](docs/TRACER_PHASE_3_DESIGN.md) — v0.3 IMPLEMENTED. Tracer Phase 3 (v1.3.0+) — quota-aware retry decorator + four-mode `--on-conflict` resolution. Closes the two highest-value Phase 2 deferrals.
- [`docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md`](docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md) — v0.3 IMPLEMENTED. The `curator_plugin_init(pm)` hookspec (v1.1.2+) that gives plugins a pluggy reference for calling other plugins' hooks from inside their own hookimpls.
- [`docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md`](docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md) — v0.3 IMPLEMENTED. The `curator_audit_event(...)` hookspec (v1.1.3+) and core `AuditWriterPlugin` that let plugins write structured audit log entries.
- [`docs/CURATOR_MCP_SERVER_DESIGN.md`](docs/CURATOR_MCP_SERVER_DESIGN.md) — v0.3 IMPLEMENTED. The Model Context Protocol server (v1.2.0+) exposing 9 read-only tools to LLM clients via stdio.
- [`Github/CURATOR_RESEARCH_NOTES.md`](Github/CURATOR_RESEARCH_NOTES.md) — research findings, decision rationale, tracker items
- [`Github/PROCUREMENT_INDEX.md`](Github/PROCUREMENT_INDEX.md) — repository catalog and adoption verdicts
- [`BUILD_TRACKER.md`](BUILD_TRACKER.md) — implementation progress

## Project layout

```
Curator/
├── DESIGN.md                  # implementation spec
├── BUILD_TRACKER.md           # implementation progress
├── README.md                  # this file
├── pyproject.toml             # package metadata + deps
├── Github/                    # research artifacts
│   ├── CURATOR_RESEARCH_NOTES.md
│   ├── PROCUREMENT_INDEX.md
│   └── 01_ppdeep ... 47_pypdf/  # source repos
├── src/
│   └── curator/               # main package
│       ├── models/            # pydantic entity definitions
│       ├── storage/           # SQLite storage layer
│       │   └── repositories/  # repository pattern impls
│       ├── plugins/           # plugin framework
│       │   └── core/          # built-in plugins
│       ├── services/          # core services (hash, lineage, trash, etc.)
│       ├── config/            # configuration loading
│       └── cli/               # command-line interface
└── tests/
    ├── unit/
    ├── integration/
    ├── property/              # hypothesis-based
    └── corpus/                # synthetic test fixtures
```

## Install

```powershell
# From Curator/ project root
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -e .[dev]
```

## Quick start

```powershell
# Inspect a file Curator already knows about
curator inspect "C:\path\to\file.txt"

# Scan a folder, populate Curator's index
curator scan local "C:\Users\jmlee\Desktop\AL"

# Find duplicate files (dry run)
curator group local

# Actually trash duplicates (requires --apply)
curator group local --apply
```

## Reactive scanning (Phase Beta gate #3)

Curator can watch your local sources for filesystem changes and react incrementally,
so the index stays in sync as you edit files instead of needing a full re-scan.

```powershell
# Just print events as they happen
curator watch

# Or restrict to a single registered source
curator watch local:my_docs

# --apply: actually run an incremental scan_paths() on each event
curator watch --apply

# Pipe-friendly JSON output (one event per line)
curator watch --json | python my_pipeline.py
```

Under the hood:

* `WatchService` wraps [`watchfiles`](https://github.com/samuelcolvin/watchfiles) (Rust-backed, cross-platform).
* Per-(path, kind) debouncing (default 1s) coalesces editor-save chatter.
* Default ignore patterns cover `.git`, `__pycache__`, vim/emacs swap files, OS metadata noise.
* `--apply` pipes each event through `ScanService.scan_paths(source_id, [path])` — same hash + classification + lineage pipeline as a full scan, but for one file at a time.

A standalone runnable example lives at [`examples/watch_demo.py`](examples/watch_demo.py).

## Migration (Tracer) — v1.1.0

Tracer is Curator's brand for relocating files across paths with full
hash-verify-before-move discipline, `curator_id` constancy (lineage
edges and bundle memberships are preserved across moves), audit log
integration, and persistent resumable jobs. Same-source
local→local, cross-source local↔gdrive (and any future plugin pair via
the `curator_source_write` hook), worker-pool concurrency, and a
PySide6 "Migrate" tab in the GUI.

```powershell
# Plan a migration (no mutations)
curator migrate local "C:/Music" "D:/Music"

# Apply with parallel workers, persistent job, resumable
curator migrate local "C:/Music" "D:/Music" --apply --workers 4

# Filter by extension, glob include/exclude, path prefix
curator migrate local "C:/Music" "D:/Music" --apply --include "**/*.flac" --exclude "**/draft/**"

# Cross-source: local → Google Drive
curator migrate local "C:/Music" /Music --apply --dst-source-id gdrive:jake@example.com

# Job lifecycle
curator migrate --list
curator migrate --status <job_id>
curator migrate --resume <job_id> --apply
curator migrate --abort <job_id>

# Keep source intact (creates a verified copy at dst, leaves src untouched)
curator migrate local "C:/Music" "D:/Music" --apply --keep-source
```

The GUI's Migrate tab provides the same capabilities with right-click
Abort/Resume on running jobs and live cross-thread progress signals
from the worker pool to the GUI thread (no manual Refresh needed).
See [`docs/TRACER_PHASE_2_DESIGN.md`](docs/TRACER_PHASE_2_DESIGN.md)
for the full Phase 2 design + per-DM implementation evidence.

### Phase 3 — quota-aware retry + conflict resolution (v1.3.0+)

v1.3.0 closes the two highest-value Phase 2 deferrals via two new
flags. Both are strictly additive: defaults preserve v1.2.0 behavior
exactly. See [`docs/TRACER_PHASE_3_DESIGN.md`](docs/TRACER_PHASE_3_DESIGN.md)
v0.3 IMPLEMENTED for the full design + per-DM resolution.

**`--max-retries N`** wraps `_cross_source_transfer` in a retry
decorator that distinguishes retryable cloud errors (HTTP 403/429/5xx,
ConnectionError, Timeout, ProtocolError) from fail-fast conditions
(local OSError, hash mismatch, plugin rejection). Backoff is
exponential capped at 60 s, with the `Retry-After` header honored
when present. Default `3`; capped at `10`; `0` disables retry.
Resumed jobs inherit their original `max_retries` from the persisted
options unless explicitly overridden.

**`--on-conflict MODE`** turns the previously-monolithic
`SKIPPED_COLLISION` branch into four modes:

* `skip` (default) — preserve v1.2.0 behavior; outcome
  `SKIPPED_COLLISION`.
* `fail` — abort the migration on the first collision; outcome
  `FAILED_DUE_TO_CONFLICT`; CLI exits with code 1.
* `overwrite-with-backup` — atomically rename existing dst to
  `<name>.curator-backup-<UTC-iso8601><ext>` (same FS), then proceed;
  outcome `MOVED_OVERWROTE_WITH_BACKUP`.
* `rename-with-suffix` — migrate to `<name>.curator-N<ext>` (lowest
  free `N` in `[1, 9999]`); outcome `MOVED_RENAMED_WITH_SUFFIX`.

Every resolution emits a `migration.conflict_resolved` audit event
with mode-specific details (backup_path, suffix_n, original_dst,
etc.). Cross-source migrations support `skip` + `fail` fully;
`overwrite-with-backup` and `rename-with-suffix` degrade to skip with
a warning + audit (the source-plugin contract lacks an atomic-rename
hook; revisit in Phase 4 if the hookspec is expanded).

```powershell
# Cross-source migration with retry budget tuned for a slow gdrive day
curator migrate local "C:/Music" /Music --apply \
    --dst-source-id gdrive:jake@example.com --max-retries 5

# Local→local migration where existing dst files should be backed up, not skipped
curator migrate local "C:/Music" "D:/Music" --apply \
    --on-conflict overwrite-with-backup

# Strict mode — abort the whole job on the first dst collision
curator migrate local "C:/Music" "D:/Music" --apply --on-conflict fail

# Migrate without overwriting; new files land at <name>.curator-1.<ext>
curator migrate local "C:/Music" "D:/Music" --apply \
    --on-conflict rename-with-suffix
```

## Plugin ecosystem (v1.1.1+)

Curator's plugin surface lets third-party plugins observe and enforce policy over migration / source / audit operations. v1.1.x ships three hookspecs that compose into a defense-in-depth pattern (each is independent; plugins consume what they need):

* **`curator_source_write_post`** (v1.1.1+) — fires after each successful cross-source write with `(source_id, file_id, src_xxhash, written_bytes_len)`. Lets plugins observe or refuse writes; refusals are caught by `MigrationService` and converted to `MigrationOutcome.FAILED` with the error preserved.
* **`curator_plugin_init`** (v1.1.2+) — fires once at runtime startup with the plugin manager reference. Lets plugins save `pm` for later use, e.g. calling `pm.hook.curator_source_read_bytes(...)` from inside another hookimpl. See [`docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md`](docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md) v0.3 for the full design.
* **`curator_audit_event`** (v1.1.3+) — lets plugins emit structured audit log entries via `pm.hook.curator_audit_event(actor, action, entity_type, entity_id, details)`. The core `AuditWriterPlugin` persists events to `AuditRepository`. Other plugins MAY also implement the hookimpl to receive events (e.g. a future SIEM-streaming plugin). See [`docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md`](docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md) v0.3 for the full design.

### Reference consumer: `curatorplug-atrium-safety`

[`curatorplug-atrium-safety`](https://github.com/KULawHawk/curatorplug-atrium-safety) (v0.3.0) is a constitutional-compliance plugin enforcing Atrium Principle 2 (Hash-Verify-Before-Move) as a cross-cutting layer over Curator's plugin ecosystem. It demonstrates the full plugin-side consumption pattern:

* Auto-discovered via setuptools entry point at Curator startup.
* Saves `pm` via `curator_plugin_init` to enable independent re-read verification.
* On `curator_source_write_post`, performs two-phase enforcement: (1) `decide()` refuses writes where source-side verify was skipped in strict mode; (2) `_verify_via_re_read` independently re-reads dst bytes via `pm.hook.curator_source_read_bytes(...)` and compares to `src_xxhash` — catches non-deterministic source-plugin bugs that single-shot verify can't see.
* Emits structured `compliance.approved` / `compliance.refused` / `compliance.warned` audit events via `curator_audit_event` for every enforcement decision (Atrium Principle 4: No Silent Failures).
* Default lax mode is observe-only; strict mode is opt-in via `CURATORPLUG_ATRIUM_SAFETY_STRICT=1`.

Install alongside Curator:

```powershell
pip install -e ../curatorplug-atrium-safety
```

Query what the plugin observed:

```powershell
curator audit --actor curatorplug.atrium_safety
```

## MCP server (v1.2.0+)

Curator ships an optional Model Context Protocol server (`curator-mcp`) that exposes 9 read-only tools to LLM clients. The headline use case: an agent connected via MCP can ask "what did the safety plugin refuse last week?" or "find PDFs from August about taxes" and get back structured data straight from Curator's index — no CLI scraping, no full Python interpreter access required. See [`docs/CURATOR_MCP_SERVER_DESIGN.md`](docs/CURATOR_MCP_SERVER_DESIGN.md) v0.3 IMPLEMENTED for the full design.

Install:

```powershell
pip install -e .[mcp]
```

The 9 v1.2.0 tools (all strictly read-only):

* **`health_check`** — server / DB / plugin sanity check.
* **`list_sources`** — every configured Curator source (enabled + disabled).
* **`query_audit_log`** — filtered query with `actor`/`action`/`entity_id`/`since`/`limit`. The atrium-safety read-back use case.
* **`query_files`** — file index with `source_ids`/`extensions`/`path_starts_with`/`min_size`/`max_size`/`limit` filters.
* **`inspect_file`** — single-file deep view (metadata + lineage edges + bundle memberships).
* **`get_lineage`** — BFS through the lineage graph (max depth 5).
* **`find_duplicates`** — by `file_id` or `xxhash3_128` hash; returns the duplicate group.
* **`list_trashed`** — trash registry with `since`/`trashed_by`/`source_id`/`limit` filters.
* **`get_migration_status`** — by `job_id`, or recent jobs filtered by `status`.

### Claude Desktop config

Claude Desktop reads MCP server config from `%APPDATA%\Claude\claude_desktop_config.json` on Windows (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS). Add `curator` under `mcpServers`:

```json
{
  "mcpServers": {
    "curator": {
      "command": "C:\\Users\\jmlee\\AppData\\Roaming\\Python\\Python313\\Scripts\\curator-mcp.exe",
      "args": []
    }
  }
}
```

Adjust the `command` path to wherever pip installed `curator-mcp.exe` for your Python (`pip show -f curator | findstr curator-mcp` reveals it). After saving, restart Claude Desktop — it'll spawn `curator-mcp` automatically and the 9 tools will appear in the UI's tool picker.

Query examples once connected (Claude Desktop will route these to the right tool):

* *"What did the safety plugin refuse in the last week?"* → `query_audit_log(actor='curatorplug.atrium_safety', action='compliance.refused', since=...)`
* *"Find PDFs in my local source larger than 10MB."* → `query_files(source_ids=['local'], extensions=['.pdf'], min_size=10000000)`
* *"Are there any duplicates of this file?"* → `find_duplicates(file_id='...')`

### HTTP transport (development only)

For local-network use, the server can speak HTTP/SSE instead of stdio:

```powershell
curator-mcp --http --port 8765
```

**v1.2.0 has NO authentication for HTTP.** The server refuses to bind to non-loopback addresses without explicit override; v1.3.0 will add API key support. Use stdio (the default) for normal Claude Desktop / Claude Code integration.

### What's NOT in v1.2.0

* **No write tools.** Migration apply, scan trigger, trash send/restore, organize — all are write operations that need careful confirmation UX. Deferred to v1.3.0+. The `curator` CLI remains the canonical write surface.
* **No HTTP authentication.** stdio is the supported production transport.
* **No real-time event stream.** `WatchService` events are not exposed as a streaming MCP capability; polling-based query tools give point-in-time snapshots.

## Optional features

Curator's core depends on a small set of always-installed packages. Larger or platform-
specific libraries live in extras and are imported lazily:

```powershell
# Phase Beta optional features (file watcher, fuzzy LSH, more file-type plugins)
pip install -e .[beta]

# Cloud source plugins (Google Drive, OneDrive, Dropbox — not yet implemented)
pip install -e .[cloud]

# MCP server (Model Context Protocol; LLM-client-facing read-only API)
pip install -e .[mcp]

# Development tooling (pytest, hypothesis, ruff, mypy)
pip install -e .[dev]
```

With the `beta` extras installed, Curator gains:

* `curator watch` — reactive scanning (requires `watchfiles`).
* MinHash-LSH-based fuzzy candidate selection in lineage detection (requires `datasketch`). Speedups: 1.6x at 100 files, 21.5x at 1k, **196.7x at 10k**.

## License

MIT
