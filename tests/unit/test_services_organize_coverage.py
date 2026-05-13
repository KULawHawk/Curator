"""Coverage closure for ``curator.services.organize`` (v1.7.143).

Targets 56 uncovered lines + 10 partial branches.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from curator._compat.datetime import utcnow_naive
from curator.models import FileEntity
from curator.services.organize import (
    STAGE_MANIFEST_NAME,
    OrganizePlan,
    OrganizeService,
    RevertOutcome,
    StageOutcome,
    StageReport,
)
from curator.services.safety import (
    SafetyLevel,
    SafetyReport,
    SafetyService,
)


def _svc(*, file_repo=None, safety=None, music=None, photo=None,
         document=None, code=None, mb_client=None, audit=None):
    if safety is None:
        safety = SafetyService(app_data_paths=[], os_managed_paths=[])
    if file_repo is None:
        file_repo = MagicMock()
        file_repo.query.return_value = []
    return OrganizeService(
        file_repo=file_repo, safety=safety,
        music=music, photo=photo, document=document, code=code,
        mb_client=mb_client, audit=audit,
    )


def _file(path: str, **overrides) -> FileEntity:
    base = dict(
        source_id="local", source_path=path, size=1, mtime=utcnow_naive(),
    )
    base.update(overrides)
    return FileEntity(**base)


# ---------------------------------------------------------------------------
# OrganizePlan.duration_seconds None branch
# ---------------------------------------------------------------------------


class TestOrganizePlanDurationNone:
    def test_duration_none_when_not_completed(self):
        plan = OrganizePlan(source_id="local", root_prefix=None)
        assert plan.duration_seconds is None

    def test_revert_report_duration_none_when_not_completed(self):
        """Line 251 (RevertReport): completed_at None -> duration None."""
        from curator.services.organize import RevertReport
        r = RevertReport(stage_root="/x", started_at=utcnow_naive())
        assert r.duration_seconds is None


# ---------------------------------------------------------------------------
# plan(): organize_type without target_root raises
# ---------------------------------------------------------------------------


class TestPlanValidation:
    def test_organize_type_without_target_root_raises(self):
        svc = _svc()
        with pytest.raises(ValueError, match="target_root is required"):
            svc.plan(source_id="local", organize_type="music")


# ---------------------------------------------------------------------------
# plan() code mode project discovery
# ---------------------------------------------------------------------------


class TestPlanCodeMode:
    def test_code_projects_discovered_with_root_prefix(self, tmp_path):
        """Lines 342-353: code mode with explicit root_prefix walks for projects."""
        file_repo = MagicMock()
        f1 = _file(str(tmp_path / "src/main.py"))
        file_repo.query.return_value = [f1]

        code = MagicMock()
        # Return a project so the per-file lookup proceeds
        fake_project = MagicMock()
        fake_project.root_path = str(tmp_path)
        code.find_projects.return_value = [fake_project]
        code.find_project_containing.return_value = fake_project
        code.propose_destination.return_value = Path(str(tmp_path)) / "target/py/main.py"

        svc = _svc(file_repo=file_repo, code=code)
        plan = svc.plan(
            source_id="local",
            root_prefix=str(tmp_path),
            organize_type="code",
            target_root=tmp_path / "target",
        )
        # find_projects called once with the root_prefix
        code.find_projects.assert_called_once_with(str(tmp_path))
        assert plan.total_files == 1

    def test_code_discovery_exception_logged(self, tmp_path):
        """Lines 352-355: code.find_projects raising is caught + logged."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "x.py"))]

        code = MagicMock()
        code.find_projects.side_effect = RuntimeError("walk denied")

        svc = _svc(file_repo=file_repo, code=code)
        # Must not raise
        plan = svc.plan(
            source_id="local",
            root_prefix=str(tmp_path),
            organize_type="code",
            target_root=tmp_path / "target",
        )
        assert plan.total_files == 1

    def test_code_walk_root_none_skips_find_projects(self, tmp_path):
        """Branch 349->357: when no root_prefix AND no files,
        code_walk_root stays None and find_projects is NOT called."""
        file_repo = MagicMock()
        file_repo.query.return_value = []  # no files

        code = MagicMock()

        svc = _svc(file_repo=file_repo, code=code)
        svc.plan(
            source_id="local",
            organize_type="code",
            target_root=tmp_path / "target",
        )
        # find_projects NOT called (code_walk_root never set)
        code.find_projects.assert_not_called()

    def test_code_walk_root_falls_back_to_drive_anchor(self, tmp_path):
        """Lines 343-348: when root_prefix is None but files present,
        falls back to first file's drive anchor for the walk."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "x.py"))]

        code = MagicMock()
        code.find_projects.return_value = []

        svc = _svc(file_repo=file_repo, code=code)
        plan = svc.plan(
            source_id="local",
            organize_type="code",
            target_root=tmp_path / "target",
        )
        # find_projects was called with the drive anchor
        assert code.find_projects.called
        # The argument should be the file's drive anchor
        call_arg = code.find_projects.call_args.args[0]
        # On Windows it's e.g. "C:\", on Linux "/" — just verify it's truthy
        assert call_arg


# ---------------------------------------------------------------------------
# plan() music / photo / document / code propose_destination integration
# ---------------------------------------------------------------------------


class TestPlanMusicMbEnrichment:
    def test_mb_enrich_when_filename_source_and_missing_album(self, tmp_path):
        """Lines 396-411: MB enrich path triggers when meta used filename
        AND album/year/track is missing."""
        file_repo = MagicMock()
        f1 = _file(str(tmp_path / "song.mp3"))
        file_repo.query.return_value = [f1]

        music = MagicMock()
        music.is_audio_file.return_value = True

        # Tags with album=None (needs enrichment) and _filename_source="true"
        meta = MagicMock()
        meta.raw = {"_filename_source": "true"}
        meta.album = None
        meta.year = None
        meta.track_number = None
        meta.has_useful_tags = True
        music.read_tags.return_value = meta

        # enrich raises -> exception arm covered
        music.enrich_via_musicbrainz.side_effect = RuntimeError("MB API down")

        music.propose_destination.return_value = Path(str(tmp_path)) / "music/x.mp3"

        mb_client = MagicMock()  # non-None so the enrich path is reachable

        svc = _svc(file_repo=file_repo, music=music, mb_client=mb_client)
        svc.plan(
            source_id="local",
            organize_type="music",
            target_root=tmp_path / "target",
            enrich_mb=True,
        )
        music.enrich_via_musicbrainz.assert_called_once()


class TestPlanMusicEnrichBranches:
    def test_mb_enrich_not_triggered_when_filename_source_false(self, tmp_path):
        """Branch 402->412: used_filename is False -> skip enrich."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "song.mp3"))]

        music = MagicMock()
        music.is_audio_file.return_value = True

        meta = MagicMock()
        # _filename_source NOT set to "true"
        meta.raw = {}
        meta.album = None
        meta.year = None
        meta.track_number = None
        meta.has_useful_tags = True
        music.read_tags.return_value = meta
        music.propose_destination.return_value = Path("/x/song.mp3")

        mb_client = MagicMock()
        svc = _svc(file_repo=file_repo, music=music, mb_client=mb_client)
        svc.plan(
            source_id="local",
            organize_type="music",
            target_root=tmp_path / "target",
            enrich_mb=True,
        )
        # MB enrich was NOT called
        music.enrich_via_musicbrainz.assert_not_called()


