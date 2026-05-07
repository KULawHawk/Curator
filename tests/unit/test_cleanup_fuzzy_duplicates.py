"""Unit tests for fuzzy near-duplicate detection (Phase Gamma F9, v0.30).

Covers:
    * match_kind validation (exact / fuzzy / invalid)
    * Empty index / no fuzzy hashes returns empty
    * Two-file fuzzy group (very similar fuzzy_hashes)
    * Three-file transitive group (A~B~C; A and C may not directly match
      but the union-find walks the connected component)
    * Isolated files (with fuzzy_hash but no neighbors) don't get flagged
    * Threshold tightening reduces match count
    * keep_strategy + keep_under work in fuzzy mode the same as exact
    * Each finding records match_kind="fuzzy" + dupset_id="fuzzy:N"
      + similarity_threshold + fuzzy_hash + kept_fuzzy_hash

Test fixtures use ``ppdeep.hash`` over varied pseudo-random prose, NOT
repeated phrases — repeated text produces degenerate fuzzy hashes
(``YYYYYYYY``-style) that don't have realistic n-gram structure.
"""

from __future__ import annotations

import random
import string
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest

from curator._vendored import ppdeep
from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.services.cleanup import (
    DEFAULT_FUZZY_SIMILARITY_THRESHOLD,
    MATCH_KINDS,
    CleanupKind,
    CleanupService,
)
from curator.services.safety import SafetyService
from curator.storage import CuratorDB
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.source_repo import SourceRepository


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "fuzzy_dedup_test.db"
    database = CuratorDB(db_path)
    database.init()
    yield database


@pytest.fixture
def file_repo(db):
    return FileRepository(db)


@pytest.fixture
def source_repo(db):
    return SourceRepository(db)


@pytest.fixture
def loose_safety():
    return SafetyService(app_data_paths=[], os_managed_paths=[])


@pytest.fixture
def cleanup(loose_safety, file_repo):
    return CleanupService(loose_safety, file_repo=file_repo)


def _seed_source(source_repo: SourceRepository, source_id: str = "local") -> None:
    try:
        source_repo.insert(SourceConfig(
            source_id=source_id,
            source_type="local",
            display_name=source_id,
        ))
    except Exception:
        pass


def _seed_file_with_fuzzy(
    file_repo: FileRepository,
    *,
    path: str,
    fuzzy_hash: str,
    size: int = 1000,
    source_id: str = "local",
    mtime: datetime | None = None,
) -> FileEntity:
    entity = FileEntity(
        curator_id=uuid4(),
        source_id=source_id,
        source_path=path,
        size=size,
        mtime=mtime or datetime(2024, 1, 1),
        fuzzy_hash=fuzzy_hash,
        extension=Path(path).suffix.lower() or None,
    )
    file_repo.upsert(entity)
    return entity


def _varied_text(seed: int, length: int = 4000) -> str:
    """Pseudo-random prose-like text. Deterministic given a seed.

    Uses lowercase + space so the result has the same character entropy
    as English prose without needing a real corpus. ppdeep produces
    realistic non-degenerate hashes from this kind of input.
    """
    rng = random.Random(seed)
    chars = string.ascii_lowercase + ' '
    return ''.join(rng.choices(chars, k=length))


# ===========================================================================
# Validation
# ===========================================================================


class TestFuzzyValidation:
    def test_match_kinds_constant_exposes_both(self):
        assert "exact" in MATCH_KINDS
        assert "fuzzy" in MATCH_KINDS

    def test_invalid_match_kind_raises(self, cleanup):
        with pytest.raises(ValueError, match="unknown match_kind"):
            cleanup.find_duplicates(match_kind="bogus")

    def test_default_threshold_is_strict(self):
        # 0.85 is "destructive-action grade" - noticeably stricter than
        # the lineage default (0.5).
        assert DEFAULT_FUZZY_SIMILARITY_THRESHOLD == 0.85


# ===========================================================================
# Empty cases
# ===========================================================================


class TestFuzzyEmpty:
    def test_no_fuzzy_hashed_files_returns_empty(self, cleanup):
        report = cleanup.find_duplicates(match_kind="fuzzy")
        assert report.kind == CleanupKind.DUPLICATE_FILE
        assert report.count == 0

    def test_isolated_files_not_flagged(
        self, cleanup, file_repo, source_repo
    ):
        # Two files with totally unrelated content -> no near-dup edge,
        # both left alone.
        _seed_source(source_repo)
        _seed_file_with_fuzzy(
            file_repo, path="/a.dat",
            fuzzy_hash=ppdeep.hash(_varied_text(seed=1)),
        )
        _seed_file_with_fuzzy(
            file_repo, path="/b.dat",
            fuzzy_hash=ppdeep.hash(_varied_text(seed=2)),
        )
        report = cleanup.find_duplicates(match_kind="fuzzy")
        assert report.count == 0


# ===========================================================================
# Real near-duplicate detection
# ===========================================================================


