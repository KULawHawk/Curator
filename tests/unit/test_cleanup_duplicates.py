"""Unit tests for CleanupService.find_duplicates (Phase Gamma F7, v0.28).

Covers:
    * file_repo None raises
    * invalid keep_strategy raises
    * empty index / no duplicates returns empty report
    * each keep_strategy (shortest_path / longest_path / oldest / newest)
    * keep_under: pure-prefix-wins, no-match-fallthrough, tiebreaker-with-strategy
    * 3-file groups keep one and flag two
    * source_id + root_prefix filter narrowing
    * apply path: send2trash, audit-logged, safety-REFUSE-skipped
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.services.cleanup import (
    KEEP_STRATEGIES,
    ApplyOutcome,
    CleanupKind,
    CleanupService,
)
from curator.services.safety import SafetyService
from curator.storage import CuratorDB
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.source_repo import SourceRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Real SQLite DB with schema applied. One per test for isolation."""
    db_path = tmp_path / "dupe_test.db"
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
    """SafetyService with no app_data / os_managed paths => permissive."""
    return SafetyService(app_data_paths=[], os_managed_paths=[])


@pytest.fixture
def cleanup(loose_safety, file_repo):
    return CleanupService(loose_safety, file_repo=file_repo)


def _seed(
    file_repo: FileRepository,
    source_repo: SourceRepository,
    *,
    source_id: str = "local",
    files: list[tuple[str, str, int, datetime]],
) -> list[FileEntity]:
    """Insert files into the index with given (path, hash, size, mtime).

    Returns the inserted FileEntity instances.
    """
    # Make sure the source exists so the FK is satisfied.
    try:
        source_repo.insert(SourceConfig(
            source_id=source_id,
            source_type="local",
            display_name=source_id,
        ))
    except Exception:
        pass  # Already exists.

    inserted = []
    for path, hash_hex, size, mtime in files:
        entity = FileEntity(
            curator_id=uuid4(),
            source_id=source_id,
            source_path=path,
            size=size,
            mtime=mtime,
            xxhash3_128=hash_hex,
            extension=Path(path).suffix.lower() or None,
        )
        file_repo.upsert(entity)
        inserted.append(entity)
    return inserted


# ===========================================================================
# Guard rails
# ===========================================================================


class TestFindDuplicatesGuards:
    def test_no_file_repo_raises(self, loose_safety):
        svc = CleanupService(loose_safety, file_repo=None)
        with pytest.raises(RuntimeError, match="find_duplicates requires"):
            svc.find_duplicates()

    def test_invalid_keep_strategy_raises(self, cleanup):
        with pytest.raises(ValueError, match="unknown keep_strategy"):
            cleanup.find_duplicates(keep_strategy="bogus")

    def test_known_strategies_listed(self):
        # Sanity check that our public constant reflects what we accept.
        assert "shortest_path" in KEEP_STRATEGIES
        assert "longest_path" in KEEP_STRATEGIES
        assert "oldest" in KEEP_STRATEGIES
        assert "newest" in KEEP_STRATEGIES


# ===========================================================================
# Empty / no-duplicate cases
# ===========================================================================


class TestEmptyAndNoDuplicates:
    def test_empty_index_returns_empty(self, cleanup):
        report = cleanup.find_duplicates()
        assert report.kind == CleanupKind.DUPLICATE_FILE
        assert report.count == 0
        assert report.errors == []

    def test_unique_files_no_duplicates(self, cleanup, file_repo, source_repo):
        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            ("C:/a.txt", "hash_a", 100, now),
            ("C:/b.txt", "hash_b", 200, now),
            ("C:/c.txt", "hash_c", 300, now),
        ])
        report = cleanup.find_duplicates()
        assert report.count == 0


# ===========================================================================
# Keep strategies on a two-file group
# ===========================================================================


