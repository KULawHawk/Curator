"""Integration tests for ``ScanService.scan_paths`` (Phase Beta v0.17).

The contract (``docs/PHASE_BETA_WATCH.md``): given a list of paths,
process each one through the same hash + classification + lineage
pipeline as a full scan, but skip enumeration. Vanished paths get
soft-deleted; new paths get inserted; modified paths get re-hashed.

These tests don't need ``watchfiles`` — they call ``scan_paths``
directly. The end-to-end watcher → scan_paths integration is exercised
by the watch CLI command and is left to manual smoke-testing for v0.17;
v0.18 may add an automated end-to-end test.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


# We use ``source_id="local"`` throughout — the local source plugin's
# registered source_type matches it, so the hash pipeline can read
# files. ``ScanService._ensure_source`` auto-creates the SourceConfig
# row on first call, so no per-test fixture setup is needed.
SOURCE_ID = "local"


# ---------------------------------------------------------------------------
# scan_paths: file added (new path, never seen before)
# ---------------------------------------------------------------------------

class TestScanPathsAdded:
    def test_creates_file_entity_for_new_path(self, services, repos, tmp_path):
        target = tmp_path / "fresh.txt"
        target.write_text("brand new content")

        report = services.scan.scan_paths(
            source_id=SOURCE_ID,
            paths=[str(target)],
        )

        assert report.files_seen == 1
        assert report.files_new == 1
        assert report.files_updated == 0
        assert report.errors == 0

        # FileEntity exists in the index
        f = repos.files.find_by_path(SOURCE_ID, str(target))
        assert f is not None
        assert f.size == len("brand new content")

    def test_hashes_are_computed(self, services, repos, tmp_path):
        target = tmp_path / "withhash.txt"
        target.write_text("content for hashing")

        services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )

        f = repos.files.find_by_path(SOURCE_ID, str(target))
        assert f is not None
        assert f.xxhash3_128 is not None
        assert f.md5 is not None
        # Phase Alpha computes fuzzy_hash conditionally based on size;
        # 19-byte content is above the ssdeep minimum so it should appear.
        # (We don't assert on it because the threshold may change.)


# ---------------------------------------------------------------------------
# scan_paths: file modified (existing path, content changed)
# ---------------------------------------------------------------------------

class TestScanPathsModified:
    def test_updates_size_after_modification(self, services, repos, tmp_path):
        target = tmp_path / "mutable.txt"
        target.write_text("original content")
        services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )

        original = repos.files.find_by_path(SOURCE_ID, str(target))
        assert original is not None
        original_size = original.size
        original_hash = original.xxhash3_128

        # Mutate. ``time.sleep`` ensures mtime advances on filesystems
        # with second-resolution timestamps.
        time.sleep(1.1)
        target.write_text("modified content with more bytes than before")

        report = services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )
        assert report.files_updated == 1
        assert report.files_new == 0

        updated = repos.files.find_by_path(SOURCE_ID, str(target))
        assert updated is not None
        assert updated.size != original_size
        # Hash must have been recomputed (different content = different bytes)
        assert updated.xxhash3_128 != original_hash

    def test_unchanged_file_does_not_rehash(self, services, repos, tmp_path):
        target = tmp_path / "same.txt"
        target.write_text("steady content")

        services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )
        first = repos.files.find_by_path(SOURCE_ID, str(target))
        assert first is not None
        first_hash = first.xxhash3_128

        report = services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )
        assert report.files_updated == 0
        assert report.files_unchanged == 1

        second = repos.files.find_by_path(SOURCE_ID, str(target))
        assert second is not None
        assert second.xxhash3_128 == first_hash


# ---------------------------------------------------------------------------
# scan_paths: file deleted (path no longer on disk)
# ---------------------------------------------------------------------------

class TestScanPathsDeleted:
    def test_marks_known_file_deleted_when_path_vanishes(
        self, services, repos, tmp_path
    ):
        target = tmp_path / "doomed.txt"
        target.write_text("this will be removed")

        # First scan: indexes the file.
        services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )
        before = repos.files.find_by_path(SOURCE_ID, str(target))
        assert before is not None
        assert before.deleted_at is None

        # Remove from disk.
        target.unlink()
        assert not target.exists()

        report = services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )
        assert report.files_deleted == 1
        assert report.files_seen == 1

        # File row is now soft-deleted (still queryable for audit purposes).
        after = repos.files.get(before.curator_id)
        assert after is not None
        assert after.deleted_at is not None

    def test_unknown_vanished_path_is_silent_skip(
        self, services, repos, tmp_path
    ):
        """A path that was never indexed AND doesn't exist on disk is
        a no-op — no error, no spurious deletion."""
        ghost = tmp_path / "never_existed.txt"

        report = services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(ghost)],
        )
        assert report.files_deleted == 0
        assert report.files_seen == 0
        assert report.errors == 0

    def test_already_deleted_is_idempotent(
        self, services, repos, tmp_path
    ):
        """Calling scan_paths twice for the same vanished file shouldn't
        increment files_deleted twice."""
        target = tmp_path / "twice.txt"
        target.write_text("content")
        services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )
        target.unlink()

        first = services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )
        assert first.files_deleted == 1

        second = services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )
        # Second call sees the entity is already deleted_at != None,
        # so it doesn't increment again.
        assert second.files_deleted == 0


# ---------------------------------------------------------------------------
# scan_paths: mixed batch
# ---------------------------------------------------------------------------

class TestScanPathsMixed:
    def test_mixed_batch_handles_each_correctly(
        self, services, repos, tmp_path
    ):
        """A single call with one new file, one modified, one deleted."""

        # Setup: index two files first.
        keep_path = tmp_path / "keep.txt"
        modify_path = tmp_path / "modify.txt"
        keep_path.write_text("kept verbatim")
        modify_path.write_text("v1")
        services.scan.scan_paths(
            source_id=SOURCE_ID,
            paths=[str(keep_path), str(modify_path)],
        )

        # Now: add a new file, modify the existing one, delete `keep`.
        new_path = tmp_path / "new.txt"
        new_path.write_text("freshly added")
        time.sleep(1.1)
        modify_path.write_text("v2 with more content this time")
        keep_path.unlink()

        report = services.scan.scan_paths(
            source_id=SOURCE_ID,
            paths=[str(new_path), str(modify_path), str(keep_path)],
        )
        assert report.files_new == 1
        assert report.files_updated == 1
        assert report.files_deleted == 1

    def test_skips_directory_paths_silently(
        self, services, repos, tmp_path
    ):
        """If a directory path slips through, scan_paths skips it
        rather than recursing or erroring."""
        subdir = tmp_path / "some_subdir"
        subdir.mkdir()
        # No file ever indexed here — just the dir.

        report = services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(subdir)],
        )
        assert report.files_seen == 0
        assert report.errors == 0

    def test_dedupes_repeated_paths_in_one_call(
        self, services, repos, tmp_path
    ):
        """If the caller hands us the same path multiple times, we
        process it once."""
        target = tmp_path / "once.txt"
        target.write_text("only once please")

        report = services.scan.scan_paths(
            source_id=SOURCE_ID,
            paths=[str(target), str(target), str(target)],
        )
        assert report.files_seen == 1
        assert report.files_new == 1


# ---------------------------------------------------------------------------
# scan_paths: audit + job rows
# ---------------------------------------------------------------------------

class TestScanPathsAudit:
    def test_writes_scan_start_and_complete_audit_entries(
        self, services, repos, tmp_path
    ):
        target = tmp_path / "audited.txt"
        target.write_text("audit me")

        services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )

        starts = repos.audit.query(action="scan.start")
        completes = repos.audit.query(action="scan.complete")
        # At least one of each — kind="incremental" distinguishes from full.
        assert any(e.details.get("kind") == "incremental" for e in starts)
        assert any(e.details.get("kind") == "incremental" for e in completes)

    def test_writes_scan_job_row(
        self, services, repos, tmp_path
    ):
        target = tmp_path / "jobrow.txt"
        target.write_text("creates a job")

        report = services.scan.scan_paths(
            source_id=SOURCE_ID, paths=[str(target)],
        )
        job = repos.jobs.get(report.job_id)
        assert job is not None
        assert job.status == "completed"
        # Synthetic root marker so audit queries can spot incremental scans.
        assert job.root_path.startswith("<paths:")
