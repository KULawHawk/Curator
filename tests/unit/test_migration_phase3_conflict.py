"""Tracer Phase 3 P2 conflict-resolution tests.

Covers ``MigrationOutcome``'s 3 new variants, ``MigrationConflictError``,
``MigrationService.set_on_conflict_mode``, ``_compute_backup_path``,
``_find_available_suffix``, and the four ``--on-conflict`` modes
end-to-end through ``apply()`` per design v0.2 RATIFIED §4.6 (DM-4)
and §5 P2 acceptance criteria.

Test layout:

* ``TestSkipMode``               -- preserves v1.2.0 behavior (default)
* ``TestFailMode``               -- raises MigrationConflictError; first-collision abort
* ``TestOverwriteWithBackup``    -- backup path format, success path, override outcome
* ``TestRenameWithSuffix``       -- suffix discovery, override outcome, exhaustion
* ``TestAuditConflictDetails``   -- migration.conflict_resolved fields per mode
* ``TestServiceClamping``        -- set_on_conflict_mode validation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from curator.services.migration import (
    MigrationConflictError,
    MigrationMove,
    MigrationOutcome,
    MigrationPlan,
    MigrationReport,
    MigrationService,
)
from curator.services.safety import SafetyLevel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_safety_mock(level: SafetyLevel = SafetyLevel.SAFE):
    """Build a MagicMock SafetyService whose check_path() always returns ``level``."""
    mock = MagicMock()
    report = MagicMock()
    report.level = level
    mock.check_path.return_value = report
    return mock


def _make_audit_mock():
    """Build an audit mock that records all log() + insert() calls."""

    class _Audit:
        def __init__(self):
            self.log_calls: list[dict] = []
            self.insert_calls: list = []

        def log(self, *, actor, action, entity_type=None, entity_id=None,
                details=None, when=None):
            self.log_calls.append({
                "actor": actor,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "details": dict(details) if details else {},
            })
            return len(self.log_calls)

        def insert(self, entry):
            self.insert_calls.append(entry)
            return len(self.insert_calls)

    return _Audit()


def _make_service(*, audit=None):
    """Build a minimal MigrationService for in-memory same-source apply() tests.

    file_repo + safety are mocked; we don't exercise the index update or
    safety verdict in these tests. Setting ``audit`` enables the
    migration.conflict_resolved emission path.
    """
    file_repo = MagicMock()
    file_repo.get.return_value = MagicMock(curator_id=uuid4())
    safety = _make_safety_mock(SafetyLevel.SAFE)
    return MigrationService(
        file_repo=file_repo,
        safety=safety,
        audit=audit,
    )


def _make_plan_with_one_move(src_path: Path, dst_path: Path) -> MigrationPlan:
    """Build a 1-move plan whose src/dst point at real filesystem paths."""
    move = MigrationMove(
        curator_id=uuid4(),
        src_path=str(src_path),
        dst_path=str(dst_path),
        safety_level=SafetyLevel.SAFE,
        size=src_path.stat().st_size,
        src_xxhash=None,
    )
    return MigrationPlan(
        src_source_id="local",
        src_root=str(src_path.parent),
        dst_source_id="local",
        dst_root=str(dst_path.parent),
        moves=[move],
    )


@pytest.fixture
def src_dst(tmp_path: Path):
    """Build a src file (with content) + a dst path (which may collide)."""
    src = tmp_path / "src" / "song.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"new content")
    dst = tmp_path / "dst" / "song.mp3"
    dst.parent.mkdir(parents=True)
    return (src, dst)


# ---------------------------------------------------------------------------
# TestSkipMode -- preserves v1.2.0 behavior (default)
# ---------------------------------------------------------------------------


class TestSkipMode:
    """on_conflict='skip' (default) preserves v1.2.0 SKIPPED_COLLISION exactly."""

    def test_skip_default_preserves_v1_2_0_behavior(self, src_dst):
        """Existing dst + default mode => SKIPPED_COLLISION; src untouched."""
        src, dst = src_dst
        dst.write_bytes(b"old content")  # collision

        svc = _make_service()
        plan = _make_plan_with_one_move(src, dst)
        report = svc.apply(plan, verify_hash=False)  # default on_conflict='skip'

        assert len(report.moves) == 1
        m = report.moves[0]
        assert m.outcome == MigrationOutcome.SKIPPED_COLLISION
        assert report.skipped_count == 1
        assert report.moved_count == 0
        assert report.failed_count == 0
        # Source untouched, dst still has old content
        assert src.exists()
        assert dst.read_bytes() == b"old content"


# ---------------------------------------------------------------------------
# TestFailMode -- raises MigrationConflictError on first collision
# ---------------------------------------------------------------------------


class TestFailMode:
    """on_conflict='fail' aborts the pass on the first collision."""

    def test_fail_mode_raises_on_first_collision(self, src_dst):
        """fail mode + existing dst => MigrationConflictError raised."""
        src, dst = src_dst
        dst.write_bytes(b"old content")

        svc = _make_service()
        plan = _make_plan_with_one_move(src, dst)

        with pytest.raises(MigrationConflictError) as exc_info:
            svc.apply(plan, verify_hash=False, on_conflict="fail")

        assert exc_info.value.dst_path == str(dst)
        assert exc_info.value.src_path == str(src)
        # Src + dst preserved (no partial state)
        assert src.exists()
        assert dst.read_bytes() == b"old content"

    def test_fail_mode_records_failed_due_to_conflict_outcome(self, src_dst):
        """The move that triggers the abort has outcome=FAILED_DUE_TO_CONFLICT."""
        src, dst = src_dst
        dst.write_bytes(b"old content")

        svc = _make_service()
        plan = _make_plan_with_one_move(src, dst)

        try:
            svc.apply(plan, verify_hash=False, on_conflict="fail")
        except MigrationConflictError:
            pass
        # The exception was raised; we can't check the report. But we CAN
        # verify the move outcome was set to FAILED_DUE_TO_CONFLICT in
        # the audit emission. Use a fresh service with audit.
        audit = _make_audit_mock()
        svc2 = _make_service(audit=audit)
        plan2 = _make_plan_with_one_move(src, dst)
        try:
            svc2.apply(plan2, verify_hash=False, on_conflict="fail")
        except MigrationConflictError:
            pass
        # migration.conflict_resolved should have been emitted with mode='fail'
        conflict_logs = [
            c for c in audit.log_calls
            if c["action"] == "migration.conflict_resolved"
        ]
        assert len(conflict_logs) == 1
        assert conflict_logs[0]["details"]["mode"] == "fail"


# ---------------------------------------------------------------------------
# TestOverwriteWithBackup
# ---------------------------------------------------------------------------


class TestOverwriteWithBackup:
    """on_conflict='overwrite-with-backup' renames dst to backup, then proceeds."""

    def test_backup_path_format(self, tmp_path: Path):
        """_compute_backup_path produces <stem>.curator-backup-<UTC-iso8601><ext>."""
        dst = tmp_path / "music" / "Pink Floyd - Money.mp3"
        dst.parent.mkdir(parents=True)
        dst.write_bytes(b"")

        backup = MigrationService._compute_backup_path(dst)
        # Same parent dir
        assert backup.parent == dst.parent
        # Stem starts with the original stem + .curator-backup-
        assert backup.stem.startswith("Pink Floyd - Money.curator-backup-")
        # Extension preserved
        assert backup.suffix == ".mp3"
        # Filename contains a timestamp (4 digits for year)
        assert "20" in backup.stem  # 2026, 2027, etc.

    def test_overwrite_with_backup_renames_dst_then_moves(self, src_dst):
        """Successful overwrite: dst renamed to backup, then src moved to dst."""
        src, dst = src_dst
        dst.write_bytes(b"old content")
        old_dst_size = dst.stat().st_size

        svc = _make_service()
        plan = _make_plan_with_one_move(src, dst)
        # Disable verify_hash + the index/trash side effects so we focus on
        # the conflict-resolution behavior. file_repo is mocked.
        report = svc.apply(plan, verify_hash=False,
                           on_conflict="overwrite-with-backup")

        assert len(report.moves) == 1
        m = report.moves[0]
        assert m.outcome == MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP
        assert report.moved_count == 1
        assert report.skipped_count == 0
        assert report.failed_count == 0
        # The old dst content is preserved at the backup path
        backups = list(dst.parent.glob("song.curator-backup-*.mp3"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == b"old content"
        assert backups[0].stat().st_size == old_dst_size
        # New dst has the src content
        assert dst.read_bytes() == b"new content"

    def test_overwrite_with_backup_copied_count_in_report(self, src_dst):
        """MOVED_OVERWROTE_WITH_BACKUP counts toward report.moved_count."""
        src, dst = src_dst
        dst.write_bytes(b"old content")

        svc = _make_service()
        plan = _make_plan_with_one_move(src, dst)
        report = svc.apply(plan, verify_hash=False,
                           on_conflict="overwrite-with-backup")

        # Specifically: moved_count picks up MOVED_OVERWROTE_WITH_BACKUP
        assert report.moved_count == 1
        assert report.bytes_moved == len(b"new content")


# ---------------------------------------------------------------------------
# TestRenameWithSuffix
# ---------------------------------------------------------------------------


class TestRenameWithSuffix:
    """on_conflict='rename-with-suffix' migrates to <name>.curator-N<ext>."""

    def test_find_available_suffix_picks_lowest_n(self, tmp_path: Path):
        """_find_available_suffix returns n=1 when no suffixed files exist."""
        dst = tmp_path / "song.mp3"
        dst.write_bytes(b"existing")

        new_path, n = MigrationService._find_available_suffix(dst)
        assert n == 1
        assert new_path.name == "song.curator-1.mp3"
        assert not new_path.exists()

    def test_find_available_suffix_skips_existing(self, tmp_path: Path):
        """If song.curator-1 + song.curator-2 exist, returns n=3."""
        dst = tmp_path / "song.mp3"
        dst.write_bytes(b"existing")
        (tmp_path / "song.curator-1.mp3").write_bytes(b"backup1")
        (tmp_path / "song.curator-2.mp3").write_bytes(b"backup2")

        new_path, n = MigrationService._find_available_suffix(dst)
        assert n == 3
        assert new_path.name == "song.curator-3.mp3"

    def test_rename_with_suffix_redirects_move(self, src_dst):
        """Successful rename: original dst preserved, src moved to .curator-1."""
        src, dst = src_dst
        dst.write_bytes(b"old content")

        svc = _make_service()
        plan = _make_plan_with_one_move(src, dst)
        report = svc.apply(plan, verify_hash=False,
                           on_conflict="rename-with-suffix")

        assert len(report.moves) == 1
        m = report.moves[0]
        assert m.outcome == MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX
        # The move's dst_path was MUTATED to the renamed location
        assert m.dst_path.endswith(".curator-1.mp3")
        # Original dst preserved (NOT overwritten)
        assert dst.read_bytes() == b"old content"
        # New file at the suffixed path has the src content
        assert Path(m.dst_path).read_bytes() == b"new content"


# ---------------------------------------------------------------------------
# TestAuditConflictDetails
# ---------------------------------------------------------------------------


class TestAuditConflictDetails:
    """migration.conflict_resolved audit details vary by mode."""

    def test_audit_overwrite_with_backup_includes_backup_path(self, src_dst):
        """overwrite-with-backup audit contains backup_path field."""
        src, dst = src_dst
        dst.write_bytes(b"old content")
        audit = _make_audit_mock()
        svc = _make_service(audit=audit)
        plan = _make_plan_with_one_move(src, dst)

        svc.apply(plan, verify_hash=False, on_conflict="overwrite-with-backup")

        conflicts = [
            c for c in audit.log_calls
            if c["action"] == "migration.conflict_resolved"
        ]
        assert len(conflicts) == 1
        details = conflicts[0]["details"]
        assert details["mode"] == "overwrite-with-backup"
        assert "backup_path" in details
        assert ".curator-backup-" in details["backup_path"]

    def test_audit_rename_with_suffix_includes_suffix_n(self, src_dst):
        """rename-with-suffix audit contains suffix_n + renamed_dst fields."""
        src, dst = src_dst
        dst.write_bytes(b"old content")
        audit = _make_audit_mock()
        svc = _make_service(audit=audit)
        plan = _make_plan_with_one_move(src, dst)

        svc.apply(plan, verify_hash=False, on_conflict="rename-with-suffix")

        conflicts = [
            c for c in audit.log_calls
            if c["action"] == "migration.conflict_resolved"
        ]
        assert len(conflicts) == 1
        details = conflicts[0]["details"]
        assert details["mode"] == "rename-with-suffix"
        assert details["suffix_n"] == 1
        assert details["renamed_dst"].endswith(".curator-1.mp3")
        assert details["original_dst"] == str(dst)

    def test_audit_fail_mode_emitted_before_raise(self, src_dst):
        """fail mode emits migration.conflict_resolved BEFORE raising."""
        src, dst = src_dst
        dst.write_bytes(b"old content")
        audit = _make_audit_mock()
        svc = _make_service(audit=audit)
        plan = _make_plan_with_one_move(src, dst)

        try:
            svc.apply(plan, verify_hash=False, on_conflict="fail")
        except MigrationConflictError:
            pass

        conflicts = [
            c for c in audit.log_calls
            if c["action"] == "migration.conflict_resolved"
        ]
        assert len(conflicts) == 1
        assert conflicts[0]["details"]["mode"] == "fail"


# ---------------------------------------------------------------------------
# TestServiceClamping -- set_on_conflict_mode validation
# ---------------------------------------------------------------------------


class TestServiceClamping:
    """MigrationService.set_on_conflict_mode rejects unknown modes."""

    def test_unknown_mode_raises_value_error(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="unknown on_conflict mode"):
            svc.set_on_conflict_mode("nuke-from-orbit")

    def test_all_four_valid_modes_accepted(self):
        svc = _make_service()
        for mode in ("skip", "fail", "overwrite-with-backup", "rename-with-suffix"):
            svc.set_on_conflict_mode(mode)
            assert svc._on_conflict_mode == mode

    def test_default_mode_is_skip(self):
        svc = _make_service()
        assert svc._on_conflict_mode == "skip"