class TestKeepStrategies:
    def test_shortest_path_default(self, cleanup, file_repo, source_repo):
        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            ("C:/Library/song.mp3", "h1", 100, now),
            ("C:/Library/Backup/Old/Music/song.mp3", "h1", 100, now),
        ])
        report = cleanup.find_duplicates()
        assert report.count == 1
        # The deeper one is the duplicate; the shorter is kept.
        finding = report.findings[0]
        assert finding.path == "C:/Library/Backup/Old/Music/song.mp3"
        assert finding.details["kept_path"] == "C:/Library/song.mp3"
        assert finding.details["kept_reason"] == "shortest_path"

    def test_longest_path(self, cleanup, file_repo, source_repo):
        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            ("C:/Library/song.mp3", "h1", 100, now),
            ("C:/Library/Backup/Old/Music/song.mp3", "h1", 100, now),
        ])
        report = cleanup.find_duplicates(keep_strategy="longest_path")
        assert report.count == 1
        # Now the SHORT one is the duplicate.
        finding = report.findings[0]
        assert finding.path == "C:/Library/song.mp3"
        assert finding.details["kept_path"] == "C:/Library/Backup/Old/Music/song.mp3"
        assert finding.details["kept_reason"] == "longest_path"

    def test_oldest(self, cleanup, file_repo, source_repo):
        old = datetime(2020, 1, 1)
        new = datetime(2024, 6, 15)
        _seed(file_repo, source_repo, files=[
            ("C:/recent.dat", "h1", 100, new),
            ("C:/original.dat", "h1", 100, old),
        ])
        report = cleanup.find_duplicates(keep_strategy="oldest")
        assert report.count == 1
        finding = report.findings[0]
        assert finding.path == "C:/recent.dat"  # the newer is the duplicate
        assert finding.details["kept_path"] == "C:/original.dat"

    def test_newest(self, cleanup, file_repo, source_repo):
        old = datetime(2020, 1, 1)
        new = datetime(2024, 6, 15)
        _seed(file_repo, source_repo, files=[
            ("C:/recent.dat", "h1", 100, new),
            ("C:/original.dat", "h1", 100, old),
        ])
        report = cleanup.find_duplicates(keep_strategy="newest")
        assert report.count == 1
        finding = report.findings[0]
        assert finding.path == "C:/original.dat"
        assert finding.details["kept_path"] == "C:/recent.dat"


# ===========================================================================
# keep_under
# ===========================================================================


class TestKeepUnder:
    def test_keep_under_wins_over_strategy(self, cleanup, file_repo, source_repo):
        # Without keep_under, shortest_path would pick C:/short.mp3.
        # With keep_under=C:/Library, the file under Library wins
        # despite being longer.
        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            ("C:/short.mp3", "h1", 100, now),
            ("C:/Library/album/song.mp3", "h1", 100, now),
        ])
        report = cleanup.find_duplicates(
            keep_strategy="shortest_path",
            keep_under="C:/Library",
        )
        assert report.count == 1
        finding = report.findings[0]
        # The Library file is the keeper; C:/short.mp3 is the duplicate.
        assert finding.path == "C:/short.mp3"
        assert finding.details["kept_path"] == "C:/Library/album/song.mp3"
        assert "keep_under" in finding.details["kept_reason"]

    def test_keep_under_falls_through_when_no_match(
        self, cleanup, file_repo, source_repo
    ):
        # No file is under the keep_under prefix, so the strategy
        # applies to the whole group as normal.
        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            ("C:/a/file.dat", "h1", 100, now),
            ("C:/b/file.dat", "h1", 100, now),
        ])
        report = cleanup.find_duplicates(
            keep_strategy="shortest_path",
            keep_under="C:/Library",
        )
        assert report.count == 1
        finding = report.findings[0]
        # Both paths are equal length (8); the kept one is the
        # tiebreaker (alphabetic via path-string sort).
        assert finding.details["kept_reason"] == "shortest_path"
        assert "keep_under" not in finding.details["kept_reason"]

    def test_keep_under_with_strategy_breaking_tie(
        self, cleanup, file_repo, source_repo
    ):
        # Two files match keep_under; oldest tiebreaker picks among them.
        old = datetime(2020, 1, 1)
        mid = datetime(2022, 1, 1)
        new = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            ("C:/Other/x.dat", "h1", 100, mid),  # NOT under Library
            ("C:/Library/recent/x.dat", "h1", 100, new),
            ("C:/Library/old/x.dat", "h1", 100, old),
        ])
        report = cleanup.find_duplicates(
            keep_strategy="oldest",
            keep_under="C:/Library",
        )
        # The keeper should be the OLDEST file under Library.
        kept_paths = {f.details["kept_path"] for f in report.findings}
        assert kept_paths == {"C:/Library/old/x.dat"}
        # 2 duplicates flagged: Other/x.dat and Library/recent/x.dat.
        flagged = {f.path for f in report.findings}
        assert flagged == {
            "C:/Other/x.dat",
            "C:/Library/recent/x.dat",
        }


# ===========================================================================
# Larger group: 3-file duplicate set
# ===========================================================================


class TestThreeFileGroup:
    def test_keeps_one_flags_rest(self, cleanup, file_repo, source_repo):
        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            ("C:/Music/song.mp3", "h1", 100, now),
            ("C:/Downloads/song.mp3", "h1", 100, now),
            ("C:/Backup/2023/Music/song.mp3", "h1", 100, now),
        ])
        report = cleanup.find_duplicates()  # shortest_path default
        assert report.count == 2
        # All findings have the same kept_path AND same dupset_id.
        kept = {f.details["kept_path"] for f in report.findings}
        assert kept == {"C:/Music/song.mp3"}  # shortest of the three (18 chars)
        dupsets = {f.details["dupset_id"] for f in report.findings}
        assert dupsets == {"h1"}


