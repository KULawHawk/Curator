"""Tests for v1.4.1 sentinel-default behavior on apply() and run_job().

Per BUILD_TRACKER.md v1.5.0 candidate (promoted to v1.4.1 patch):
v1.4.0 and earlier: ``apply()`` and ``run_job()`` accepted
``max_retries: int = 3`` and ``on_conflict: str = "skip"`` as kwarg
defaults and unconditionally called ``self.set_max_retries(...)`` /
``self.set_on_conflict_mode(...)`` at entry. This silently overwrote any
prior call to ``set_max_retries()`` / ``set_on_conflict_mode()`` made by
library callers, making the sticky setters not actually sticky.

v1.4.1: defaults changed to ``_UNCHANGED`` sentinel; setters only invoked
when the caller explicitly passes a value. Sticky setters now stick.

These tests verify:

* Sticky-setter persistence: ``set_max_retries(N)`` followed by ``apply()``
  with no kwarg uses N, not 3.
* Explicit override: ``apply(plan, max_retries=N)`` still works exactly
  as before (overrides any sticky setting).
* Same patterns for ``set_on_conflict_mode()`` and ``apply(on_conflict=)``.
* ``run_job()`` parallel: sticky setter persists when no kwarg passed,
  explicit kwarg overrides, persisted job.options inheritance still
  works as expected.
* ``__init__`` defaults are unchanged: a fresh service still gets 3 / "skip".
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from curator.services.migration import (
    MigrationOutcome,
    MigrationPlan,
    MigrationService,
)
from curator.services.safety import SafetyService


@pytest.fixture
def service():
    """A bare MigrationService with no real repos -- sufficient for
    apply()/run_job() *entry-point* behavior tests that don't actually
    move files. The plan we feed in is empty so the per-file loop is
    a no-op."""
    file_repo = MagicMock()
    safety = MagicMock(spec=SafetyService)
    return MigrationService(file_repo=file_repo, safety=safety)


# ---------------------------------------------------------------------------
# __init__ defaults unchanged
# ---------------------------------------------------------------------------


def test_init_defaults_unchanged_in_v141(service):
    """Fresh MigrationService still defaults to max_retries=3, conflict='skip'."""
    assert service._max_retries == 3
    assert service._on_conflict_mode == "skip"


# ---------------------------------------------------------------------------
# apply(): sticky setter persists when no kwarg
# ---------------------------------------------------------------------------


def test_set_max_retries_persists_through_apply_when_kwarg_omitted(service):
    """``set_max_retries(7)`` then ``apply(plan)`` (no kwarg) -> still 7."""
    service.set_max_retries(7)
    assert service._max_retries == 7

    plan = MigrationPlan(
        src_source_id="local", src_root="/", dst_source_id="local", dst_root="/",
    )
    service.apply(plan)  # no max_retries kwarg

    assert service._max_retries == 7  # NOT reset to 3


def test_set_on_conflict_mode_persists_through_apply_when_kwarg_omitted(service):
    """``set_on_conflict_mode('fail')`` then ``apply(plan)`` -> still 'fail'."""
    service.set_on_conflict_mode("fail")
    assert service._on_conflict_mode == "fail"

    plan = MigrationPlan(
        src_source_id="local", src_root="/", dst_source_id="local", dst_root="/",
    )
    service.apply(plan)  # no on_conflict kwarg

    assert service._on_conflict_mode == "fail"  # NOT reset to 'skip'


def test_apply_no_kwargs_after_init_uses_init_defaults(service):
    """Bare apply() on a fresh service uses the __init__ defaults (3 / 'skip').

    This guards against accidental v1.4.0-era behavior change that would
    break backward compatibility. The sentinel approach must preserve
    the bare-call default semantics."""
    plan = MigrationPlan(
        src_source_id="local", src_root="/", dst_source_id="local", dst_root="/",
    )
    service.apply(plan)

    assert service._max_retries == 3
    assert service._on_conflict_mode == "skip"


# ---------------------------------------------------------------------------
# apply(): explicit kwargs still work
# ---------------------------------------------------------------------------


def test_apply_with_explicit_max_retries_still_overrides(service):
    """Explicit max_retries=N still works even after a set_max_retries() call."""
    service.set_max_retries(7)

    plan = MigrationPlan(
        src_source_id="local", src_root="/", dst_source_id="local", dst_root="/",
    )
    service.apply(plan, max_retries=2)

    assert service._max_retries == 2


def test_apply_with_explicit_on_conflict_still_overrides(service):
    """Explicit on_conflict=mode still works even after a set_on_conflict_mode() call."""
    service.set_on_conflict_mode("fail")

    plan = MigrationPlan(
        src_source_id="local", src_root="/", dst_source_id="local", dst_root="/",
    )
    service.apply(plan, on_conflict="rename-with-suffix")

    assert service._on_conflict_mode == "rename-with-suffix"


def test_apply_with_invalid_on_conflict_raises(service):
    """Explicit on_conflict='nonsense' still raises ValueError as before."""
    plan = MigrationPlan(
        src_source_id="local", src_root="/", dst_source_id="local", dst_root="/",
    )
    with pytest.raises(ValueError, match="unknown on_conflict mode"):
        service.apply(plan, on_conflict="nonsense")


# ---------------------------------------------------------------------------
# apply(): mixed kwargs
# ---------------------------------------------------------------------------


def test_apply_overrides_one_kwarg_preserves_other_sticky_setting(service):
    """Calling apply(plan, max_retries=N) preserves a previously-set
    on_conflict_mode (only the explicitly-passed kwarg overrides)."""
    service.set_max_retries(7)
    service.set_on_conflict_mode("fail")

    plan = MigrationPlan(
        src_source_id="local", src_root="/", dst_source_id="local", dst_root="/",
    )
    service.apply(plan, max_retries=2)  # only override max_retries

    assert service._max_retries == 2  # explicit override won
    assert service._on_conflict_mode == "fail"  # sticky setter preserved


# ---------------------------------------------------------------------------
# apply(): clamping behavior preserved
# ---------------------------------------------------------------------------


def test_apply_max_retries_clamping_still_applies_with_explicit_kwarg(service):
    """Explicit max_retries values still get clamped to [0, 10]."""
    plan = MigrationPlan(
        src_source_id="local", src_root="/", dst_source_id="local", dst_root="/",
    )

    service.apply(plan, max_retries=-5)
    assert service._max_retries == 0  # clamped up

    service.apply(plan, max_retries=99)
    assert service._max_retries == 10  # clamped down


# ---------------------------------------------------------------------------
# run_job() sentinel behavior (mirrors apply())
# ---------------------------------------------------------------------------


def test_run_job_sticky_setter_persists_when_kwarg_omitted_and_no_persisted_options():
    """run_job() with no max_retries kwarg AND no persisted job.options uses
    whatever set_max_retries() last set."""
    file_repo = MagicMock()
    safety = MagicMock(spec=SafetyService)
    jobs = MagicMock()

    # Stub out a job that's already 'completed' so run_job() short-circuits
    # AFTER the policy resolution but BEFORE running workers.
    fake_job = MagicMock()
    fake_job.status = "completed"
    fake_job.options = {}  # empty options -- nothing to inherit
    fake_job.job_id = MagicMock()
    jobs.get_job.return_value = fake_job

    service = MigrationService(
        file_repo=file_repo, safety=safety, migration_jobs=jobs,
    )
    service._build_report_from_persisted = MagicMock(
        return_value=MagicMock()
    )
    service.set_max_retries(7)
    service.set_on_conflict_mode("fail")

    service.run_job(MagicMock())

    # Both stuck despite no kwargs and no persisted options.
    assert service._max_retries == 7
    assert service._on_conflict_mode == "fail"


def test_run_job_persisted_options_used_when_kwarg_omitted():
    """run_job() with no kwargs but persisted job.options inherits from options.

    This was the v1.4.0 behavior (resumed jobs inherited their original
    retry policy from job.options); v1.4.1 preserves it because the
    sentinel still triggers the persisted-options path when no kwarg
    is passed."""
    file_repo = MagicMock()
    safety = MagicMock(spec=SafetyService)
    jobs = MagicMock()

    fake_job = MagicMock()
    fake_job.status = "completed"
    fake_job.options = {"max_retries": 8, "on_conflict": "rename-with-suffix"}
    fake_job.job_id = MagicMock()
    jobs.get_job.return_value = fake_job

    service = MigrationService(
        file_repo=file_repo, safety=safety, migration_jobs=jobs,
    )
    service._build_report_from_persisted = MagicMock(
        return_value=MagicMock()
    )

    service.run_job(MagicMock())

    # Inherited from job.options (kwarg sentinel triggers the inheritance).
    assert service._max_retries == 8
    assert service._on_conflict_mode == "rename-with-suffix"


def test_run_job_explicit_kwarg_beats_persisted_options():
    """run_job(max_retries=N, on_conflict=M) wins over persisted options."""
    file_repo = MagicMock()
    safety = MagicMock(spec=SafetyService)
    jobs = MagicMock()

    fake_job = MagicMock()
    fake_job.status = "completed"
    fake_job.options = {"max_retries": 8, "on_conflict": "rename-with-suffix"}
    fake_job.job_id = MagicMock()
    jobs.get_job.return_value = fake_job

    service = MigrationService(
        file_repo=file_repo, safety=safety, migration_jobs=jobs,
    )
    service._build_report_from_persisted = MagicMock(
        return_value=MagicMock()
    )

    service.run_job(MagicMock(), max_retries=2, on_conflict="fail")

    # Explicit kwargs override BOTH persisted options AND any sticky setter.
    assert service._max_retries == 2
    assert service._on_conflict_mode == "fail"


def test_run_job_invalid_persisted_on_conflict_falls_back_to_skip():
    """Persisted options with stale/unknown on_conflict don't crash run_job;
    they fall back to 'skip' so a resume can still succeed."""
    file_repo = MagicMock()
    safety = MagicMock(spec=SafetyService)
    jobs = MagicMock()

    fake_job = MagicMock()
    fake_job.status = "completed"
    fake_job.options = {"on_conflict": "totally-bogus-mode"}
    fake_job.job_id = MagicMock()
    jobs.get_job.return_value = fake_job

    service = MigrationService(
        file_repo=file_repo, safety=safety, migration_jobs=jobs,
    )
    service._build_report_from_persisted = MagicMock(
        return_value=MagicMock()
    )

    # Should not raise; should fall back to 'skip'
    service.run_job(MagicMock())

    assert service._on_conflict_mode == "skip"


def test_run_job_explicit_invalid_on_conflict_raises():
    """run_job(on_conflict='bogus') still raises ValueError immediately."""
    file_repo = MagicMock()
    safety = MagicMock(spec=SafetyService)
    jobs = MagicMock()

    fake_job = MagicMock()
    fake_job.status = "queued"
    fake_job.options = {}
    fake_job.job_id = MagicMock()
    jobs.get_job.return_value = fake_job

    service = MigrationService(
        file_repo=file_repo, safety=safety, migration_jobs=jobs,
    )

    with pytest.raises(ValueError, match="unknown on_conflict mode"):
        service.run_job(MagicMock(), on_conflict="bogus")


def test_run_job_invalid_persisted_max_retries_silently_preserves_current():
    """Persisted options with unparseable max_retries don't crash;
    self._max_retries is preserved (sticky from setter or __init__)."""
    file_repo = MagicMock()
    safety = MagicMock(spec=SafetyService)
    jobs = MagicMock()

    fake_job = MagicMock()
    fake_job.status = "completed"
    fake_job.options = {"max_retries": "not-an-int"}
    fake_job.job_id = MagicMock()
    jobs.get_job.return_value = fake_job

    service = MigrationService(
        file_repo=file_repo, safety=safety, migration_jobs=jobs,
    )
    service._build_report_from_persisted = MagicMock(
        return_value=MagicMock()
    )
    service.set_max_retries(7)

    service.run_job(MagicMock())

    # Sticky setter value preserved despite unparseable persisted value.
    assert service._max_retries == 7
