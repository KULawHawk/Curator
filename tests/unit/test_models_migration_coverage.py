"""Focused coverage tests for models/migration.py.

Sub-ship v1.7.109 of Round 2 Tier 1.

Closes lines 91, 96, 134, 138-140 — the property bodies on
`MigrationJob` (is_terminal, is_same_source) and `MigrationProgress`
(is_pending, duration_seconds).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from curator.models.migration import MigrationJob, MigrationProgress


NOW = datetime(2026, 5, 13, 12, 0, 0)


def _make_job(**kw) -> MigrationJob:
    base = dict(
        src_source_id="local",
        src_root="/a",
        dst_source_id="local",
        dst_root="/b",
    )
    base.update(kw)
    return MigrationJob(**base)


def _make_progress(**kw) -> MigrationProgress:
    base = dict(
        job_id=uuid4(),
        curator_id=uuid4(),
        src_path="/a/x.txt",
        dst_path="/b/x.txt",
        safety_level="safe",
    )
    base.update(kw)
    return MigrationProgress(**base)


# ---------------------------------------------------------------------------
# MigrationJob.is_terminal (91)
# ---------------------------------------------------------------------------


def test_migrationjob_is_terminal_true_for_terminal_statuses():
    for status in ("completed", "failed", "cancelled", "partial"):
        assert _make_job(status=status).is_terminal is True


def test_migrationjob_is_terminal_false_for_non_terminal_statuses():
    for status in ("queued", "running"):
        assert _make_job(status=status).is_terminal is False


# ---------------------------------------------------------------------------
# MigrationJob.is_same_source (96)
# ---------------------------------------------------------------------------


def test_migrationjob_is_same_source_true_when_ids_match():
    job = _make_job(src_source_id="local", dst_source_id="local")
    assert job.is_same_source is True


def test_migrationjob_is_same_source_false_when_ids_differ():
    job = _make_job(src_source_id="local", dst_source_id="gdrive")
    assert job.is_same_source is False


# ---------------------------------------------------------------------------
# MigrationProgress.is_pending (134)
# ---------------------------------------------------------------------------


def test_migrationprogress_is_pending_true_only_for_pending():
    assert _make_progress(status="pending").is_pending is True
    assert _make_progress(status="in_progress").is_pending is False
    assert _make_progress(status="completed").is_pending is False


# ---------------------------------------------------------------------------
# MigrationProgress.duration_seconds (138-140)
# ---------------------------------------------------------------------------


def test_migrationprogress_duration_none_when_started_at_missing():
    assert _make_progress(completed_at=NOW).duration_seconds is None


def test_migrationprogress_duration_none_when_completed_at_missing():
    assert _make_progress(started_at=NOW).duration_seconds is None


def test_migrationprogress_duration_computed_when_both_set():
    p = _make_progress(
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=4.0),
    )
    assert p.duration_seconds == 4.0
