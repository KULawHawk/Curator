-- Curator: Initial schema (migration 001)
-- DESIGN.md §4.3
--
-- This file is loaded by curator/storage/migrations.py and applied
-- via conn.executescript() inside a transaction.
--
-- Schema rules:
--   * UUIDs stored as TEXT (UUID hex string with dashes).
--   * Timestamps stored as TIMESTAMP, converted to/from datetime.datetime
--     by adapters registered in curator/storage/connection.py.
--   * Free-form attributes stored in *_flex_attrs companion tables as JSON.
--   * Soft-delete via deleted_at on files (preserves relationships).
--   * Foreign keys are ENABLED (PRAGMA in connection.py).
--   * Journal mode is WAL (PRAGMA in connection.py).

-- ============================================================================
-- Schema versioning
-- ============================================================================

CREATE TABLE IF NOT EXISTS schema_versions (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- Sources
-- ============================================================================

CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    display_name TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- Files
-- ============================================================================

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

CREATE INDEX idx_files_xxhash    ON files(xxhash3_128) WHERE xxhash3_128 IS NOT NULL;
CREATE INDEX idx_files_md5       ON files(md5)         WHERE md5         IS NOT NULL;
CREATE INDEX idx_files_fuzzy     ON files(fuzzy_hash)  WHERE fuzzy_hash  IS NOT NULL;
CREATE INDEX idx_files_size      ON files(size);
CREATE INDEX idx_files_inode     ON files(source_id, inode) WHERE inode IS NOT NULL;
CREATE INDEX idx_files_extension ON files(extension);
CREATE INDEX idx_files_deleted   ON files(deleted_at)  WHERE deleted_at IS NOT NULL;

-- Flex attrs for files
CREATE TABLE file_flex_attrs (
    curator_id TEXT NOT NULL REFERENCES files(curator_id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (curator_id, key)
);

-- ============================================================================
-- Bundles
-- ============================================================================

CREATE TABLE bundles (
    bundle_id   TEXT PRIMARY KEY,
    bundle_type TEXT NOT NULL,
    name        TEXT,
    description TEXT,
    confidence  REAL NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bundle_memberships (
    bundle_id  TEXT NOT NULL REFERENCES bundles(bundle_id) ON DELETE CASCADE,
    curator_id TEXT NOT NULL REFERENCES files(curator_id)  ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'member',
    confidence REAL NOT NULL DEFAULT 1.0,
    added_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bundle_id, curator_id)
);

CREATE INDEX idx_bundle_memberships_file ON bundle_memberships(curator_id);

CREATE TABLE bundle_flex_attrs (
    bundle_id  TEXT NOT NULL REFERENCES bundles(bundle_id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (bundle_id, key)
);

-- ============================================================================
-- Lineage edges
-- ============================================================================

CREATE TABLE lineage_edges (
    edge_id          TEXT PRIMARY KEY,
    from_curator_id  TEXT NOT NULL REFERENCES files(curator_id) ON DELETE CASCADE,
    to_curator_id    TEXT NOT NULL REFERENCES files(curator_id) ON DELETE CASCADE,
    edge_kind        TEXT NOT NULL,
    confidence       REAL NOT NULL,
    detected_by      TEXT NOT NULL,
    detected_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes            TEXT,
    UNIQUE (from_curator_id, to_curator_id, edge_kind, detected_by)
);

CREATE INDEX idx_lineage_from ON lineage_edges(from_curator_id);
CREATE INDEX idx_lineage_to   ON lineage_edges(to_curator_id);
CREATE INDEX idx_lineage_kind ON lineage_edges(edge_kind);

-- ============================================================================
-- Trash registry
-- ============================================================================

CREATE TABLE trash_registry (
    curator_id                          TEXT PRIMARY KEY REFERENCES files(curator_id) ON DELETE CASCADE,
    original_source_id                  TEXT NOT NULL,
    original_path                       TEXT NOT NULL,
    file_hash                           TEXT,
    trashed_at                          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trashed_by                          TEXT NOT NULL,
    reason                              TEXT NOT NULL,
    bundle_memberships_snapshot_json    TEXT NOT NULL DEFAULT '[]',
    file_attrs_snapshot_json            TEXT NOT NULL DEFAULT '{}',
    os_trash_location                   TEXT,
    restore_path_override               TEXT
);

-- ============================================================================
-- Audit log (append-only)
-- ============================================================================

CREATE TABLE audit_log (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    entity_type  TEXT,
    entity_id    TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_audit_time   ON audit_log(occurred_at);
CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_action ON audit_log(action);

-- ============================================================================
-- Hash cache (DESIGN §4.3 / §7.3)
-- ============================================================================

CREATE TABLE hash_cache (
    source_id    TEXT NOT NULL,
    source_path  TEXT NOT NULL,
    mtime        TIMESTAMP NOT NULL,
    size         INTEGER NOT NULL,
    xxhash3_128  TEXT,
    md5          TEXT,
    fuzzy_hash   TEXT,
    computed_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_id, source_path)
);

-- ============================================================================
-- Scan jobs
-- ============================================================================

CREATE TABLE scan_jobs (
    job_id        TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    source_id     TEXT,
    root_path     TEXT,
    options_json  TEXT NOT NULL DEFAULT '{}',
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP,
    files_seen    INTEGER NOT NULL DEFAULT 0,
    files_hashed  INTEGER NOT NULL DEFAULT 0,
    error         TEXT
);

CREATE INDEX idx_scan_jobs_status ON scan_jobs(status);
