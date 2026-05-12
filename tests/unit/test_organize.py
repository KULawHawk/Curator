"""Unit tests for OrganizeService + OrganizeBucket + OrganizePlan.

Uses lightweight mocks for FileRepository and SafetyService so the
plan-mode logic can be exercised in isolation. End-to-end DB+real-file
behavior is covered by ``tests/integration/test_organize_flow.py``.
"""

from __future__ import annotations

from datetime import datetime
from curator._compat.datetime import utcnow_naive
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from curator.models.file import FileEntity
from curator.services.organize import (
    OrganizeBucket,
    OrganizePlan,
    OrganizeService,
)
from curator.services.safety import (
    SafetyConcern,
    SafetyLevel,
    SafetyReport,
    SafetyService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file(path: str, size: int = 100, source_id: str = "local") -> FileEntity:
    """Lightweight FileEntity for testing \u2014 path + size are what OrganizeService cares about."""
    return FileEntity(
        curator_id=uuid4(),
        source_id=source_id,
        source_path=path,
        size=size,
        mtime=utcnow_naive(),
    )


def _safe_report(path: str) -> SafetyReport:
    return SafetyReport(path=path, level=SafetyLevel.SAFE)


def _caution_report(path: str, concern: SafetyConcern, detail: str) -> SafetyReport:
    r = SafetyReport(path=path)
    r.add_concern(concern, detail)
    return r


def _refuse_report(path: str, detail: str = "under OS") -> SafetyReport:
    r = SafetyReport(path=path)
    r.add_concern(SafetyConcern.OS_MANAGED, detail)
    return r


# ===========================================================================
# OrganizeBucket
# ===========================================================================


class TestOrganizeBucket:
    def test_empty_bucket_has_zero_count_and_size(self):
        b = OrganizeBucket()
        assert b.count == 0
        assert b.total_size == 0
        assert b.concern_counts() == {}

    def test_add_increments_count_and_size(self):
        b = OrganizeBucket()
        f = _make_file("/x", size=42)
        b.add(f, _safe_report("/x"))
        assert b.count == 1
        assert b.total_size == 42

    def test_add_records_concerns_per_file(self):
        b = OrganizeBucket()
        f1 = _make_file("/a", size=1)
        f2 = _make_file("/b", size=2)
        b.add(f1, _caution_report("/a", SafetyConcern.PROJECT_FILE, "p"))
        b.add(f2, _caution_report("/b", SafetyConcern.SYMLINK, "s"))
        counts = b.concern_counts()
        assert counts == {SafetyConcern.PROJECT_FILE: 1, SafetyConcern.SYMLINK: 1}

    def test_add_handles_multiple_concerns_per_file(self):
        b = OrganizeBucket()
        f = _make_file("/x", size=1)
        report = SafetyReport(path="/x")
        report.add_concern(SafetyConcern.PROJECT_FILE, "p")
        report.add_concern(SafetyConcern.SYMLINK, "s")
        b.add(f, report)
        counts = b.concern_counts()
        # Same file appears under both concerns.
        assert counts[SafetyConcern.PROJECT_FILE] == 1
        assert counts[SafetyConcern.SYMLINK] == 1

    def test_add_handles_zero_size(self):
        # The defensive ``f.size or 0`` in OrganizeBucket.add still matters
        # — a zero-byte file should add 0 to total_size, not skip the file.
        b = OrganizeBucket()
        f = _make_file("/empty", size=0)
        b.add(f, _safe_report("/empty"))
        assert b.count == 1
        assert b.total_size == 0


# ===========================================================================
# OrganizePlan
# ===========================================================================


class TestOrganizePlan:
    def test_empty_plan_has_zero_totals(self):
        p = OrganizePlan(source_id="local", root_prefix=None)
        assert p.total_files == 0
        assert p.total_size == 0

    def test_bucket_for_returns_correct_bucket(self):
        p = OrganizePlan(source_id="local", root_prefix=None)
        assert p.bucket_for(SafetyLevel.SAFE) is p.safe
        assert p.bucket_for(SafetyLevel.CAUTION) is p.caution
        assert p.bucket_for(SafetyLevel.REFUSE) is p.refuse

    def test_total_files_aggregates_buckets(self):
        p = OrganizePlan(source_id="local", root_prefix=None)
        p.safe.add(_make_file("/a"), _safe_report("/a"))
        p.caution.add(
            _make_file("/b"),
            _caution_report("/b", SafetyConcern.APP_DATA, "x"),
        )
        p.refuse.add(_make_file("/c"), _refuse_report("/c"))
        assert p.total_files == 3

    def test_total_size_sums_bucket_sizes(self):
        p = OrganizePlan(source_id="local", root_prefix=None)
        p.safe.add(_make_file("/a", size=10), _safe_report("/a"))
        p.caution.add(
            _make_file("/b", size=20),
            _caution_report("/b", SafetyConcern.APP_DATA, "x"),
        )
        p.refuse.add(_make_file("/c", size=30), _refuse_report("/c"))
        assert p.total_size == 60

    def test_duration_none_until_completed(self):
        p = OrganizePlan(source_id="local", root_prefix=None)
        assert p.duration_seconds is None

    def test_duration_computed_after_completed(self):
        p = OrganizePlan(source_id="local", root_prefix=None)
        p.completed_at = utcnow_naive()
        # Don't assert exact value; just that it's non-negative.
        assert p.duration_seconds is not None
        assert p.duration_seconds >= 0


# ===========================================================================
# OrganizeService.plan() with mocks
# ===========================================================================


class TestOrganizeServicePlan:
    def _build_service(self, files: list[FileEntity], reports_by_path: dict[str, SafetyReport]):
        """Build a service with mocked FileRepository + SafetyService.

        Each file in ``files`` will be returned from ``files.query()``;
        each report in ``reports_by_path`` will be returned from
        ``safety.check_path(path)``.
        """
        file_repo = MagicMock()
        file_repo.query.return_value = files

        safety = MagicMock(spec=SafetyService)
        safety.check_path.side_effect = lambda path, **kwargs: reports_by_path[str(path)]

        return OrganizeService(file_repo, safety), file_repo, safety

    def test_buckets_match_safety_levels(self):
        files = [
            _make_file("/safe/a"),
            _make_file("/caution/b"),
            _make_file("/refuse/c"),
        ]
        reports = {
            "/safe/a": _safe_report("/safe/a"),
            "/caution/b": _caution_report("/caution/b", SafetyConcern.PROJECT_FILE, "p"),
            "/refuse/c": _refuse_report("/refuse/c"),
        }
        svc, _, _ = self._build_service(files, reports)
        plan = svc.plan(source_id="local")
        assert plan.safe.count == 1
        assert plan.caution.count == 1
        assert plan.refuse.count == 1
        assert plan.total_files == 3

    def test_file_query_uses_source_id(self):
        files: list[FileEntity] = []
        svc, file_repo, _ = self._build_service(files, {})
        svc.plan(source_id="local:home")
        # The query passed to file_repo should restrict by this source_id.
        query_arg = file_repo.query.call_args[0][0]
        assert query_arg.source_ids == ["local:home"]
        assert query_arg.deleted is False

    def test_root_prefix_passed_to_query(self):
        svc, file_repo, _ = self._build_service([], {})
        svc.plan(source_id="local", root_prefix="/home/jake/Music")
        query_arg = file_repo.query.call_args[0][0]
        assert query_arg.source_path_starts_with == "/home/jake/Music"

    def test_limit_passed_to_query(self):
        svc, file_repo, _ = self._build_service([], {})
        svc.plan(source_id="local", limit=50)
        query_arg = file_repo.query.call_args[0][0]
        assert query_arg.limit == 50

    def test_check_handles_propagates_to_safety(self):
        files = [_make_file("/x")]
        reports = {"/x": _safe_report("/x")}
        svc, _, safety = self._build_service(files, reports)
        svc.plan(source_id="local", check_handles=True)
        # SafetyService.check_path should have been called with
        # check_handles=True for that file.
        call = safety.check_path.call_args
        assert call.kwargs.get("check_handles") is True

    def test_safety_failure_routes_to_refuse(self):
        files = [
            _make_file("/x", size=99),
            _make_file("/y", size=11),
        ]
        # Both will fail the safety check; service should put them in REFUSE.
        safety = MagicMock(spec=SafetyService)
        safety.check_path.side_effect = OSError("boom")
        file_repo = MagicMock()
        file_repo.query.return_value = files

        svc = OrganizeService(file_repo, safety)
        plan = svc.plan(source_id="local")
        assert plan.refuse.count == 2
        assert plan.refuse.total_size == 110
        assert plan.safe.count == 0
        assert plan.caution.count == 0

    def test_plan_records_started_and_completed(self):
        svc, _, _ = self._build_service([], {})
        plan = svc.plan(source_id="local")
        assert isinstance(plan.started_at, datetime)
        assert isinstance(plan.completed_at, datetime)
        assert plan.completed_at >= plan.started_at

    def test_empty_source_returns_empty_plan(self):
        svc, _, _ = self._build_service([], {})
        plan = svc.plan(source_id="local")
        assert plan.total_files == 0
        assert plan.safe.count == 0
        assert plan.caution.count == 0
        assert plan.refuse.count == 0
        assert plan.completed_at is not None

    def test_repo_failure_returns_empty_plan_without_raising(self):
        file_repo = MagicMock()
        file_repo.query.side_effect = RuntimeError("db down")
        safety = MagicMock(spec=SafetyService)
        svc = OrganizeService(file_repo, safety)

        plan = svc.plan(source_id="local")
        assert plan.total_files == 0
        # The plan should still have a completed_at so callers can render it.
        assert plan.completed_at is not None

    def test_concern_aggregation_in_caution_bucket(self):
        # Three files, two of which have project_file concerns and one
        # has app_data. All should land in CAUTION.
        files = [
            _make_file("/proj1/a"),
            _make_file("/proj2/b"),
            _make_file("/appdata/c"),
        ]
        reports = {
            "/proj1/a": _caution_report("/proj1/a", SafetyConcern.PROJECT_FILE, "p1"),
            "/proj2/b": _caution_report("/proj2/b", SafetyConcern.PROJECT_FILE, "p2"),
            "/appdata/c": _caution_report("/appdata/c", SafetyConcern.APP_DATA, "a"),
        }
        svc, _, _ = self._build_service(files, reports)
        plan = svc.plan(source_id="local")
        assert plan.caution.count == 3
        counts = plan.caution.concern_counts()
        assert counts[SafetyConcern.PROJECT_FILE] == 2
        assert counts[SafetyConcern.APP_DATA] == 1
