"""Tests for v1.7.47 datetime compatibility shim.

The :mod:`curator._compat.datetime` module provides :func:`utcnow_naive`
as a drop-in replacement for ``datetime.utcnow()`` (deprecated in Python
3.12, removed in 3.14). These tests verify the contract:

  * Returns a ``datetime`` instance
  * The datetime is *naive* (tzinfo is None) -- matches the historical
    behavior of ``datetime.utcnow()``
  * The value is approximately "now" in UTC -- not local time, not
    yesterday's cached value
  * Repeated calls produce monotonically non-decreasing values
  * Does NOT emit a DeprecationWarning (the whole point of the helper)

The tests do NOT cover the future migration path to timezone-aware
datetimes -- that's a separate ship documented in the module's docstring.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timezone, timedelta

import pytest

from curator._compat.datetime import utcnow_naive


class TestUtcnowNaive:
    """v1.7.47: contract tests for the datetime.utcnow() replacement."""

    def test_returns_datetime_instance(self):
        """v1.7.47: utcnow_naive() returns a real datetime."""
        result = utcnow_naive()
        assert isinstance(result, datetime)

    def test_returned_datetime_is_naive(self):
        """v1.7.47: tzinfo is None -- matches deprecated utcnow() behavior.

        Critical: the whole point of this helper (vs migrating to
        timezone.utc) is to preserve the naive-output semantics. If
        this regresses, callers that do naive-vs-naive comparisons
        will start raising TypeError.
        """
        result = utcnow_naive()
        assert result.tzinfo is None, (
            f"utcnow_naive() must return a naive datetime; "
            f"got tzinfo={result.tzinfo!r}"
        )

    def test_value_is_approximately_now_in_utc(self):
        """v1.7.47: returned value is within 5 seconds of stdlib's UTC now."""
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        result = utcnow_naive()
        after = datetime.now(timezone.utc).replace(tzinfo=None)
        # Result should fall in the [before, after] window (with leeway)
        delta = timedelta(seconds=5)
        assert before - delta <= result <= after + delta, (
            f"utcnow_naive()={result!r} not within "
            f"[{before!r}, {after!r}] +- 5s"
        )

    def test_value_is_not_local_time(self):
        """v1.7.47: returned value is UTC, not local time.

        On any machine where local != UTC (i.e. most of them), the
        wall-clock local time differs from UTC by the local offset.
        Subtle bug: if utcnow_naive() accidentally used
        datetime.now() instead of datetime.now(timezone.utc), the
        local timezone offset would be silently applied.

        Check: utcnow_naive() should be CLOSER to
        datetime.now(timezone.utc).replace(tzinfo=None) than to
        datetime.now() (local naive).
        """
        local_naive = datetime.now()
        utc_naive_via_stdlib = datetime.now(timezone.utc).replace(tzinfo=None)
        result = utcnow_naive()

        delta_to_local = abs((result - local_naive).total_seconds())
        delta_to_utc = abs((result - utc_naive_via_stdlib).total_seconds())

        # On a UTC machine, both deltas would be tiny -- in that case the
        # test passes trivially. On non-UTC, utcnow_naive() should hug
        # the UTC reference, not the local one.
        # If they're within a few hours, we're probably on a UTC machine;
        # the substantive check is "no worse than local".
        assert delta_to_utc <= delta_to_local + 1, (
            f"utcnow_naive() drifted to local time: "
            f"delta_to_utc={delta_to_utc:.1f}s, "
            f"delta_to_local={delta_to_local:.1f}s"
        )

    def test_two_calls_monotonic_or_equal(self):
        """v1.7.47: a later call returns a >= datetime than an earlier one."""
        first = utcnow_naive()
        second = utcnow_naive()
        assert second >= first, (
            f"Monotonicity violated: first={first!r} second={second!r}"
        )

    def test_does_not_emit_deprecation_warning(self):
        """v1.7.47: the whole point of the helper -- no DeprecationWarning.

        This is the regression test for the entire ship: if a future
        Python version starts warning about datetime.now(timezone.utc)
        or .replace(tzinfo=None), this test catches it before users
        feel the warning spam.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for _ in range(50):  # multiple calls in case sampling matters
                utcnow_naive()
            dep_warnings = [
                w for w in caught
                if issubclass(w.category, DeprecationWarning)
            ]
            assert not dep_warnings, (
                f"utcnow_naive() emitted DeprecationWarning(s): "
                f"{[str(w.message) for w in dep_warnings]}"
            )

    def test_exported_in_all(self):
        """v1.7.47: utcnow_naive is in module.__all__."""
        from curator._compat import datetime as compat_dt
        assert "utcnow_naive" in compat_dt.__all__