class TestPlanPhotoMode:
    def test_photo_propose_destination(self, tmp_path):
        """Lines 423-433: photo mode proposes destination."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "img.jpg"))]

        photo = MagicMock()
        photo.is_photo_file.return_value = True
        photo.read_metadata.return_value = MagicMock()
        photo.propose_destination.return_value = Path("/target/photos/img.jpg")

        svc = _svc(file_repo=file_repo, photo=photo)
        svc.plan(
            source_id="local",
            organize_type="photo",
            target_root=tmp_path / "target",
        )
        photo.propose_destination.assert_called_once()


class TestPlanPhotoNoMeta:
    def test_photo_meta_none_skips_propose(self, tmp_path):
        """Branch 427->464: photo.read_metadata returns None -> skip propose."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "img.jpg"))]

        photo = MagicMock()
        photo.is_photo_file.return_value = True
        photo.read_metadata.return_value = None  # no metadata

        svc = _svc(file_repo=file_repo, photo=photo)
        svc.plan(
            source_id="local",
            organize_type="photo",
            target_root=tmp_path / "target",
        )
        photo.propose_destination.assert_not_called()


class TestPlanDocumentMode:
    def test_document_propose_destination(self, tmp_path):
        """Lines 438-448: document mode proposes destination."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "doc.pdf"))]

        document = MagicMock()
        document.is_document_file.return_value = True
        document.read_metadata.return_value = MagicMock()
        document.propose_destination.return_value = Path("/target/docs/doc.pdf")

        svc = _svc(file_repo=file_repo, document=document)
        svc.plan(
            source_id="local",
            organize_type="document",
            target_root=tmp_path / "target",
        )
        document.propose_destination.assert_called_once()


class TestPlanDocumentNoMeta:
    def test_document_meta_none_skips_propose(self, tmp_path):
        """Branch 442->464: document.read_metadata returns None."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "doc.pdf"))]

        document = MagicMock()
        document.is_document_file.return_value = True
        document.read_metadata.return_value = None

        svc = _svc(file_repo=file_repo, document=document)
        svc.plan(
            source_id="local",
            organize_type="document",
            target_root=tmp_path / "target",
        )
        document.propose_destination.assert_not_called()


