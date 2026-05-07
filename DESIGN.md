# Curator: Design Specification v1.0

**Status:** Draft v1.0 — Implementation-ready specification
**Date:** 2026-05-05
**Author:** Claude (with Jake Leese)
**Companion documents:**
- `Github/CURATOR_RESEARCH_NOTES.md` — research findings, decision rationale (D1-D26), tracker items 1-98
- `Github/PROCUREMENT_INDEX.md` — repository catalog and adoption verdicts

## Document purpose

This document specifies HOW Curator is built. It does NOT re-litigate WHY (those decisions live in CURATOR_RESEARCH_NOTES.md as D1-D26). When you see "(D17)" in this doc, look up the research notes for full reasoning.

This doc is implementation-ready. A developer should be able to start writing code from any section.

---

## Table of Contents

1. [Vision and Scope](#1-vision-and-scope)
2. [Architecture Overview](#2-architecture-overview)
3. [Entity Model](#3-entity-model)
4. [Storage Layer](#4-storage-layer)
5. [Plugin Framework](#5-plugin-framework)
6. [Source Plugin Contract](#6-source-plugin-contract)
7. [Hash Pipeline](#7-hash-pipeline)
8. [Lineage Detection](#8-lineage-detection)
9. [Bundle Awareness](#9-bundle-awareness)
10. [Trash and Restore Registry](#10-trash-and-restore-registry)
11. [CLI Surface](#11-cli-surface)
12. [Service Mode and REST API](#12-service-mode-and-rest-api)
13. [Rules Engine](#13-rules-engine)
14. [Tier 6: File Watching](#14-tier-6-file-watching)
15. [Tier 7: GUI Plan](#15-tier-7-gui-plan)
16. [Configuration](#16-configuration)
17. [Audit Log](#17-audit-log)
18. [Testing Strategy](#18-testing-strategy)
19. [Distribution](#19-distribution)
20. [APEX Integration](#20-apex-integration)
21. [Open Questions](#21-open-questions)

---

## 1. Vision and Scope

### 1.1 Mission statement

Curator is a content-aware artifact intelligence layer for files. It provides five core capabilities:

1. **Identity** — every file gets a stable, unique Curator ID independent of its path
2. **Relationship** — Curator knows which files are related (duplicates, versions, derivatives)
3. **Lineage** — Curator tracks how files evolve over time with confidence-scored edges
4. **Placement** — Curator knows where files belong and can move them safely
5. **Recovery** — every destructive operation is reversible via dual-trash + audit log

Curator's design accommodates two scales:
- **Personal:** academic projects, research files, code repositories on a single machine
- **System-wide:** continuous management of all drives (local + cloud), serving as the data layer for other tools (APEX, future apps)

### 1.2 Phased delivery

#### Phase Alpha (6-8 weeks)

**Goal:** working foundation. Curator manages local files, detects duplicates and near-duplicates, tracks lineage, supports manual bundles, dual-trash with restore.

Deliverables:
- Entity model and storage layer (sqlite3 + pydantic)
- Plugin framework (pluggy hookspecs defined; first plugins are core)
- Source plugin contract; `LocalFSSource` plugin
- Multi-stage hash pipeline (xxhash3 + ppdeep + md5)
- File-type detection (filetype.py)
- Confidence-scored lineage detection (filename, size, hash similarity)
- Bundle membership (manual)
- Dual-trash with restore registry
- Audit log
- CLI commands: `inspect`, `scan`, `group`, `lineage`, `bundles`, `trash`, `restore`, `doctor`, `explain`
- Configuration via `curator.toml`
- Loguru structured logging
- Migration infrastructure (with first migration)
- pytest + hypothesis test harness

**Out of scope for Alpha:** cloud sources, file watching, GUI, REST API, rules engine, full-text search.

#### Phase Beta (8-12 weeks after Alpha)

**Goal:** continuous monitoring + first cloud source + extensibility plugins.

Deliverables:
- Tier 6 file watching (watchfiles integration)
- First cloud source: `GoogleDriveSource` plugin via PyDrive2
- Detection plugins: `curatorplug.broken_pdf`, `curatorplug.broken_xlsx`, `curatorplug.broken_docx`
- Lineage analyzer plugins: `curatorplug.lineage_python_ast`, `curatorplug.lineage_vba_stub`
- Basic GUI (Tier 7 first cut — likely PyQt6)
- APSW + FTS5 for content search
- Rules engine v1 (simple match-and-move rules in `curator.toml`)
- python-magic as optional enhancement (richer file type descriptions)

#### Phase Gamma (3-6 months after Beta)

**Goal:** system-wide deployment.

Deliverables:
- OneDrive source via msgraph-sdk-python
- Dropbox source via dropbox-sdk-python
- Windows service mode (pywin32)
- System tray integration (pystray)
- Service-mode REST API (FastAPI + uvicorn)
- Periodic scan jobs (APScheduler AsyncIOScheduler)
- Advanced rules engine (multi-condition, plugin-extensible)
- APEX integration (REST API consumption from APEX)
- Cross-source bundle membership

#### Phase Delta+ (ongoing)

**Goal:** polish and broader integration.

Deliverables (ongoing):
- Standalone .exe distribution (PyInstaller)
- Cross-platform polish (macOS via Homebrew, Linux via apt/dnf)
- Indexer integration (Windows Search, Spotlight)
- Perceptual image hashing (imagehash)
- Tantivy search upgrade (richer than FTS5)
- VBA full AST analysis (tree-sitter-vba when grammar matures)
- Sophisticated auto-organization with conflict resolution
- Multi-user / web UI (only if scope warrants)

### 1.3 Non-goals (explicitly out of scope)

Curator will NEVER:
- Edit file contents (Curator is read-only with respect to content; only metadata and location)
- Replace OS file systems
- Provide file synchronization (cloud SDKs handle that)
- Provide version control (git/svn/etc. handle that)
- Provide full-text editing (notes about files, yes; editing files, no)

### 1.4 Quality bars

- **99.5% accuracy floor** on all classifications and lineage edges
- **Two-phase commands:** every command is either Scan/Read (no mutations) OR Act/Mutate (requires `--apply`)
- **All destructive operations reversible** within trash retention period
- **Confidence scores everywhere:** every classification, edge, detection has 0.0-1.0 confidence
- **No silent backtracking:** all changes audit-logged with timestamp, actor, reason
- **Graceful degradation:** missing optional dependencies don't break core functionality

---

## 2. Architecture Overview

### 2.1 Tier diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ Tier 7 — User Interface                                          │
│ ┌────────────┐  ┌────────────────┐  ┌──────────────────┐        │
│ │ CLI        │  │ GUI (Phase β)  │  │ REST API (Phase γ)│       │
│ │ Typer+Rich │  │ PyQt6 / web    │  │ FastAPI+uvicorn   │       │
│ └─────┬──────┘  └────────┬───────┘  └─────────┬─────────┘       │
└───────┼──────────────────┼────────────────────┼─────────────────┘
        │                  │                    │
┌───────┼──────────────────┼────────────────────┼─────────────────┐
│  Tier 6 — Monitoring                                              │
│ ┌─────▼──────────────────▼────────────────────▼──────┐           │
│ │ File Watcher (watchfiles)                          │           │
│ │ Periodic Jobs (APScheduler — Phase γ)              │           │
│ └────────────────────┬───────────────────────────────┘           │
└──────────────────────┼───────────────────────────────────────────┘
                       │
┌──────────────────────┼───────────────────────────────────────────┐
│  Tier 5 — HITL Escalation                                         │
│ ┌────────────────────▼──────────────┐                            │
│ │ Questionary prompts                │                            │
│ │ Conflict resolution                │                            │
│ │ Confidence-threshold gating        │                            │
│ └────────────────────┬───────────────┘                            │
└──────────────────────┼───────────────────────────────────────────┘
                       │
┌──────────────────────┼───────────────────────────────────────────┐
│  Tier 4 — Rules Engine (Phase β+)                                 │
│ ┌────────────────────▼──────────────┐                            │
│ │ Rules from curator.toml            │                            │
│ │ Auto-organization                  │                            │
│ │ Plugin-extensible rule types       │                            │
│ └────────────────────┬───────────────┘                            │
└──────────────────────┼───────────────────────────────────────────┘
                       │
┌──────────────────────┼───────────────────────────────────────────┐
│  Tier 3 — Plugin Layer (pluggy)                                   │
│ ┌─────────────┐  ┌──▼────────┐  ┌─────────────┐  ┌───────────┐  │
│ │ File-type   │  │ Source    │  │ Lineage     │  │ Detection │  │
│ │ detectors   │  │ plugins   │  │ analyzers   │  │ plugins   │  │
│ │ (filetype+) │  │ (local,   │  │ (py-ast,    │  │ (broken-  │  │
│ │             │  │  gdrive,  │  │  vba-stub,  │  │  pdf,     │  │
│ │             │  │  onedrive)│  │  imghash)   │  │  xlsx, …) │  │
│ └─────┬───────┘  └────┬──────┘  └──────┬──────┘  └─────┬─────┘  │
└───────┼───────────────┼────────────────┼────────────────┼───────┘
        │               │                │                │
┌───────┼───────────────┼────────────────┼────────────────┼───────┐
│  Tier 2 — Core Services                                           │
│ ┌─────▼───────────────▼────────────────▼────────────────▼─────┐  │
│ │ Hash Pipeline   │ Classification    │ Lineage Computation   │  │
│ │ Bundle Awareness│ Dual-Trash        │ Audit Log             │  │
│ │ Confidence Eng. │ Event Dispatcher  │ Credential Store      │  │
│ └────────────────────────────┬────────────────────────────────┘  │
└──────────────────────────────┼───────────────────────────────────┘
                               │
┌──────────────────────────────┼───────────────────────────────────┐
│  Tier 1 — Data Layer                                              │
│ ┌──────────────┐ ┌──────────▼────────┐ ┌────────────────────┐   │
│ │ Pydantic     │ │ SQLite (stdlib    │ │ Hash Cache         │   │
│ │ Entity       │ │ sqlite3 — Phase α)│ │ Migration Runner   │   │
│ │ Models       │ │ APSW — Phase β    │ │ Connection Pool    │   │
│ └──────────────┘ └───────────────────┘ └────────────────────┘   │
└───────────────────────────────────────────────────────────────────┘
```

### 2.2 Key data flows

#### 2.2.1 Scan flow

```
User: curator scan ~/Documents
  │
  ▼
CLI parses args → builds ScanRequest(source_id="local", root="~/Documents")
  │
  ▼
ScanService.scan(request)
  │
  ├─ Source plugin: LocalFSSource.enumerate(root)
  │      │
  │      ▼ yields FileInfo objects (path, size, mtime, ctime)
  │
  ├─ For each FileInfo:
  │   │
  │   ├─ Lookup or assign curator_id (UUID)
  │   ├─ Hash Pipeline (multi-stage):
  │   │     1. size grouping
  │   │     2. inode dedup (skip if hardlink to known)
  │   │     3. prefix hash (4KB head)
  │   │     4. suffix hash (4KB tail)
  │   │     5. full hash (xxhash3_128)
  │   │     6. fuzzy hash (ppdeep) if text-eligible
  │   │     7. md5 (cheap, store for compat)
  │   │
  │   ├─ Classification Service:
  │   │     ├─ filetype.py (always)
  │   │     ├─ python-magic (if available, Phase β+)
  │   │     └─ Plugin hookspec: curator_classify_file
  │   │
  │   ├─ Detection Service (Phase β+):
  │   │     ├─ Plugin hookspec: curator_validate_file
  │   │     │     (broken_pdf, broken_xlsx, etc.)
  │   │
  │   ├─ Lineage Service:
  │   │     ├─ Plugin hookspec: curator_compute_lineage
  │   │     │     For each potentially related file in DB,
  │   │     │     compute LineageEdge with confidence
  │   │
  │   ├─ Persist to SQLite (transaction)
  │   │     ├─ files row
  │   │     ├─ file_flex_attrs rows
  │   │     ├─ lineage_edges rows
  │   │     └─ audit_log entry
  │   │
  │   └─ Emit events:
  │         ├─ file_seen
  │         └─ file_classified
  │
  └─ Return ScanReport (counts, durations, errors)
```

#### 2.2.2 Act flow (e.g., trash duplicates)

```
User: curator group --apply ~/Documents
  │
  ▼
CLI checks for --apply flag (required for mutations)
  │
  ▼
GroupService.find_groups(source_id="local", root="~/Documents")
  │
  ├─ Query files with same xxhash3_128
  ├─ Build Group objects (one per dedup cluster)
  └─ For each Group:
        ├─ Apply prioritization (which file to keep)
        │     - Default: oldest seen, shortest path
        │     - Configurable via curator.toml
        │
        ├─ For each file to remove:
        │     ├─ Check confidence (must be 1.0 for hash dups)
        │     ├─ Check rules engine for restrictions
        │     ├─ If above threshold AND no rule blocks:
        │     │     ├─ TrashService.trash(curator_id, reason="duplicate")
        │     │     │     ├─ Snapshot bundle memberships
        │     │     │     ├─ Snapshot file_flex_attrs
        │     │     │     ├─ Send to OS trash via send2trash
        │     │     │     ├─ Insert trash_registry row
        │     │     │     ├─ Update files.deleted_at
        │     │     │     └─ Audit log entry
        │     │     │
        │     │     └─ Emit file_trashed event
        │     │
        │     └─ Else: escalate to user via questionary
        │
        └─ Print summary: kept N, trashed N, escalated N
```

### 2.3 Component dependencies

```
        ┌────────────────────────────────────────────┐
        │ Pydantic Models (no other Curator deps)    │
        └─────────────────┬──────────────────────────┘
                          │
        ┌─────────────────▼──────────────────────────┐
        │ Storage Layer (uses Models)                 │
        └────┬─────────────────┬──────────────────────┘
             │                 │
   ┌─────────▼─────────┐   ┌──▼─────────────────┐
   │ Hash Pipeline     │   │ Plugin Framework   │
   │ (uses Storage)    │   │ (uses Storage)     │
   └─────────┬─────────┘   └──┬─────────────────┘
             │                │
       ┌─────▼────────────────▼──────────────┐
       │ Core Services (Hash, Classify,      │
       │   Lineage, Bundle, Trash, Audit)    │
       └─────┬───────────────────────────────┘
             │
       ┌─────▼─────────────┐
       │ CLI / API / GUI   │
       └───────────────────┘
```


---

## 3. Entity Model

### 3.1 Three-tier attribute model (D3)

Every Curator entity has three kinds of attributes:

- **Fixed:** schema-defined columns. Type-checked, indexed, queried directly in SQL.
- **Flex:** free-form key-value attributes stored in a `*_flex_attrs` companion table. Plugins and users can add arbitrary fields without schema changes.
- **Computed:** read-only derived values from plugin-provided getter functions. Not stored; recomputed on demand.

### 3.2 Pydantic base model

```python
# curator/models/base.py

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict


class CuratorEntity(BaseModel):
    """Base class for all Curator entities."""
    
    model_config = ConfigDict(
        validate_assignment=True,
        arbitrary_types_allowed=False,
    )
    
    _flex: dict[str, Any] = {}
    
    @property
    def flex(self) -> dict[str, Any]:
        """Free-form key-value attributes. Persisted to *_flex_attrs table."""
        return self._flex
    
    def get_computed(self, key: str) -> Any:
        """Computed attributes provided by plugins. Raises KeyError if no plugin handles."""
        from curator.plugins import get_plugin_manager
        results = get_plugin_manager().hook.curator_compute_attr(entity=self, key=key)
        for r in results:
            if r is not None:
                return r
        raise KeyError(f"No plugin provides computed attribute {key!r}")
```

### 3.3 FileEntity

```python
# curator/models/file.py

class FileEntity(CuratorEntity):
    """A file Curator knows about."""
    
    # === Fixed (SQLite columns) ===
    curator_id: UUID = Field(default_factory=uuid4)
    source_id: str  # 'local', 'gdrive:jake@example.com'
    source_path: str  # path within the source
    
    # File metadata
    size: int
    mtime: datetime
    ctime: Optional[datetime] = None
    inode: Optional[int] = None  # local FS only
    
    # Hashes
    xxhash3_128: Optional[str] = None
    md5: Optional[str] = None
    fuzzy_hash: Optional[str] = None  # ppdeep, only for text-eligible files
    
    # Classification
    file_type: Optional[str] = None  # MIME from filetype.py
    extension: Optional[str] = None
    file_type_confidence: float = 0.0
    
    # Tracking
    seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_scanned_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = None  # soft-delete (in trash)
```

### 3.4 BundleEntity, BundleMembership

```python
class BundleEntity(CuratorEntity):
    bundle_id: UUID = Field(default_factory=uuid4)
    bundle_type: str  # 'manual' | 'auto' | 'plugin:<name>'
    name: Optional[str] = None
    description: Optional[str] = None
    confidence: float = 1.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BundleMembership(CuratorEntity):
    bundle_id: UUID
    curator_id: UUID
    role: str = "member"  # 'primary' | 'related' | 'reference'
    confidence: float = 1.0
    added_at: datetime = Field(default_factory=datetime.utcnow)
```

### 3.5 LineageEdge

```python
from enum import Enum

class LineageKind(str, Enum):
    DUPLICATE = "duplicate"           # byte-identical
    NEAR_DUPLICATE = "near_duplicate" # high fuzzy hash similarity
    DERIVED_FROM = "derived_from"     # one is processed/transformed from the other
    VERSION_OF = "version_of"         # explicit version chain
    REFERENCED_BY = "referenced_by"   # one mentions/links to the other
    SAME_LOGICAL_FILE = "same_logical_file"  # different paths, same conceptual file


class LineageEdge(CuratorEntity):
    edge_id: UUID = Field(default_factory=uuid4)
    from_curator_id: UUID
    to_curator_id: UUID
    edge_kind: LineageKind
    confidence: float = Field(..., ge=0.0, le=1.0)
    detected_by: str  # plugin name
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = None
```

### 3.6 TrashRecord, AuditEntry, SourceConfig, ScanJob

```python
class TrashRecord(CuratorEntity):
    curator_id: UUID
    original_source_id: str
    original_path: str
    file_hash: Optional[str] = None
    trashed_at: datetime = Field(default_factory=datetime.utcnow)
    trashed_by: str  # 'user' | 'auto' | 'plugin:<name>'
    reason: str
    bundle_memberships_snapshot: list[dict] = []
    file_attrs_snapshot: dict[str, Any] = {}
    os_trash_location: Optional[str] = None
    restore_path_override: Optional[str] = None


class AuditEntry(CuratorEntity):
    audit_id: int
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    actor: str  # 'user' | 'auto' | 'plugin:<name>' | 'service'
    action: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    details: dict[str, Any] = {}


class SourceConfig(CuratorEntity):
    source_id: str
    source_type: str  # 'local' | 'gdrive' | 'onedrive' | 'dropbox'
    display_name: Optional[str] = None
    config: dict[str, Any] = {}
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ScanJob(CuratorEntity):
    job_id: UUID = Field(default_factory=uuid4)
    status: str  # 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
    source_id: str
    root_path: str
    options: dict[str, Any] = {}
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    files_seen: int = 0
    files_hashed: int = 0
    error: Optional[str] = None
```

---

## 4. Storage Layer

### 4.1 Technology stack (Phase Alpha)

- **stdlib `sqlite3`** for the database — no ORM, handwritten queries
- **Single SQLite file** at `<user_data_dir>/Curator.db`
- **WAL mode** for concurrency
- **Foreign keys enforced** (PRAGMA)
- **One connection per thread** (sqlite3 limitation)
- **Schema versioning** via `schema_versions` table

Phase Beta consideration: switch to APSW for FTS5 + virtual tables (D16). Connection abstraction stays the same.

### 4.2 Connection management

```python
# curator/storage/connection.py

class CuratorDB:
    """Thread-safe SQLite connection manager."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False
    
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn'):
            self._local.conn = self._make_connection()
        return self._local.conn
    
    def _make_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn
    
    def init(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            from curator.storage.migrations import apply_migrations
            apply_migrations(self.conn())
            self._initialized = True
```

### 4.3 Initial schema (migration 001)

```sql
-- Migration tracking
CREATE TABLE IF NOT EXISTS schema_versions (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Sources
CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    display_name TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Files
CREATE TABLE files (
    curator_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE RESTRICT,
    source_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime TIMESTAMP NOT NULL,
    ctime TIMESTAMP,
    inode INTEGER,
    xxhash3_128 TEXT,
    md5 TEXT,
    fuzzy_hash TEXT,
    file_type TEXT,
    extension TEXT,
    file_type_confidence REAL NOT NULL DEFAULT 0.0,
    seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_scanned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    UNIQUE (source_id, source_path)
);

CREATE INDEX idx_files_xxhash ON files(xxhash3_128) WHERE xxhash3_128 IS NOT NULL;
CREATE INDEX idx_files_md5 ON files(md5) WHERE md5 IS NOT NULL;
CREATE INDEX idx_files_fuzzy ON files(fuzzy_hash) WHERE fuzzy_hash IS NOT NULL;
CREATE INDEX idx_files_size ON files(size);
CREATE INDEX idx_files_inode ON files(source_id, inode) WHERE inode IS NOT NULL;
CREATE INDEX idx_files_extension ON files(extension);
CREATE INDEX idx_files_deleted ON files(deleted_at) WHERE deleted_at IS NOT NULL;

-- Flex attrs for files
CREATE TABLE file_flex_attrs (
    curator_id TEXT NOT NULL REFERENCES files(curator_id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (curator_id, key)
);

-- Bundles
CREATE TABLE bundles (
    bundle_id TEXT PRIMARY KEY,
    bundle_type TEXT NOT NULL,
    name TEXT,
    description TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bundle_memberships (
    bundle_id TEXT NOT NULL REFERENCES bundles(bundle_id) ON DELETE CASCADE,
    curator_id TEXT NOT NULL REFERENCES files(curator_id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    confidence REAL NOT NULL DEFAULT 1.0,
    added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bundle_id, curator_id)
);

CREATE INDEX idx_bundle_memberships_file ON bundle_memberships(curator_id);

CREATE TABLE bundle_flex_attrs (
    bundle_id TEXT NOT NULL REFERENCES bundles(bundle_id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (bundle_id, key)
);

-- Lineage edges
CREATE TABLE lineage_edges (
    edge_id TEXT PRIMARY KEY,
    from_curator_id TEXT NOT NULL REFERENCES files(curator_id) ON DELETE CASCADE,
    to_curator_id TEXT NOT NULL REFERENCES files(curator_id) ON DELETE CASCADE,
    edge_kind TEXT NOT NULL,
    confidence REAL NOT NULL,
    detected_by TEXT NOT NULL,
    detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    UNIQUE (from_curator_id, to_curator_id, edge_kind, detected_by)
);

CREATE INDEX idx_lineage_from ON lineage_edges(from_curator_id);
CREATE INDEX idx_lineage_to ON lineage_edges(to_curator_id);
CREATE INDEX idx_lineage_kind ON lineage_edges(edge_kind);

-- Trash registry
CREATE TABLE trash_registry (
    curator_id TEXT PRIMARY KEY REFERENCES files(curator_id) ON DELETE CASCADE,
    original_source_id TEXT NOT NULL,
    original_path TEXT NOT NULL,
    file_hash TEXT,
    trashed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trashed_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    bundle_memberships_snapshot_json TEXT NOT NULL DEFAULT '[]',
    file_attrs_snapshot_json TEXT NOT NULL DEFAULT '{}',
    os_trash_location TEXT,
    restore_path_override TEXT
);

-- Audit log
CREATE TABLE audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_audit_time ON audit_log(occurred_at);
CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_action ON audit_log(action);

-- Hash cache
CREATE TABLE hash_cache (
    source_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    mtime TIMESTAMP NOT NULL,
    size INTEGER NOT NULL,
    xxhash3_128 TEXT,
    md5 TEXT,
    fuzzy_hash TEXT,
    computed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_id, source_path)
);

-- Scan jobs
CREATE TABLE scan_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    source_id TEXT,
    root_path TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    files_seen INTEGER NOT NULL DEFAULT 0,
    files_hashed INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE INDEX idx_scan_jobs_status ON scan_jobs(status);
```

### 4.4 Migration system

```python
# curator/storage/migrations.py

MigrationFunc = Callable[[sqlite3.Connection], None]

def migration_001_initial(conn: sqlite3.Connection) -> None:
    from curator.storage.schema_v1 import INITIAL_SCHEMA_SQL
    conn.executescript(INITIAL_SCHEMA_SQL)

MIGRATIONS: list[tuple[str, MigrationFunc]] = [
    ("001_initial", migration_001_initial),
]

def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply all pending migrations. Idempotent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    
    cursor = conn.execute("SELECT name FROM schema_versions")
    applied = {row[0] for row in cursor.fetchall()}
    
    for name, func in MIGRATIONS:
        if name in applied:
            continue
        try:
            with conn:
                func(conn)
                conn.execute("INSERT INTO schema_versions(name) VALUES (?)", (name,))
        except Exception as e:
            raise RuntimeError(f"Migration {name} failed: {e}") from e
```

### 4.5 Repository pattern

Each entity gets a Repository class for SQL ↔ pydantic conversion. Pattern (using `FileRepository` as exemplar):

```python
class FileRepository:
    def __init__(self, db: CuratorDB):
        self.db = db
    
    def insert(self, file: FileEntity) -> None:
        with self.db.conn() as conn:
            conn.execute(
                """INSERT INTO files (curator_id, source_id, source_path, size, mtime,
                   ctime, inode, xxhash3_128, md5, fuzzy_hash, file_type, extension,
                   file_type_confidence, seen_at, last_scanned_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(file.curator_id), file.source_id, file.source_path, file.size,
                 file.mtime, file.ctime, file.inode, file.xxhash3_128, file.md5,
                 file.fuzzy_hash, file.file_type, file.extension,
                 file.file_type_confidence, file.seen_at, file.last_scanned_at)
            )
            self._save_flex(conn, file)
    
    def get(self, curator_id: UUID) -> Optional[FileEntity]: ...
    def find_by_hash(self, xxhash: str) -> list[FileEntity]: ...
    def find_by_path(self, source_id: str, path: str) -> Optional[FileEntity]: ...
    def update(self, file: FileEntity) -> None: ...
    def mark_deleted(self, curator_id: UUID) -> None: ...
```

Same pattern for `BundleRepository`, `LineageRepository`, `TrashRepository`, `AuditRepository`, `SourceRepository`, `ScanJobRepository`, `HashCacheRepository`.

### 4.6 Query helpers

```python
# curator/storage/queries.py

@dataclass
class FileQuery:
    """Composable file query."""
    source_ids: Optional[list[str]] = None
    extensions: Optional[list[str]] = None
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    has_xxhash: bool = False
    has_fuzzy_hash: bool = False
    deleted: Optional[bool] = None
    flex_attrs: Optional[dict[str, Any]] = None
    
    def build_sql(self) -> tuple[str, list]:
        clauses, params = [], []
        if self.source_ids:
            placeholders = ",".join("?" * len(self.source_ids))
            clauses.append(f"source_id IN ({placeholders})")
            params.extend(self.source_ids)
        # ... etc
        where = " AND ".join(clauses) if clauses else "1"
        return f"SELECT * FROM files WHERE {where}", params
```


---

## 5. Plugin Framework

### 5.1 Pluggy hookspec organization (D11)

All hookspecs live in `curator.plugins.hookspecs`. External plugins implement them in `curatorplug.<plugin_name>` packages.

```python
# curator/plugins/hookspecs.py

from typing import Any, AsyncIterator, Iterator, Optional
from uuid import UUID

import pluggy

hookspec = pluggy.HookspecMarker("curator")
hookimpl = pluggy.HookimplMarker("curator")


# === File classification ===
@hookspec
def curator_classify_file(file: 'FileEntity') -> Optional['FileClassification']:
    """Classify a file's type. Plugins return None if they have no opinion."""


# === File validation / detection ===
@hookspec
def curator_validate_file(file: 'FileEntity') -> Optional['ValidationResult']:
    """Validate a file's integrity. Plugins return None if they don't apply."""


# === Lineage detection ===
@hookspec
def curator_compute_lineage(file_a: 'FileEntity', file_b: 'FileEntity') -> Optional['LineageEdge']:
    """Compute potential lineage between two files. Return None if no relationship."""


# === Bundle proposal ===
@hookspec
def curator_propose_bundle(files: list['FileEntity']) -> Optional['BundleProposal']:
    """Propose a bundle from a set of files. Return None if no pattern matches."""


# === Source plugin contract ===
@hookspec
def curator_source_register() -> 'SourcePluginInfo':
    """Register a source plugin. Returns metadata about the source type."""

@hookspec
def curator_source_enumerate(source_id: str, root: str, options: dict[str, Any]) -> Iterator['FileInfo']:
    """Enumerate files in a source root."""

@hookspec
def curator_source_read_bytes(source_id: str, file_id: str, offset: int, length: int) -> bytes:
    """Read bytes from a file in a source."""

@hookspec
def curator_source_stat(source_id: str, file_id: str) -> 'FileStat':
    """Get file metadata from a source."""

@hookspec
def curator_source_move(source_id: str, file_id: str, new_path: str) -> 'FileInfo':
    """Move a file within a source. Returns updated FileInfo."""

@hookspec
def curator_source_delete(source_id: str, file_id: str, to_trash: bool) -> bool:
    """Delete a file (or send to trash). Returns success."""

@hookspec
async def curator_source_watch(source_id: str, root: str) -> AsyncIterator['ChangeEvent']:
    """Watch a source root for changes."""


# === Computed attributes ===
@hookspec
def curator_compute_attr(entity: 'CuratorEntity', key: str) -> Optional[Any]:
    """Compute a derived attribute. Plugins return None if they don't provide this key."""


# === Trash hooks ===
@hookspec
def curator_pre_trash(file: 'FileEntity', reason: str) -> Optional['ConfirmationResult']:
    """Pre-trash hook. Plugins can veto by returning ConfirmationResult(allow=False)."""

@hookspec
def curator_post_trash(trash_record: 'TrashRecord') -> None:
    """Post-trash hook. For cleanup, notification, etc."""

@hookspec
def curator_pre_restore(trash_record: 'TrashRecord', target_path: str) -> Optional['ConfirmationResult']:
    """Pre-restore hook."""

@hookspec
def curator_post_restore(file: 'FileEntity') -> None:
    """Post-restore hook."""


# === Rules engine extensions ===
@hookspec
def curator_rule_types() -> dict[str, type]:
    """Register custom rule types. Returns {rule_type_name: RuleClass}."""


# === CLI commands ===
@hookspec
def curator_cli_commands() -> list['typer.Typer']:
    """Register additional CLI subcommands."""


# === Service API endpoints ===
@hookspec
def curator_api_routers() -> list['fastapi.APIRouter']:
    """Register additional REST API routers."""
```

### 5.2 Plugin manager singleton

```python
# curator/plugins/manager.py

import pluggy
from curator.plugins import hookspecs

_pm: Optional[pluggy.PluginManager] = None

def get_plugin_manager() -> pluggy.PluginManager:
    """Get the singleton plugin manager."""
    global _pm
    if _pm is None:
        _pm = _create_plugin_manager()
    return _pm

def _create_plugin_manager() -> pluggy.PluginManager:
    pm = pluggy.PluginManager("curator")
    pm.add_hookspecs(hookspecs)
    
    # Load core (built-in) plugins
    from curator.plugins.core import register_core_plugins
    register_core_plugins(pm)
    
    # Discover external plugins via setuptools entry points
    pm.load_setuptools_entrypoints("curator")
    return pm
```

### 5.3 Plugin namespace

External plugins are Python packages named `curatorplug.<name>` declaring entry points:

```toml
# Example: curatorplug-broken-pdf/pyproject.toml

[project]
name = "curatorplug-broken-pdf"
version = "0.1.0"
dependencies = ["curator", "pypdf>=4.0"]

[project.entry-points.curator]
broken_pdf = "curatorplug.broken_pdf:Plugin"
```

```python
# curatorplug/broken_pdf/__init__.py

import pypdf
from curator.plugins.hookspecs import hookimpl
from curator.models import FileEntity, ValidationResult


class Plugin:
    @hookimpl
    def curator_validate_file(self, file: FileEntity) -> Optional[ValidationResult]:
        if file.extension != ".pdf":
            return None
        try:
            with open(file.source_path, "rb") as f:
                reader = pypdf.PdfReader(f, strict=True)
                _ = reader.metadata
                _ = len(reader.pages)
            return ValidationResult(ok=True, detector="curatorplug.broken_pdf", confidence=1.0)
        except Exception as e:
            return ValidationResult(
                ok=False, detector="curatorplug.broken_pdf", confidence=1.0, error=str(e)
            )
```

### 5.4 Core plugins (built-in, Phase Alpha)

Registered automatically by `register_core_plugins()`:

- `curator.plugins.core.local_source` — local filesystem source
- `curator.plugins.core.classify_filetype` — filetype.py-based classification
- `curator.plugins.core.lineage_hash_dup` — exact-duplicate detection (xxhash match)
- `curator.plugins.core.lineage_fuzzy_dup` — near-duplicate detection (fuzzy hash similarity)
- `curator.plugins.core.lineage_filename` — filename-similarity detection (version chains)

### 5.5 Plugin loading order and conflicts

Pluggy calls all implementations of a hookspec; results returned as a list. Conventions:

- For "compute" hooks (lineage, classification): collect all non-None results. Multiple plugins MAY contribute.
- For "source" hooks: each plugin checks if `source_id` matches its source_type and returns None if not. Only ONE plugin should match any source_id.
- For "veto" hooks (pre_trash, pre_restore): if ANY plugin returns `ConfirmationResult(allow=False)`, the operation is blocked.
- Plugin order is undefined unless explicitly set via `tryfirst=True` / `trylast=True`.

---

## 6. Source Plugin Contract

### 6.1 Concept (D17)

Every source — local filesystem, Google Drive, OneDrive, Dropbox, etc. — is a plugin implementing the source hookspecs from §5.1. Source plugins normalize their backend's quirks behind a common interface.

### 6.2 Data shapes

```python
# curator/sources/types.py

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class FileInfo(BaseModel):
    """Source-agnostic file info from enumerate()."""
    file_id: str  # source-specific identifier (path on local; ID on cloud)
    path: str  # human-readable path within source
    size: int
    mtime: datetime
    ctime: Optional[datetime] = None
    is_directory: bool = False
    extras: dict = {}  # source-specific extras


class FileStat(BaseModel):
    """Source-agnostic file stat result."""
    file_id: str
    size: int
    mtime: datetime
    ctime: Optional[datetime] = None
    inode: Optional[int] = None  # local FS only
    permissions: Optional[str] = None
    extras: dict = {}


class ChangeKind(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"  # synthesized from add+delete with same content


class ChangeEvent(BaseModel):
    """Source-agnostic change notification."""
    kind: ChangeKind
    file_id: str
    path: str
    new_path: Optional[str] = None  # for MOVED
    timestamp: datetime


class SourcePluginInfo(BaseModel):
    """Metadata returned from curator_source_register."""
    source_type: str  # 'local' | 'gdrive' | 'onedrive' | 'dropbox'
    display_name: str
    requires_auth: bool
    supports_watch: bool
    config_schema: dict  # JSON Schema for source config
```

### 6.3 LocalFSSource (Phase Alpha — first source plugin)

```python
# curator/plugins/core/local_source.py

import os
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from curator.plugins.hookspecs import hookimpl
from curator.sources.types import (
    FileInfo, FileStat, ChangeEvent, ChangeKind, SourcePluginInfo
)


class Plugin:
    SOURCE_TYPE = "local"
    
    @hookimpl
    def curator_source_register(self) -> SourcePluginInfo:
        return SourcePluginInfo(
            source_type=self.SOURCE_TYPE,
            display_name="Local Filesystem",
            requires_auth=False,
            supports_watch=True,
            config_schema={
                "type": "object",
                "properties": {
                    "roots": {"type": "array", "items": {"type": "string"}},
                    "ignore": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["roots"]
            }
        )
    
    @hookimpl
    def curator_source_enumerate(self, source_id, root, options) -> Iterator[FileInfo]:
        if not source_id.startswith(self.SOURCE_TYPE):
            return
        ignore_patterns = options.get("ignore", [])
        root_path = Path(root)
        for path in root_path.rglob("*"):
            if not path.is_file():
                continue
            if any(self._matches_pattern(path, p) for p in ignore_patterns):
                continue
            stat = path.stat()
            yield FileInfo(
                file_id=str(path),
                path=str(path),
                size=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime),
                ctime=datetime.fromtimestamp(stat.st_ctime),
                is_directory=False,
                extras={"inode": stat.st_ino},
            )
    
    @hookimpl
    def curator_source_read_bytes(self, source_id, file_id, offset, length) -> bytes:
        if not source_id.startswith(self.SOURCE_TYPE):
            return None
        with open(file_id, "rb") as f:
            f.seek(offset)
            return f.read(length)
    
    @hookimpl
    def curator_source_stat(self, source_id, file_id) -> Optional[FileStat]:
        if not source_id.startswith(self.SOURCE_TYPE):
            return None
        path = Path(file_id)
        if not path.exists():
            return None
        stat = path.stat()
        return FileStat(
            file_id=file_id, size=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime),
            ctime=datetime.fromtimestamp(stat.st_ctime),
            inode=stat.st_ino,
        )
    
    @hookimpl
    def curator_source_move(self, source_id, file_id, new_path):
        if not source_id.startswith(self.SOURCE_TYPE):
            return None
        old_path = Path(file_id)
        new_path_obj = Path(new_path)
        new_path_obj.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path_obj)
        return self.curator_source_stat(source_id, str(new_path_obj))
    
    @hookimpl
    def curator_source_delete(self, source_id, file_id, to_trash):
        if not source_id.startswith(self.SOURCE_TYPE):
            return None
        if to_trash:
            from send2trash import send2trash
            send2trash(file_id)
        else:
            os.remove(file_id)
        return True
    
    # @hookimpl  # Phase Beta
    # async def curator_source_watch(self, source_id, root):
    #     from watchfiles import awatch
    #     async for changes in awatch(root):
    #         for change_type, path in changes:
    #             yield ChangeEvent(
    #                 kind=self._map_change_type(change_type),
    #                 file_id=path, path=path, timestamp=datetime.utcnow(),
    #             )
    
    def _matches_pattern(self, path: Path, pattern: str) -> bool:
        return path.match(pattern) or any(parent.match(pattern) for parent in path.parents)
```

### 6.4 GoogleDriveSource (Phase Beta sketch)

```python
# curatorplug/source_gdrive/__init__.py

from datetime import datetime
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

from curator.plugins.hookspecs import hookimpl
from curator.sources.types import FileInfo, FileStat, SourcePluginInfo


class Plugin:
    SOURCE_TYPE = "gdrive"
    
    def __init__(self):
        self._drives: dict[str, GoogleDrive] = {}
    
    @hookimpl
    def curator_source_register(self):
        return SourcePluginInfo(
            source_type=self.SOURCE_TYPE,
            display_name="Google Drive",
            requires_auth=True,
            supports_watch=False,  # poll-based; Phase γ adds push notifications
            config_schema={
                "type": "object",
                "properties": {
                    "account": {"type": "string"},
                    "folder_ids": {"type": "array", "items": {"type": "string"}},
                    "credentials_file": {"type": "string"},
                },
                "required": ["account"]
            }
        )
    
    def _get_drive(self, source_id: str) -> GoogleDrive:
        if source_id not in self._drives:
            gauth = GoogleAuth()
            gauth.LocalWebserverAuth()
            self._drives[source_id] = GoogleDrive(gauth)
        return self._drives[source_id]
    
    @hookimpl
    def curator_source_enumerate(self, source_id, root, options):
        if not source_id.startswith(self.SOURCE_TYPE):
            return
        drive = self._get_drive(source_id)
        # root is folder_id for Google Drive
        query = f"'{root}' in parents and trashed=false"
        for f in drive.ListFile({'q': query}).GetList():
            if f['mimeType'] == 'application/vnd.google-apps.folder':
                yield from self.curator_source_enumerate(source_id, f['id'], options)
                continue
            yield FileInfo(
                file_id=f['id'],
                path=f['title'],
                size=int(f.get('fileSize', 0)),
                mtime=datetime.fromisoformat(f['modifiedDate']),
                extras={"mime_type": f['mimeType'], "drive_md5": f.get('md5Checksum')},
            )
    
    # ... read_bytes, stat, move, delete, watch (similar pattern)
```

---

## 7. Hash Pipeline

### 7.1 Multi-stage strategy (D1, ported from fclones)

The pipeline uses cheap filters first, expensive ones last. Files that pass each stage become candidates for the next; files that fail are confirmed-different and don't need further hashing.

```
Stage 1: Group by size              cheapest, no I/O on file content
Stage 2: Dedup by inode             skip hardlinks (free dedup)
Stage 3: Hash 4KB prefix            small I/O, eliminates most non-matches
Stage 4: Hash 4KB suffix            small I/O, catches files with same prefix
Stage 5: Full hash (xxhash3_128)    full file read, only on remaining candidates
Stage 6: Fuzzy hash (ppdeep)        only for text-eligible files
Stage 7: MD5                        cheap once full content is in cache
```

### 7.2 Implementation skeleton

```python
# curator/services/hash_pipeline.py

from collections import defaultdict
from typing import Iterator, Optional

import xxhash
import ppdeep


PREFIX_BYTES = 4096
SUFFIX_BYTES = 4096

TEXT_EXTENSIONS = {
    ".py", ".bas", ".vb", ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts", ".sql",
    ".csv", ".tsv", ".log", ".xml",
}


class HashPipeline:
    """Multi-stage hash pipeline."""
    
    def __init__(self, source_plugin_manager, hash_cache_repo):
        self.spm = source_plugin_manager
        self.cache = hash_cache_repo
    
    def process(self, files: list[FileEntity]) -> Iterator[FileEntity]:
        """Process files through all stages, yielding fully-hashed entities."""
        # Stage 1: group by size
        size_groups = defaultdict(list)
        for f in files:
            size_groups[f.size].append(f)
        
        for size, group in size_groups.items():
            yield from self._process_size_group(group, isolate_hashing=(len(group) == 1))
    
    def _process_size_group(self, files, isolate_hashing) -> Iterator[FileEntity]:
        # Stage 2: dedup by inode (local FS hardlinks)
        if not isolate_hashing:
            inode_groups = defaultdict(list)
            for f in files:
                if f.inode is not None:
                    inode_groups[(f.source_id, f.inode)].append(f)
                else:
                    inode_groups[id(f)].append(f)
            files = [grp[0] for grp in inode_groups.values()]
        
        # Stage 3+4: prefix + suffix hash to split groups
        if not isolate_hashing and len(files) > 1:
            for prefix, prefix_grp in self._group_by_prefix(files).items():
                for suffix, sub_grp in self._group_by_suffix(prefix_grp).items():
                    yield from self._full_hash_group(sub_grp)
        else:
            yield from self._full_hash_group(files)
    
    def _full_hash_group(self, files) -> Iterator[FileEntity]:
        for f in files:
            # Check cache first
            cached = self.cache.get(f.source_id, f.source_path)
            if cached and cached.mtime == f.mtime and cached.size == f.size:
                f.xxhash3_128 = cached.xxhash3_128
                f.md5 = cached.md5
                f.fuzzy_hash = cached.fuzzy_hash
                yield f
                continue
            
            # Compute hashes
            f.xxhash3_128 = self._xxhash3_128(f)
            f.md5 = self._md5(f)
            if f.extension in TEXT_EXTENSIONS:
                f.fuzzy_hash = self._fuzzy_hash(f)
            
            self.cache.upsert(f)
            yield f
    
    def _xxhash3_128(self, file) -> str:
        h = xxhash.xxh3_128()
        for chunk in self._read_chunks(file):
            h.update(chunk)
        return h.hexdigest()
    
    def _md5(self, file) -> str:
        import hashlib
        h = hashlib.md5()
        for chunk in self._read_chunks(file):
            h.update(chunk)
        return h.hexdigest()
    
    def _fuzzy_hash(self, file) -> Optional[str]:
        content = b""
        for chunk in self._read_chunks(file):
            content += chunk
        return ppdeep.hash(content) if content else None
    
    def _read_chunks(self, file, chunk_size=65536):
        offset = 0
        while offset < file.size:
            results = self.spm.hook.curator_source_read_bytes(
                source_id=file.source_id,
                file_id=file.source_path,
                offset=offset, length=chunk_size,
            )
            chunk = next((c for c in results if c is not None), None)
            if chunk is None:
                break
            yield chunk
            offset += len(chunk)
            if len(chunk) < chunk_size:
                break  # EOF
    
    def _group_by_prefix(self, files):
        groups = defaultdict(list)
        for f in files:
            prefix = self._read_segment(f, 0, PREFIX_BYTES)
            groups[prefix].append(f)
        return groups
    
    def _group_by_suffix(self, files):
        groups = defaultdict(list)
        for f in files:
            offset = max(0, f.size - SUFFIX_BYTES)
            suffix = self._read_segment(f, offset, SUFFIX_BYTES)
            groups[suffix].append(f)
        return groups
    
    def _read_segment(self, file, offset, length):
        results = self.spm.hook.curator_source_read_bytes(
            source_id=file.source_id, file_id=file.source_path,
            offset=offset, length=length,
        )
        return next((c for c in results if c is not None), b"")
```

### 7.3 Hash cache (item 42 / D15)

The hash cache table stores `(source_id, source_path, mtime, size) → (xxhash, md5, fuzzy_hash)`. Invalidated when mtime or size changes. Survives across runs. Schema in §4.3.

The cache's purpose: subsequent scans don't re-read unchanged files. Critical for system-wide deployments where Curator scans 100k+ files regularly.

### 7.4 Performance tuning

- Stage 1+2 (size, inode): pure metadata, no file I/O. Fast.
- Stage 3+4 (prefix, suffix): 8KB total per file. Cheap.
- Stage 5 (xxhash3_128): full file read but xxhash3 is multi-GB/sec.
- Stage 6 (ppdeep): only text-eligible; slower but bounded by file size.
- Stage 7 (md5): computed alongside xxhash3 in same read pass.

Parallelization: `ThreadPoolExecutor` for the hash stages. SQLite write batching reduces commit overhead.


---

## 8. Lineage Detection

### 8.1 Lineage edge kinds

From §3.5:

- `DUPLICATE` — byte-identical (same xxhash)
- `NEAR_DUPLICATE` — high fuzzy hash similarity (≥70%)
- `DERIVED_FROM` — one is a transformation of the other (export, conversion)
- `VERSION_OF` — explicit version chain (filename pattern + similarity)
- `REFERENCED_BY` — one mentions the other (link, import, attachment)
- `SAME_LOGICAL_FILE` — different paths, same conceptual file (cross-source)

### 8.2 Confidence thresholds

| Edge kind | Auto-confirm threshold | Escalate threshold |
|---|---|---|
| DUPLICATE | 1.0 (always certain — hash match) | N/A |
| NEAR_DUPLICATE | ≥ 0.95 | 0.7 ≤ x < 0.95 |
| DERIVED_FROM | ≥ 0.90 | 0.6 ≤ x < 0.90 |
| VERSION_OF | ≥ 0.85 | 0.6 ≤ x < 0.85 |
| REFERENCED_BY | 1.0 (literal mention) | N/A |
| SAME_LOGICAL_FILE | ≥ 0.95 | 0.7 ≤ x < 0.95 |

Below escalate threshold: edge not stored unless plugin explicitly insists.

### 8.3 Core lineage detectors (Phase Alpha)

```python
# curator/plugins/core/lineage_hash_dup.py

@hookimpl
def curator_compute_lineage(file_a, file_b) -> Optional[LineageEdge]:
    if not (file_a.xxhash3_128 and file_b.xxhash3_128):
        return None
    if file_a.xxhash3_128 == file_b.xxhash3_128:
        return LineageEdge(
            from_curator_id=file_a.curator_id,
            to_curator_id=file_b.curator_id,
            edge_kind=LineageKind.DUPLICATE,
            confidence=1.0,
            detected_by="curator.core.lineage_hash_dup",
        )
    return None
```

```python
# curator/plugins/core/lineage_fuzzy_dup.py

import ppdeep

@hookimpl
def curator_compute_lineage(file_a, file_b):
    if not (file_a.fuzzy_hash and file_b.fuzzy_hash):
        return None
    if file_a.xxhash3_128 == file_b.xxhash3_128:
        return None  # exact duplicate, handled elsewhere
    similarity = ppdeep.compare(file_a.fuzzy_hash, file_b.fuzzy_hash)
    if similarity >= 70:
        confidence = similarity / 100.0
        return LineageEdge(
            from_curator_id=file_a.curator_id,
            to_curator_id=file_b.curator_id,
            edge_kind=LineageKind.NEAR_DUPLICATE,
            confidence=confidence,
            detected_by="curator.core.lineage_fuzzy_dup",
            notes=f"fuzzy similarity: {similarity}%"
        )
    return None
```

```python
# curator/plugins/core/lineage_filename.py

import re
from pathlib import Path

VERSION_PATTERNS = [
    r"^(?P<base>.+?)[\s_-]?v?(?P<version>\d+(\.\d+)*)\.(?P<ext>\w+)$",
    r"^(?P<base>.+?)[\s_-]\((?P<version>\d+)\)\.(?P<ext>\w+)$",  # "file (1).txt"
    r"^(?P<base>.+?)[\s_-]Copy[\s_-]?(?:\((?P<version>\d+)\))?\.(?P<ext>\w+)$",
]

@hookimpl
def curator_compute_lineage(file_a, file_b):
    name_a = Path(file_a.source_path).name
    name_b = Path(file_b.source_path).name
    
    for pattern in VERSION_PATTERNS:
        ma = re.match(pattern, name_a)
        mb = re.match(pattern, name_b)
        if not (ma and mb):
            continue
        if ma.group('base') != mb.group('base'):
            continue
        if ma.group('ext') != mb.group('ext'):
            continue
        return LineageEdge(
            from_curator_id=file_a.curator_id,
            to_curator_id=file_b.curator_id,
            edge_kind=LineageKind.VERSION_OF,
            confidence=0.85,
            detected_by="curator.core.lineage_filename",
            notes=f"version chain: {ma.group('version')} → {mb.group('version')}"
        )
    return None
```

### 8.4 Lineage service

```python
# curator/services/lineage.py

class LineageService:
    """Orchestrates lineage detection across files."""
    
    def __init__(self, db, plugin_manager):
        self.db = db
        self.pm = plugin_manager
    
    def compute_for_file(self, file: FileEntity) -> list[LineageEdge]:
        """Compute potential edges from this file to candidates."""
        edges = []
        candidates = self._find_candidates(file)
        
        for candidate in candidates:
            results = self.pm.hook.curator_compute_lineage(file_a=file, file_b=candidate)
            for result in results:
                if result is None:
                    continue
                if result.confidence < self._threshold(result.edge_kind):
                    continue
                edges.append(result)
        
        return edges
    
    def _find_candidates(self, file: FileEntity) -> list[FileEntity]:
        """Cheap filtering of candidate files for lineage comparison.
        
        Strategy: query the DB for files that share a hash bucket, similar size,
        or similar filename. Avoid O(n²) by using indices.
        """
        ...
    
    def _threshold(self, kind: LineageKind) -> float:
        return {
            LineageKind.DUPLICATE: 1.0,
            LineageKind.NEAR_DUPLICATE: 0.7,
            LineageKind.DERIVED_FROM: 0.6,
            LineageKind.VERSION_OF: 0.6,
            LineageKind.REFERENCED_BY: 1.0,
            LineageKind.SAME_LOGICAL_FILE: 0.7,
        }[kind]
```

### 8.5 Phase Beta lineage plugins

- `curatorplug.lineage_python_ast` — uses pycode_similar's AST normalization (item 02). Compares Python files by structural similarity, not just bytes.
- `curatorplug.lineage_vba_stub` — detects VBA module relationships (function/sub similarity, common module patterns).
- `curatorplug.lineage_image_perceptual` — perceptual image hashing via imagehash (item 20). Detects the same image at different resolutions/formats.
- `curatorplug.lineage_office_metadata` — uses Office document metadata to detect copy-edit chains (Title, LastModifiedBy, RevisionNumber).

---

## 9. Bundle Awareness

### 9.1 Bundle types

- **manual** — user explicitly created via `curator bundles create <name>` and `curator bundles add <id> <files>`
- **auto** — Curator detected via plugin pattern matching
- **plugin:<name>** — plugin-proposed (e.g., `curatorplug.bundle_assessment_package`)

### 9.2 Bundle membership semantics

A file can belong to multiple bundles. A bundle has at least one member with `role='primary'`. Confidence per-membership lets us flag low-confidence auto-membership for review.

Common roles:
- `primary` — the canonical/main file in the bundle
- `related` — secondary file
- `reference` — referenced from the primary (e.g., assessment scoresheet referenced by report)
- `derivative` — derived from the primary (e.g., PDF export of a docx)
- `attachment` — supporting file (e.g., images for a report)

### 9.3 BundleService

```python
# curator/services/bundle.py

class BundleService:
    def __init__(self, db, plugin_manager, bundle_repo, file_repo):
        self.db = db
        self.pm = plugin_manager
        self.bundles = bundle_repo
        self.files = file_repo
    
    def create_manual(self, name: str, members: list[UUID]) -> BundleEntity:
        bundle = BundleEntity(bundle_type="manual", name=name, confidence=1.0)
        self.bundles.insert(bundle)
        for cid in members:
            self.bundles.add_membership(
                bundle_id=bundle.bundle_id,
                curator_id=cid,
                role="primary" if cid == members[0] else "member",
                confidence=1.0,
            )
        return bundle
    
    def propose_auto(self, files: list[FileEntity]) -> list[BundleProposal]:
        """Run plugin proposers, return suggestions for user review."""
        results = self.pm.hook.curator_propose_bundle(files=files)
        return [r for r in results if r is not None]
    
    def confirm_proposal(self, proposal: BundleProposal) -> BundleEntity:
        bundle = BundleEntity(
            bundle_type=f"plugin:{proposal.proposer}",
            name=proposal.name,
            confidence=proposal.confidence,
        )
        self.bundles.insert(bundle)
        for member in proposal.members:
            self.bundles.add_membership(
                bundle_id=bundle.bundle_id,
                curator_id=member.curator_id,
                role=member.role,
                confidence=member.confidence,
            )
        return bundle
    
    def members(self, bundle_id: UUID) -> list[FileEntity]:
        memberships = self.bundles.get_memberships(bundle_id)
        return [self.files.get(m.curator_id) for m in memberships]
    
    def cross_source_check(self, bundle_id: UUID) -> dict:
        """Audit: are all members reachable in their respective sources?"""
        members = self.members(bundle_id)
        result = {"total": len(members), "reachable": 0, "missing": []}
        for f in members:
            stat_results = self.pm.hook.curator_source_stat(
                source_id=f.source_id, file_id=f.source_path
            )
            if any(s is not None for s in stat_results):
                result["reachable"] += 1
            else:
                result["missing"].append(str(f.curator_id))
        return result
```

### 9.4 Cross-source bundles (Phase Beta+)

A bundle can have members from `local`, `gdrive`, `onedrive`. Membership stores `curator_id` only; each member is queried via its source plugin via the curator_id → source_id, source_path lookup. This works automatically given the design — no special-casing needed.

Use case: an APEX assessment package may have:
- `Test_Results.xlsx` on local FS
- `Report_Draft.docx` on Google Drive
- `Reference_Norms.pdf` on OneDrive

All three are members of the same bundle with `role` indicating their function.

---

## 10. Trash and Restore Registry

### 10.1 Dual-trash pattern (D9)

When a file is trashed:

1. **Snapshot metadata** — bundle memberships, flex attrs, lineage edges (preserved in TrashRecord)
2. **Send to OS trash** — via `send2trash` (Windows Recycle Bin, macOS Trash, Linux freedesktop)
3. **Insert TrashRecord row** — Curator's authoritative record
4. **Mark file row `deleted_at = now()`** — soft-delete (keeps relationships intact)
5. **Audit log entry**

### 10.2 TrashService

```python
# curator/services/trash.py

class TrashService:
    def __init__(self, db, plugin_manager, file_repo, trash_repo, bundle_repo, audit_repo):
        self.db = db
        self.pm = plugin_manager
        self.file_repo = file_repo
        self.trash_repo = trash_repo
        self.bundle_repo = bundle_repo
        self.audit_repo = audit_repo
    
    def trash(self, curator_id: UUID, reason: str, actor: str = "user") -> TrashRecord:
        file = self.file_repo.get(curator_id)
        if not file:
            raise FileNotFoundError(curator_id)
        
        # Pre-trash plugin hook (allows veto)
        veto_results = self.pm.hook.curator_pre_trash(file=file, reason=reason)
        for v in veto_results:
            if v and not v.allow:
                raise TrashVetoed(v.reason)
        
        # Snapshot metadata
        bundle_memberships = self.bundle_repo.get_memberships_for_file(curator_id)
        attrs_snapshot = dict(file.flex)
        
        # Send to OS trash
        from send2trash import send2trash, TrashPermissionError
        os_trash = None
        try:
            send2trash(file.source_path)
            os_trash = self._derive_os_trash_location(file.source_path)
        except TrashPermissionError:
            pass  # fallback: just mark deleted in DB
        
        # Create trash record
        trash_record = TrashRecord(
            curator_id=curator_id,
            original_source_id=file.source_id,
            original_path=file.source_path,
            file_hash=file.xxhash3_128,
            trashed_by=actor,
            reason=reason,
            bundle_memberships_snapshot=[
                {"bundle_id": str(m.bundle_id), "role": m.role, "confidence": m.confidence}
                for m in bundle_memberships
            ],
            file_attrs_snapshot=attrs_snapshot,
            os_trash_location=os_trash,
        )
        
        with self.db.conn() as conn:
            self.trash_repo.insert(trash_record)
            self.file_repo.mark_deleted(curator_id)
            self.audit_repo.log(
                actor=actor, action="trash",
                entity_type="file", entity_id=str(curator_id),
                details={"reason": reason},
            )
        
        # Post-trash hook
        self.pm.hook.curator_post_trash(trash_record=trash_record)
        return trash_record
    
    def restore(self, curator_id: UUID, target_path: Optional[str] = None,
                actor: str = "user") -> FileEntity:
        trash_record = self.trash_repo.get(curator_id)
        if not trash_record:
            raise NotInTrashError(curator_id)
        
        restore_path = (
            target_path
            or trash_record.restore_path_override
            or trash_record.original_path
        )
        
        # Pre-restore hook
        veto_results = self.pm.hook.curator_pre_restore(
            trash_record=trash_record, target_path=restore_path
        )
        for v in veto_results:
            if v and not v.allow:
                raise RestoreVetoed(v.reason)
        
        # Restore from OS trash
        if trash_record.os_trash_location:
            self._restore_from_os_trash(trash_record.os_trash_location, restore_path)
        else:
            raise RestoreImpossibleError(
                "No OS trash location; check Windows Recycle Bin manually."
            )
        
        # Reactivate file row + restore metadata
        file = self.file_repo.get(curator_id)
        file.deleted_at = None
        file.source_path = restore_path
        for k, v in trash_record.file_attrs_snapshot.items():
            file.flex[k] = v
        self.file_repo.update(file)
        
        # Restore bundle memberships
        for m in trash_record.bundle_memberships_snapshot:
            self.bundle_repo.add_membership(
                bundle_id=UUID(m['bundle_id']),
                curator_id=curator_id,
                role=m['role'],
                confidence=m['confidence'],
            )
        
        # Remove trash record
        self.trash_repo.delete(curator_id)
        
        self.audit_repo.log(
            actor=actor, action="restore",
            entity_type="file", entity_id=str(curator_id),
            details={"restored_to": restore_path},
        )
        
        self.pm.hook.curator_post_restore(file=file)
        return file
    
    def list_trashed(self, since: Optional[datetime] = None) -> list[TrashRecord]:
        return self.trash_repo.list(since=since)
```

### 10.3 Trash retention

Default: never auto-purge from Curator's TrashRegistry (preserves restore metadata indefinitely). The OS trash has its own retention (Windows Recycle Bin defaults to ~10% of disk). `curator trash purge` lets users explicitly drop trash records older than N days.

---

## 11. CLI Surface

### 11.1 Command structure

Built with Typer + Rich. Two-phase split (D2): every command is either Scan/Read OR Act/Mutate; mutations require `--apply`.

```
curator [global-options] <command> [command-options] [args]

Global options:
  --config PATH        Path to curator.toml (default: platformdirs)
  --db PATH            Path to Curator.db (default: platformdirs)
  --verbose / -v       Increase logging detail (cumulative)
  --quiet / -q         Suppress non-error output
  --json               Output in JSON instead of human-readable
  --no-color           Disable Rich color output

Scan/Read commands:
  inspect <path>           Show detailed Curator info about a file
  scan <source_id> <root>  Scan a source root, populate DB
  group <source> [opts]    Find groups of related files
  lineage <curator_id>     Show lineage edges from this file
  bundles list             List bundles
  bundles show <id>        Show bundle members
  trash list               List trashed files
  doctor                   Check Curator health, dependencies, integrity
  explain <decision_id>    Explain why Curator made a decision

Act/Mutate commands (require --apply):
  group --apply            Trash duplicates per group prioritization
  bundles create <name>    Create a manual bundle
  bundles add <id> <files> Add files to bundle
  bundles dissolve <id>    Delete a bundle (members preserved)
  trash <file_id> --apply  Send file to trash
  restore <file_id> --apply  Restore from trash
  source add <type> [opts] --apply  Add a source
  source remove <id> --apply        Remove a source

Service commands (Phase γ):
  service install --apply  Install Curator as Windows service
  service uninstall --apply  Remove Windows service
  service status           Show service status
  service start/stop       Control service
  api start                Run REST API in foreground (uvicorn)
```

### 11.2 Sample command implementations

```python
# curator/cli/main.py

import typer
from rich.console import Console
from rich.table import Table

from curator.config import Config
from curator.storage import CuratorDB

app = typer.Typer(
    name="curator",
    help="Content-aware artifact intelligence layer",
    add_completion=True,
)
console = Console()


@app.callback()
def global_options(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(None, "--config"),
    db: Optional[Path] = typer.Option(None, "--db"),
    verbose: int = typer.Option(0, "-v", "--verbose", count=True),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
    json_output: bool = typer.Option(False, "--json"),
    no_color: bool = typer.Option(False, "--no-color"),
):
    """Global options applied to all commands."""
    from loguru import logger
    import sys
    logger.remove()
    log_level = "WARNING" if quiet else ["INFO", "DEBUG", "TRACE"][min(verbose, 2)]
    logger.add(sys.stderr, level=log_level)
    
    cfg = Config.load(config)
    if no_color:
        console.no_color = True
    
    ctx.obj = {
        "config": cfg,
        "db": CuratorDB(db or cfg.db_path),
        "json_output": json_output,
    }


@app.command()
def inspect(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Path to file"),
    deep: bool = typer.Option(False, "--deep", help="Run all available analyzers"),
):
    """Show detailed Curator information about a file."""
    db = ctx.obj["db"]
    db.init()
    
    from curator.storage.repositories import FileRepository
    file_repo = FileRepository(db)
    file = file_repo.find_by_path("local", path)
    
    if not file:
        console.print(f"[yellow]File not in Curator's index:[/] {path}")
        raise typer.Exit(code=1)
    
    if ctx.obj["json_output"]:
        console.print_json(file.model_dump_json())
        return
    
    table = Table(title=f"Curator inspection: {path}")
    table.add_column("Attribute")
    table.add_column("Value")
    table.add_row("Curator ID", str(file.curator_id))
    table.add_row("Source", file.source_id)
    table.add_row("Size", f"{file.size:,} bytes")
    table.add_row("xxhash3_128", file.xxhash3_128 or "(not computed)")
    table.add_row("MD5", file.md5 or "(not computed)")
    table.add_row("Fuzzy hash", file.fuzzy_hash or "(not eligible)")
    table.add_row("File type", file.file_type or "(not classified)")
    table.add_row("Last scanned", file.last_scanned_at.isoformat())
    
    console.print(table)
    
    # Lineage edges
    from curator.services import LineageService
    from curator.plugins import get_plugin_manager
    lineage_svc = LineageService(db, get_plugin_manager())
    edges = lineage_svc.get_edges_for(file.curator_id)
    if edges:
        edge_table = Table(title="Lineage edges")
        edge_table.add_column("Edge kind")
        edge_table.add_column("Other file")
        edge_table.add_column("Confidence")
        edge_table.add_column("Detected by")
        for edge in edges:
            other_id = edge.to_curator_id if edge.from_curator_id == file.curator_id else edge.from_curator_id
            other = file_repo.get(other_id)
            edge_table.add_row(
                edge.edge_kind.value,
                other.source_path if other else "(unknown)",
                f"{edge.confidence:.2f}",
                edge.detected_by,
            )
        console.print(edge_table)


@app.command()
def scan(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Source identifier (e.g., 'local')"),
    root: str = typer.Argument(..., help="Root path to scan"),
    workers: int = typer.Option(4, "--workers", help="Parallel workers"),
):
    """Scan a source root and populate Curator's index."""
    from curator.services import ScanService
    from curator.plugins import get_plugin_manager
    
    db = ctx.obj["db"]
    db.init()
    
    service = ScanService(db, get_plugin_manager())
    
    with console.status(f"Scanning {source_id}:{root}..."):
        report = service.scan(source_id=source_id, root=root, workers=workers)
    
    console.print(f"[green]✓[/] Scan complete:")
    console.print(f"  Files seen: {report.files_seen}")
    console.print(f"  Files hashed: {report.files_hashed}")
    console.print(f"  New files: {report.new_files}")
    console.print(f"  Updated files: {report.updated_files}")
    console.print(f"  Duration: {report.duration:.2f}s")


@app.command()
def group(
    ctx: typer.Context,
    source_id: str = typer.Argument("local"),
    root: Optional[str] = typer.Option(None, "--root"),
    apply: bool = typer.Option(False, "--apply", help="Actually trash duplicates"),
    keep: str = typer.Option("oldest", "--keep",
                              help="oldest|newest|shortest_path|longest_path"),
):
    """Find groups of duplicate or related files."""
    from curator.services import GroupService, TrashService
    from curator.plugins import get_plugin_manager
    
    db = ctx.obj["db"]
    db.init()
    
    service = GroupService(db, get_plugin_manager())
    groups = service.find_groups(source_id=source_id, root=root)
    
    if not groups:
        console.print("No groups found.")
        return
    
    for grp in groups:
        console.print(f"\n[bold]Group of {len(grp.members)} files (size {grp.size_each:,} bytes each):[/]")
        primary = grp.choose_primary(strategy=keep)
        for f in grp.members:
            marker = "[green]✓ KEEP[/]" if f.curator_id == primary.curator_id else "[red]✗ DROP[/]"
            console.print(f"  {marker} {f.source_path}")
    
    if not apply:
        console.print("\n[yellow]Dry run.[/] Re-run with --apply to actually trash duplicates.")
        return
    
    trash_service = TrashService(db, get_plugin_manager(), ...)
    trashed = 0
    for grp in groups:
        primary = grp.choose_primary(strategy=keep)
        for f in grp.members:
            if f.curator_id == primary.curator_id:
                continue
            trash_service.trash(
                curator_id=f.curator_id,
                reason=f"duplicate of {primary.source_path}",
                actor="user",
            )
            trashed += 1
    
    console.print(f"\n[green]✓[/] Trashed {trashed} duplicates.")


# ... lineage, bundles, trash, restore, doctor, explain commands
```

### 11.3 Tab completion

Typer auto-generates completion scripts:

```bash
# Bash
curator --install-completion bash

# PowerShell (Windows)
curator --install-completion pwsh
```

---

## 12. Service Mode and REST API

### 12.1 When service mode applies

**Phase Alpha:** no service mode. CLI is the only entry point.

**Phase Gamma:** Curator runs as a long-lived process. CLI commands detect a running service and route through it. External tools (APEX) query the REST API directly.

### 12.2 FastAPI app structure

```python
# curator/api/app.py

from fastapi import FastAPI
from curator.api.routers import files, bundles, lineage, scan, jobs, trash, sources

app = FastAPI(
    title="Curator API",
    version="1.0.0",
    description="Content-aware artifact intelligence layer",
)

app.include_router(files.router, prefix="/files", tags=["files"])
app.include_router(bundles.router, prefix="/bundles", tags=["bundles"])
app.include_router(lineage.router, prefix="/lineage", tags=["lineage"])
app.include_router(scan.router, prefix="/scan", tags=["scan"])
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(trash.router, prefix="/trash", tags=["trash"])
app.include_router(sources.router, prefix="/sources", tags=["sources"])

# Plugin-contributed routers
from curator.plugins import get_plugin_manager
for router_list in get_plugin_manager().hook.curator_api_routers():
    for router in router_list or []:
        app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
```

### 12.3 REST endpoints

```
GET    /files                       List/search files
GET    /files/{curator_id}          Get file detail
PATCH  /files/{curator_id}          Update file flex attrs
DELETE /files/{curator_id}          Trash a file (soft-delete)

GET    /bundles                     List bundles
POST   /bundles                     Create a bundle
GET    /bundles/{id}                Get bundle detail with members
PATCH  /bundles/{id}                Update bundle
DELETE /bundles/{id}                Dissolve bundle (members preserved)
POST   /bundles/{id}/members        Add members
DELETE /bundles/{id}/members/{cid}  Remove member

GET    /lineage/{curator_id}        Get edges from this file
POST   /lineage/{a}/{b}             Manually create edge

POST   /scan                        Trigger scan (returns job_id)
GET    /scan/{source_id}/status     Source's last scan status

GET    /jobs/{job_id}               Get scan job status
GET    /jobs                        List recent jobs

GET    /trash                       List trashed files
POST   /trash/{curator_id}/restore  Restore from trash

GET    /sources                     List configured sources
POST   /sources                     Add a source (triggers OAuth if needed)
DELETE /sources/{source_id}         Remove a source

GET    /auth/callback/{source_id}   OAuth callback (for cloud sources)

GET    /health                      Health check
GET    /metrics                     Prometheus-style metrics
GET    /openapi.json                OpenAPI spec (auto-generated)
GET    /docs                        Swagger UI (auto-generated)
```

### 12.4 OAuth callback handling

When a cloud source plugin needs OAuth:

```
1. CLI/GUI: POST /sources with source_type=gdrive
2. API: returns {oauth_url, state_token}
3. User: opens oauth_url in browser, authorizes
4. Browser redirects to /auth/callback/gdrive?code=...&state=...
5. API: exchanges code for tokens, stores via CredentialStore
6. API: returns success; source is now usable
```

In CLI mode (no service running), PyDrive2's `LocalWebserverAuth()` opens a temporary local web server for the same flow.

### 12.5 Service-mode-vs-CLI dispatch

```python
# curator/cli/dispatch.py

import os
import httpx

def get_client():
    """Returns either a direct DB client or a REST client.
    
    Detection order:
    1. CURATOR_API_URL env var → use REST
    2. Try connecting to localhost:8765 → if succeeds, use REST
    3. Fall back to direct DB access
    """
    api_url = os.getenv("CURATOR_API_URL")
    if api_url:
        return RESTClient(api_url)
    
    try:
        r = httpx.get("http://localhost:8765/health", timeout=0.5)
        if r.status_code == 200:
            return RESTClient("http://localhost:8765")
    except Exception:
        pass
    
    return DirectDBClient(get_db())
```

Both clients implement the same `CuratorClient` interface, so CLI commands work identically regardless of mode.

### 12.6 CredentialStore (for cloud sources)

Each source plugin handles its own credential storage via the `CredentialStore` abstraction:

```python
# curator/services/credentials.py

class CredentialStore:
    """Encrypted storage for OAuth tokens, API keys, etc.
    
    Phase Alpha: SQLite-backed with simple obfuscation (NOT encryption).
    Phase Gamma: keyring (OS-native) + cryptography for at-rest encryption.
    """
    
    def store(self, source_id: str, key: str, value: str) -> None: ...
    def retrieve(self, source_id: str, key: str) -> Optional[str]: ...
    def delete(self, source_id: str, key: str) -> None: ...
    def list_keys(self, source_id: str) -> list[str]: ...
```

---

## 13. Rules Engine

### 13.1 Phase Alpha: minimal

Phase Alpha rules engine is just configurable detection patterns in `curator.toml` (temp file patterns, ignore patterns, etc.). No move actions yet.

```toml
[detection.temp_files]
patterns = ["*.tmp", "~$*", ".DS_Store", "Thumbs.db", "desktop.ini"]
auto_trash = false  # if true, auto-trash on detection

[detection.large_files]
threshold_mb = 100
flag_only = true

[detection.old_files]
older_than_days = 365
flag_only = true
```

### 13.2 Phase Beta: simple match-and-move rules

```toml
[[rules]]
name = "Sort screenshots"
priority = 100
match.extension = ".png"
match.name_contains = "Screenshot"
move_to = "{user_pictures}/Screenshots/{year}/{month}"
confidence_threshold = 0.95

[[rules]]
name = "Archive old downloads"
priority = 50
match.source_path_starts_with = "C:\\Users\\jmlee\\Downloads"
match.older_than_days = 30
move_to = "{user_documents}/Downloads_Archive/{year}/{month}"
confidence_threshold = 0.90
```

Variables in `move_to`:
- `{year}`, `{month}`, `{day}` — file mtime parts
- `{user_documents}`, `{user_pictures}`, `{user_desktop}` — platformdirs paths
- `{name}`, `{ext}`, `{stem}` — file path parts
- `{file_type}` — classification result
- `{xxhash[:8]}` — first 8 chars of xxhash (for collision avoidance)

Match conditions:
- `match.extension` — exact extension match (e.g., `.png`)
- `match.extensions` — any of a list
- `match.name_contains` — substring match in filename
- `match.name_regex` — regex match in filename
- `match.source_path_starts_with` — path prefix match
- `match.source_path_regex` — full path regex
- `match.size_min`, `match.size_max` — byte range
- `match.older_than_days`, `match.newer_than_days` — mtime constraints
- `match.file_type` — MIME type match
- `match.in_bundle` — bundle membership (by name or ID)
- `match.has_lineage_to` — file has lineage edge of given kind

Multiple match conditions are AND-combined. To express OR, create multiple rules.

### 13.3 Phase Gamma: plugin-extensible rule types

Plugins can register custom rule types via `curator_rule_types`:

```python
# curatorplug/rule_assessment_package/__init__.py

from curator.plugins.hookspecs import hookimpl
from curator.rules import RuleBase


class AssessmentPackageRule(RuleBase):
    """Bundles assessment files into a single package and moves them."""
    
    rule_type = "assessment_package"
    
    def __init__(self, config: dict):
        self.client_id = config["client_id"]
        self.archive_root = config["archive_root"]
    
    def match(self, file: FileEntity) -> bool:
        # Match files referenced in any bundle named "Assessment - {client_id}"
        return any(
            b.name == f"Assessment - {self.client_id}"
            for b in file.bundles
        )
    
    def action(self, file: FileEntity) -> RuleAction:
        target = f"{self.archive_root}/{self.client_id}/{file.source_path.split('/')[-1]}"
        return RuleAction.move(target_path=target)


class Plugin:
    @hookimpl
    def curator_rule_types(self) -> dict[str, type]:
        return {"assessment_package": AssessmentPackageRule}
```

Used in `curator.toml`:

```toml
[[rules]]
type = "assessment_package"
client_id = "12345"
archive_root = "G:\\Clinical\\Archive"
priority = 80
```

### 13.4 Rule evaluation

```python
# curator/services/rules.py

class RulesEngine:
    def __init__(self, config: Config, plugin_manager):
        self.config = config
        self.pm = plugin_manager
        self.rules = self._load_rules()
    
    def evaluate(self, file: FileEntity) -> list[RuleAction]:
        """Evaluate all rules against a file. Returns actions to take."""
        actions = []
        for rule in sorted(self.rules, key=lambda r: -r.priority):
            if rule.match(file):
                actions.append(rule.action(file))
                if rule.terminal:  # stop after first matching rule
                    break
        return actions
    
    def apply(self, file: FileEntity, actions: list[RuleAction]) -> None:
        """Apply rule actions (with audit logging, dual-trash, etc.)."""
        for action in actions:
            if action.kind == "move":
                self._move_file(file, action.target_path, audit_reason=f"rule:{action.rule_name}")
            elif action.kind == "tag":
                file.flex[f"tag:{action.tag}"] = True
                # persist via file_repo
            elif action.kind == "bundle":
                # add to named bundle
                ...
```

Rule actions are subject to confidence thresholds and dual-trash semantics. An auto-rule that wants to delete a file does it via `TrashService` like any other actor.


---

## 14. Tier 6: File Watching

### 14.1 Technology (D12)

`watchfiles` (Rust-backed, async-native, supersedes watchdog). Per-source watching is exposed via `curator_source_watch` hook.

### 14.2 LocalFSSource watch implementation

```python
# curator/plugins/core/local_source.py (Phase Beta addition)

@hookimpl
async def curator_source_watch(self, source_id, root):
    if not source_id.startswith(self.SOURCE_TYPE):
        return
    from watchfiles import awatch, Change
    
    async for changes in awatch(root):
        for change_type, path in changes:
            kind = {
                Change.added: ChangeKind.ADDED,
                Change.modified: ChangeKind.MODIFIED,
                Change.deleted: ChangeKind.DELETED,
            }.get(change_type, ChangeKind.MODIFIED)
            
            yield ChangeEvent(
                kind=kind,
                file_id=path,
                path=path,
                timestamp=datetime.utcnow(),
            )
```

### 14.3 WatchService (orchestrator)

```python
# curator/services/watch.py

class WatchService:
    """Watches all enabled sources, dispatches changes to handlers."""
    
    def __init__(self, db, plugin_manager, source_repo):
        self.db = db
        self.pm = plugin_manager
        self.sources = source_repo
        self._tasks: dict[str, asyncio.Task] = {}
    
    async def start(self) -> None:
        """Start watching all enabled sources."""
        for source in self.sources.list_enabled():
            self._tasks[source.source_id] = asyncio.create_task(
                self._watch_source(source)
            )
    
    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
    
    async def _watch_source(self, source: SourceConfig) -> None:
        for root in source.config.get("roots", []):
            results = self.pm.hook.curator_source_watch(
                source_id=source.source_id, root=root
            )
            for async_iter in results:
                if async_iter is None:
                    continue
                async for change in async_iter:
                    await self._handle_change(source.source_id, change)
    
    async def _handle_change(self, source_id: str, change: ChangeEvent) -> None:
        """Dispatch a change to the right service based on kind."""
        if change.kind == ChangeKind.ADDED or change.kind == ChangeKind.MODIFIED:
            # Trigger incremental scan for this file
            ...
        elif change.kind == ChangeKind.DELETED:
            # Mark file as deleted in our DB (without trash registry — this was an
            # OS-level deletion outside Curator's control)
            ...
        elif change.kind == ChangeKind.MOVED:
            # Update source_path; preserve curator_id and all relationships
            ...
```

### 14.4 Debouncing

`watchfiles.awatch` already debounces (default 50ms). For high-churn directories (build outputs, IDE temp files), add ignore patterns at the source level.

---

## 15. Tier 7: GUI Plan

### 15.1 Phase Beta GUI: PyQt6 (proposed)

Rationale (deferred to Phase Beta for final decision):
- PyQt6: mature, native look, full feature set; license is GPL/commercial
- PySide6: same Qt foundation, LGPL, more business-friendly
- Web UI (FastAPI + React): leverages REST API; cross-platform "for free"
- Textual: console-based; nice for CLI users but not a real GUI

Tracker item 23 has PyQt6 examples for reference. Likely path: PySide6 for Qt-based desktop with no licensing friction.

### 15.2 Core GUI views

- **Inbox** — recent scans, pending HITL escalations, recent trash
- **Browser** — file tree across sources with Curator metadata (Curator ID, hashes, lineage edges, bundles)
- **Bundles** — list and edit bundles
- **Lineage Graph** — visual graph of related files (D3.js or similar)
- **Trash** — list trashed items with restore button
- **Audit Log** — recent actions, filterable
- **Settings** — config editor for `curator.toml`, source management

### 15.3 GUI ↔ backend communication

GUI talks to the local REST API (Phase γ). For Phase β, GUI may directly use the same Python services as the CLI. This dual-mode is similar to the CLI dispatch (§12.5).

---

## 16. Configuration

### 16.1 curator.toml structure

```toml
# Curator configuration
# Default location: <user_config_dir>/curator/curator.toml

[curator]
db_path = "auto"  # auto = platformdirs; or absolute path
log_path = "auto"
log_level = "INFO"

[hash]
primary = "xxh3_128"
secondary = "md5"
fuzzy_for = [".py", ".bas", ".vb", ".md", ".txt", ".rst", ".json", ".yaml",
             ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts", ".sql",
             ".csv", ".tsv", ".log", ".xml"]
prefix_bytes = 4096
suffix_bytes = 4096

[trash]
provider = "windows_recycle_bin"  # or "macos_trash" | "linux_freedesktop"
restore_metadata = true
purge_older_than_days = null  # null = never auto-purge

[detection.temp_files]
patterns = ["*.tmp", "~$*", ".DS_Store", "Thumbs.db", "desktop.ini"]
auto_trash = false

[lineage]
fuzzy_threshold = 70  # ppdeep similarity score (0-100) below which we don't store
auto_confirm_threshold = 0.95  # confidence ≥ this is auto-confirmed
escalate_threshold = 0.70     # confidence in [escalate, auto_confirm) goes to user

[source.local]
roots = [
    "C:\\Users\\jmlee\\Desktop\\AL",
    "C:\\Users\\jmlee\\Desktop\\Apex"
]
ignore = [".git", "node_modules", "__pycache__", "venv", ".idea", ".vscode"]

# Phase Beta+
# [source.gdrive]
# account = "jake@example.com"
# folder_ids = ["1py38t20LyDJB84uIeaPlD14Je3AeRL8g"]

# Phase Beta+
# [[rules]]
# name = "Sort screenshots"
# priority = 100
# match.extension = ".png"
# match.name_contains = "Screenshot"
# move_to = "{user_pictures}/Screenshots/{year}/{month}"

[group]
default_keep_strategy = "oldest"  # oldest | newest | shortest_path | longest_path

[plugins]
disabled = []  # plugin names to skip loading
```

### 16.2 Config loading (D14, item 79)

```python
# curator/config/__init__.py

from pathlib import Path
from typing import Optional

import platformdirs
import sys
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class Config:
    def __init__(self, data: dict):
        self.data = data
    
    @classmethod
    def load(cls, path: Optional[Path] = None) -> 'Config':
        if path is None:
            path = Path(platformdirs.user_config_dir("Curator", "JakeLeese")) / "curator.toml"
        if not path.exists():
            return cls(DEFAULT_CONFIG)
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return cls(_merge_with_defaults(data, DEFAULT_CONFIG))
    
    def save(self, path: Optional[Path] = None) -> None:
        import tomli_w
        path = path or Path(platformdirs.user_config_dir("Curator", "JakeLeese")) / "curator.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            tomli_w.dump(self.data, f)
    
    @property
    def db_path(self) -> Path:
        v = self.data["curator"]["db_path"]
        if v == "auto":
            return Path(platformdirs.user_data_dir("Curator", "JakeLeese")) / "Curator.db"
        return Path(v)
    
    # ... other property accessors
```

---

## 17. Audit Log

### 17.1 Schema

See §4.3 — `audit_log` table with `actor`, `action`, `entity_type`, `entity_id`, `details_json`.

### 17.2 What gets logged

Every meaningful action:
- `scan` — start/end with file counts
- `classify` — file type changed
- `lineage_detected` — new edge added (with confidence)
- `bundle_create` / `bundle_member_add` / `bundle_member_remove` / `bundle_dissolve`
- `trash` — file sent to trash with reason
- `restore` — file restored from trash
- `rule_applied` — auto-rule moved/tagged a file
- `source_added` / `source_removed`
- `service_started` / `service_stopped`
- `config_changed` — curator.toml updated
- `migration_applied` — schema version bumped
- `plugin_loaded` / `plugin_failed`

### 17.3 Loguru integration (D14)

Loguru is the application logger; audit log is the persistent decision log. Loguru events with the `audit=True` extra are also written to the audit table:

```python
# curator/services/audit.py

from loguru import logger

class AuditService:
    def __init__(self, audit_repo):
        self.repo = audit_repo
    
    def log(self, actor: str, action: str, **details) -> None:
        # Persist to DB
        self.repo.insert(AuditEntry(
            actor=actor, action=action, details=details,
            entity_type=details.pop("entity_type", None),
            entity_id=details.pop("entity_id", None),
        ))
        # Also stream to Loguru
        logger.bind(audit=True, actor=actor, action=action, **details).info(
            f"{actor} performed {action}"
        )
```

### 17.4 Query patterns

CLI: `curator audit --since "2026-01-01" --action trash --entity-id <curator_id>`

```python
# curator/storage/repositories/audit_repo.py

class AuditRepository:
    def query(
        self,
        since: Optional[datetime] = None,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[AuditEntry]:
        clauses, params = [], []
        if since:
            clauses.append("occurred_at >= ?")
            params.append(since)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if entity_type:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if entity_id:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        where = " AND ".join(clauses) if clauses else "1"
        sql = f"SELECT * FROM audit_log WHERE {where} ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        # ... execute and convert
```

---

## 18. Testing Strategy

### 18.1 Stack (D-decisions: pytest, hypothesis)

- **pytest** — test framework
- **hypothesis** — property-based testing (critical for fuzzy hash and lineage accuracy)
- **pytest-asyncio** — for async code (Phase Beta+)
- **httpx + FastAPI TestClient** — for REST API testing (Phase Gamma)

### 18.2 Repository layout

```
tests/
├── conftest.py                  # shared fixtures
├── unit/                        # fast, no I/O
│   ├── test_models.py
│   ├── test_storage_queries.py
│   ├── test_hash_pipeline.py    # uses fake source plugin
│   ├── test_lineage_detectors.py
│   ├── test_rules_engine.py
│   └── test_config.py
├── integration/                 # uses tmp_path, real SQLite
│   ├── test_scan_flow.py
│   ├── test_trash_restore.py
│   ├── test_bundle_lifecycle.py
│   └── test_local_source.py
├── property/                    # hypothesis-based
│   ├── test_lineage_accuracy.py     # synthetic file pairs
│   ├── test_hash_pipeline_invariants.py
│   └── test_rule_evaluation.py
├── corpus/                      # synthetic test fixtures
│   ├── fixtures.py              # generators
│   └── samples/                 # known files (curated)
└── e2e/                         # full CLI/API flows (Phase γ+)
    ├── test_cli_commands.py
    └── test_api_endpoints.py
```

### 18.3 Synthetic test corpus generator

```python
# tests/corpus/fixtures.py

from hypothesis import strategies as st
from pathlib import Path

@st.composite
def file_pair_with_relationship(draw, kind="duplicate"):
    """Generate a pair of files with a specified relationship."""
    base_content = draw(st.binary(min_size=100, max_size=10000))
    
    if kind == "duplicate":
        return (base_content, base_content)
    
    if kind == "near_duplicate":
        # Modify a small fraction of bytes
        modified = bytearray(base_content)
        n_changes = max(1, len(modified) // 100)
        for _ in range(n_changes):
            idx = draw(st.integers(min_value=0, max_value=len(modified) - 1))
            modified[idx] = draw(st.integers(min_value=0, max_value=255))
        return (base_content, bytes(modified))
    
    if kind == "version_chain":
        # Two files with similar content and version-number filename pattern
        modified = base_content + b"\n# v2 update"
        return (base_content, modified)
    
    raise ValueError(f"Unknown relationship kind: {kind}")


def write_pair_to_disk(tmp_path: Path, content_a: bytes, content_b: bytes,
                       name_a: str = "a.txt", name_b: str = "b.txt"):
    (tmp_path / name_a).write_bytes(content_a)
    (tmp_path / name_b).write_bytes(content_b)
    return (tmp_path / name_a, tmp_path / name_b)
```

### 18.4 Property-based test example

```python
# tests/property/test_lineage_accuracy.py

from hypothesis import given, settings
from tests.corpus.fixtures import file_pair_with_relationship, write_pair_to_disk

from curator.services import LineageService

@given(file_pair_with_relationship(kind="duplicate"))
@settings(max_examples=100, deadline=None)
def test_duplicate_files_always_detected(content_pair, tmp_path):
    """Property: byte-identical files MUST be detected as DUPLICATE with confidence 1.0."""
    a_content, b_content = content_pair
    path_a, path_b = write_pair_to_disk(tmp_path, a_content, b_content)
    
    # Set up Curator with this pair
    db, pm = setup_test_curator(tmp_path)
    scan_service = ScanService(db, pm)
    scan_service.scan("local", str(tmp_path))
    
    # Query lineage edges
    file_a = file_repo.find_by_path("local", str(path_a))
    file_b = file_repo.find_by_path("local", str(path_b))
    edges = lineage_service.get_edges_between(file_a.curator_id, file_b.curator_id)
    
    assert any(
        e.edge_kind == LineageKind.DUPLICATE and e.confidence == 1.0
        for e in edges
    ), "Duplicate not detected"


@given(file_pair_with_relationship(kind="near_duplicate"))
def test_near_duplicates_detected_at_high_confidence(content_pair, tmp_path):
    """Property: near-duplicates (≥99% similar) get NEAR_DUPLICATE edge with confidence ≥0.95."""
    # ... similar pattern
```

### 18.5 Coverage targets

- Phase Alpha: 85% line coverage minimum
- 100% coverage on critical paths (hash pipeline, trash service, audit log)
- All public Repository methods covered
- All CLI commands have at least one happy-path integration test

---

## 19. Distribution

### 19.1 Phase Alpha: pip install in venv

```bash
# Recommended user install
python -m venv ~/curator-env
source ~/curator-env/bin/activate  # or curator-env\Scripts\activate on Windows
pip install curator-cli
```

`pyproject.toml` declares all dependencies (xxhash, ppdeep, send2trash, datasketch, watchfiles, pluggy, pydantic, questionary, loguru, rich, typer, filetype, platformdirs, tomli, tomli-w, pytest, hypothesis, fastapi, uvicorn, httpx).

### 19.2 Phase Beta: optional plugin packages

External plugin packages distributed separately:
- `curatorplug-source-gdrive` (depends on PyDrive2)
- `curatorplug-broken-pdf` (depends on pypdf)
- `curatorplug-image-perceptual` (depends on imagehash)

Installed via `pip install curatorplug-source-gdrive` etc. The plugin manager auto-discovers them via setuptools entry points.

### 19.3 Phase Gamma: Windows service installer

```bash
# After pip install, run as elevated
curator service install --apply

# Service registered as "Curator" in Services.msc
# Runs on system startup as LocalSystem (default) or specified user
```

Implementation uses pywin32's `win32serviceutil`.

### 19.4 Phase Gamma+: Standalone .exe (PyInstaller)

For users without Python:

```bash
# Build (run on Windows)
pyinstaller --onefile --windowed curator/cli/main.py

# Output: dist/curator.exe (~80-150MB depending on bundled deps)
```

Distributed via:
- Direct download from GitHub releases
- Optional MSI wrapper (using WiX or InnoSetup) for proper Windows installation

### 19.5 Cross-platform support (Phase Delta+)

- macOS: same pip install approach. PyInstaller for .app bundle. Code signing required for distribution.
- Linux: pip install for distros with Python 3.11+. PyInstaller for AppImage if needed.
- All Curator code paths use `pathlib.Path` and avoid Windows-specific assumptions outside `pywin32`-gated modules.

---

## 20. APEX Integration

### 20.1 Use case

APEX (Jake's clinical assessment platform) needs robust file management for clinical documents:
- Assessment scoresheets (Excel)
- Generated reports (Word/PDF)
- Reference materials (PDFs, scoring guides)
- Client folders with mixed file types
- All managed under 99.5% accuracy floor and full audit trail

Curator provides this layer; APEX consumes via the REST API (Phase γ).

### 20.2 Integration architecture

```
┌──────────────────────────────────────────────────┐
│                APEX (clinical app)                │
│                                                   │
│   Assessment workflows, report generation,        │
│   scoring, clinical decision support              │
│                                                   │
│   Talks to Curator via REST API                   │
└──────────────┬───────────────────────────────────┘
               │ HTTP
               ▼
┌──────────────────────────────────────────────────┐
│      Curator REST API (FastAPI on localhost)     │
└──────────────┬───────────────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
┌──────────────┐  ┌────────────────┐
│ Local FS     │  │ Cloud sources  │
│ (clinical    │  │ (G:\Clinical,  │
│  folders)    │  │  OneDrive)     │
└──────────────┘  └────────────────┘
```

### 20.3 Key APEX → Curator interactions

#### Assessment package creation

When APEX completes an assessment:
1. APEX writes the scoresheet, report, and supporting files locally
2. APEX calls `POST /bundles` to create a bundle with `name="Assessment - <client_id> - <date>"`
3. APEX adds each file as a member with appropriate role (`primary` for the main report, `reference` for scoresheet, etc.)
4. Curator scans the bundle, computes lineage, audits creation
5. APEX stores only `bundle_id` in its own DB; rich metadata lives in Curator

#### Forensic traceability

When a clinical question arises ("how was this score derived?"):
1. APEX queries `GET /lineage/<curator_id>` for the assessment file
2. Curator returns all edges: where the file came from, what it was derived from, what's been derived from it
3. APEX queries `GET /audit_log?entity_id=<curator_id>` for the full action history
4. Forensic-grade traceability without APEX needing its own audit infrastructure

#### Bad-extension detection

When a clinical file appears with mismatched extension (e.g., `.docx` that's actually a `.zip`):
1. Curator's `bad_extension` detection plugin flags the file
2. APEX queries `GET /files?has_flag=bad_extension`
3. APEX surfaces a warning in the clinician UI

#### Cross-source bundles for distributed work

A clinical case may have files spanning local + Google Drive + OneDrive:
- Local: scratch notes, draft scoresheets
- Google Drive: shared with treatment team
- OneDrive: archival storage

Curator's bundle membership transparently spans sources. APEX queries `GET /bundles/<id>` and gets the unified view.

### 20.4 APEX-specific Curator plugins

Plugins APEX may want to author or use:

- `curatorplug-apex-assessment-detector` — detects assessment package patterns and proposes bundles
- `curatorplug-apex-bundle-rules` — auto-organizes assessment files into the canonical APEX folder structure
- `curatorplug-apex-validation` — additional file integrity checks specific to clinical formats

### 20.5 Constitution compliance

APEX's Constitution-governed accuracy floor (99.5%) propagates to its file operations through Curator:
- Curator's `confidence_threshold` config prevents auto-actions below the floor
- Curator's audit log provides the verifiable decision trail Constitution requires
- Curator's dual-trash + restore pattern means no destructive action is irreversible within the retention period

This makes Curator the **infrastructure for Constitution-governed file management**.

---

## 21. Open Questions

These are questions explicitly NOT decided in this spec. They'll be resolved during implementation or in future revisions.

### 21.1 Phase Alpha decisions deferred

1. **Plugin discovery in development mode** — should `curator --plugin-dir <path>` exist for local plugin development before pip install?
2. **Hash cache eviction policy** — never evict (current plan), or LRU when DB exceeds N MB?
3. **Concurrent scan handling** — should two `curator scan` invocations be allowed on overlapping roots (with locking) or refused?
4. **Timezone handling** — store timestamps in UTC always (current plan)? Or local time? Audit log timestamps especially matter.
5. **File path encoding** — Windows paths can be non-UTF-8. Currently storing as Python str (Unicode); need explicit handling for path round-trips.

### 21.2 Phase Beta+ decisions deferred

6. **GUI framework final choice** — PyQt6 vs PySide6 vs web UI. Decide after Phase Alpha API surface stabilizes.
7. **Cloud source watch mechanism** — Google Drive Push Notifications require a public callback URL; polling is the fallback. Curator's design needs to accommodate both.
8. **Rule conflict resolution** — when two rules match a file with different actions, what wins? Currently: highest priority. Need to verify this is sufficient.
9. **Cross-source duplicate handling** — if the same file exists on local AND Google Drive, is it ONE FileEntity with multiple source_paths or TWO FileEntities with a SAME_LOGICAL_FILE edge?
10. **Event bus** — should plugin event dispatch (file_seen, file_classified, file_trashed) use an async event bus pattern or synchronous pluggy hooks? Currently: synchronous.

### 21.3 Phase Gamma+ decisions deferred

11. **REST API authentication** — for local-only service, no auth needed. For multi-machine or remote use (future), what auth scheme? OAuth? API tokens?
12. **Multi-user / shared instance** — Curator is currently single-user. If multi-user emerges, what's the data model change?
13. **Browser extension integration** — should Curator track files downloaded from browsers? Via Native Messaging API?
14. **Mobile companion** — view-only Curator on phone? Out of scope for now but worth noting.
15. **Backup strategy** — Curator.db is a single SQLite file. Should Curator manage its own backups, or rely on user's backup solution?

---

## Document end

**Implementation start sequence:**

1. Create `curator/` Python package structure
2. Implement Tier 1: pydantic models (`curator/models/`)
3. Implement Tier 1: storage layer with first migration (`curator/storage/`)
4. Implement Tier 2: hash pipeline + classification + audit (`curator/services/`)
5. Implement Tier 3: plugin framework + LocalFSSource + core lineage detectors (`curator/plugins/`)
6. Implement Tier 7: minimal CLI (scan, inspect, group) (`curator/cli/`)
7. End-to-end test on a real folder (the Curator project itself)
8. Iterate

**Companion file maintenance:**

- `Github/CURATOR_RESEARCH_NOTES.md` — append revisions as new decisions emerge
- `Github/PROCUREMENT_INDEX.md` — append Round 5+ when implementation reveals gaps
- `DESIGN.md` (this file) — version major revisions; track minor edits in a `Revision Log` section below

---

## Revision Log

- **2026-05-05 v1.0** — Initial draft. All 21 sections written. Ready for implementation.
