"""Tracer Phase 4 P2 tests: cross-source ``overwrite-with-backup`` + ``rename-with-suffix`` dispatch.

Covers the new flows in :class:`MigrationService` that wire the
``curator_source_rename`` hookspec (added in P1) into cross-source
collision resolution. v1.3.0 degraded these modes to skip-with-warning;
v1.4.0+ ships the real implementation.

Test layout:

* ``TestOverwriteWithBackupCrossSource`` -- the rename + retry flow on success
* ``TestOverwriteWithBackupFallback``    -- plugin-doesn't-implement / rename-fails degradation paths
* ``TestRenameWithSuffixCrossSource``    -- the FileExistsError retry-write loop
* ``TestRenameWithSuffixFallback``       -- 9999 exhaustion + non-collision failure paths

Tests focus on the two dispatch helpers directly
(:meth:`_cross_source_overwrite_with_backup`,
:meth:`_cross_source_rename_with_suffix`) by stubbing the lower-level
methods (`_cross_source_transfer`, `_find_existing_dst_file_id_for_overwrite`,
`_attempt_cross_source_backup_rename`) -- isolating the dispatch logic
from full pluggy infrastructure. End-to-end coverage of the full
`apply()` -> `_execute_one_cross_source` flow lives in
test_migration_cross_source.py at the next test-coverage pass.

Per design TRACER_PHASE_4_DESIGN.md v0.2 RATIFIED §5.2.
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock
from uuid import uuid4

import pytest

from curator.services.migration import (
    MigrationMove,
    MigrationOutcome,
    MigrationService,
)
from curator.services.safety import SafetyLevel


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def service():
    """A MigrationService with mocked dependencies sufficient for dispatch tests.

    The file_repo and safety services are mocked because the dispatch
    helpers under test don't touch them; the audit repo is a MagicMock
    so we can assert on `audit.log` calls. `pm` is a MagicMock so the
    helpers' attempts to call hooks don't blow up (we'll override the
    helper methods that USE pm in the tests where needed).
    """
    file_repo = mock.MagicMock()
    safety = mock.MagicMock()
    audit = mock.MagicMock()
    pm = mock.MagicMock()
    return MigrationService(
        file_repo, safety, audit=audit, pm=pm,
    )


@pytest.fixture
def move():
    """A canonical MigrationMove for a cross-source local->gdrive transfer."""
    return MigrationMove(
        curator_id=uuid4(),
        src_path="/src/foo.mp3",
        dst_path="/dst/foo.mp3",
        safety_level=SafetyLevel.SAFE,
        size=1024,
        src_xxhash="abc123def456",
    )


# ===========================================================================
# overwrite-with-backup dispatch
# ===========================================================================


class TestOverwriteWithBackupCrossSource:
    """``_cross_source_overwrite_with_backup`` success-path coverage."""

    def test_rename_succeeds_then_retry_succeeds_returns_moved(
        self, service, move,
    ):
        """Resolve dst file_id -> rename to backup -> retry transfer -> MOVED."""
        # Stub the lower-level methods.
        service._find_existing_dst_file_id_for_overwrite = mock.MagicMock(
            return_value="existing_drive_id_xyz",
        )
        service._attempt_cross_source_backup_rename = mock.MagicMock(
            return_value=(True, None),
        )
        service._cross_source_transfer = mock.MagicMock(
            return_value=(MigrationOutcome.MOVED, "new_drive_id_abc", "hash_verified"),
        )

        result = service._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        assert result is not None
        outcome, actual_dst_file_id, verified_hash = result
        assert outcome == MigrationOutcome.MOVED
        assert actual_dst_file_id == "new_drive_id_abc"
        assert verified_hash == "hash_verified"
        # file_id resolution was called once
        service._find_existing_dst_file_id_for_overwrite.assert_called_once_with(
            "gdrive", "/dst/foo.mp3",
        )
        # rename was called with the backup name pattern
        rename_args = service._attempt_cross_source_backup_rename.call_args
        assert rename_args[0][0] == "gdrive"
        assert rename_args[0][1] == "existing_drive_id_xyz"
        backup_name = rename_args[0][2]
        assert backup_name.startswith("foo.curator-backup-")
        assert backup_name.endswith(".mp3")
        # retry transfer was called once (the only transfer the helper makes)
        assert service._cross_source_transfer.call_count == 1

    def test_audit_captures_backup_name_and_cross_source_marker(
        self, service, move,
    ):
        """On successful rename, the audit details capture backup_name + cross_source: True."""
        service._find_existing_dst_file_id_for_overwrite = mock.MagicMock(
            return_value="existing_drive_id",
        )
        service._attempt_cross_source_backup_rename = mock.MagicMock(
            return_value=(True, None),
        )
        service._cross_source_transfer = mock.MagicMock(
            return_value=(MigrationOutcome.MOVED, "/dst/foo.mp3", "h"),
        )

        service._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        # Find the audit.log call with mode='overwrite-with-backup' (success audit)
        success_audit_calls = [
            c for c in service.audit.log.call_args_list
            if c.kwargs.get("details", {}).get("mode") == "overwrite-with-backup"
        ]
        assert len(success_audit_calls) == 1
        details = success_audit_calls[0].kwargs["details"]
        assert details["cross_source"] is True
        assert details["backup_name"].startswith("foo.curator-backup-")
        assert details["existing_file_id"] == "existing_drive_id"

    def test_retry_failure_after_rename_leaves_backup_per_dm5(
        self, service, move,
    ):
        """If the retry transfer raises an exception, backup is preserved (DM-5)."""
        service._find_existing_dst_file_id_for_overwrite = mock.MagicMock(
            return_value="existing_drive_id",
        )
        service._attempt_cross_source_backup_rename = mock.MagicMock(
            return_value=(True, None),
        )
        service._cross_source_transfer = mock.MagicMock(
            side_effect=RuntimeError("network down on retry"),
        )

        result = service._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.FAILED
        assert "network down on retry" in move.error
        # Per DM-5: error message advertises that backup is preserved
        assert "preserved per DM-5" in move.error
        # The backup name appears in the error so user can find it
        assert "foo.curator-backup-" in move.error

    def test_retry_hash_mismatch_after_rename_leaves_backup_per_dm5(
        self, service, move,
    ):
        """If the retry transfer hash-mismatches, backup is still preserved (DM-5)."""
        service._find_existing_dst_file_id_for_overwrite = mock.MagicMock(
            return_value="existing_drive_id",
        )
        service._attempt_cross_source_backup_rename = mock.MagicMock(
            return_value=(True, None),
        )
        service._cross_source_transfer = mock.MagicMock(
            return_value=(MigrationOutcome.HASH_MISMATCH, "/dst/foo.mp3", "wrong_hash"),
        )

        result = service._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.HASH_MISMATCH
        assert "preserved per DM-5" in move.error
        assert move.verified_xxhash == "wrong_hash"


class TestOverwriteWithBackupFallback:
    """Cross-source overwrite-with-backup degradation paths.

    Plugins that don't implement ``curator_source_rename`` OR can't
    resolve the existing dst's file_id should degrade to v1.3.0 skip-
    with-warning behavior + audit fallback marker.
    """

    def test_resolver_returns_none_degrades_to_skip(self, service, move):
        """If file_id can't be resolved, helper returns None + sets outcome=SKIPPED_COLLISION."""
        service._find_existing_dst_file_id_for_overwrite = mock.MagicMock(
            return_value=None,
        )
        # Don't stub _attempt or _cross_source_transfer -- they shouldn't be called.
        service._attempt_cross_source_backup_rename = mock.MagicMock()
        service._cross_source_transfer = mock.MagicMock()

        result = service._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION
        service._attempt_cross_source_backup_rename.assert_not_called()
        service._cross_source_transfer.assert_not_called()
        # Audit captures the degrade reason
        degrade_calls = [
            c for c in service.audit.log.call_args_list
            if "degraded-cross-source" in c.kwargs.get("details", {}).get("mode", "")
        ]
        assert len(degrade_calls) == 1
        details = degrade_calls[0].kwargs["details"]
        assert details["cross_source"] is True
        assert details["fallback"] == "skipped"
        assert "could not resolve" in details["reason"]

    def test_rename_hook_returns_false_degrades_to_skip(self, service, move):
        """If rename hook fails (plugin doesn't implement OR rename raises),
        helper returns None + sets outcome=SKIPPED_COLLISION + audits the reason."""
        service._find_existing_dst_file_id_for_overwrite = mock.MagicMock(
            return_value="existing_drive_id",
        )
        service._attempt_cross_source_backup_rename = mock.MagicMock(
            return_value=(False, "plugin does not implement curator_source_rename"),
        )
        service._cross_source_transfer = mock.MagicMock()

        result = service._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="onedrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION
        # Retry transfer NEVER attempted when rename fails
        service._cross_source_transfer.assert_not_called()
        # Audit captures the plugin reason
        degrade_calls = [
            c for c in service.audit.log.call_args_list
            if "degraded-cross-source" in c.kwargs.get("details", {}).get("mode", "")
        ]
        assert len(degrade_calls) == 1
        details = degrade_calls[0].kwargs["details"]
        assert details["fallback"] == "skipped"
        assert "plugin does not implement" in details["reason"]


