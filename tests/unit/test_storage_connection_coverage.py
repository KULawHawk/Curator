"""Coverage closure for ``curator.storage.connection`` (v1.7.127).

Targets the 3 missing branches:
- Line 41: ``_adapt_datetime`` tz-aware branch (converts to UTC + strips tzinfo)
- Line 102: ``CuratorDB.init`` early-return when already initialized
- Line 115->exit: ``close_thread_connection`` when no connection exists for thread
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from curator.storage.connection import (
    CuratorDB,
    _adapt_datetime,
    _convert_timestamp,
)


class TestAdaptDatetimeAware:
    def test_aware_datetime_converted_to_utc_iso(self):
        """Line 41: aware datetime is converted to UTC and stripped of tzinfo
        before ISO-format."""
        # 10:00 in UTC+5 -> 05:00 UTC
        eastern = timezone(timedelta(hours=5))
        dt = datetime(2026, 1, 15, 10, 0, 0, tzinfo=eastern)
        result = _adapt_datetime(dt)
        # Should be ISO at 05:00 UTC (no timezone suffix; convention is naive UTC)
        assert "05:00:00" in result
        # And not the original 10:00
        assert "10:00:00" not in result

    def test_naive_datetime_unchanged(self):
        """Naive datetimes pass through (tzinfo branch is skipped)."""
        dt = datetime(2026, 1, 15, 10, 0, 0)
        result = _adapt_datetime(dt)
        assert "10:00:00" in result


class TestConvertTimestamp:
    """Sanity coverage for ``_convert_timestamp`` (not in the miss list but
    exercised here for round-trip symmetry)."""

    def test_round_trip(self):
        dt = datetime(2026, 1, 15, 10, 30, 45, 123456)
        stored = _adapt_datetime(dt)
        back = _convert_timestamp(stored.encode("ascii"))
        assert back == dt

    def test_str_input(self):
        # The function tolerates str input (not just bytes).
        back = _convert_timestamp("2026-01-15T10:30:45")
        assert back == datetime(2026, 1, 15, 10, 30, 45)


class TestInitIdempotent:
    def test_init_returns_early_when_already_initialized(self, tmp_path):
        """Line 102: second ``init()`` call short-circuits."""
        db_path = tmp_path / "test.db"
        db = CuratorDB(db_path)
        db.init()
        assert db._initialized is True

        # Second init: must not re-run migrations. We assert by setting a
        # marker that would be cleared if apply_migrations ran again
        # (apply_migrations itself is idempotent so we can't catch it by
        # state; instead we patch the import to raise if called again).
        import curator.storage.migrations as mig_mod
        original = mig_mod.apply_migrations

        def _raise(_conn):
            raise AssertionError(
                "apply_migrations should not be called on second init()"
            )

        mig_mod.apply_migrations = _raise
        try:
            db.init()  # must short-circuit before importing
        finally:
            mig_mod.apply_migrations = original


class TestCloseThreadConnection:
    def test_close_with_no_connection_is_noop(self, tmp_path):
        """Line 115->exit: ``close_thread_connection`` is safe to call
        before any ``conn()`` access."""
        db_path = tmp_path / "test.db"
        db = CuratorDB(db_path)
        # No connection has been created for this thread yet.
        db.close_thread_connection()  # must not raise

    def test_close_after_conn_clears_thread_local(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = CuratorDB(db_path)
        db.init()
        _ = db.conn()
        assert hasattr(db._local, "conn")
        db.close_thread_connection()
        assert not hasattr(db._local, "conn")
