"""Schema migration runner.

DESIGN.md §4.4.

Migrations are simple ``Callable[[sqlite3.Connection], None]`` functions
registered in the :data:`MIGRATIONS` list. Each migration is identified by
a unique name and applied in declaration order. Already-applied migrations
are tracked in the ``schema_versions`` table.

Adding a migration:
    1. Define a function ``def migration_NNN_description(conn): ...``
    2. Append ``("NNN_description", migration_NNN_description)`` to ``MIGRATIONS``.
    3. NEVER reorder or remove existing entries; only append.

Each migration runs inside a transaction. If it raises, the transaction
rolls back and the migration is NOT marked as applied.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

MigrationFunc = Callable[[sqlite3.Connection], None]


# Path to the SQL file containing the initial schema.
_SCHEMA_V1_SQL = Path(__file__).parent / "schema_v1.sql"


def migration_001_initial(conn: sqlite3.Connection) -> None:
    """Initial schema (DESIGN.md §4.3).

    Loaded from ``schema_v1.sql`` rather than embedded inline so the SQL
    is reviewable as a normal SQL file.
    """
    sql = _SCHEMA_V1_SQL.read_text(encoding="utf-8")
    conn.executescript(sql)


def migration_002_migration_jobs_and_progress(conn: sqlite3.Connection) -> None:
    """Add ``migration_jobs`` and ``migration_progress`` tables for Tracer
    Phase 2 (resumable, worker-pool-able, GUI-trackable migrations).

    See ``docs/TRACER_PHASE_2_DESIGN.md`` §4 for the full schema rationale.

    Phase 1 (v1.1.0a1) doesn't use these tables; it executes plans
    in-memory and returns a transient :class:`MigrationReport`. Phase 2's
    job-based path persists the plan as ``migration_jobs`` row + N
    ``migration_progress`` rows so workers can pick them up, the user
    can ``--resume`` after an interruption, and the GUI can show live
    progress.

    Both tables are empty after this migration; rows are populated
    only when ``MigrationService.create_job`` is called by Phase 2 code.
    """
    conn.executescript(
        """
        CREATE TABLE migration_jobs (
            job_id TEXT PRIMARY KEY,
            src_source_id TEXT NOT NULL,
            src_root TEXT NOT NULL,
            dst_source_id TEXT NOT NULL,
            dst_root TEXT NOT NULL,
            status TEXT NOT NULL,
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

        CREATE INDEX idx_migration_jobs_status
            ON migration_jobs(status);
        CREATE INDEX idx_migration_jobs_started_at
            ON migration_jobs(started_at DESC);

        CREATE TABLE migration_progress (
            job_id TEXT NOT NULL
                REFERENCES migration_jobs(job_id) ON DELETE CASCADE,
            curator_id TEXT NOT NULL,
            src_path TEXT NOT NULL,
            dst_path TEXT NOT NULL,
            src_xxhash TEXT,
            verified_xxhash TEXT,
            size INTEGER NOT NULL DEFAULT 0,
            safety_level TEXT NOT NULL,
            status TEXT NOT NULL,
            outcome TEXT,
            error TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            PRIMARY KEY (job_id, curator_id)
        );

        CREATE INDEX idx_migration_progress_status
            ON migration_progress(job_id, status);
        """
    )


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------
#
# Append-only. Order matters. Names must be globally unique.

MIGRATIONS: list[tuple[str, MigrationFunc]] = [
    ("001_initial", migration_001_initial),
    ("002_migration_jobs_and_progress", migration_002_migration_jobs_and_progress),
]


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply all pending migrations. Idempotent.

    Creates the ``schema_versions`` table if it doesn't exist (so the
    very first run can record migration 001). Each migration runs in a
    transaction; if it raises, we re-raise with context.
    """
    # Bootstrap: schema_versions must exist before we can record applied migrations.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_versions (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()

    cursor = conn.execute("SELECT name FROM schema_versions")
    applied = {row[0] for row in cursor.fetchall()}

    for name, func in MIGRATIONS:
        if name in applied:
            continue
        try:
            with conn:  # transaction
                func(conn)
                conn.execute(
                    "INSERT INTO schema_versions(name) VALUES (?)",
                    (name,),
                )
        except Exception as e:
            raise RuntimeError(f"Migration {name!r} failed: {e}") from e


def applied_migrations(conn: sqlite3.Connection) -> list[str]:
    """Return list of applied migration names (in application order)."""
    cursor = conn.execute(
        "SELECT name FROM schema_versions ORDER BY applied_at, name"
    )
    return [row[0] for row in cursor.fetchall()]
