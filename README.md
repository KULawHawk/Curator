# Curator

A content-aware artifact intelligence layer for files.

**Status:** v1.1.0 stable (released 2026-05-08). v1.0.0rc1 was the stability anchor; v1.1.0 ships the Migration tool ("Tracer") with persistent resumable jobs, worker-pool concurrency, cross-source migration, and a PySide6 Migrate tab. See [`CHANGELOG.md`](CHANGELOG.md) for the full release history.

Curator gives every file a stable identity, tracks relationships and lineage between files with confidence scores, knows where files belong, and makes every destructive operation reversible.

## Documentation

- [`CHANGELOG.md`](CHANGELOG.md) — release history (v1.0.0rc1, v1.1.0a1, v1.1.0)
- [`DESIGN.md`](DESIGN.md) — implementation specification (21 sections)
- [`docs/TRACER_PHASE_2_DESIGN.md`](docs/TRACER_PHASE_2_DESIGN.md) — Migration tool (Tracer) Phase 2 design + implementation evidence
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

## Optional features

Curator's core depends on a small set of always-installed packages. Larger or platform-
specific libraries live in extras and are imported lazily:

```powershell
# Phase Beta optional features (file watcher, fuzzy LSH, more file-type plugins)
pip install -e .[beta]

# Cloud source plugins (Google Drive, OneDrive, Dropbox — not yet implemented)
pip install -e .[cloud]

# Development tooling (pytest, hypothesis, ruff, mypy)
pip install -e .[dev]
```

With the `beta` extras installed, Curator gains:

* `curator watch` — reactive scanning (requires `watchfiles`).
* MinHash-LSH-based fuzzy candidate selection in lineage detection (requires `datasketch`). Speedups: 1.6x at 100 files, 21.5x at 1k, **196.7x at 10k**.

## License

MIT
