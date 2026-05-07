"""Shared utilities used by repository implementations.

These are kept as module-level helpers (not a base class) because the
repositories don't share enough behavior to justify inheritance — they
each have entity-specific concerns. What they DO share is conversion
helpers (UUID, JSON, datetime) and the flex-attrs persistence pattern.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any
from uuid import UUID


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _json_default(obj: Any) -> Any:
    """Default serializer for json.dumps that handles UUID and datetime."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        # Match the format used by our datetime adapter (see connection.py).
        if obj.tzinfo is not None:
            from datetime import timezone

            obj = obj.astimezone(timezone.utc).replace(tzinfo=None)
        return obj.isoformat(sep=" ", timespec="microseconds")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def json_dumps(value: Any) -> str:
    """Serialize a value to JSON, handling UUID and datetime."""
    return json.dumps(value, default=_json_default)


def json_loads(value: str | bytes | None) -> Any:
    """Deserialize a JSON value, returning ``None`` for null/empty inputs."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if value == "":
        return None
    return json.loads(value)


# ---------------------------------------------------------------------------
# UUID helpers
# ---------------------------------------------------------------------------

def uuid_to_str(value: UUID | str | None) -> str | None:
    """Normalize a UUID-or-string to its hex string form."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def str_to_uuid(value: str | None) -> UUID | None:
    """Parse a UUID string; return None for None inputs."""
    if value is None:
        return None
    return UUID(value)


# ---------------------------------------------------------------------------
# Flex attrs persistence
# ---------------------------------------------------------------------------

def save_flex_attrs(
    conn: sqlite3.Connection,
    table: str,
    pk_column: str,
    pk_value: str,
    flex: dict[str, Any],
) -> None:
    """Persist a dict of flex attrs to a ``*_flex_attrs`` table.

    Uses INSERT OR REPLACE so this is idempotent — call it on both insert
    and update paths. If ``flex`` is empty, this is a no-op (existing rows
    are NOT cleared; use :func:`clear_flex_attrs` if you need that).

    Args:
        conn: live SQLite connection (caller controls transaction).
        table: companion table name, e.g. ``"file_flex_attrs"``.
        pk_column: foreign-key column, e.g. ``"curator_id"``.
        pk_value: foreign-key value (already stringified).
        flex: dict of attributes to persist.
    """
    if not flex:
        return
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({pk_column}, key, value_json) VALUES (?, ?, ?)",
        [(pk_value, k, json_dumps(v)) for k, v in flex.items()],
    )


def load_flex_attrs(
    conn: sqlite3.Connection,
    table: str,
    pk_column: str,
    pk_value: str,
) -> dict[str, Any]:
    """Load all flex attrs for a single entity."""
    cursor = conn.execute(
        f"SELECT key, value_json FROM {table} WHERE {pk_column} = ?",
        (pk_value,),
    )
    return {row["key"]: json_loads(row["value_json"]) for row in cursor.fetchall()}


def clear_flex_attrs(
    conn: sqlite3.Connection,
    table: str,
    pk_column: str,
    pk_value: str,
) -> None:
    """Delete all flex attrs for a single entity (used on full replace)."""
    conn.execute(f"DELETE FROM {table} WHERE {pk_column} = ?", (pk_value,))


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row into a dict, preserving None on missing rows."""
    return dict(row) if row is not None else None