class TestPlanCodeProposeDestination:
    def test_code_propose_destination(self, tmp_path):
        """Lines 452-462: code mode finds project and proposes destination."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "src/x.py"))]

        code = MagicMock()
        project = MagicMock()
        code.find_projects.return_value = [project]
        code.find_project_containing.return_value = project
        code.propose_destination.return_value = Path("/target/py/proj/x.py")

        svc = _svc(file_repo=file_repo, code=code)
        svc.plan(
            source_id="local",
            organize_type="code",
            target_root=tmp_path / "target",
            root_prefix=str(tmp_path),
        )
        code.propose_destination.assert_called_once()


# ---------------------------------------------------------------------------
# stage(): proposal not under target_root
# ---------------------------------------------------------------------------


class TestPlanCodeNoProject:
    def test_code_no_project_for_file_skips_propose(self, tmp_path):
        """Branch 455->464: code.find_project_containing returns None."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "x.py"))]

        code = MagicMock()
        code.find_projects.return_value = [MagicMock()]
        code.find_project_containing.return_value = None  # no project

        svc = _svc(file_repo=file_repo, code=code)
        svc.plan(
            source_id="local",
            organize_type="code",
            target_root=tmp_path / "target",
            root_prefix=str(tmp_path),
        )
        code.propose_destination.assert_not_called()


class TestPlanCodeDestNone:
    def test_code_dest_none_skips_proposed(self, tmp_path):
        """Branch 461->464: code.propose_destination returns None."""
        file_repo = MagicMock()
        file_repo.query.return_value = [_file(str(tmp_path / "x.py"))]

        code = MagicMock()
        project = MagicMock()
        code.find_projects.return_value = [project]
        code.find_project_containing.return_value = project
        code.propose_destination.return_value = None  # no destination

        svc = _svc(file_repo=file_repo, code=code)
        plan = svc.plan(
            source_id="local",
            organize_type="code",
            target_root=tmp_path / "target",
            root_prefix=str(tmp_path),
        )
        # The file is in plan.safe but has no proposal (proposed stays None)
        assert plan.safe.count == 1


class TestStageProposalNotUnderTargetRoot:
    def test_proposal_outside_target_root_is_failed(self, tmp_path):
        """Lines 557-571: proposal.relative_to(target_root) raises ValueError
        -> FAILED outcome."""
        target_root = tmp_path / "target"
        target_root.mkdir()
        stage_root = tmp_path / "stage"
        outside = tmp_path / "outside.txt"
        outside.write_text("data")

        # Build plan with target_root set + a safe file with a proposal OUTSIDE target_root
        plan = OrganizePlan(
            source_id="local",
            root_prefix=None,
            target_root=str(target_root),
            started_at=utcnow_naive(),
        )
        f = _file(str(outside))
        plan.safe.add(f, MagicMock(level=SafetyLevel.SAFE, concerns=[]),
                      proposed_destination=str(outside))  # OUTSIDE target_root
        plan.completed_at = utcnow_naive()

        svc = _svc()
        report = svc.stage(plan, stage_root=stage_root)
        assert any(m.outcome == StageOutcome.FAILED for m in report.moves)


# ---------------------------------------------------------------------------
# _write_manifest: existing manifest read failure + write failure
# ---------------------------------------------------------------------------


