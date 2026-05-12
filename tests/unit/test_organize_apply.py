"""Unit + CLI tests for Apply mode (Phase Gamma F2 v0.23).

Apply mode is structurally identical to Stage mode (see
tests/unit/test_organize_stage.py for full coverage). These tests
focus on what's DIFFERENT about Apply:

  * Destination IS plan.target_root (not a separate staging dir).
  * Audit entries are tagged 'organize.apply.move', not
    'organize.stage.move' \u2014 audit consumers can tell a final
    apply apart from a preview stage.
  * The CLI flag is mutually exclusive with --stage.
  * The renderer says "Apply" instead of "Stage".
"""

from __future__ import annotations

import json
from datetime import datetime
from curator._compat.datetime import utcnow_naive
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.models.file import FileEntity
from curator.services.music import MusicMetadata
from curator.services.organize import (
    STAGE_MANIFEST_NAME,
    OrganizePlan,
    OrganizeService,
    StageOutcome,
)
from curator.services.safety import (
    SafetyLevel,
    SafetyReport,
    SafetyService,
)


# ---------------------------------------------------------------------------
# Helpers (pared down from test_organize_stage.py)
# ---------------------------------------------------------------------------


def _make_file(path: str, size: int = 100) -> FileEntity:
    return FileEntity(
        curator_id=uuid4(),
        source_id="local",
        source_path=path,
        size=size,
        mtime=utcnow_naive(),
    )


def _safe_report(path: str) -> SafetyReport:
    return SafetyReport(path=path, level=SafetyLevel.SAFE)


def _build_service(audit=None) -> OrganizeService:
    return OrganizeService(MagicMock(), MagicMock(spec=SafetyService), audit=audit)


def _build_plan_with_proposal(
    *, src_file: Path, target_root: Path, proposed_relative: str,
) -> OrganizePlan:
    plan = OrganizePlan(
        source_id="local",
        root_prefix=None,
        target_root=str(target_root),
    )
    fe = _make_file(str(src_file), size=src_file.stat().st_size)
    plan.safe.add(
        fe, _safe_report(str(src_file)),
        proposed_destination=str(target_root / proposed_relative),
    )
    plan.completed_at = utcnow_naive()
    return plan


# ===========================================================================
# OrganizeService.apply
# ===========================================================================


class TestApply:
    def test_raises_if_plan_has_no_target_root(self):
        plan = OrganizePlan(source_id="local", root_prefix=None)
        plan.completed_at = utcnow_naive()
        svc = _build_service()
        with pytest.raises(ValueError, match="target_root"):
            svc.apply(plan)

    def test_moves_to_target_root_not_a_separate_dir(self, tmp_path):
        target_root = tmp_path / "library"
        src = tmp_path / "src.mp3"
        src.write_bytes(b"audio")

        plan = _build_plan_with_proposal(
            src_file=src,
            target_root=target_root,
            proposed_relative="Artist/Album/01 - Track.mp3",
        )
        svc = _build_service()
        report = svc.apply(plan)

        assert report.moved_count == 1
        # File ended up at target_root, not at some staging dir.
        final = target_root / "Artist" / "Album" / "01 - Track.mp3"
        assert final.exists()
        assert final.read_bytes() == b"audio"
        assert not src.exists()
        # The report's stage_root is target_root for an apply.
        assert Path(report.stage_root).resolve() == target_root.resolve()

    def test_writes_manifest_at_target_root(self, tmp_path):
        target_root = tmp_path / "lib"
        src = tmp_path / "x.mp3"
        src.write_bytes(b"x")
        plan = _build_plan_with_proposal(
            src_file=src, target_root=target_root,
            proposed_relative="A/B/x.mp3",
        )
        svc = _build_service()
        svc.apply(plan)

        manifest = target_root / STAGE_MANIFEST_NAME
        assert manifest.exists()
        entries = json.loads(manifest.read_text())
        assert len(entries) == 1
        assert entries[0]["original"] == str(src)

    def test_audit_action_is_apply_not_stage(self, tmp_path):
        target_root = tmp_path / "lib"
        src = tmp_path / "x.mp3"
        src.write_bytes(b"x")
        plan = _build_plan_with_proposal(
            src_file=src, target_root=target_root,
            proposed_relative="A/B/x.mp3",
        )
        audit = MagicMock()
        svc = _build_service(audit=audit)
        svc.apply(plan)

        kwargs = audit.log.call_args.kwargs
        # The actor + action distinguish apply from stage.
        assert kwargs["actor"] == "curator.organize.apply"
        assert kwargs["action"] == "organize.apply.move"
        # Mode is also recorded in details.
        assert kwargs["details"]["mode"] == "apply"

    def test_apply_then_revert_round_trip(self, tmp_path):
        target_root = tmp_path / "lib"
        src = tmp_path / "originals" / "song.mp3"
        src.parent.mkdir()
        src.write_bytes(b"audio")
        original_path = src

        plan = _build_plan_with_proposal(
            src_file=src, target_root=target_root,
            proposed_relative="A/B/song.mp3",
        )
        svc = _build_service()
        svc.apply(plan)
        # After apply, original is gone, file is at target.
        assert not original_path.exists()
        assert (target_root / "A" / "B" / "song.mp3").exists()

        # Revert reads the manifest at target_root and moves back.
        report = svc.revert_stage(target_root)
        assert report.restored_count == 1
        assert original_path.exists()
        assert original_path.read_bytes() == b"audio"
        # Manifest deleted.
        assert not (target_root / STAGE_MANIFEST_NAME).exists()


