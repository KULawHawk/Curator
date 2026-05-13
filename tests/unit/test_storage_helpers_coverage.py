"""Coverage closure for ``curator.storage.repositories._helpers`` (v1.7.136).

Targets 16 lines + 6 partial branches across:
- ``_json_default`` UUID/datetime/TypeError arms
- ``json_loads`` None/bytes/empty-string arms
- ``uuid_to_str`` None/str/UUID arms
- ``str_to_uuid`` None arm
- ``clear_flex_attrs`` + ``row_to_dict``
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from curator.storage.connection import CuratorDB
from curator.storage.repositories._helpers import (
    _json_default,
    clear_flex_attrs,
    json_dumps,
    json_loads,
    load_flex_attrs,
    row_to_dict,
    save_flex_attrs,
    str_to_uuid,
    uuid_to_str,
)


class TestJsonDefault:
    def test_uuid_becomes_str(self):
        u = uuid4()
        assert _json_default(u) == str(u)

    def test_naive_datetime_isoformatted(self):
        dt = datetime(2026, 1, 15, 10, 30, 0)
        result = _json_default(dt)
        assert "2026-01-15" in result
        assert "10:30:00" in result

    def test_aware_datetime_converted_to_utc(self):
        eastern = timezone(timedelta(hours=5))
        dt = datetime(2026, 1, 15, 10, 0, 0, tzinfo=eastern)
        result = _json_default(dt)
        # 10:00 +05 -> 05:00 UTC
        assert "05:00:00" in result

    def test_unsupported_type_raises_type_error(self):
        with pytest.raises(TypeError, match="not JSON serializable"):
            _json_default(object())

    def test_json_dumps_round_trips_uuid_and_datetime(self):
        u = uuid4()
        dt = datetime(2026, 1, 15)
        s = json_dumps({"u": u, "dt": dt})
        # The values are stringified
        loaded = json.loads(s)
        assert loaded["u"] == str(u)
        assert "2026-01-15" in loaded["dt"]


class TestJsonLoads:
    def test_none_returns_none(self):
        assert json_loads(None) is None

    def test_bytes_input_decoded(self):
        assert json_loads(b'{"k": 1}') == {"k": 1}

    def test_bytearray_input_decoded(self):
        assert json_loads(bytearray(b'[1,2,3]')) == [1, 2, 3]

    def test_empty_string_returns_none(self):
        assert json_loads("") is None

    def test_str_input_decoded(self):
        assert json_loads('{"a": "b"}') == {"a": "b"}


class TestUuidHelpers:
    def test_uuid_to_str_none(self):
        assert uuid_to_str(None) is None

    def test_uuid_to_str_passthrough_string(self):
        s = "abc-not-actually-a-uuid"
        assert uuid_to_str(s) == s

    def test_uuid_to_str_real_uuid(self):
        u = uuid4()
        assert uuid_to_str(u) == str(u)

    def test_str_to_uuid_none(self):
        assert str_to_uuid(None) is None

    def test_str_to_uuid_valid(self):
        u = uuid4()
        assert str_to_uuid(str(u)) == u


class TestFlexAttrsAndRowToDict:
    """Light end-to-end against a real CuratorDB (uses the file_flex_attrs table)."""

    def test_clear_flex_attrs_removes_rows(self, tmp_path):
        db = CuratorDB(tmp_path / "fa.db")
        db.init()

        # Insert a source + a file first (FK chain)
        with db.conn() as conn:
            conn.execute(
                "INSERT INTO sources (source_id, source_type, display_name, "
                "config_json, enabled, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?)",
                ("local", "local", "Local", "{}", 1, datetime(2026, 1, 1)),
            )
            curator_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO files (curator_id, source_id, source_path, size, mtime,
                    seen_at, last_scanned_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (curator_id, "local", "/x", 1, datetime(2026, 1, 1),
                 datetime(2026, 1, 1), datetime(2026, 1, 1), "active"),
            )

        # Save some flex attrs
        with db.conn() as conn:
            save_flex_attrs(conn, "file_flex_attrs", "curator_id", curator_id,
                            {"k": "v", "n": 42})

        with db.conn() as conn:
            loaded = load_flex_attrs(conn, "file_flex_attrs", "curator_id", curator_id)
        assert loaded == {"k": "v", "n": 42}

        # Clear
        with db.conn() as conn:
            clear_flex_attrs(conn, "file_flex_attrs", "curator_id", curator_id)

        with db.conn() as conn:
            assert load_flex_attrs(
                conn, "file_flex_attrs", "curator_id", curator_id,
            ) == {}

    def test_save_flex_attrs_empty_is_noop(self, tmp_path):
        db = CuratorDB(tmp_path / "fa2.db")
        db.init()
        with db.conn() as conn:
            # No-op shortcut for empty dict
            save_flex_attrs(conn, "file_flex_attrs", "curator_id", "any", {})
            # No exception => pass


class TestRowToDict:
    def test_row_to_dict_with_row(self, tmp_path):
        db = CuratorDB(tmp_path / "rd.db")
        db.init()
        with db.conn() as conn:
            conn.execute(
                "INSERT INTO sources (source_id, source_type, display_name, "
                "config_json, enabled, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?)",
                ("local", "local", "Local", "{}", 1, datetime(2026, 1, 1)),
            )
        cursor = db.conn().execute(
            "SELECT * FROM sources WHERE source_id = ?", ("local",),
        )
        row = cursor.fetchone()
        d = row_to_dict(row)
        assert d is not None
        assert d["source_id"] == "local"

    def test_row_to_dict_none(self):
        assert row_to_dict(None) is None
