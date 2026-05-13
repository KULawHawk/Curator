"""Focused coverage tests for services/forecast.py.

Sub-ship v1.7.95 of the Coverage Sweep arc.
Scope plan: docs/COVERAGE_SWEEP_SCOPE.md

Closes the one uncovered statement + one partial branch in
`_linear_fit` — specifically the `denom == 0` fallback path that
fires when all month buckets have the same date offset (e.g. two
buckets in the same month, which produces identical xs and therefore
a zero denominator in the least-squares slope formula).

Companion source change: the `if n else 0.0` defensive guard inside
that return was provably unreachable (n >= 2 enforced upstream) and
has been removed for honesty per doctrine item 1.

Stub-free; `_linear_fit` is a pure helper.
"""

from __future__ import annotations

from curator.services.forecast import MonthlyBucket, _linear_fit


def test_linear_fit_denom_zero_returns_zero_slope():
    # Lines 294-299: when all xs are identical (two buckets in the
    # same month → both days_offset = 30 → denom = n*sum_xx - sum_x^2
    # = 2*1800 - 60^2 = 0), the function returns (0.0, sum_y/n, 0.0)
    # without attempting the division by `denom`.
    history = [
        MonthlyBucket(month="2026-01", file_count=10, bytes_added=1_000_000_000),
        MonthlyBucket(month="2026-01", file_count=20, bytes_added=2_000_000_000),
    ]

    slope, intercept, r_sq = _linear_fit(history)

    assert slope == 0.0
    assert r_sq == 0.0
    # intercept = (sum of cumulative GB values) / n. First bucket adds
    # ~0.931 GB cumulative; second adds another ~1.862 GB cumulative.
    # The exact value isn't load-bearing for this test — we care that
    # the function returned cleanly without ZeroDivisionError.
    assert intercept > 0.0
