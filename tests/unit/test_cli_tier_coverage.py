"""Coverage closure for cli/main.py `tier` command (v1.7.172).

Tier 3 sub-ship 18 of the CLI Coverage Arc. Largest single sub-ship
(~320 lines). Per Lesson #88 scope plan, considered for split but
landing as single ship — the apply branch reuses scan infrastructure
so the test surface is unified.

Covers: TierRecipe.from_string validation, scan/render (human/JSON/CSV
with show-files, limit, all 3 recipe colors), --apply branch (validation,
plan building, dry-run, confirm prompt, execution + outcome tally).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from curator._compat.datetime import utcnow_naive
from curator.cli.main import app
from curator.models import FileEntity
from curator.services.migration import (
    MigrationMove, MigrationOutcome, MigrationPlan, MigrationReport,
)
from curator.services.safety import SafetyLevel
from curator.services.tier import (
    TierCandidate, TierCriteria, TierRecipe, TierReport,
)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_tier.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


def _file(*, path: str = "/x.txt", size: int = 100,
           status: str = "provisional", source_id: str = "local") -> FileEntity:
    return FileEntity(
        source_id=source_id, source_path=path,
        size=size, mtime=utcnow_naive(),
        status=status,
        last_scanned_at=datetime(2025, 1, 1),
    )


def _candidate(file: FileEntity, reason: str = "stale 200 days") -> TierCandidate:
    return TierCandidate(file=file, reason=reason)


def _report(
    *, recipe: TierRecipe = TierRecipe.COLD,
    candidates: list[TierCandidate] | None = None,
    source_id: str | None = None,
    root_prefix: str | None = None,
) -> TierReport:
    criteria = TierCriteria(
        recipe=recipe, min_age_days=90,
        source_id=source_id, root_prefix=root_prefix,
    )
    return TierReport(
        recipe=recipe, criteria=criteria,
        candidates=candidates or [],
        scanned_count=10,
        started_at=datetime(2026, 1, 1, 12, 0, 0),
        completed_at=datetime(2026, 1, 1, 12, 0, 1),
    )


def _migration_plan(moves: list[MigrationMove] | None = None) -> MigrationPlan:
    return MigrationPlan(
        src_source_id="local", src_root="/src",
        dst_source_id="local", dst_root="/dst",
        moves=moves or [],
    )


def _mig_move(*, curator_id=None, src: str = "/src/a.txt",
               size: int = 100,
               outcome: MigrationOutcome | None = MigrationOutcome.MOVED,
               error: str | None = None) -> MigrationMove:
    return MigrationMove(
        curator_id=curator_id or uuid4(),
        src_path=src, dst_path=src.replace("src", "dst"),
        safety_level=SafetyLevel.SAFE, size=size, src_xxhash="h",
        outcome=outcome, error=error,
    )


def _mig_report(moves: list[MigrationMove] | None = None) -> MigrationReport:
    plan = _migration_plan(moves)
    rep = MigrationReport(
        plan=plan,
        started_at=datetime(2026, 1, 1, 12, 0, 0),
        completed_at=datetime(2026, 1, 1, 12, 0, 1),
    )
    rep.moves = moves or []
    return rep


# ---------------------------------------------------------------------------
# Recipe validation
# ---------------------------------------------------------------------------


class TestTierRecipeValidation:
    def test_unknown_recipe_exits_2(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "tier", "bogus"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Unknown tier recipe" in combined


# ---------------------------------------------------------------------------
# Scan / render (detect-only mode)
# ---------------------------------------------------------------------------


class TestTierScan:
    def test_no_candidates_human(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.tier import TierService
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report())
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "tier", "cold"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No cold candidates" in combined

    def test_with_candidates_human(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.tier import TierService
        candidates = [
            _candidate(_file(path="/c1.txt", size=1024)),
            _candidate(_file(path="/c2.txt", size=2048)),
        ]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "tier", "cold"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Candidates" in combined
        assert "By source" in combined
        assert "local: 2" in combined

    def test_all_three_recipes(self, runner, isolated_cli_db, monkeypatch):
        """COLD, EXPIRED, ARCHIVE color branches."""
        from curator.services.tier import TierService
        for recipe in (TierRecipe.COLD, TierRecipe.EXPIRED, TierRecipe.ARCHIVE):
            monkeypatch.setattr(
                TierService, "scan",
                lambda self, c, _r=recipe: _report(recipe=_r,
                    candidates=[_candidate(_file())]),
            )
            result = runner.invoke(
                app,
                ["--db", str(isolated_cli_db["db_path"]),
                 "tier", recipe.value],
            )
            assert result.exit_code == 0
            combined = result.stdout + (result.stderr or "")
            assert recipe.value in combined

    def test_expired_recipe_skips_min_age_label(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """EXPIRED skips the 'Min age' line."""
        from curator.services.tier import TierService
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(recipe=TierRecipe.EXPIRED))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "expired"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Min age:" not in combined

    def test_show_files_renders_paths(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.tier import TierService
        candidates = [_candidate(_file(path=f"/file_{i}.txt"))
                      for i in range(3)]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--show-files"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "file_0.txt" in combined
        assert "Candidates (oldest" in combined

    def test_show_files_with_limit_caps(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.tier import TierService
        candidates = [_candidate(_file(path=f"/lim_{i}.txt"))
                      for i in range(10)]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--show-files", "--limit", "3"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "and 7 more" in combined

    def test_source_and_root_filters_passed(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.tier import TierService
        captured = {}

        def _stub(self, criteria):
            captured["criteria"] = criteria
            return _report(source_id=criteria.source_id,
                            root_prefix=criteria.root_prefix)

        monkeypatch.setattr(TierService, "scan", _stub)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold",
             "--source-id", "local:other",
             "--root", "/sub",
             "--min-age-days", "180"],
        )
        assert result.exit_code == 0
        c = captured["criteria"]
        assert c.source_id == "local:other"
        assert c.root_prefix == "/sub"
        assert c.min_age_days == 180

    def test_archive_default_min_age(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """ARCHIVE recipe default min_age_days is 365."""
        from curator.services.tier import TierService
        captured = {}

        def _stub(self, criteria):
            captured["criteria"] = criteria
            return _report(recipe=TierRecipe.ARCHIVE)

        monkeypatch.setattr(TierService, "scan", _stub)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "archive"],
        )
        assert result.exit_code == 0
        assert captured["criteria"].min_age_days == 365

    def test_json_output(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.tier import TierService
        candidates = [_candidate(_file(path="/j.txt"))]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "tier", "cold"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"recipe": "cold"' in combined
        assert '"candidate_count": 1' in combined
        assert '"by_source"' in combined

    def test_csv_with_header(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.tier import TierService
        f = _file(path="/csv.txt")
        f.expires_at = datetime(2026, 12, 31)
        candidates = [_candidate(f)]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--csv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "curator_id,source_id,source_path" in combined
        assert "/csv.txt" in combined

    def test_csv_no_header_tsv(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.tier import TierService
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=[
                                _candidate(_file(path="/t.txt"))
                             ]))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold",
             "--csv", "--no-header", "--csv-dialect", "tsv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "curator_id\tsource_id" not in combined
        assert "/t.txt" in combined


# ---------------------------------------------------------------------------
# --apply branch
# ---------------------------------------------------------------------------


class TestTierApply:
    def test_apply_without_target_exits_2(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.tier import TierService
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=[_candidate(_file())]))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "--apply requires --target" in combined

    def test_apply_without_root_exits_2(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        from curator.services.tier import TierService
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=[_candidate(_file())]))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply",
             "--target", str(tmp_path / "dst")],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "--apply requires --root" in combined

    def test_apply_no_candidates_returns(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        from curator.services.tier import TierService
        # Empty candidates -> "No cold candidates" early return.
        # But --apply happens AFTER that branch in human mode... actually
        # the early-return for empty happens at line 4562-4564 (BEFORE
        # the apply check). Let me verify by routing into apply with
        # empty candidates.
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=[]))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply",
             "--root", "/x", "--target", str(tmp_path / "dst")],
        )
        # Empty candidates returns before apply path
        assert result.exit_code == 0

    def test_apply_target_makedirs_failure_exits_2(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """makedirs OSError -> exit 2."""
        from curator.services.tier import TierService
        candidates = [_candidate(_file())]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        import os
        monkeypatch.setattr(os, "makedirs",
                             lambda p, **kw: (_ for _ in ()).throw(
                                OSError("read-only filesystem")))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply",
             "--root", "/x", "--target", str(tmp_path / "dst")],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Could not create --target" in combined

    def test_apply_filtered_moves_empty(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Plan filtered to candidate_ids returns empty -> warning + return."""
        from curator.services.tier import TierService
        from curator.services.migration import MigrationService
        candidates = [_candidate(_file())]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        # MigrationService.plan returns a plan whose moves don't match the
        # candidate curator_ids
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: _migration_plan(
                                 moves=[_mig_move(curator_id=uuid4())]))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply",
             "--root", "/x", "--target", str(tmp_path / "dst")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No candidates matched" in combined

    def test_apply_dry_run(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """--dry-run shows preview without executing."""
        from curator.services.tier import TierService
        from curator.services.migration import MigrationService
        cid = uuid4()
        f = _file()
        f.curator_id = cid
        candidates = [TierCandidate(file=f, reason="stale")]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: _migration_plan(
                                 moves=[_mig_move(curator_id=cid)]))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply", "--dry-run",
             "--root", "/x", "--target", str(tmp_path / "dst")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "--dry-run set" in combined
        assert "Would move" in combined

    def test_apply_dry_run_caps_at_10(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Dry-run shows 10 moves + 'and N more' if > 10."""
        from curator.services.tier import TierService
        from curator.services.migration import MigrationService
        files = [_file(path=f"/f{i}.txt") for i in range(15)]
        candidates = [TierCandidate(file=f, reason="stale") for f in files]
        cids = {c.file.curator_id for c in candidates}
        # All matching moves
        moves = [_mig_move(curator_id=c.file.curator_id,
                            src=c.file.source_path) for c in candidates]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: _migration_plan(moves=moves))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply", "--dry-run",
             "--root", "/x", "--target", str(tmp_path / "dst")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "and 5 more" in combined

    def test_apply_yes_skips_prompt_and_executes(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """--yes skips confirm, executes migration, prints tally."""
        from curator.services.tier import TierService
        from curator.services.migration import MigrationService
        cid = uuid4()
        f = _file()
        f.curator_id = cid
        candidates = [TierCandidate(file=f, reason="stale")]
        moves = [_mig_move(curator_id=cid, outcome=MigrationOutcome.MOVED)]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: _migration_plan(moves=moves))
        monkeypatch.setattr(MigrationService, "apply",
                             lambda self, p, **kw: _mig_report(moves=moves))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply", "--yes",
             "--root", "/x", "--target", str(tmp_path / "dst")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Migration complete" in combined
        assert "Moved/copied" in combined

    def test_apply_confirm_declined_exits_1(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Without --yes, declining the confirm -> exit 1."""
        from curator.services.tier import TierService
        from curator.services.migration import MigrationService
        cid = uuid4()
        f = _file()
        f.curator_id = cid
        candidates = [TierCandidate(file=f, reason="stale")]
        moves = [_mig_move(curator_id=cid)]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: _migration_plan(moves=moves))
        # CliRunner provides "no" by default on confirm; pass "n\n" as input
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply",
             "--root", "/x", "--target", str(tmp_path / "dst")],
            input="n\n",
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Aborted" in combined

    def test_apply_with_failures_tallied(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Apply tallies moved/skipped/failed correctly + renders failures."""
        from curator.services.tier import TierService
        from curator.services.migration import MigrationService
        ids = [uuid4() for _ in range(3)]
        files = [_file(path=f"/f{i}.txt") for i in range(3)]
        for i, f in enumerate(files):
            f.curator_id = ids[i]
        candidates = [TierCandidate(file=f, reason="stale") for f in files]
        moves = [
            _mig_move(curator_id=ids[0], src="/f0.txt",
                       outcome=MigrationOutcome.MOVED),
            _mig_move(curator_id=ids[1], src="/f1.txt",
                       outcome=MigrationOutcome.SKIPPED_NOT_SAFE),
            _mig_move(curator_id=ids[2], src="/f2.txt",
                       outcome=MigrationOutcome.FAILED,
                       error="io error"),
        ]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: _migration_plan(moves=moves))
        monkeypatch.setattr(MigrationService, "apply",
                             lambda self, p, **kw: _mig_report(moves=moves))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply", "--yes",
             "--root", "/x", "--target", str(tmp_path / "dst")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Moved/copied:" in combined
        assert "Skipped:" in combined
        assert "Failed:" in combined
        assert "Failures" in combined
        assert "io error" in combined

    def test_apply_keep_source_mode(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """--keep-source switches MOVE -> COPY mode."""
        from curator.services.tier import TierService
        from curator.services.migration import MigrationService
        cid = uuid4()
        f = _file()
        f.curator_id = cid
        candidates = [TierCandidate(file=f, reason="stale")]
        moves = [_mig_move(curator_id=cid, outcome=MigrationOutcome.COPIED)]
        monkeypatch.setattr(TierService, "scan",
                             lambda self, c: _report(candidates=candidates))
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: _migration_plan(moves=moves))
        monkeypatch.setattr(MigrationService, "apply",
                             lambda self, p, **kw: _mig_report(moves=moves))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "tier", "cold", "--apply", "--yes",
             "--keep-source",
             "--root", "/x", "--target", str(tmp_path / "dst")],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "COPY" in combined
