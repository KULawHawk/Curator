"""Coverage closure for cli/main.py `organize` + `organize-revert` + helpers (v1.7.163).

Tier 3 sub-ship 9 of the CLI Coverage Arc.

Targets ~200 uncovered lines spanning:
- Validation branches: --type without --target, unknown --type, --stage/--apply
  without --type+--target, --stage + --apply mutually exclusive, --enrich-mb
  without --type music, --enrich-mb without --mb-contact
- --enrich-mb MusicBrainz client import error + missing musicbrainzngs
- organize.plan happy path (basic, JSON, --show-files)
- --stage path: success + ValueError
- --apply path: success + ValueError
- _organize_plan_to_dict + _render_organize_plan (REFUSE/CAUTION/SAFE buckets,
  proposals, file lists, empty-plan hint)
- _stage_report_to_dict + _render_stage_report (failures/skipped/cap-at-5/mode hint)
- organize-revert: happy + FileNotFoundError (exit 1) + RuntimeError (exit 2) +
  JSON + human (with failed/skipped moves)
"""

from __future__ import annotations

import sys
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from curator._compat.datetime import utcnow_naive
from curator.cli.main import app
from curator.models import FileEntity, SourceConfig
from curator.services.organize import (
    OrganizeBucket, OrganizePlan,
    RevertMove, RevertOutcome, RevertReport,
    StageMove, StageOutcome, StageReport,
)
from curator.services.safety import SafetyConcern


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_organize.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


def _empty_plan(source_id="local") -> OrganizePlan:
    return OrganizePlan(
        source_id=source_id, root_prefix=None,
        completed_at=datetime(2026, 1, 1, 0, 0, 1),
    )


def _populated_plan(source_id="local", *, with_proposals=False) -> OrganizePlan:
    plan = OrganizePlan(
        source_id=source_id, root_prefix="/sub",
        completed_at=datetime(2026, 1, 1, 0, 0, 1),
    )
    # 2 SAFE, 1 CAUTION, 1 REFUSE
    from curator.services.safety import SafetyReport, SafetyLevel
    f1 = FileEntity(
        source_id=source_id, source_path="/safe1.txt", size=100,
        mtime=utcnow_naive(),
    )
    f2 = FileEntity(
        source_id=source_id, source_path="/safe2.txt", size=200,
        mtime=utcnow_naive(),
    )
    fc = FileEntity(
        source_id=source_id, source_path="/cau.txt", size=50,
        mtime=utcnow_naive(),
    )
    fr = FileEntity(
        source_id=source_id, source_path="/ref.txt", size=10,
        mtime=utcnow_naive(),
    )
    plan.safe.add(f1, SafetyReport(path="/safe1.txt", level=SafetyLevel.SAFE))
    plan.safe.add(f2, SafetyReport(path="/safe2.txt", level=SafetyLevel.SAFE))
    cau_rep = SafetyReport(path="/cau.txt")
    cau_rep.add_concern(SafetyConcern.APP_DATA, "in roaming")
    plan.caution.add(fc, cau_rep)
    ref_rep = SafetyReport(path="/ref.txt")
    ref_rep.add_concern(SafetyConcern.OS_MANAGED, "/Windows")
    plan.refuse.add(fr, ref_rep)
    if with_proposals:
        plan.safe.proposals[str(f1.curator_id)] = "/target/A.txt"
        plan.safe.proposals[str(f2.curator_id)] = "/target/B.txt"
    return plan


def _stage_report(*, moved=0, skipped=0, failed=0,
                  mode="stage", with_errors=False) -> StageReport:
    rep = StageReport(
        stage_root="/stage",
        started_at=datetime(2026, 1, 1),
        completed_at=datetime(2026, 1, 1, 0, 0, 1),
    )
    for i in range(moved):
        rep.moves.append(StageMove(
            curator_id=f"m{i}", original=f"/o{i}", staged=f"/s{i}",
            outcome=StageOutcome.MOVED,
        ))
    for i in range(skipped):
        rep.moves.append(StageMove(
            curator_id=f"sk{i}", original=f"/sko{i}", staged=None,
            outcome=StageOutcome.SKIPPED_NO_PROPOSAL,
        ))
    for i in range(failed):
        rep.moves.append(StageMove(
            curator_id=f"f{i}", original=f"/fo{i}", staged=None,
            outcome=StageOutcome.FAILED,
            error=f"err{i}" if with_errors else None,
        ))
    return rep


# ---------------------------------------------------------------------------
# Validation branches (lines 1914-1944)
# ---------------------------------------------------------------------------


