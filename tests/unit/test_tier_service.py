"""tests/unit/test_tier_service.py   (v1.7.81)

Focused unit tests for TierService (services/tier.py). Lifts coverage of
that module from 52.78% toward 90%+ by exercising:

  * TierRecipe.from_string (valid + invalid paths)
  * TierCriteria.cutoff() with injectable now
  * TierReport derived properties (candidate_count, total_size,
    duration_seconds, by_source)
  * TierService.scan() across all 3 recipes (COLD, EXPIRED, ARCHIVE)
  * Helpers: _matches_root_prefix

Uses a minimal stub FileRepository so tests don't require a real
SQLite database. The service interface is small enough that a stub
is cleaner than fixturing the whole storage layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from uuid import uuid4

import pytest

from curator.services.tier import (
    TierCandidate,
    TierCriteria,
    TierRecipe,
    TierReport,
    TierService,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

# Fixed "now" so duration/age math is deterministic.
NOW = datetime(2026, 5, 12, 12, 0, 0)


@dataclass
class FakeFile:
    """Minimal FileEntity stand-in. The service only reads attributes
    (source_id, source_path, size, status, last_scanned_at, expires_at);
    a dataclass is sufficient.
    """
    source_id: str
    source_path: str
    size: int
    status: str
    last_scanned_at: datetime | None
    expires_at: datetime | None = None
    curator_id: object = None

    def __post_init__(self) -> None:
        if self.curator_id is None:
            self.curator_id = uuid4()


class StubFileRepository:
    """Minimal FileRepository for TierService tests.

    Holds a list of files; supports query_by_status and find_expiring_before.
    """

    def __init__(self, files: Iterable[FakeFile]) -> None:
        self._files: list[FakeFile] = list(files)

    def query_by_status(self, *, status: str, source_id: str | None = None):
        return [
            f for f in self._files
            if f.status == status
            and (source_id is None or f.source_id == source_id)
        ]

    def find_expiring_before(self, *, when: datetime, source_id: str | None = None):
        return [
            f for f in self._files
            if f.expires_at is not None
            and f.expires_at < when
            and (source_id is None or f.source_id == source_id)
        ]


# ===========================================================================
# TierRecipe enum
# ===========================================================================

class TestTierRecipeFromString:
    def test_valid_recipes_parse(self):
        assert TierRecipe.from_string("cold") is TierRecipe.COLD
        assert TierRecipe.from_string("expired") is TierRecipe.EXPIRED
        assert TierRecipe.from_string("archive") is TierRecipe.ARCHIVE

    def test_case_insensitive_and_strip(self):
        assert TierRecipe.from_string("  COLD  ") is TierRecipe.COLD
        assert TierRecipe.from_string("Expired") is TierRecipe.EXPIRED

    def test_unknown_recipe_raises_with_helpful_message(self):
        with pytest.raises(ValueError) as excinfo:
            TierRecipe.from_string("warm")
        msg = str(excinfo.value)
        assert "warm" in msg
        # Error message must list valid options so the user can recover.
        for valid in ("cold", "expired", "archive"):
            assert valid in msg


# ===========================================================================
# TierCriteria
# ===========================================================================

class TestTierCriteriaCutoff:
    def test_cutoff_uses_injected_now(self):
        crit = TierCriteria(recipe=TierRecipe.COLD, min_age_days=30, now=NOW)
        assert crit.cutoff() == NOW - timedelta(days=30)

    def test_cutoff_defaults_to_utcnow(self):
        crit = TierCriteria(recipe=TierRecipe.ARCHIVE, min_age_days=365)
        cutoff = crit.cutoff()
        # Just sanity-check: cutoff is ~365 days before "now-ish".
        # Uses the same helper the production code uses to avoid the
        # deprecated stdlib `datetime.utcnow()` (and to stay consistent
        # with codebase conventions).
        from curator._compat.datetime import utcnow_naive
        delta = utcnow_naive() - cutoff
        # Allow generous slack for slow test runners (1 day).
        assert timedelta(days=364) <= delta <= timedelta(days=366)


# ===========================================================================
# TierReport derived properties
# ===========================================================================

class TestTierReportProperties:
    def _make_report(self, candidates: list[TierCandidate]) -> TierReport:
        return TierReport(
            recipe=TierRecipe.COLD,
            criteria=TierCriteria(recipe=TierRecipe.COLD, now=NOW),
            candidates=candidates,
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=2.5),
        )

    def _candidate(self, source_id: str = "local", size: int = 1024) -> TierCandidate:
        f = FakeFile(
            source_id=source_id,
            source_path=f"/data/file_{uuid4().hex[:6]}.bin",
            size=size,
            status="provisional",
            last_scanned_at=NOW - timedelta(days=200),
        )
        return TierCandidate(file=f, reason="test")

    def test_candidate_count(self):
        report = self._make_report([self._candidate(), self._candidate()])
        assert report.candidate_count == 2

    def test_candidate_count_empty(self):
        report = self._make_report([])
        assert report.candidate_count == 0

    def test_total_size_sums_candidate_files(self):
        report = self._make_report([
            self._candidate(size=100),
            self._candidate(size=250),
            self._candidate(size=50),
        ])
        assert report.total_size == 400

    def test_total_size_skips_zero_size_files(self):
        # The implementation guards against falsy sizes (e.g. 0).
        report = self._make_report([
            self._candidate(size=100),
            self._candidate(size=0),
        ])
        assert report.total_size == 100

    def test_duration_seconds(self):
        report = self._make_report([self._candidate()])
        assert report.duration_seconds == pytest.approx(2.5, abs=0.001)

    def test_duration_seconds_zero_if_unfinished(self):
        report = TierReport(
            recipe=TierRecipe.COLD,
            criteria=TierCriteria(recipe=TierRecipe.COLD, now=NOW),
            started_at=NOW,
            completed_at=None,
        )
        assert report.duration_seconds == 0.0

    def test_by_source_groups_correctly(self):
        report = self._make_report([
            self._candidate(source_id="local"),
            self._candidate(source_id="local"),
            self._candidate(source_id="gdrive"),
        ])
        assert report.by_source() == {"local": 2, "gdrive": 1}

    def test_by_source_empty(self):
        report = self._make_report([])
        assert report.by_source() == {}


# ===========================================================================
# TierService.scan() -- COLD recipe
# ===========================================================================

class TestTierServiceScanCold:
    def test_cold_picks_stale_provisional_files(self):
        # 3 provisional files: one old (candidate), one fresh (skip),
        # one with no last_scanned_at (skip).
        files = [
            FakeFile("local", "/a.txt", 100, "provisional",
                     last_scanned_at=NOW - timedelta(days=150)),  # stale: pick
            FakeFile("local", "/b.txt", 200, "provisional",
                     last_scanned_at=NOW - timedelta(days=30)),   # fresh: skip
            FakeFile("local", "/c.txt", 300, "provisional",
                     last_scanned_at=None),                       # missing: skip
            FakeFile("local", "/d.txt", 400, "active",
                     last_scanned_at=NOW - timedelta(days=400)),  # wrong status: skip
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.COLD,
            min_age_days=90,
            now=NOW,
        ))

        assert report.candidate_count == 1
        assert report.candidates[0].file.source_path == "/a.txt"
        assert "150d ago" in report.candidates[0].reason

    def test_cold_respects_source_id_filter(self):
        files = [
            FakeFile("local", "/a.txt", 100, "provisional",
                     last_scanned_at=NOW - timedelta(days=200)),
            FakeFile("gdrive", "/b.txt", 100, "provisional",
                     last_scanned_at=NOW - timedelta(days=200)),
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.COLD,
            min_age_days=90,
            source_id="local",
            now=NOW,
        ))
        assert report.candidate_count == 1
        assert report.candidates[0].file.source_id == "local"

    def test_cold_respects_root_prefix_filter(self):
        files = [
            FakeFile("local", "/keep/a.txt", 100, "provisional",
                     last_scanned_at=NOW - timedelta(days=200)),
            FakeFile("local", "/skip/b.txt", 100, "provisional",
                     last_scanned_at=NOW - timedelta(days=200)),
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.COLD,
            min_age_days=90,
            root_prefix="/keep",
            now=NOW,
        ))
        assert report.candidate_count == 1
        assert report.candidates[0].file.source_path == "/keep/a.txt"

    def test_cold_sorts_oldest_first(self):
        files = [
            FakeFile("local", "/recent.txt", 100, "provisional",
                     last_scanned_at=NOW - timedelta(days=100)),
            FakeFile("local", "/ancient.txt", 100, "provisional",
                     last_scanned_at=NOW - timedelta(days=400)),
            FakeFile("local", "/middle.txt", 100, "provisional",
                     last_scanned_at=NOW - timedelta(days=200)),
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.COLD,
            min_age_days=90,
            now=NOW,
        ))
        paths = [c.file.source_path for c in report.candidates]
        assert paths == ["/ancient.txt", "/middle.txt", "/recent.txt"]


# ===========================================================================
# TierService.scan() -- EXPIRED recipe
# ===========================================================================

class TestTierServiceScanExpired:
    def test_expired_picks_files_past_expiry(self):
        files = [
            FakeFile("local", "/old.txt", 100, "junk",
                     last_scanned_at=NOW - timedelta(days=5),
                     expires_at=NOW - timedelta(days=10)),
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.EXPIRED,
            now=NOW,
        ))
        assert report.candidate_count == 1
        assert "expired 10d ago" in report.candidates[0].reason
        assert "status=junk" in report.candidates[0].reason

    def test_expired_respects_root_prefix(self):
        files = [
            FakeFile("local", "/keep/a.txt", 100, "junk",
                     last_scanned_at=NOW,
                     expires_at=NOW - timedelta(days=1)),
            FakeFile("local", "/skip/b.txt", 100, "junk",
                     last_scanned_at=NOW,
                     expires_at=NOW - timedelta(days=1)),
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.EXPIRED,
            root_prefix="/keep",
            now=NOW,
        ))
        assert report.candidate_count == 1


# ===========================================================================
# TierService.scan() -- ARCHIVE recipe
# ===========================================================================

class TestTierServiceScanArchive:
    def test_archive_picks_stale_vital_files(self):
        files = [
            FakeFile("local", "/contract.pdf", 5000, "vital",
                     last_scanned_at=NOW - timedelta(days=500)),  # very old vital: pick
            FakeFile("local", "/recent.pdf", 5000, "vital",
                     last_scanned_at=NOW - timedelta(days=100)),  # too fresh: skip
            FakeFile("local", "/junk.txt", 1, "provisional",
                     last_scanned_at=NOW - timedelta(days=500)),  # wrong status: skip
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.ARCHIVE,
            min_age_days=365,
            now=NOW,
        ))
        assert report.candidate_count == 1
        assert report.candidates[0].file.source_path == "/contract.pdf"
        assert "archive candidate" in report.candidates[0].reason

    def test_archive_skips_files_with_no_last_scanned_at(self):
        files = [
            FakeFile("local", "/no_scan.pdf", 100, "vital",
                     last_scanned_at=None),
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.ARCHIVE,
            min_age_days=365,
            now=NOW,
        ))
        assert report.candidate_count == 0

    def test_archive_respects_root_prefix_filter(self):
        files = [
            FakeFile("local", "/keep/contract.pdf", 5000, "vital",
                     last_scanned_at=NOW - timedelta(days=500)),
            FakeFile("local", "/skip/contract.pdf", 5000, "vital",
                     last_scanned_at=NOW - timedelta(days=500)),
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.ARCHIVE,
            min_age_days=365,
            root_prefix="/keep",
            now=NOW,
        ))
        assert report.candidate_count == 1
        assert report.candidates[0].file.source_path == "/keep/contract.pdf"


# ===========================================================================
# Helpers: _matches_root_prefix
# ===========================================================================

class TestMatchesRootPrefix:
    def _make_file(self, path: str) -> FakeFile:
        return FakeFile("local", path, 1, "provisional", last_scanned_at=NOW)

    def test_none_prefix_matches_everything(self):
        assert TierService._matches_root_prefix(self._make_file("/anything"), None)

    def test_exact_prefix_match(self):
        assert TierService._matches_root_prefix(
            self._make_file("/users/jake/docs/file.txt"),
            "/users/jake",
        )

    def test_case_insensitive(self):
        assert TierService._matches_root_prefix(
            self._make_file("/Users/Jake/docs/file.txt"),
            "/users/jake",
        )

    def test_non_matching_prefix(self):
        assert not TierService._matches_root_prefix(
            self._make_file("/elsewhere/file.txt"),
            "/users/jake",
        )


# ===========================================================================
# Report metadata
# ===========================================================================

class TestScanReportMetadata:
    """The TierReport should always have started_at and completed_at set."""

    def test_started_at_and_completed_at_populated(self):
        service = TierService(StubFileRepository([]))
        report = service.scan(TierCriteria(recipe=TierRecipe.COLD, now=NOW))
        assert report.started_at is not None
        assert report.completed_at is not None
        assert report.completed_at >= report.started_at

    def test_scanned_count_matches_candidate_count(self):
        files = [
            FakeFile("local", "/a.txt", 100, "provisional",
                     last_scanned_at=NOW - timedelta(days=200)),
        ]
        service = TierService(StubFileRepository(files))
        report = service.scan(TierCriteria(
            recipe=TierRecipe.COLD,
            min_age_days=90,
            now=NOW,
        ))
        assert report.scanned_count == report.candidate_count == 1