# ===========================================================================
# Filter narrowing
# ===========================================================================


class TestFiltering:
    def test_root_prefix_narrows(self, cleanup, file_repo, source_repo):
        now = datetime(2024, 1, 1)
        # Two duplicate sets: one under Downloads, one under Library.
        _seed(file_repo, source_repo, files=[
            ("C:/Downloads/a.dat", "hashA", 100, now),
            ("C:/Downloads/copies/a.dat", "hashA", 100, now),
            ("C:/Library/b.dat", "hashB", 100, now),
            ("C:/Library/Old/b.dat", "hashB", 100, now),
        ])
        # Restrict to Downloads only.
        report = cleanup.find_duplicates(root_prefix="C:/Downloads")
        # Only the Downloads-side duplicate is reported.
        assert report.count == 1
        assert report.findings[0].path == "C:/Downloads/copies/a.dat"

    def test_source_id_narrows(self, cleanup, file_repo, source_repo):
        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, source_id="local", files=[
            ("C:/local_a.dat", "hX", 100, now),
            ("C:/local_b.dat", "hX", 100, now),
        ])
        _seed(file_repo, source_repo, source_id="other", files=[
            ("/other/a.dat", "hY", 100, now),
            ("/other/b.dat", "hY", 100, now),
        ])
        report = cleanup.find_duplicates(source_id="local")
        assert report.count == 1
        assert "local" in report.findings[0].path


# ===========================================================================
# Apply path: send2trash, audit, safety
# ===========================================================================


class TestApplyDuplicates:
    def test_apply_deletes_duplicates(
        self, cleanup, file_repo, source_repo, tmp_path
    ):
        # Create real files on disk, seed both into the index, then
        # apply. The duplicate should be removed from disk; the keeper
        # untouched.
        keeper = tmp_path / "keeper.dat"
        dup = tmp_path / "deep" / "dir" / "dup.dat"
        dup.parent.mkdir(parents=True)
        keeper.write_bytes(b"identical content")
        dup.write_bytes(b"identical content")

        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            (str(keeper), "h1", 17, now),
            (str(dup), "h1", 17, now),
        ])
        report = cleanup.find_duplicates()
        assert report.count == 1
        # Apply with --no-trash so test doesn't depend on trash backend.
        result = cleanup.apply(report, use_trash=False)
        assert result.deleted_count == 1
        assert result.failed_count == 0
        assert keeper.exists()
        assert not dup.exists()

    def test_apply_audit_records_kind(
        self, cleanup, file_repo, source_repo, tmp_path
    ):
        audit = MagicMock()
        cleanup.audit = audit
        keeper = tmp_path / "keep.dat"
        dup = tmp_path / "subdir" / "dup.dat"
        dup.parent.mkdir(parents=True)
        keeper.write_bytes(b"x")
        dup.write_bytes(b"x")
        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            (str(keeper), "hk", 1, now),
            (str(dup), "hk", 1, now),
        ])
        report = cleanup.find_duplicates()
        cleanup.apply(report, use_trash=False)
        assert audit.log.call_count == 1
        kwargs = audit.log.call_args.kwargs
        assert kwargs["actor"] == "curator.cleanup"
        assert kwargs["action"] == "cleanup.duplicate_file.delete"
        assert kwargs["details"]["kind"] == "duplicate_file"
        assert "kept_path" in kwargs["details"]
        assert "dupset_id" in kwargs["details"]

    def test_safety_refuse_skips_dup(
        self, file_repo, source_repo, tmp_path
    ):
        # Set up SafetyService to REFUSE the tmp_path tree.
        strict_safety = SafetyService(
            app_data_paths=[], os_managed_paths=[tmp_path],
        )
        svc = CleanupService(strict_safety, file_repo=file_repo)
        keeper = tmp_path / "k.dat"
        dup = tmp_path / "deep" / "d.dat"
        dup.parent.mkdir(parents=True)
        keeper.write_bytes(b"x")
        dup.write_bytes(b"x")
        now = datetime(2024, 1, 1)
        _seed(file_repo, source_repo, files=[
            (str(keeper), "hk", 1, now),
            (str(dup), "hk", 1, now),
        ])
        report = svc.find_duplicates()
        result = svc.apply(report, use_trash=False)
        assert result.deleted_count == 0
        assert result.skipped_count == 1
        assert result.results[0].outcome == ApplyOutcome.SKIPPED_REFUSE
        # The duplicate file is preserved \u2014 SafetyService blocked it.
        assert dup.exists()
        assert keeper.exists()