class TestOrganizeValidation:
    def test_type_without_target_errors(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "--type requires --target" in combined

    def test_unknown_type_errors(self, runner, isolated_cli_db, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "bogus", "--target", str(tmp_path)],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Unknown --type" in combined

    def test_stage_without_type_or_target_errors(
        self, runner, isolated_cli_db, tmp_path,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--stage", str(tmp_path / "stage")],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "--stage requires --type and --target" in combined

    def test_apply_without_type_or_target_errors(
        self, runner, isolated_cli_db,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--apply"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "--apply requires --type and --target" in combined

    def test_stage_and_apply_mutually_exclusive(
        self, runner, isolated_cli_db, tmp_path,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--stage", str(tmp_path / "stage"), "--apply"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "mutually exclusive" in combined


# ---------------------------------------------------------------------------
# --enrich-mb validation + MusicBrainz import (lines 1946-1976)
# ---------------------------------------------------------------------------


class TestEnrichMb:
    def test_enrich_mb_without_music_type_errors(
        self, runner, isolated_cli_db, tmp_path,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "photo", "--target", str(tmp_path),
             "--enrich-mb"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "--enrich-mb only applies with --type music" in combined

    def test_enrich_mb_without_contact_errors(
        self, runner, isolated_cli_db, tmp_path,
    ):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path),
             "--enrich-mb"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "--enrich-mb requires --mb-contact" in combined

    def test_enrich_mb_import_error_exits_2(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """Force `from curator.services.musicbrainz import ...` to fail."""
        # Pop + poison so the import re-runs and fails
        monkeypatch.delitem(sys.modules, "curator.services.musicbrainz", raising=False)
        monkeypatch.setitem(sys.modules, "curator.services.musicbrainz", None)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path),
             "--enrich-mb", "--mb-contact", "me@example.com"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "MusicBrainz client unavailable" in combined

    def test_enrich_mb_missing_musicbrainzngs(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """`_musicbrainzngs_available` returns False -> error + exit 2."""
        import curator.services.musicbrainz as mb_mod
        monkeypatch.setattr(mb_mod, "_musicbrainzngs_available", lambda: False)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path),
             "--enrich-mb", "--mb-contact", "me@example.com"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "musicbrainzngs is not installed" in combined

    def test_enrich_mb_happy_path_attaches_client(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """When everything is in order, MusicBrainzClient is attached to
        the runtime's organize service."""
        import curator.services.musicbrainz as mb_mod
        from curator.services.organize import OrganizeService
        monkeypatch.setattr(mb_mod, "_musicbrainzngs_available", lambda: True)

        # Stub plan to avoid running real plan logic
        def _stub_plan(self, **kwargs):
            return _empty_plan(source_id="local")
        monkeypatch.setattr(OrganizeService, "plan", _stub_plan)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path),
             "--enrich-mb", "--mb-contact", "me@example.com"],
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Plan rendering + JSON
# ---------------------------------------------------------------------------


class TestOrganizeRendering:
    def test_human_empty_plan(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.organize import OrganizeService
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: _empty_plan())
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Organize plan" in combined
        assert "No files indexed" in combined

    def test_human_populated_with_show_files(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 2083-2126: all three buckets rendered + --show-files
        prints each file path + the dest-arrow line for SAFE w/ proposal."""
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--show-files"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "REFUSE" in combined
        assert "CAUTION" in combined
        assert "SAFE" in combined
        # File paths shown
        assert "/safe1.txt" in combined
        assert "/cau.txt" in combined
        assert "/ref.txt" in combined
        # Proposed destinations rendered with arrow
        assert "/target/A.txt" in combined or "A.txt" in combined
        # Hint text (not "No files indexed" because plan is populated)
        assert "plan preview" in combined

    def test_human_populated_no_show_files_still_renders_buckets(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Verify buckets render WITHOUT --show-files (file lists hidden,
        proposals count still shown)."""
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "SAFE" in combined
        assert "2 destinations proposed" in combined
        # Individual file paths NOT in output
        assert "/safe1.txt" not in combined

    def test_json_empty_plan(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.organize import OrganizeService
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: _empty_plan())
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "organize", "local"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"source_id": "local"' in combined
        assert '"total_files": 0' in combined

    def test_json_populated_with_show_files(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """_organize_plan_to_dict include_files=True branch."""
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "organize", "local", "--show-files"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"files"' in combined
        assert '"safe"' in combined
        assert '"caution"' in combined
        assert '"refuse"' in combined
        assert '"proposals_count": 2' in combined


# ---------------------------------------------------------------------------
# --stage / --apply (lines 1991-2017)
# ---------------------------------------------------------------------------


class TestOrganizeStageApply:
    def test_stage_happy_path(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        report = _stage_report(moved=2)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(OrganizeService, "stage",
                             lambda self, p, *, stage_root: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--stage", str(tmp_path / "stage")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Stage" in combined
        assert "moved=2" in combined

    def test_stage_value_error_exits_2(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.organize import OrganizeService
        plan = _populated_plan()
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)

        def _raise(self, p, *, stage_root):
            raise ValueError("stage went wrong")
        monkeypatch.setattr(OrganizeService, "stage", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--stage", str(tmp_path / "stage")],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "stage failed" in combined

    def test_apply_happy_path(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        report = _stage_report(moved=2)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(OrganizeService, "apply",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Apply" in combined

    def test_apply_value_error_exits_2(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.organize import OrganizeService
        plan = _populated_plan()
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)

        def _raise(self, p):
            raise ValueError("apply went wrong")
        monkeypatch.setattr(OrganizeService, "apply", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--apply"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "apply failed" in combined

    def test_stage_json_output_includes_stage(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """_stage_report_to_dict + JSON merge with plan."""
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        report = _stage_report(moved=2)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(OrganizeService, "stage",
                             lambda self, p, *, stage_root: report)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--stage", str(tmp_path / "stage")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"stage"' in combined
        assert '"mode": "stage"' in combined
        assert '"moved_count": 2' in combined


# ---------------------------------------------------------------------------
# _render_stage_report branches (failures + skipped + mode hint)
# ---------------------------------------------------------------------------


class TestRenderStageReport:
    def test_failures_rendered(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        report = _stage_report(moved=1, failed=2, with_errors=True)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(OrganizeService, "stage",
                             lambda self, p, *, stage_root: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--stage", str(tmp_path / "stage")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Failures" in combined
        assert "err0" in combined

    def test_skipped_rendered_with_cap(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """Show only first 5 skipped + 'and N more' message."""
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        report = _stage_report(moved=0, skipped=10)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(OrganizeService, "stage",
                             lambda self, p, *, stage_root: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--stage", str(tmp_path / "stage")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Skipped" in combined
        assert "and 5 more" in combined

    def test_apply_mode_hint(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """Apply mode shows 'Files moved to their final destinations'."""
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        report = _stage_report(moved=2)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(OrganizeService, "apply",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "final destinations" in combined
        assert "organize-revert" in combined

    def test_stage_mode_hint(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """Stage mode shows 'Review the staged tree'."""
        from curator.services.organize import OrganizeService
        plan = _populated_plan(with_proposals=True)
        report = _stage_report(moved=2)
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(OrganizeService, "stage",
                             lambda self, p, *, stage_root: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--type", "music", "--target", str(tmp_path / "tgt"),
             "--stage", str(tmp_path / "stage")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Review the staged tree" in combined


# ---------------------------------------------------------------------------
# organize-revert
# ---------------------------------------------------------------------------


def _revert_report(*, restored=0, skipped=0, failed=0,
                    with_errors=False) -> RevertReport:
    rep = RevertReport(
        stage_root="/stage",
        started_at=datetime(2026, 1, 1),
        completed_at=datetime(2026, 1, 1, 0, 0, 1),
    )
    for i in range(restored):
        rep.moves.append(RevertMove(
            curator_id=f"r{i}", original=f"/o{i}", staged=f"/s{i}",
            outcome=RevertOutcome.RESTORED,
        ))
    for i in range(skipped):
        rep.moves.append(RevertMove(
            curator_id=f"sk{i}", original=f"/sko{i}", staged=f"/sks{i}",
            outcome=RevertOutcome.SKIPPED_ORIGINAL_OCCUPIED,
            error="someone else lives there" if with_errors else None,
        ))
    for i in range(failed):
        rep.moves.append(RevertMove(
            curator_id=f"f{i}", original=f"/fo{i}", staged=f"/fs{i}",
            outcome=RevertOutcome.FAILED,
            error=f"io_err_{i}" if with_errors else None,
        ))
    return rep


class TestOrganizeRevert:
    def test_file_not_found_exits_1(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.organize import OrganizeService

        def _raise(self, p):
            raise FileNotFoundError("no manifest at /stage")
        monkeypatch.setattr(OrganizeService, "revert_stage", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "organize-revert", str(tmp_path / "stage")],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "no manifest" in combined

    def test_runtime_error_exits_2(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.organize import OrganizeService

        def _raise(self, p):
            raise RuntimeError("corrupt manifest")
        monkeypatch.setattr(OrganizeService, "revert_stage", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "organize-revert", str(tmp_path / "stage")],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "revert failed" in combined

    def test_happy_human_output(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.organize import OrganizeService
        report = _revert_report(restored=3, skipped=1, failed=1,
                                 with_errors=True)
        monkeypatch.setattr(OrganizeService, "revert_stage",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "organize-revert", str(tmp_path / "stage")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Revert" in combined
        assert "restored=3" in combined
        assert "skipped=1" in combined
        assert "failed=1" in combined
        # Skipped + failed moves listed with errors
        assert "someone else lives there" in combined
        assert "io_err_0" in combined

    def test_happy_json_output(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.organize import OrganizeService
        report = _revert_report(restored=2)
        monkeypatch.setattr(OrganizeService, "revert_stage",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "organize-revert", str(tmp_path / "stage")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"restored_count": 2' in combined
        assert '"stage_root"' in combined
        assert '"moves"' in combined