class TestWriteManifestExistingUnreadable:
    def test_existing_manifest_unreadable_rewritten(self, tmp_path):
        """Lines 695-704: existing manifest unreadable -> warning, rewrite."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        manifest = stage_root / STAGE_MANIFEST_NAME
        # Write garbage that won't parse as JSON
        manifest.write_text("not-json-at-all")

        # Construct a StageReport with one successful move
        report = StageReport(
            stage_root=str(stage_root),
            started_at=utcnow_naive(),
        )
        from curator.services.organize import StageMove
        report.moves.append(StageMove(
            curator_id=str(uuid4()),
            original="/orig.txt",
            staged=str(stage_root / "x.txt"),
            outcome=StageOutcome.MOVED,
        ))

        svc = _svc()
        svc._write_manifest(stage_root, report)

        # Manifest now contains a list with our one entry
        loaded = json.loads(manifest.read_text(encoding="utf-8"))
        assert isinstance(loaded, list)
        assert len(loaded) == 1

    def test_existing_manifest_valid_list_preserved(self, tmp_path):
        """Branch 697->706: existing manifest is a valid list -> kept,
        new entries appended (not reset to [])."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        manifest = stage_root / STAGE_MANIFEST_NAME
        # Write a valid list with one prior entry
        manifest.write_text(json.dumps([
            {"curator_id": "prior", "original": "/p", "staged": "/s",
             "moved_at": "2026-01-01T00:00:00"},
        ]))

        report = StageReport(
            stage_root=str(stage_root),
            started_at=utcnow_naive(),
        )
        svc = _svc()
        svc._write_manifest(stage_root, report)

        loaded = json.loads(manifest.read_text(encoding="utf-8"))
        assert isinstance(loaded, list)
        assert len(loaded) == 1
        assert loaded[0]["curator_id"] == "prior"  # prior entry preserved

    def test_existing_manifest_not_list_treated_as_empty(self, tmp_path):
        """Branch 697: existing.isinstance check — dict-shaped manifest
        is replaced with empty list."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        manifest = stage_root / STAGE_MANIFEST_NAME
        # Write a dict instead of a list
        manifest.write_text(json.dumps({"not": "a list"}))

        report = StageReport(
            stage_root=str(stage_root),
            started_at=utcnow_naive(),
        )
        svc = _svc()
        svc._write_manifest(stage_root, report)
        # Manifest is now an empty list (no moves to add)
        loaded = json.loads(manifest.read_text(encoding="utf-8"))
        assert loaded == []

    def test_manifest_write_failure_logged(self, tmp_path, monkeypatch):
        """Lines 720-724: manifest.write_text OSError logged."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        report = StageReport(
            stage_root=str(stage_root),
            started_at=utcnow_naive(),
        )

        # Monkeypatch Path.write_text on the manifest path to raise
        original_write_text = Path.write_text

        def _flaky_write(self, *a, **kw):
            if self.name == STAGE_MANIFEST_NAME:
                raise OSError("write denied")
            return original_write_text(self, *a, **kw)

        monkeypatch.setattr(Path, "write_text", _flaky_write)
        svc = _svc()
        # Must not raise
        svc._write_manifest(stage_root, report)


# ---------------------------------------------------------------------------
# revert_stage: unreadable manifest + malformed entry + shutil.move failure
# ---------------------------------------------------------------------------


class TestRevertStageManifestUnreadable:
    def test_unreadable_manifest_raises_runtime_error(self, tmp_path):
        """Lines 761-764: unreadable manifest raises RuntimeError."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        manifest = stage_root / STAGE_MANIFEST_NAME
        manifest.write_text("not-json")

        svc = _svc()
        with pytest.raises(RuntimeError, match="unreadable"):
            svc.revert_stage(stage_root)


class TestRevertStageMalformedEntry:
    def test_malformed_entry_kept_in_remaining(self, tmp_path):
        """Lines 780-781: entry missing staged/original -> kept in remaining."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        manifest = stage_root / STAGE_MANIFEST_NAME
        manifest.write_text(json.dumps([
            {"curator_id": "xxx", "original": "", "staged": ""},  # malformed
        ]))

        svc = _svc()
        report = svc.revert_stage(stage_root)
        # Manifest still exists with the malformed entry preserved
        loaded = json.loads(manifest.read_text(encoding="utf-8"))
        assert len(loaded) == 1


class TestRevertStageMoveFailure:
    def test_shutil_move_failure_recorded_as_failed(self, tmp_path, monkeypatch):
        """Lines 810-823: shutil.move raises -> FAILED outcome."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        staged_file = stage_root / "f.txt"
        staged_file.write_text("data")
        original = tmp_path / "original_dir" / "f.txt"

        manifest = stage_root / STAGE_MANIFEST_NAME
        manifest.write_text(json.dumps([
            {"curator_id": str(uuid4()), "original": str(original), "staged": str(staged_file)},
        ]))

        # Force shutil.move to raise
        def _move_boom(src, dst, *a, **kw):
            raise RuntimeError("move denied")

        monkeypatch.setattr(shutil, "move", _move_boom)

        svc = _svc()
        report = svc.revert_stage(stage_root)
        assert any(m.outcome == RevertOutcome.FAILED for m in report.moves)


# ---------------------------------------------------------------------------
# _sync_index_after_move: malformed UUID + entity is None
# ---------------------------------------------------------------------------


class TestSyncIndexAfterMove:
    def test_malformed_uuid_logged_and_returned(self):
        """Lines 915-920: UUID() raises -> debug log, return."""
        svc = _svc()  # file_repo is MagicMock
        # Must not raise
        svc._sync_index_after_move("not-a-uuid", "/x")
        # file_repo.get was never called
        svc.files.get.assert_not_called()

    def test_entity_is_none_returns_without_update(self):
        """Line 933: file_repo.get returns None -> return without update."""
        file_repo = MagicMock()
        file_repo.get.return_value = None
        svc = _svc(file_repo=file_repo)
        # Valid UUID
        svc._sync_index_after_move(str(uuid4()), "/x")
        file_repo.get.assert_called_once()
        file_repo.update.assert_not_called()
