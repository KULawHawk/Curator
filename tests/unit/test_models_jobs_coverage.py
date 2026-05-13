"""Focused coverage tests for models/jobs.py.

Sub-ship v1.7.108 of Round 2 Tier 1.

Closes lines 42-44 (duration_seconds body) + line 49 (is_terminal body).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from curator.models.jobs import ScanJob


NOW = datetime(2026, 5, 13, 12, 0, 0)


def _make_job(**kw) -> ScanJob:
    return ScanJob(
        source_id="local",
        root_path="/data",
        **kw,
    )


# ---------------------------------------------------------------------------
# duration_seconds (42-44)
# ---------------------------------------------------------------------------


def test_duration_none_when_started_at_missing():
    # Line 42-43: started_at is None → return None.
    assert _make_job(completed_at=NOW).duration_seconds is None


def test_duration_none_when_completed_at_missing():
    # Line 42-43: completed_at is None → return None.
    assert _make_job(started_at=NOW).duration_seconds is None


def test_duration_computed_when_both_timestamps_present():
    # Line 44: both timestamps present → return float seconds.
    job = _make_job(
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2.5),
    )
    assert job.duration_seconds == 2.5


# ---------------------------------------------------------------------------
# is_terminal (49)
# ---------------------------------------------------------------------------


def test_is_terminal_true_for_terminal_statuses():
    # Line 49 True branch.
    for status in ("completed", "failed", "cancelled"):
        assert _make_job(status=status).is_terminal is True


def test_is_terminal_false_for_non_terminal_statuses():
    # Line 49 False branch.
    for status in ("queued", "running"):
        assert _make_job(status=status).is_terminal is False