# ===========================================================================
# rename-with-suffix dispatch
# ===========================================================================


class TestRenameWithSuffixCrossSource:
    """``_cross_source_rename_with_suffix`` retry-write loop coverage."""

    def test_first_attempt_succeeds_returns_moved_at_suffix_1(
        self, service, move,
    ):
        """First .curator-1 attempt succeeds -> MOVED + audit with suffix_n=1."""
        service._cross_source_transfer = mock.MagicMock(
            return_value=(MigrationOutcome.MOVED, "/dst/foo.curator-1.mp3", "h1"),
        )

        result = service._cross_source_rename_with_suffix(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        assert result is not None
        outcome, actual_dst, verified = result
        assert outcome == MigrationOutcome.MOVED
        assert actual_dst == "/dst/foo.curator-1.mp3"
        assert verified == "h1"
        # Only one transfer attempt
        assert service._cross_source_transfer.call_count == 1
        # The transfer was called with the .curator-1 path
        call_kwargs = service._cross_source_transfer.call_args.kwargs
        assert call_kwargs["dst_path"].endswith("foo.curator-1.mp3")
        # Audit captures suffix_n=1 + cross_source: True
        success_audit = [
            c for c in service.audit.log.call_args_list
            if c.kwargs.get("details", {}).get("mode") == "rename-with-suffix"
        ]
        assert len(success_audit) == 1
        details = success_audit[0].kwargs["details"]
        assert details["cross_source"] is True
        assert details["suffix_n"] == 1
        assert details["original_dst"] == "/dst/foo.mp3"
        assert details["renamed_dst"].endswith("foo.curator-1.mp3")

    def test_two_collisions_then_third_succeeds_returns_suffix_3(
        self, service, move,
    ):
        """First two suffix attempts collide; third succeeds -> suffix_n=3 in audit."""
        # SKIPPED_COLLISION twice (suffix-1, suffix-2), then MOVED on suffix-3.
        service._cross_source_transfer = mock.MagicMock(
            side_effect=[
                (MigrationOutcome.SKIPPED_COLLISION, "/dst/foo.curator-1.mp3", None),
                (MigrationOutcome.SKIPPED_COLLISION, "/dst/foo.curator-2.mp3", None),
                (MigrationOutcome.MOVED, "/dst/foo.curator-3.mp3", "h3"),
            ],
        )

        result = service._cross_source_rename_with_suffix(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        assert result is not None
        outcome, actual_dst, verified = result
        assert outcome == MigrationOutcome.MOVED
        assert actual_dst == "/dst/foo.curator-3.mp3"
        assert verified == "h3"
        assert service._cross_source_transfer.call_count == 3
        # Success audit fires once at the end with suffix_n=3
        success_audit = [
            c for c in service.audit.log.call_args_list
            if c.kwargs.get("details", {}).get("mode") == "rename-with-suffix"
        ]
        assert len(success_audit) == 1
        details = success_audit[0].kwargs["details"]
        assert details["suffix_n"] == 3
        assert details["renamed_dst"].endswith("foo.curator-3.mp3")

    def test_hash_mismatch_during_suffix_retry_propagates(
        self, service, move,
    ):
        """A HASH_MISMATCH during a suffix attempt sets HASH_MISMATCH; further suffixes NOT tried."""
        service._cross_source_transfer = mock.MagicMock(
            side_effect=[
                (MigrationOutcome.SKIPPED_COLLISION, "/dst/foo.curator-1.mp3", None),
                (MigrationOutcome.HASH_MISMATCH, "/dst/foo.curator-2.mp3", "wrong"),
            ],
        )

        result = service._cross_source_rename_with_suffix(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.HASH_MISMATCH
        assert move.verified_xxhash == "wrong"
        assert service._cross_source_transfer.call_count == 2
        # No success audit fired
        success_audit = [
            c for c in service.audit.log.call_args_list
            if c.kwargs.get("details", {}).get("mode") == "rename-with-suffix"
        ]
        assert len(success_audit) == 0

    def test_transfer_exception_during_suffix_propagates_as_failed(
        self, service, move,
    ):
        """Non-collision exception during retry sets FAILED + halts the loop."""
        service._cross_source_transfer = mock.MagicMock(
            side_effect=[
                (MigrationOutcome.SKIPPED_COLLISION, "/dst/foo.curator-1.mp3", None),
                RuntimeError("transient network failure"),
            ],
        )

        result = service._cross_source_rename_with_suffix(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.FAILED
        assert "transient network failure" in move.error
        assert service._cross_source_transfer.call_count == 2


class TestRenameWithSuffixFallback:
    """rename-with-suffix exhaustion path."""

    def test_9999_exhausted_degrades_to_skip_with_audit(
        self, service, move,
    ):
        """If all 9999 suffix candidates collide, degrade to SKIPPED_COLLISION + audit fallback."""
        # All 9999 attempts return SKIPPED_COLLISION.
        service._cross_source_transfer = mock.MagicMock(
            return_value=(MigrationOutcome.SKIPPED_COLLISION, "/dst/foo.curator-N.mp3", None),
        )

        result = service._cross_source_rename_with_suffix(
            move, verify_hash=True,
            src_source_id="local",
            dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION
        assert service._cross_source_transfer.call_count == 9999
        # Audit captures the exhaustion reason
        degrade_calls = [
            c for c in service.audit.log.call_args_list
            if "degraded-cross-source" in c.kwargs.get("details", {}).get("mode", "")
        ]
        assert len(degrade_calls) == 1
        details = degrade_calls[0].kwargs["details"]
        assert details["cross_source"] is True
        assert details["fallback"] == "skipped"
        assert "9999" in details["reason"]


# ===========================================================================
# Helper-method coverage
# ===========================================================================


class TestComputeSuffixName:
    """``_compute_suffix_name(dst_p, n)`` static helper."""

    def test_basic_suffix_construction(self):
        from pathlib import Path
        result = MigrationService._compute_suffix_name(
            Path("/dst/foo.mp3"), 3,
        )
        assert result.name == "foo.curator-3.mp3"
        assert str(result.parent).replace("\\", "/") == "/dst"

    def test_no_extension(self):
        from pathlib import Path
        result = MigrationService._compute_suffix_name(
            Path("/dst/foo"), 1,
        )
        assert result.name == "foo.curator-1"

    def test_multi_dot_filename(self):
        """Path.with_name + Path.stem use the LAST dot (so foo.tar.gz -> foo.tar.curator-N.gz)."""
        from pathlib import Path
        result = MigrationService._compute_suffix_name(
            Path("/dst/archive.tar.gz"), 7,
        )
        assert result.name == "archive.tar.curator-7.gz"
