"""Tests for v1.7.57: forecast.py coverage lift (Tier 3).

Backstory: v1.7.51's coverage baseline showed forecast.py at 29.46%.
v1.7.55 (pii_scanner) and v1.7.56 (metadata_stripper) closed the two
weaker modules; this ship targets the third.

The module computes drive-capacity forecasts by linear-fitting monthly
file-ingestion buckets against drive usage. Tests cover:

  * **MonthlyBucket** dataclass (TestMonthlyBucket) -- gb_added property
  * **_linear_fit** pure helper (TestLinearFit) -- boundary cases for
    the least-squares regression
  * **ForecastService.compute_disk_forecast** branches:
    - past_99pct: drive already at critical
    - insufficient_data: <2 months of history
    - no_growth: slope <= 0 after fit
    - past_95pct: past warning, projecting to critical
    - fit_ok: normal projection
  * **ForecastService.compute_all_drives** (TestComputeAllDrives) -- via
    monkeypatched psutil.disk_partitions
  * **_monthly_history** DB query path

Strategy: use the conftest ``db`` fixture for the DB. Monkeypatch
``psutil.disk_usage`` to feed in controlled disk-usage values; the
real disk state of the test machine would make tests non-deterministic.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from curator.services.forecast import (
    DiskForecast,
    ForecastService,
    MonthlyBucket,
    _linear_fit,
)


# ---------------------------------------------------------------------------
# MonthlyBucket dataclass
# ---------------------------------------------------------------------------


class TestMonthlyBucket:
    def test_gb_added_conversion(self):
        # 1 GB in bytes: 1024^3
        b = MonthlyBucket(month="2025-01", file_count=10, bytes_added=1024**3)
        assert b.gb_added == 1.0

    def test_gb_added_partial(self):
        b = MonthlyBucket(month="2025-01", file_count=1, bytes_added=512 * 1024 * 1024)
        assert abs(b.gb_added - 0.5) < 0.001

    def test_gb_added_zero(self):
        b = MonthlyBucket(month="2025-01", file_count=0, bytes_added=0)
        assert b.gb_added == 0.0


# ---------------------------------------------------------------------------
# _linear_fit pure helper
# ---------------------------------------------------------------------------


class TestLinearFit:
    def test_raises_on_empty_history(self):
        with pytest.raises(ValueError, match="at least 2"):
            _linear_fit([])

    def test_raises_on_single_bucket(self):
        h = [MonthlyBucket("2025-01", 1, 1024**3)]
        with pytest.raises(ValueError, match="at least 2"):
            _linear_fit(h)

    def test_two_buckets_constant_growth(self):
        # 1 GB added per month for 2 months -> slope ~ 1/30 GB/day
        h = [
            MonthlyBucket("2025-01", 1, 1024**3),
            MonthlyBucket("2025-02", 1, 1024**3),
        ]
        slope, intercept, r_sq = _linear_fit(h)
        # With only 2 points the fit is exact -> r_sq == 1.0
        assert r_sq == pytest.approx(1.0, abs=1e-6)
        # Slope should be positive (growth)
        assert slope > 0
        # ~1 GB / 30 days for the second-month delta
        assert slope == pytest.approx(1.0 / 30, abs=0.01)

    def test_perfectly_linear_growth(self):
        # 5 buckets, 2 GB each month: cumulative 2, 4, 6, 8, 10
        h = [
            MonthlyBucket(f"2025-{m:02d}", 10, 2 * 1024**3)
            for m in range(1, 6)
        ]
        slope, intercept, r_sq = _linear_fit(h)
        # Near-perfect fit (small deviation from variable month lengths:
        # Jan/Mar/May = 31 days, Feb = 28, Apr = 30; cumulative GB grows
        # by exact 2 each bucket but x-spacing is uneven)
        assert r_sq > 0.99
        # Slope is positive and finite
        assert slope > 0

    def test_zero_growth_history(self):
        # All buckets contribute 0 GB; cumulative stays at 0
        h = [
            MonthlyBucket("2025-01", 5, 0),
            MonthlyBucket("2025-02", 5, 0),
            MonthlyBucket("2025-03", 5, 0),
        ]
        slope, intercept, r_sq = _linear_fit(h)
        # Slope should be 0 (no change)
        assert slope == pytest.approx(0.0, abs=1e-9)

    def test_negative_growth(self):
        # Mathematically valid but unusual; slope can be negative if
        # later buckets contribute LESS cumulative growth. Not realistic
        # for file ingestion (cumulative only grows), but lock the
        # math behavior.
        h = [
            MonthlyBucket("2025-01", 1, 5 * 1024**3),  # +5 GB
            MonthlyBucket("2025-02", 1, 0),
            MonthlyBucket("2025-03", 1, 0),
        ]
        slope, _, _ = _linear_fit(h)
        # Slope is positive (cumulative GB increases at month 1, then plateaus)
        # but small
        assert slope >= 0  # cumulative can't go down here


# ---------------------------------------------------------------------------
# ForecastService.compute_disk_forecast branches
# ---------------------------------------------------------------------------


def _seed_files(db, *, month_buckets: list[tuple[str, int, int]]) -> None:
    """Insert file rows into the DB to fake monthly history.

    month_buckets: list of (month_iso, file_count, total_bytes).
    Inserts file_count rows per month, each with size=total_bytes/file_count.

    Also inserts a source row first (FK constraint on files.source_id).
    """
    # Insert source (FK requirement). INSERT OR IGNORE in case called twice.
    db.execute(
        """INSERT OR IGNORE INTO sources (
            source_id, source_type, display_name, config_json, enabled, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        ("test_source", "local", "Test", "{}", 1, "2025-01-01T00:00:00"),
    )

    file_id = 1
    for month, file_count, total_bytes in month_buckets:
        per_file_size = total_bytes // file_count if file_count else 0
        # seen_at: 15th of the month (any day in the month works for the
        # year-month bucket query)
        seen_at = f"{month}-15T12:00:00"
        for _ in range(file_count):
            db.execute(
                """INSERT INTO files (
                    curator_id, source_id, source_path, size, mtime, seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    f"file-{file_id:06d}",
                    "test_source",
                    f"/test/{month}/{file_id}.txt",
                    per_file_size,
                    1700000000.0,
                    seen_at,
                ),
            )
            file_id += 1


def _mk_usage(used_bytes: int, total_bytes: int, free_bytes: int):
    """Build a psutil.disk_usage-like return value."""
    class _Usage:
        def __init__(self, used, total, free):
            self.used = used
            self.total = total
            self.free = free
    return _Usage(used_bytes, total_bytes, free_bytes)


class TestComputeDiskForecast:
    """v1.7.57: all five status branches in compute_disk_forecast."""

    def test_past_critical_short_circuits(self, db):
        """Drive >= 99% full -> past_99pct status, no fit attempted."""
        usage = _mk_usage(
            used_bytes=int(99.5 * 1024**3),  # 99.5 GB used
            total_bytes=100 * 1024**3,       # 100 GB total
            free_bytes=int(0.5 * 1024**3),
        )
        svc = ForecastService(db)
        with patch("curator.services.forecast.psutil.disk_usage", return_value=usage):
            forecast = svc.compute_disk_forecast("C:\\")

        assert forecast.status == "past_99pct"
        assert forecast.slope_gb_per_day is None
        assert forecast.days_to_95pct is None
        assert forecast.days_to_99pct is None
        assert "99.5%" in forecast.status_message or "99.5 %" in forecast.status_message
        # current_pct populated
        assert forecast.current_pct >= 99.0

    def test_insufficient_data_with_no_history(self, db):
        """No file history -> insufficient_data status."""
        usage = _mk_usage(50 * 1024**3, 100 * 1024**3, 50 * 1024**3)
        svc = ForecastService(db)
        with patch("curator.services.forecast.psutil.disk_usage", return_value=usage):
            forecast = svc.compute_disk_forecast("C:\\")

        assert forecast.status == "insufficient_data"
        assert forecast.slope_gb_per_day is None
        # Message tells the user what to do
        assert "month" in forecast.status_message.lower()

    def test_insufficient_data_with_one_month(self, db):
        """Only one month of history -> insufficient_data status."""
        _seed_files(db, month_buckets=[("2025-01", 10, 5 * 1024**3)])
        usage = _mk_usage(50 * 1024**3, 100 * 1024**3, 50 * 1024**3)
        svc = ForecastService(db)
        with patch("curator.services.forecast.psutil.disk_usage", return_value=usage):
            forecast = svc.compute_disk_forecast("C:\\")
        assert forecast.status == "insufficient_data"
        assert len(forecast.monthly_history) == 1

    def test_no_growth_status(self, db):
        """slope <= 0 after fit -> no_growth status."""
        # Seed buckets where cumulative GB doesn't grow:
        # month1: 5 GB, then 0 thereafter -- but cumulative is monotonic.
        # To get slope=0, all months need same cumulative -> seed 0-bytes
        _seed_files(db, month_buckets=[
            ("2025-01", 1, 0),
            ("2025-02", 1, 0),
            ("2025-03", 1, 0),
        ])
        usage = _mk_usage(50 * 1024**3, 100 * 1024**3, 50 * 1024**3)
        svc = ForecastService(db)
        with patch("curator.services.forecast.psutil.disk_usage", return_value=usage):
            forecast = svc.compute_disk_forecast("C:\\")

        assert forecast.status == "no_growth"
        # slope reported but no projections
        assert forecast.days_to_95pct is None
        assert forecast.days_to_99pct is None

    def test_fit_ok_branch(self, db):
        """Normal projection: drive not yet past 95%, slope > 0."""
        # Seed steady growth of 1 GB per month over 5 months
        _seed_files(db, month_buckets=[
            (f"2025-{m:02d}", 5, 1 * 1024**3) for m in range(1, 6)
        ])
        usage = _mk_usage(
            used_bytes=50 * 1024**3,  # 50% used
            total_bytes=100 * 1024**3,
            free_bytes=50 * 1024**3,
        )
        svc = ForecastService(db)
        with patch("curator.services.forecast.psutil.disk_usage", return_value=usage):
            forecast = svc.compute_disk_forecast("C:\\")

        assert forecast.status == "fit_ok"
        assert forecast.slope_gb_per_day > 0
        assert forecast.fit_r_squared is not None
        assert forecast.days_to_95pct > 0
        assert forecast.days_to_99pct > 0
        assert forecast.eta_95pct is not None
        # ETA in the future
        assert forecast.eta_95pct > datetime.now()
        # Message mentions both thresholds
        assert "95" in forecast.status_message
        assert "99" in forecast.status_message

    def test_past_95_but_not_99_branch(self, db):
        """Drive past warning threshold but not critical -> past_95pct."""
        _seed_files(db, month_buckets=[
            (f"2025-{m:02d}", 5, 1 * 1024**3) for m in range(1, 6)
        ])
        usage = _mk_usage(
            used_bytes=int(96 * 1024**3),  # 96% used, between WARN and CRITICAL
            total_bytes=100 * 1024**3,
            free_bytes=int(4 * 1024**3),
        )
        svc = ForecastService(db)
        with patch("curator.services.forecast.psutil.disk_usage", return_value=usage):
            forecast = svc.compute_disk_forecast("C:\\")

        assert forecast.status == "past_95pct"
        assert forecast.days_to_95pct == 0  # already past
        # eta_95pct set to now (already crossed)
        assert forecast.eta_95pct is not None
        # Days-to-critical should be finite and small (only 3 GB to go)
        assert forecast.days_to_99pct >= 0

    def test_current_metrics_populated(self, db):
        """The current_used/total/free/pct fields are always populated."""
        usage = _mk_usage(40 * 1024**3, 100 * 1024**3, 60 * 1024**3)
        svc = ForecastService(db)
        with patch("curator.services.forecast.psutil.disk_usage", return_value=usage):
            forecast = svc.compute_disk_forecast("C:\\")

        assert forecast.current_used_gb == pytest.approx(40.0, abs=0.1)
        assert forecast.current_total_gb == pytest.approx(100.0, abs=0.1)
        assert forecast.current_free_gb == pytest.approx(60.0, abs=0.1)
        assert forecast.current_pct == pytest.approx(40.0, abs=0.1)

    def test_zero_total_size_handled(self, db):
        """If usage.total is 0, pct calc should not crash."""
        usage = _mk_usage(0, 0, 0)
        svc = ForecastService(db)
        with patch("curator.services.forecast.psutil.disk_usage", return_value=usage):
            forecast = svc.compute_disk_forecast("C:\\")
        # No division by zero; current_pct should be 0
        assert forecast.current_pct == 0.0


# ---------------------------------------------------------------------------
# ForecastService.compute_all_drives
# ---------------------------------------------------------------------------


class _Partition:
    """Fake psutil partition object."""
    def __init__(self, mountpoint: str, fstype: str = "NTFS"):
        self.mountpoint = mountpoint
        self.fstype = fstype


class TestComputeAllDrives:
    def test_aggregates_all_partitions(self, db):
        """Iterates psutil.disk_partitions(all=False), forecasts each."""
        parts = [
            _Partition("C:\\", "NTFS"),
            _Partition("D:\\", "NTFS"),
        ]
        usage = _mk_usage(40 * 1024**3, 100 * 1024**3, 60 * 1024**3)
        svc = ForecastService(db)
        with patch(
            "curator.services.forecast.psutil.disk_partitions",
            return_value=parts,
        ), patch(
            "curator.services.forecast.psutil.disk_usage", return_value=usage,
        ):
            results = svc.compute_all_drives()

        assert len(results) == 2
        mountpoints = [r.drive_path for r in results]
        assert "C:\\" in mountpoints
        assert "D:\\" in mountpoints

    def test_skips_partitions_with_no_fstype(self, db):
        """Removable/optical drives often have empty fstype -> skipped."""
        parts = [
            _Partition("C:\\", "NTFS"),
            _Partition("E:\\", ""),  # no fstype = removable / not mounted
        ]
        usage = _mk_usage(40 * 1024**3, 100 * 1024**3, 60 * 1024**3)
        svc = ForecastService(db)
        with patch(
            "curator.services.forecast.psutil.disk_partitions",
            return_value=parts,
        ), patch(
            "curator.services.forecast.psutil.disk_usage", return_value=usage,
        ):
            results = svc.compute_all_drives()
        # E:\ should be skipped
        assert len(results) == 1
        assert results[0].drive_path == "C:\\"

    def test_swallows_per_drive_errors(self, db):
        """A drive that raises during forecast doesn't kill the whole walk."""
        parts = [
            _Partition("C:\\", "NTFS"),
            _Partition("Z:\\", "NTFS"),
        ]

        # First call succeeds, second raises
        usage_ok = _mk_usage(40 * 1024**3, 100 * 1024**3, 60 * 1024**3)
        call_count = {"n": 0}
        def _usage_side_effect(path):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return usage_ok
            raise PermissionError("Access denied")

        svc = ForecastService(db)
        with patch(
            "curator.services.forecast.psutil.disk_partitions",
            return_value=parts,
        ), patch(
            "curator.services.forecast.psutil.disk_usage",
            side_effect=_usage_side_effect,
        ):
            results = svc.compute_all_drives()
        # Only the first drive succeeded
        assert len(results) == 1


# ---------------------------------------------------------------------------
# _monthly_history DB query
# ---------------------------------------------------------------------------


class TestMonthlyHistory:
    def test_empty_db_returns_empty_list(self, db):
        svc = ForecastService(db)
        assert svc._monthly_history() == []

    def test_groups_files_by_month(self, db):
        _seed_files(db, month_buckets=[
            ("2025-01", 5, 1024**3),
            ("2025-01", 3, 512 * 1024 * 1024),  # same month -> aggregates
            ("2025-02", 4, 2 * 1024**3),
        ])
        svc = ForecastService(db)
        history = svc._monthly_history()
        # 2 unique months
        assert len(history) == 2
        # First month aggregates BOTH inserts
        jan = next(b for b in history if b.month == "2025-01")
        assert jan.file_count == 8  # 5 + 3
        # Total bytes ~ 1 GB + 0.5 GB = 1.5 GB
        assert jan.gb_added == pytest.approx(1.5, abs=0.01)

    def test_orders_by_month_ascending(self, db):
        _seed_files(db, month_buckets=[
            ("2025-03", 1, 1024**3),
            ("2025-01", 1, 1024**3),
            ("2025-02", 1, 1024**3),
        ])
        svc = ForecastService(db)
        history = svc._monthly_history()
        months = [b.month for b in history]
        assert months == sorted(months)
