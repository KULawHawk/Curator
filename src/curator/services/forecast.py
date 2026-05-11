"""Forecasting service - predicts when local drives reach capacity.

DESIGN.md: not yet documented; T-B01 from docs/FEATURE_TODO.md.

The service:
    1. Aggregates ``files.size`` by month bucket using ``files.seen_at``
       to get a per-month "GB added to the index" signal.
    2. Linear-fits the monthly aggregates to compute a daily fill rate.
    3. Pulls current disk usage via psutil.disk_usage().
    4. Projects when the drive reaches 95% / 99% full at the current rate.

Caveats:
    - With <2 months of seen_at history, no fit is possible (returns None
      slope + appropriate status message). The fix is: keep using Curator
      and check back next month.
    - The index size != the drive's actual used space. Curator only knows
      about files it has indexed. The forecast assumes the indexed-growth
      rate is representative of total-drive-growth, which is a strong
      assumption.
    - "Already past threshold" surfaces as a useful signal (don't bother
      projecting; the drive is already in the warning zone).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from curator.storage.connection import CuratorDB


@dataclass
class MonthlyBucket:
    """One row of the monthly file-ingestion aggregate."""

    month: str  # 'YYYY-MM'
    file_count: int
    bytes_added: int

    @property
    def gb_added(self) -> float:
        return self.bytes_added / (1024 ** 3)


@dataclass
class DiskForecast:
    """Forecast result for a single drive.

    All GB values are gigabytes (1024**3 bytes), not gigabytes
    (1000**3).
    """

    drive_path: str
    current_used_gb: float
    current_total_gb: float
    current_pct: float
    current_free_gb: float

    # Fit (may be None if insufficient history)
    slope_gb_per_day: float | None
    fit_r_squared: float | None

    # Projections (None if no fit or already past threshold)
    days_to_95pct: int | None
    days_to_99pct: int | None
    eta_95pct: datetime | None
    eta_99pct: datetime | None

    # Status banner
    status: str  # 'insufficient_data' | 'past_95pct' | 'past_99pct' | 'fit_ok'
    status_message: str

    # Historical data for display
    monthly_history: list[MonthlyBucket] = field(default_factory=list)


class ForecastService:
    """Predict when local drives reach capacity.

    Reads ``files`` table to get historical indexing rate; combines with
    live ``psutil.disk_usage()`` to project days-to-95pct/99pct.
    """

    PCT_WARN = 95.0
    PCT_CRITICAL = 99.0

    def __init__(self, db: "CuratorDB") -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_disk_forecast(self, drive_path: str) -> DiskForecast:
        """Compute a forecast for a single drive path.

        ``drive_path`` should be a mount point: ``C:\\`` on Windows or
        ``/`` on Unix. The function uses :func:`psutil.disk_usage` for
        live state and the canonical DB for historical fill rate.
        """
        usage = psutil.disk_usage(drive_path)
        used_gb = usage.used / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        pct = 100.0 * usage.used / usage.total if usage.total else 0.0

        # Already past threshold? short-circuit
        if pct >= self.PCT_CRITICAL:
            return DiskForecast(
                drive_path=drive_path,
                current_used_gb=used_gb, current_total_gb=total_gb,
                current_pct=pct, current_free_gb=free_gb,
                slope_gb_per_day=None, fit_r_squared=None,
                days_to_95pct=None, days_to_99pct=None,
                eta_95pct=None, eta_99pct=None,
                status='past_99pct',
                status_message=(
                    f'Drive is already at {pct:.1f}% capacity '
                    f'(>= {self.PCT_CRITICAL:.0f}% critical). '
                    f'No projection needed - cleanup is urgent.'
                ),
                monthly_history=self._monthly_history(),
            )

        history = self._monthly_history()
        if len(history) < 2:
            return DiskForecast(
                drive_path=drive_path,
                current_used_gb=used_gb, current_total_gb=total_gb,
                current_pct=pct, current_free_gb=free_gb,
                slope_gb_per_day=None, fit_r_squared=None,
                days_to_95pct=None, days_to_99pct=None,
                eta_95pct=None, eta_99pct=None,
                status='insufficient_data',
                status_message=(
                    f'Only {len(history)} month(s) of indexing history. '
                    f'Need at least 2 months for a linear fit. '
                    f'Keep using Curator and check back next month.'
                ),
                monthly_history=history,
            )

        # Linear fit on monthly cumulative GB.
        # X = days-since-first-month-start, Y = cumulative GB indexed
        slope, intercept, r_sq = _linear_fit(history)

        # Compute projection - but only if slope > 0
        if slope <= 0:
            return DiskForecast(
                drive_path=drive_path,
                current_used_gb=used_gb, current_total_gb=total_gb,
                current_pct=pct, current_free_gb=free_gb,
                slope_gb_per_day=slope, fit_r_squared=r_sq,
                days_to_95pct=None, days_to_99pct=None,
                eta_95pct=None, eta_99pct=None,
                status='no_growth',
                status_message=(
                    f'No growth detected (slope = {slope:.3f} GB/day). '
                    f'Forecast not applicable.'
                ),
                monthly_history=history,
            )

        # GB remaining to reach each threshold
        gb_to_95 = (self.PCT_WARN / 100.0) * total_gb - used_gb
        gb_to_99 = (self.PCT_CRITICAL / 100.0) * total_gb - used_gb

        # Past 95 but not 99?
        if gb_to_95 <= 0:
            days_95 = 0
            eta_95 = datetime.now()
            status = 'past_95pct'
            msg = (
                f'Drive at {pct:.1f}% capacity - past {self.PCT_WARN:.0f}% '
                f'warning threshold. At current rate '
                f'({slope:.2f} GB/day), {self.PCT_CRITICAL:.0f}% critical '
                f'in '
            )
        else:
            days_95 = int(gb_to_95 / slope)
            eta_95 = datetime.now() + timedelta(days=days_95)
            status = 'fit_ok'
            msg = (
                f'Drive at {pct:.1f}% capacity. At current rate '
                f'({slope:.2f} GB/day), warning ({self.PCT_WARN:.0f}%) in '
            )

        days_99 = int(gb_to_99 / slope) if gb_to_99 > 0 else 0
        eta_99 = datetime.now() + timedelta(days=days_99) if days_99 > 0 else None

        if status == 'fit_ok':
            msg += f'{days_95} days, critical ({self.PCT_CRITICAL:.0f}%) in {days_99} days.'
        else:
            msg += f'{days_99} days.'

        return DiskForecast(
            drive_path=drive_path,
            current_used_gb=used_gb, current_total_gb=total_gb,
            current_pct=pct, current_free_gb=free_gb,
            slope_gb_per_day=slope, fit_r_squared=r_sq,
            days_to_95pct=days_95, days_to_99pct=days_99,
            eta_95pct=eta_95, eta_99pct=eta_99,
            status=status,
            status_message=msg,
            monthly_history=history,
        )

    def compute_all_drives(self) -> list[DiskForecast]:
        """Forecast every mounted fixed-disk partition.

        Skips removable/optical drives. On Windows, this typically yields
        C:\, D:\, etc. On Unix, the root ``/``.
        """
        results: list[DiskForecast] = []
        for part in psutil.disk_partitions(all=False):
            if not part.fstype:
                continue
            try:
                results.append(self.compute_disk_forecast(part.mountpoint))
            except Exception:  # noqa: BLE001 -- can't access drive
                continue
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _monthly_history(self) -> list[MonthlyBucket]:
        """Aggregate files.size by year-month bucket on seen_at."""
        cur = self.db.execute("""
            SELECT strftime('%Y-%m', seen_at) AS month,
                   COUNT(*) AS file_count,
                   SUM(size) AS bytes_added
            FROM files
            WHERE deleted_at IS NULL
              AND seen_at IS NOT NULL
            GROUP BY month
            ORDER BY month
        """)
        return [
            MonthlyBucket(
                month=row[0],
                file_count=row[1] or 0,
                bytes_added=row[2] or 0,
            )
            for row in cur
        ]


# ----------------------------------------------------------------------
# Pure-function helpers (no I/O; trivially testable)
# ----------------------------------------------------------------------

def _linear_fit(
    history: list[MonthlyBucket],
) -> tuple[float, float, float]:
    """Least-squares fit cumulative GB vs day-offset.

    Returns (slope_gb_per_day, intercept_gb, r_squared).
    """
    if len(history) < 2:
        raise ValueError('Need at least 2 buckets to fit')

    # X: days since the first month's start (treated as day 0)
    # Y: cumulative GB at end of that month
    from datetime import datetime as _dt

    first_month = history[0].month
    first_dt = _dt.strptime(first_month + '-01', '%Y-%m-%d')

    xs: list[float] = []
    ys: list[float] = []
    cumulative_gb = 0.0
    for b in history:
        # Treat each month's bucket as if it landed at the END of the month.
        # For a 30-day-month proxy: month_offset_days = months_since_first * 30
        month_dt = _dt.strptime(b.month + '-01', '%Y-%m-%d')
        days_offset = (month_dt - first_dt).days + 30
        cumulative_gb += b.gb_added
        xs.append(float(days_offset))
        ys.append(cumulative_gb)

    n = len(xs)
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xx = sum(x * x for x in xs)
    sum_xy = sum(x * y for x, y in zip(xs, ys))

    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        return 0.0, sum_y / n if n else 0.0, 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R^2
    mean_y = sum_y / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_sq = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0

    return slope, intercept, r_sq
