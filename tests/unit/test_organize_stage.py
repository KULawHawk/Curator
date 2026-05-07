"""Unit tests for Stage / Revert (Phase Gamma F2 v0.22).

Covers ``StageOutcome`` enum / ``StageMove`` / ``StageReport`` / their
``RevertReport`` counterparts and the OrganizeService.stage and
revert_stage methods. Uses real file I/O on tmp_path for the move
behavior since shutil.move is the unit under test as much as the
service logic itself.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from curator.models.file import FileEntity
from curator.services.organize import (
    STAGE_MANIFEST_NAME,
    OrganizeBucket,
    OrganizePlan,
    OrganizeService,
    RevertMove,
    RevertOutcome,
    RevertReport,
    StageMove,
    StageOutcome,
    StageReport,
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
    return FileEntity(
        curator_id=uuid4(),
        source_id=source_id,
        source_path=path,
        size=size,
        mtime=datetime.utcnow(),
    )


def _safe_report(path: str) -> SafetyReport:
    return SafetyReport(path=path, level=SafetyLevel.SAFE)


def _build_service(audit=None) -> OrganizeService:
    """Build an OrganizeService with mocked deps suitable for stage tests."""
    file_repo = MagicMock()
    safety = MagicMock(spec=SafetyService)
    return OrganizeService(file_repo, safety, audit=audit)


def _build_plan_with_one_proposal(
    *,
    src_file: Path,
    target_root: Path,
    proposed_relative: str,
) -> OrganizePlan:
    """Build an OrganizePlan with a single SAFE file + proposal."""
    plan = OrganizePlan(
        source_id="local",
        root_prefix=None,
        target_root=str(target_root),
    )
    fe = _make_file(str(src_file), size=src_file.stat().st_size)
    plan.safe.add(
        fe,
        _safe_report(str(src_file)),
        proposed_destination=str(target_root / proposed_relative),
    )
    plan.completed_at = datetime.utcnow()
    return plan


# ===========================================================================
# StageOutcome / StageMove / StageReport
# ===========================================================================


class TestStageDataclasses:
    def test_outcome_enum_values(self):
        # Stable string values matter — they appear in audit entries + JSON.
        assert StageOutcome.MOVED.value == "moved"
        assert StageOutcome.SKIPPED_NO_PROPOSAL.value == "skipped_no_proposal"
        assert StageOutcome.SKIPPED_COLLISION.value == "skipped_collision"
        assert StageOutcome.FAILED.value == "failed"

    def test_stage_move_holds_basic_fields(self):
        m = StageMove(
            curator_id="abc",
            original="/orig/a.mp3",
            staged="/stage/a.mp3",
            outcome=StageOutcome.MOVED,
        )
        assert m.error is None

    def test_stage_report_counts_by_outcome(self):
        r = StageReport(stage_root="/stage", started_at=datetime.utcnow())
        r.moves = [
            StageMove("1", "/a", "/s/a", StageOutcome.MOVED),
            StageMove("2", "/b", "/s/b", StageOutcome.MOVED),
            StageMove("3", "/c", None, StageOutcome.SKIPPED_NO_PROPOSAL),
            StageMove("4", "/d", "/s/d", StageOutcome.SKIPPED_COLLISION),
            StageMove("5", "/e", None, StageOutcome.FAILED, error="x"),
        ]
        assert r.moved_count == 2
        assert r.skipped_count == 2
        assert r.failed_count == 1

    def test_stage_report_duration_none_until_completed(self):
        r = StageReport(stage_root="/s", started_at=datetime.utcnow())
        assert r.duration_seconds is None
        r.completed_at = datetime.utcnow()
        assert r.duration_seconds is not None
        assert r.duration_seconds >= 0


class TestRevertDataclasses:
    def test_outcome_enum_values(self):
        assert RevertOutcome.RESTORED.value == "restored"
        assert RevertOutcome.SKIPPED_ORIGINAL_OCCUPIED.value == "skipped_original_occupied"
        assert RevertOutcome.SKIPPED_STAGED_MISSING.value == "skipped_staged_missing"
        assert RevertOutcome.FAILED.value == "failed"

    def test_revert_report_counts_by_outcome(self):
        r = RevertReport(stage_root="/s", started_at=datetime.utcnow())
        r.moves = [
            RevertMove("1", "/a", "/s/a", RevertOutcome.RESTORED),
            RevertMove("2", "/b", "/s/b", RevertOutcome.RESTORED),
            RevertMove("3", "/c", "/s/c", RevertOutcome.SKIPPED_STAGED_MISSING),
            RevertMove("4", "/d", "/s/d", RevertOutcome.SKIPPED_ORIGINAL_OCCUPIED),
            RevertMove("5", "/e", "/s/e", RevertOutcome.FAILED, error="x"),
        ]
        assert r.restored_count == 2
        assert r.skipped_count == 2
        assert r.failed_count == 1


# ===========================================================================
# OrganizeService.stage
# ===========================================================================


class TestStage:
    def test_raises_if_plan_has_no_target_root(self, tmp_path):
        # A plan from regular plan() (no organize_type) has target_root=None.
        plan = OrganizePlan(source_id="local", root_prefix=None)
        plan.completed_at = datetime.utcnow()
        svc = _build_service()
        with pytest.raises(ValueError, match="target_root"):
            svc.stage(plan, stage_root=tmp_path)

    def test_moves_safe_file_with_proposal(self, tmp_path):
        target_root = tmp_path / "library"
        stage_root = tmp_path / "staging"
        src = tmp_path / "source.mp3"
        src.write_bytes(b"audio data")

        plan = _build_plan_with_one_proposal(
            src_file=src,
            target_root=target_root,
            proposed_relative="Artist/Album/01 - Track.mp3",
        )
        svc = _build_service()
        report = svc.stage(plan, stage_root=stage_root)

        assert report.moved_count == 1
        assert report.failed_count == 0
        assert report.skipped_count == 0

        # Original moved out, staged exists.
        assert not src.exists()
        staged = stage_root / "Artist" / "Album" / "01 - Track.mp3"
        assert staged.exists()
        assert staged.read_bytes() == b"audio data"

    def test_writes_manifest(self, tmp_path):
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src = tmp_path / "x.mp3"
        src.write_bytes(b"x")
        plan = _build_plan_with_one_proposal(
            src_file=src,
            target_root=target_root,
            proposed_relative="A/B/x.mp3",
        )
        svc = _build_service()
        svc.stage(plan, stage_root=stage_root)

        manifest = stage_root / STAGE_MANIFEST_NAME
        assert manifest.exists()
        entries = json.loads(manifest.read_text())
        assert len(entries) == 1
        entry = entries[0]
        assert entry["original"] == str(src)
        assert "staged" in entry
        assert "moved_at" in entry

    def test_safe_without_proposal_is_skipped_no_proposal(self, tmp_path):
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src = tmp_path / "audio.mp3"
        src.write_bytes(b"x")

        # Build plan with the file in SAFE but NO proposal entry.
        plan = OrganizePlan(
            source_id="local", root_prefix=None,
            target_root=str(target_root),
        )
        fe = _make_file(str(src), size=1)
        plan.safe.add(fe, _safe_report(str(src)))  # no proposal
        plan.completed_at = datetime.utcnow()

        svc = _build_service()
        report = svc.stage(plan, stage_root=stage_root)

        assert report.moved_count == 0
        assert report.skipped_count == 1
        assert report.moves[0].outcome == StageOutcome.SKIPPED_NO_PROPOSAL
        # Source untouched.
        assert src.exists()

    def test_collision_is_skipped(self, tmp_path):
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src = tmp_path / "src.mp3"
        src.write_bytes(b"new")

        # Pre-create the destination path so stage detects collision.
        dest_rel = "Artist/Album/01 - x.mp3"
        existing = stage_root / dest_rel
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"old")

        plan = _build_plan_with_one_proposal(
            src_file=src,
            target_root=target_root,
            proposed_relative=dest_rel,
        )
        svc = _build_service()
        report = svc.stage(plan, stage_root=stage_root)

        assert report.moved_count == 0
        assert report.skipped_count == 1
        assert report.moves[0].outcome == StageOutcome.SKIPPED_COLLISION
        # Source unmoved, existing untouched.
        assert src.exists()
        assert existing.read_bytes() == b"old"

    def test_failed_move_recorded_as_failed(self, tmp_path, monkeypatch):
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src = tmp_path / "src.mp3"
        src.write_bytes(b"x")

        plan = _build_plan_with_one_proposal(
            src_file=src,
            target_root=target_root,
            proposed_relative="A/B/x.mp3",
        )

        # Force shutil.move to raise.
        import curator.services.organize as org_mod
        def boom(*a, **kw):
            raise PermissionError("denied")
        monkeypatch.setattr(org_mod.shutil, "move", boom)

        svc = _build_service()
        report = svc.stage(plan, stage_root=stage_root)

        assert report.failed_count == 1
        assert report.moves[0].outcome == StageOutcome.FAILED
        assert "denied" in (report.moves[0].error or "")

    def test_audit_entry_written_per_move(self, tmp_path):
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src = tmp_path / "x.mp3"
        src.write_bytes(b"x")
        plan = _build_plan_with_one_proposal(
            src_file=src,
            target_root=target_root,
            proposed_relative="A/B/x.mp3",
        )
        audit = MagicMock()
        svc = _build_service(audit=audit)
        svc.stage(plan, stage_root=stage_root)

        # One audit.log call for the one successful move.
        assert audit.log.call_count == 1
        kwargs = audit.log.call_args.kwargs
        assert kwargs["actor"] == "curator.organize.stage"
        assert kwargs["action"] == "organize.stage.move"
        assert kwargs["entity_type"] == "file"

    def test_no_audit_calls_when_audit_is_none(self, tmp_path):
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src = tmp_path / "x.mp3"
        src.write_bytes(b"x")
        plan = _build_plan_with_one_proposal(
            src_file=src, target_root=target_root,
            proposed_relative="A/B/x.mp3",
        )
        # No audit \u2014 should still work.
        svc = _build_service(audit=None)
        report = svc.stage(plan, stage_root=stage_root)
        assert report.moved_count == 1


# ===========================================================================
# OrganizeService.revert_stage
# ===========================================================================


class TestRevertStage:
    def _stage_one_file(self, tmp_path) -> tuple[Path, Path, Path, OrganizeService]:
        """Helper: stage one file, return (src, staged_root, original_path, svc).

        After this runs:
            * src has been moved into staging
            * staging contains the manifest
            * original_path is where revert should put it back
        """
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src = tmp_path / "originals" / "song.mp3"
        src.parent.mkdir()
        src.write_bytes(b"audio")
        original_path = src  # remembered before stage moves it

        plan = _build_plan_with_one_proposal(
            src_file=src,
            target_root=target_root,
            proposed_relative="A/B/song.mp3",
        )
        svc = _build_service()
        svc.stage(plan, stage_root=stage_root)
        return src, stage_root, original_path, svc

    def test_missing_manifest_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        svc = _build_service()
        with pytest.raises(FileNotFoundError):
            svc.revert_stage(empty)

    def test_full_round_trip_restores_file_and_deletes_manifest(self, tmp_path):
        src, stage_root, original_path, svc = self._stage_one_file(tmp_path)
        assert not src.exists()  # was moved during stage

        report = svc.revert_stage(stage_root)
        assert report.restored_count == 1
        assert report.skipped_count == 0
        assert report.failed_count == 0

        # File is back at its original path.
        assert original_path.exists()
        assert original_path.read_bytes() == b"audio"

        # Manifest deleted now that everything reverted.
        assert not (stage_root / STAGE_MANIFEST_NAME).exists()

    def test_skipped_when_original_occupied(self, tmp_path):
        src, stage_root, original_path, svc = self._stage_one_file(tmp_path)
        # Simulate user putting something else at the original path.
        original_path.write_bytes(b"other content")

        report = svc.revert_stage(stage_root)
        assert report.restored_count == 0
        assert report.skipped_count == 1
        assert report.moves[0].outcome == RevertOutcome.SKIPPED_ORIGINAL_OCCUPIED
        # The "other content" file still there, untouched.
        assert original_path.read_bytes() == b"other content"
        # Manifest still exists with the unresolved entry.
        manifest = stage_root / STAGE_MANIFEST_NAME
        assert manifest.exists()
        assert len(json.loads(manifest.read_text())) == 1

    def test_skipped_when_staged_file_missing(self, tmp_path):
        src, stage_root, original_path, svc = self._stage_one_file(tmp_path)
        # Simulate user deleting the staged copy.
        manifest = stage_root / STAGE_MANIFEST_NAME
        entries = json.loads(manifest.read_text())
        Path(entries[0]["staged"]).unlink()

        report = svc.revert_stage(stage_root)
        assert report.restored_count == 0
        assert report.skipped_count == 1
        assert report.moves[0].outcome == RevertOutcome.SKIPPED_STAGED_MISSING
        # Manifest deleted because staged file is gone (nothing more to do).
        assert not manifest.exists()

    def test_partial_revert_leaves_remaining_entries(self, tmp_path):
        # Stage two files, then occupy one original. Revert should
        # restore one and keep the other in the manifest.
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src_a = tmp_path / "originals" / "a.mp3"
        src_b = tmp_path / "originals" / "b.mp3"
        src_a.parent.mkdir()
        src_a.write_bytes(b"a")
        src_b.write_bytes(b"b")

        plan = OrganizePlan(
            source_id="local", root_prefix=None,
            target_root=str(target_root),
        )
        for src, rel in [(src_a, "X/Y/a.mp3"), (src_b, "X/Y/b.mp3")]:
            fe = _make_file(str(src), size=1)
            plan.safe.add(
                fe, _safe_report(str(src)),
                proposed_destination=str(target_root / rel),
            )
        plan.completed_at = datetime.utcnow()

        svc = _build_service()
        svc.stage(plan, stage_root=stage_root)

        # Occupy A's original path so revert skips it.
        src_a.write_bytes(b"squatter")

        report = svc.revert_stage(stage_root)
        assert report.restored_count == 1
        assert report.skipped_count == 1

        # B is restored; A is still in the manifest.
        assert src_b.exists() and src_b.read_bytes() == b"b"
        manifest = stage_root / STAGE_MANIFEST_NAME
        assert manifest.exists()
        remaining = json.loads(manifest.read_text())
        assert len(remaining) == 1
        assert remaining[0]["original"] == str(src_a)

    def test_revert_writes_audit_entries(self, tmp_path):
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src = tmp_path / "originals" / "z.mp3"
        src.parent.mkdir()
        src.write_bytes(b"z")

        plan = _build_plan_with_one_proposal(
            src_file=src,
            target_root=target_root,
            proposed_relative="A/B/z.mp3",
        )
        audit = MagicMock()
        svc = _build_service(audit=audit)
        svc.stage(plan, stage_root=stage_root)
        # 1 audit call from stage so far.
        assert audit.log.call_count == 1

        svc.revert_stage(stage_root)
        # +1 audit call from revert.
        assert audit.log.call_count == 2
        last_kwargs = audit.log.call_args.kwargs
        assert last_kwargs["actor"] == "curator.organize.revert"
        assert last_kwargs["action"] == "organize.revert.move"
