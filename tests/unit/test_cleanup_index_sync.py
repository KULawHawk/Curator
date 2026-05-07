"""Index-sync tests for CleanupService.apply (Phase Gamma F8, v0.29).

When cleanup removes a file from disk, the corresponding FileEntity in
the Curator index should be soft-deleted so subsequent queries don't
return phantoms. This is best-effort: failures and missing entries are
swallowed silently.

Covers:
    * Duplicate apply marks the duplicate's FileEntity deleted but
      leaves the keeper untouched.
    * Subsequent find_duplicates run on the same index doesn't
      re-flag the just-deleted file (proves the phantom-file gap is closed).
    * Junk-file apply on an indexed file marks it deleted.
    * Junk-file apply on an UN-indexed file doesn't error (the common
      Thumbs.db case where the file was never scanned).
    * Empty-dir apply does NOT touch the index (directories aren't
      FileEntity rows).
    * CleanupService constructed without file_repo still applies
      cleanly (no AttributeError, no crash).
    * mark_deleted raising doesn't fail the overall apply pass.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.services.cleanup import (
    ApplyOutcome,
    CleanupFinding,
    CleanupKind,
    CleanupService,
)
from curator.services.safety import SafetyService
from curator.storage import CuratorDB
from curator.storage.queries import FileQuery
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.source_repo import SourceRepository


# ---------------------------------------------------------------------------
# Fixtures (mirror test_cleanup_duplicates fixtures)
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "index_sync_test.db"
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


def _seed_file(
    file_repo: FileRepository,
    *,
    path: str,
    hash_hex: str | None = None,
    size: int = 100,
    source_id: str = "local",
    mtime: datetime | None = None,
) -> FileEntity:
    entity = FileEntity(
        curator_id=uuid4(),
        source_id=source_id,
        source_path=path,
        size=size,
        mtime=mtime or datetime(2024, 1, 1),
        xxhash3_128=hash_hex,
        extension=Path(path).suffix.lower() or None,
    )
    file_repo.upsert(entity)
    return entity


# ===========================================================================
# Duplicate apply
# ===========================================================================


class TestDuplicateApplyIndexSync:
    def test_apply_marks_duplicate_deleted_keeps_keeper(
        self, cleanup, file_repo, source_repo, tmp_path
    ):
        _seed_source(source_repo)
        keeper_path = tmp_path / "keeper.dat"
        dup_path = tmp_path / "deep" / "dup.dat"
        dup_path.parent.mkdir(parents=True)
        keeper_path.write_bytes(b"identical")
        dup_path.write_bytes(b"identical")

        keeper_entity = _seed_file(
            file_repo, path=str(keeper_path), hash_hex="hX",
        )
        dup_entity = _seed_file(
            file_repo, path=str(dup_path), hash_hex="hX",
        )

        report = cleanup.find_duplicates()
        assert report.count == 1
        result = cleanup.apply(report, use_trash=False)
        assert result.deleted_count == 1

        # Re-fetch by curator_id to check soft-delete state.
        kept_after = file_repo.get(keeper_entity.curator_id)
        dup_after = file_repo.get(dup_entity.curator_id)
        assert kept_after is not None
        assert kept_after.deleted_at is None  # keeper preserved
        assert dup_after is not None
        assert dup_after.deleted_at is not None  # duplicate marked deleted

    def test_subsequent_find_duplicates_does_not_re_flag(
        self, cleanup, file_repo, source_repo, tmp_path
    ):
        # The proof that the phantom-file gap is closed: after apply,
        # running find_duplicates again returns count=0 because the
        # deleted FileEntity is filtered out by FileQuery(deleted=False).
        _seed_source(source_repo)
        keeper = tmp_path / "keep.dat"
        dup = tmp_path / "subdir" / "dup.dat"
        dup.parent.mkdir(parents=True)
        keeper.write_bytes(b"same")
        dup.write_bytes(b"same")

        _seed_file(file_repo, path=str(keeper), hash_hex="hY")
        _seed_file(file_repo, path=str(dup), hash_hex="hY")

        first = cleanup.find_duplicates()
        assert first.count == 1
        cleanup.apply(first, use_trash=False)

        # Run dedup AGAIN \u2014 the duplicate should no longer appear.
        second = cleanup.find_duplicates()
        assert second.count == 0


# ===========================================================================
# Junk-file apply (indexed and un-indexed)
# ===========================================================================


class TestJunkApplyIndexSync:
    def test_junk_apply_marks_indexed_file_deleted(
        self, cleanup, file_repo, source_repo, tmp_path
    ):
        _seed_source(source_repo)
        thumbs = tmp_path / "Thumbs.db"
        thumbs.write_bytes(b"junk")
        # Pretend this Thumbs.db was scanned + indexed.
        entity = _seed_file(file_repo, path=str(thumbs), size=4)

        report = cleanup.find_junk_files(tmp_path)
        cleanup.apply(report, use_trash=False)

        after = file_repo.get(entity.curator_id)
        assert after is not None
        assert after.deleted_at is not None

    def test_junk_apply_unindexed_file_no_error(
        self, cleanup, source_repo, tmp_path
    ):
        # Common case: Thumbs.db was never indexed (filtered by classification).
        # The cleanup should still succeed; index sync silently skips.
        _seed_source(source_repo)  # source exists but no file entries
        thumbs = tmp_path / "Thumbs.db"
        thumbs.write_bytes(b"junk")

        report = cleanup.find_junk_files(tmp_path)
        result = cleanup.apply(report, use_trash=False)
        assert result.deleted_count == 1
        assert result.failed_count == 0
        assert not thumbs.exists()


# ===========================================================================
# Empty-dir apply (should NOT touch index)
# ===========================================================================


class TestEmptyDirApplyIndexSync:
    def test_empty_dir_apply_does_not_query_index(
        self, loose_safety, file_repo, tmp_path
    ):
        # Use a Mock for file_repo so we can verify that empty-dir apply
        # never invokes find_by_path / mark_deleted (directories aren't
        # FileEntity rows).
        mock_repo = MagicMock(spec=file_repo)
        svc = CleanupService(loose_safety, file_repo=mock_repo)

        empty = tmp_path / "empty_leaf"
        empty.mkdir()
        report = svc.find_empty_dirs(tmp_path)
        assert report.count == 1
        result = svc.apply(report)
        assert result.deleted_count == 1

        # The directory was rmdir'd successfully.
        assert not empty.exists()
        # And we did NOT touch the index for it.
        assert mock_repo.find_by_path.call_count == 0
        assert mock_repo.mark_deleted.call_count == 0


# ===========================================================================
# Defensive cases
# ===========================================================================


class TestIndexSyncDefensive:
    def test_apply_works_with_no_file_repo(
        self, loose_safety, tmp_path
    ):
        # CleanupService constructed without file_repo (legacy callers)
        # should still apply cleanly. No AttributeError on _mark_index_deleted.
        svc = CleanupService(loose_safety, file_repo=None)
        thumbs = tmp_path / "Thumbs.db"
        thumbs.write_bytes(b"x")
        report = svc.find_junk_files(tmp_path)
        result = svc.apply(report, use_trash=False)
        assert result.deleted_count == 1
        assert not thumbs.exists()

    def test_mark_deleted_exception_does_not_fail_apply(
        self, loose_safety, source_repo, tmp_path, monkeypatch
    ):
        # If mark_deleted raises (e.g. DB locked), the cleanup operation
        # is still considered DELETED \u2014 the file is gone from disk,
        # which is what the user asked for. Index sync is best-effort.
        _seed_source(source_repo)
        bad_repo = MagicMock()
        # find_by_path returns something that looks like an indexed file.
        fake_entity = MagicMock()
        fake_entity.curator_id = uuid4()
        bad_repo.find_by_path.return_value = fake_entity
        bad_repo.mark_deleted.side_effect = RuntimeError("DB locked")

        svc = CleanupService(loose_safety, file_repo=bad_repo)
        thumbs = tmp_path / "Thumbs.db"
        thumbs.write_bytes(b"x")
        report = svc.find_junk_files(tmp_path)
        result = svc.apply(report, use_trash=False)

        # Apply should report DELETED despite the index-sync failure.
        assert result.deleted_count == 1
        assert result.failed_count == 0
        assert not thumbs.exists()
        # And we DID try to mark_deleted (proving the exception path
        # was exercised, not just skipped because of an earlier guard).
        assert bad_repo.mark_deleted.call_count == 1

    def test_find_by_path_exception_does_not_fail_apply(
        self, loose_safety, tmp_path
    ):
        # Same idea but the failure happens at lookup time, not delete time.
        bad_repo = MagicMock()
        bad_repo.find_by_path.side_effect = RuntimeError("DB unreachable")

        svc = CleanupService(loose_safety, file_repo=bad_repo)
        thumbs = tmp_path / "Thumbs.db"
        thumbs.write_bytes(b"x")
        report = svc.find_junk_files(tmp_path)
        result = svc.apply(report, use_trash=False)
        assert result.deleted_count == 1
        assert result.failed_count == 0