class TestFuzzyNearDuplicates:
    def test_two_near_identical_files_grouped(
        self, cleanup, file_repo, source_repo
    ):
        # Two files derived from the same prose with a 3-char edit at end
        # produce ~0.85 jaccard fuzzy similarity - well above the 0.5
        # threshold and even at the 0.85 default.
        _seed_source(source_repo)
        base = _varied_text(seed=10)
        _seed_file_with_fuzzy(
            file_repo, path="/Library/orig.dat",
            fuzzy_hash=ppdeep.hash(base),
        )
        _seed_file_with_fuzzy(
            file_repo, path="/Backup/Old/copy.dat",
            fuzzy_hash=ppdeep.hash(base + "xyz"),  # tiny variation
        )
        report = cleanup.find_duplicates(
            match_kind="fuzzy", similarity_threshold=0.5,
        )
        assert report.count == 1
        finding = report.findings[0]
        # shortest_path picks /Library/orig.dat (17 chars) over
        # /Backup/Old/copy.dat (20 chars).
        assert finding.path == "/Backup/Old/copy.dat"
        assert finding.details["kept_path"] == "/Library/orig.dat"
        assert finding.details["match_kind"] == "fuzzy"
        assert finding.details["dupset_id"].startswith("fuzzy:")
        assert "fuzzy_hash" in finding.details
        assert "kept_fuzzy_hash" in finding.details
        assert finding.details["similarity_threshold"] == 0.5

    def test_higher_threshold_suppresses_loose_matches(
        self, cleanup, file_repo, source_repo
    ):
        # Substantial divergence (replacing 50 chars near the end) should
        # produce ~0.81 jaccard - matches at threshold 0.5 but NOT at 0.99.
        _seed_source(source_repo)
        base = _varied_text(seed=20)
        diverged = base[:-50] + _varied_text(seed=99, length=50)
        _seed_file_with_fuzzy(
            file_repo, path="/a.dat",
            fuzzy_hash=ppdeep.hash(base),
        )
        _seed_file_with_fuzzy(
            file_repo, path="/b.dat",
            fuzzy_hash=ppdeep.hash(diverged),
        )
        # At 0.5 these should match.
        loose = cleanup.find_duplicates(
            match_kind="fuzzy", similarity_threshold=0.5,
        )
        assert loose.count == 1
        # At 0.99 they should NOT match.
        strict = cleanup.find_duplicates(
            match_kind="fuzzy", similarity_threshold=0.99,
        )
        assert strict.count == 0

    def test_transitive_grouping_three_files(
        self, cleanup, file_repo, source_repo
    ):
        # A, B, C all derived from the same base with progressive small
        # edits -> all three land in the same connected component via
        # transitive matches, even if A~C isn't a direct LSH bucket hit.
        _seed_source(source_repo)
        base = _varied_text(seed=30)
        _seed_file_with_fuzzy(
            file_repo, path="/A.dat",
            fuzzy_hash=ppdeep.hash(base),
        )
        _seed_file_with_fuzzy(
            file_repo, path="/library/B.dat",
            fuzzy_hash=ppdeep.hash(base + "a"),
        )
        _seed_file_with_fuzzy(
            file_repo, path="/backup/old/C.dat",
            fuzzy_hash=ppdeep.hash(base + "ab"),
        )
        report = cleanup.find_duplicates(
            match_kind="fuzzy", similarity_threshold=0.5,
        )
        # All three should be in one component => 2 findings + 1 keeper.
        assert report.count == 2
        # All findings share one dupset_id.
        dupsets = {f.details["dupset_id"] for f in report.findings}
        assert len(dupsets) == 1
        # The keeper is /A.dat (shortest path, 6 chars).
        for f in report.findings:
            assert f.details["kept_path"] == "/A.dat"


# ===========================================================================
# Strategy + keep_under interactions in fuzzy mode
# ===========================================================================


class TestFuzzyStrategy:
    def test_oldest_strategy_in_fuzzy_mode(
        self, cleanup, file_repo, source_repo
    ):
        _seed_source(source_repo)
        base = _varied_text(seed=40)
        _seed_file_with_fuzzy(
            file_repo, path="/recent.dat",
            fuzzy_hash=ppdeep.hash(base),
            mtime=datetime(2024, 6, 15),
        )
        _seed_file_with_fuzzy(
            file_repo, path="/original.dat",
            fuzzy_hash=ppdeep.hash(base + "xx"),
            mtime=datetime(2020, 1, 1),
        )
        report = cleanup.find_duplicates(
            match_kind="fuzzy",
            similarity_threshold=0.5,
            keep_strategy="oldest",
        )
        assert report.count == 1
        assert report.findings[0].path == "/recent.dat"
        assert report.findings[0].details["kept_path"] == "/original.dat"

    def test_keep_under_in_fuzzy_mode(
        self, cleanup, file_repo, source_repo
    ):
        _seed_source(source_repo)
        base = _varied_text(seed=50)
        _seed_file_with_fuzzy(
            file_repo, path="/short.dat",
            fuzzy_hash=ppdeep.hash(base),
        )
        _seed_file_with_fuzzy(
            file_repo, path="/Library/long/path/version.dat",
            fuzzy_hash=ppdeep.hash(base + "yy"),
        )
        report = cleanup.find_duplicates(
            match_kind="fuzzy",
            similarity_threshold=0.5,
            keep_strategy="shortest_path",
            keep_under="/Library",
        )
        assert report.count == 1
        # Despite /short.dat being shorter, /Library/... wins via keep_under.
        assert report.findings[0].path == "/short.dat"
        assert "Library" in report.findings[0].details["kept_path"]
        assert "keep_under" in report.findings[0].details["kept_reason"]
