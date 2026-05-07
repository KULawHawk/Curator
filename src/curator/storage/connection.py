"""SQLite connection management for Curator.

DESIGN.md §4.2.

Key decisions:
  * Phase Alpha uses stdlib ``sqlite3`` (Phase Beta may switch to APSW for FTS5).
  * One connection per thread (sqlite3's threading model).
  * WAL journal mode for concurrent readers + a single writer.
  * Foreign keys are enforced (PRAGMA foreign_keys = ON).
  * UUIDs are stored as TEXT; conversion happens at the repository boundary.
  * Datetimes are stored as TIMESTAMP; we register custom adapters because
    Python 3.12+ deprecated the default datetime adapters.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Type adapters (Python <-> SQLite)
# ---------------------------------------------------------------------------
#
# Python 3.12 deprecated the default datetime adapters/converters that
# ``detect_types=PARSE_DECLTYPES`` relied on. We register our own so behavior
# is consistent across 3.11, 3.12, 3.13.
#
# Convention: store datetimes in UTC ISO-8601 with microseconds.

def _adapt_datetime(dt: datetime) -> str:
    """Convert datetime -> string for storage.

    If the datetime is naive, we treat it as UTC (Curator's convention).
    If it's aware, we convert to UTC before formatting.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(sep=" ", timespec="microseconds")


def _convert_timestamp(value: bytes) -> datetime:
    """Convert string from storage -> datetime.

    Returns naive datetimes (UTC by convention). Callers that need timezone
    awareness can attach ``timezone.utc``.
    """
    s = value.decode("ascii") if isinstance(value, (bytes, bytearray)) else str(value)
    # Tolerate both 'T' and ' ' separators, and optional 'Z' suffix.
    s = s.replace("T", " ").rstrip("Z").rstrip()
    # Tolerate fewer microsecond digits.
    return datetime.fromisoformat(s)


def _register_adapters() -> None:
    """Register our datetime adapters/converters globally.

    Idempotent — safe to call multiple times.
    """
    sqlite3.register_adapter(datetime, _adapt_datetime)
    sqlite3.register_converter("TIMESTAMP", _convert_timestamp)


# Register adapters at import time so any sqlite3 connection benefits.
_register_adapters()


# ---------------------------------------------------------------------------
# CuratorDB
# ---------------------------------------------------------------------------

class CuratorDB:
    """Thread-safe SQLite connection manager.

    Each thread gets its own ``sqlite3.Connection``. Initialization (running
    migrations) happens once via :meth:`init`; subsequent connections from
    other threads skip that step.

    Usage::

        db = CuratorDB(Path("Curator.db"))
        db.init()  # runs migrations
        with db.conn() as conn:  # transaction context
            conn.execute("INSERT INTO ...")
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False

    # ---- lifecycle ----

    def init(self) -> None:
        """Apply migrations to bring schema up to date. Idempotent across threads."""
        with self._init_lock:
            if self._initialized:
                return
            # Ensure parent directory exists.
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # Local import to avoid a circular dependency at module load time
            # (migrations imports nothing from this module, but keeps imports tidy).
            from curator.storage.migrations import apply_migrations

            apply_migrations(self.conn())
            self._initialized = True

    def close_thread_connection(self) -> None:
        """Close this thread's connection (e.g. on graceful shutdown)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            del self._local.conn

    # ---- connection access ----

    def conn(self) -> sqlite3.Connection:
        """Return the current thread's connection, creating it if needed."""
        if not hasattr(self._local, "conn"):
            self._local.conn = self._make_connection()
        return self._local.conn

    def _make_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            # We manage threading ourselves (one Connection per thread, never
            # shared). check_same_thread=False is required so we can pass the
            # connection to the threading.local container without complaints.
            check_same_thread=False,
            isolation_level="DEFERRED",  # explicit transaction control via "with conn"
        )
        conn.row_factory = sqlite3.Row
        # PRAGMAs that must be set on every connection (some are per-connection,
        # some are per-database — we set both for clarity).
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")  # WAL-safe and faster than FULL
        conn.execute("PRAGMA temp_store = MEMORY;")
        return conn

    # ---- convenience helpers ----

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Execute a single statement on this thread's connection.

        Auto-commit only happens within a ``with conn:`` block, so this is
        for queries (SELECT) primarily. For mutations, use ``with db.conn() as conn:``.
        """
        return self.conn().execute(sql, params)

    def __repr__(self) -> str:  # pragma: no cover
        return f"CuratorDB(db_path={self.db_path!r})"