# ===========================================================================
# stage() with mode="apply" parameter (lower-level entry point)
# ===========================================================================


class TestStageWithApplyMode:
    def test_mode_param_changes_audit_actor(self, tmp_path):
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "elsewhere"
        src = tmp_path / "x.mp3"
        src.write_bytes(b"x")
        plan = _build_plan_with_proposal(
            src_file=src, target_root=target_root,
            proposed_relative="A/x.mp3",
        )
        audit = MagicMock()
        svc = _build_service(audit=audit)
        # Calling stage() directly with mode="apply" \u2014 even with a
        # different stage_root than target \u2014 still produces apply audit.
        svc.stage(plan, stage_root=stage_root, mode="apply")
        kwargs = audit.log.call_args.kwargs
        assert kwargs["action"] == "organize.apply.move"

    def test_default_mode_is_stage_for_backward_compat(self, tmp_path):
        target_root = tmp_path / "lib"
        stage_root = tmp_path / "stage"
        src = tmp_path / "x.mp3"
        src.write_bytes(b"x")
        plan = _build_plan_with_proposal(
            src_file=src, target_root=target_root,
            proposed_relative="A/x.mp3",
        )
        audit = MagicMock()
        svc = _build_service(audit=audit)
        # No mode given \u2014 v0.22 callers still get stage semantics.
        svc.stage(plan, stage_root=stage_root)
        kwargs = audit.log.call_args.kwargs
        assert kwargs["action"] == "organize.stage.move"


# ===========================================================================
# CLI integration
# ===========================================================================


pytestmark_int = pytest.mark.integration


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "curator_apply_cli.db"


def _patch_tags(monkeypatch, mapping: dict[str, MusicMetadata]) -> None:
    def fake_read_tags(self, path):
        return mapping.get(str(path))
    monkeypatch.setattr(
        "curator.services.music.MusicService.read_tags",
        fake_read_tags,
    )


def _isolated_safety_env(monkeypatch) -> None:
    """Force tmp_path subtrees to be SAFE (override platform safety registries)."""
    real_init = OrganizeService.__init__
    def patched_init(self, file_repo, safety, *args, **kwargs):
        # **kwargs is future-proof against new optional collaborators on
        # OrganizeService (music, photo, audit, etc.).
        loose = SafetyService(app_data_paths=[], os_managed_paths=[])
        real_init(self, file_repo, loose, *args, **kwargs)
    monkeypatch.setattr(OrganizeService, "__init__", patched_init)


@pytest.mark.integration
class TestApplyCli:
    def test_apply_without_type_errors(self, runner, db_path, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--apply"],
        )
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "--apply" in combined and ("--type" in combined or "--target" in combined)

    def test_apply_without_target_errors(self, runner, db_path):
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--type", "music", "--apply"],
        )
        assert result.exit_code != 0

    def test_apply_and_stage_mutually_exclusive(
        self, runner, db_path, tmp_path
    ):
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "lib"),
             "--apply", "--stage", str(tmp_path / "stage")],
        )
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "mutually exclusive" in combined

    def test_apply_round_trip(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(monkeypatch)

        media = tmp_path / "in"
        media.mkdir()
        track = media / "song.mp3"
        track.write_bytes(b"audio")

        _patch_tags(monkeypatch, {
            str(track): MusicMetadata(
                artist="Artist", album="Album", title="Song", track_number=3,
            ),
        })

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(media)])

        target = tmp_path / "library"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "music", "--target", str(target),
             "--apply", "--root", str(media)],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)
        # The JSON output includes mode="apply" so consumers can tell.
        assert payload["stage"]["mode"] == "apply"
        assert payload["stage"]["moved_count"] == 1

        # File ended up at target_root (not some staging dir).
        final = target / "Artist" / "Album" / "03 - Song.mp3"
        assert final.exists()
        # Original gone.
        assert not track.exists()

        # Revert via the same organize-revert command.
        revert = runner.invoke(
            app, ["--db", str(db_path), "organize-revert", str(target)],
        )
        assert revert.exit_code == 0
        assert track.exists()
        assert track.read_bytes() == b"audio"

    def test_apply_renderer_says_apply_not_stage(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(monkeypatch)
        media = tmp_path / "in"
        media.mkdir()
        track = media / "x.mp3"
        track.write_bytes(b"a")
        _patch_tags(monkeypatch, {
            str(track): MusicMetadata(artist="A", album="B", title="C", track_number=1),
        })
        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(media)])

        target = tmp_path / "lib"
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--type", "music", "--target", str(target),
             "--apply", "--root", str(media)],
        )
        assert result.exit_code == 0
        # Heading should say "Apply", not "Stage".
        assert "Apply" in result.stdout
        assert "moved=" in result.stdout
        # The trailing hint should mention "final destinations".
        assert "final destinations" in result.stdout
