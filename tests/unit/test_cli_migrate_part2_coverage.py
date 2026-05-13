"""Coverage closure for cli/main.py `migrate` command Part 2 (v1.7.167).

Tier 3 sub-ship 13 of the CLI Coverage Arc. Second half of v1.7.166/167 split.

Scope: `_render_migration_report`, Phase 1 (`apply()`) execution path,
Phase 2 (`create_job` + `run_job`) execution path, `_migrate_resume`
full execution, all conflict modes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.migration import (
    MigrationConflictError, MigrationMove, MigrationOutcome,
    MigrationPlan, MigrationReport,
)
from curator.services.safety import SafetyLevel


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_migrate2.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


def _make_move(*, path: str = "/x.txt", level=SafetyLevel.SAFE,
                size: int = 100,
                outcome: MigrationOutcome | None = MigrationOutcome.MOVED,
                error: str | None = None) -> MigrationMove:
    m = MigrationMove(
        curator_id=uuid4(),
        src_path=path, dst_path=path.replace("src", "dst"),
        safety_level=level, size=size, src_xxhash="h",
        outcome=outcome, error=error,
    )
    return m


def _make_plan(*, n_safe=2, n_caution=0, n_refuse=0) -> MigrationPlan:
    moves = []
    for i in range(n_safe):
        moves.append(_make_move(path=f"/src/safe_{i}.txt"))
    for i in range(n_caution):
        moves.append(_make_move(path=f"/src/cau_{i}.txt",
                                 level=SafetyLevel.CAUTION))
    for i in range(n_refuse):
        moves.append(_make_move(path=f"/src/ref_{i}.txt",
                                 level=SafetyLevel.REFUSE))
    return MigrationPlan(
        src_source_id="local", src_root="/src",
        dst_source_id="local", dst_root="/dst",
        moves=moves,
    )


def _make_report(
    plan: MigrationPlan | None = None,
    *,
    moved: int = 0, skipped: int = 0, failed: int = 0,
    with_errors: bool = False,
) -> MigrationReport:
    plan = plan or _make_plan()
    rep = MigrationReport(
        plan=plan,
        started_at=datetime(2026, 1, 1, 12, 0, 0),
        completed_at=datetime(2026, 1, 1, 12, 0, 5),
    )
    rep.moves = []
    for i in range(moved):
        rep.moves.append(_make_move(
            path=f"/src/m{i}.txt", outcome=MigrationOutcome.MOVED, size=1024,
        ))
    for i in range(skipped):
        rep.moves.append(_make_move(
            path=f"/src/s{i}.txt", outcome=MigrationOutcome.SKIPPED_NOT_SAFE,
        ))
    for i in range(failed):
        rep.moves.append(_make_move(
            path=f"/src/f{i}.txt", outcome=MigrationOutcome.FAILED,
            error=f"io_err_{i}" if with_errors else None,
        ))
    return rep


# ---------------------------------------------------------------------------
# Phase 1: --apply path
# ---------------------------------------------------------------------------


class TestMigrateApplyPhase1:
    def test_no_eligible_files_human(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Plan has 0 SAFE + 0 CAUTION (or include_caution false) -> early return."""
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=0, n_caution=2, n_refuse=1)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No eligible files" in combined

    def test_no_eligible_files_json(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=0, n_caution=2, n_refuse=1)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"moved": 0' in combined
        assert '"reason": "no eligible files in plan"' in combined

    def test_happy_path_human(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=3)
        report = _make_report(plan, moved=3)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(MigrationService, "apply",
                             lambda self, p, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Migration applied" in combined
        assert "MOVED" in combined
        assert "Bytes moved" in combined

    def test_keep_source_changes_heading(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """--keep-source -> 'COPIED' heading + label."""
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=2)
        report = _make_report(plan, moved=2)
        # Mark moves as COPIED outcome for keep_source flow
        for m in report.moves:
            m.outcome = MigrationOutcome.COPIED
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(MigrationService, "apply",
                             lambda self, p, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst",
             "--apply", "--keep-source"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Migration copied" in combined
        assert "COPIED" in combined

    def test_failed_count_exits_1(self, runner, isolated_cli_db, monkeypatch):
        """Any failed move -> exit 1."""
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=2)
        report = _make_report(plan, moved=1, failed=1, with_errors=True)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(MigrationService, "apply",
                             lambda self, p, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Failures" in combined
        assert "io_err_0" in combined

    def test_apply_json_output_includes_results(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=2)
        report = _make_report(plan, moved=2)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(MigrationService, "apply",
                             lambda self, p, **kw: report)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"action": "migrate.apply"' in combined
        assert '"moved": 2' in combined
        assert '"results"' in combined

    def test_apply_value_error_exits_2(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """apply() ValueError (e.g. unknown conflict mode) -> exit 2."""
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=2)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)

        def _raise(self, p, **kw):
            raise ValueError("unknown --on-conflict mode")
        monkeypatch.setattr(MigrationService, "apply", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply",
             "--on-conflict", "bogus"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "unknown --on-conflict mode" in combined

    def test_apply_migration_conflict_error_exits_1(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """MigrationConflictError (--on-conflict=fail aborts) -> exit 1."""
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=2)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)

        def _raise(self, p, **kw):
            raise MigrationConflictError(
                src_path="/src/a.txt", dst_path="/dst/a.txt",
            )
        monkeypatch.setattr(MigrationService, "apply", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply",
             "--on-conflict", "fail"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Migration aborted" in combined
        assert "dst:" in combined
        assert "src:" in combined

    def test_apply_unexpected_exception_reraises(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Non-ValueError, non-MigrationConflictError exception -> propagates."""
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=2)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)

        def _raise(self, p, **kw):
            raise RuntimeError("totally unexpected")
        monkeypatch.setattr(MigrationService, "apply", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply"],
            catch_exceptions=True,
        )
        # RuntimeError propagates; exit code is non-zero
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Phase 2: --workers > 1 -> create_job + run_job
# ---------------------------------------------------------------------------


class TestMigrateApplyPhase2:
    def test_phase2_happy_path(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=3)
        report = _make_report(plan, moved=3)
        job_id = uuid4()
        captured = {}

        def _stub_create(self, plan, *, options, db_path_guard, include_caution):
            captured["create_called"] = True
            captured["options"] = options
            captured["include_caution"] = include_caution
            return job_id

        def _stub_run(self, jid, *, workers, verify_hash, keep_source,
                       max_retries, on_conflict):
            captured["run_called"] = True
            captured["workers"] = workers
            return report

        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(MigrationService, "create_job", _stub_create)
        monkeypatch.setattr(MigrationService, "run_job", _stub_run)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst",
             "--apply", "--workers", "4"],
        )
        assert result.exit_code == 0
        assert captured.get("create_called") is True
        assert captured.get("run_called") is True
        assert captured.get("workers") == 4
        combined = result.stdout + (result.stderr or "")
        assert "Migration job created" in combined
        # job_id appears in heading of migration report
        assert str(job_id) in combined

    def test_phase2_failed_count_exits_1(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=3)
        report = _make_report(plan, moved=2, failed=1, with_errors=True)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(MigrationService, "create_job",
                             lambda self, plan, *, options, db_path_guard, include_caution: uuid4())
        monkeypatch.setattr(MigrationService, "run_job",
                             lambda self, jid, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst",
             "--apply", "--workers", "2"],
        )
        assert result.exit_code == 1

    def test_phase2_json_includes_job_id(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=2)
        report = _make_report(plan, moved=2)
        job_id = uuid4()
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(MigrationService, "create_job",
                             lambda self, plan, *, options, db_path_guard, include_caution: job_id)
        monkeypatch.setattr(MigrationService, "run_job",
                             lambda self, jid, **kw: report)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst",
             "--apply", "--workers", "2"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"job_id"' in combined
        assert str(job_id) in combined


# ---------------------------------------------------------------------------
# --resume execution
# ---------------------------------------------------------------------------


class TestMigrateResumeFull:
    def test_resume_runs_and_succeeds(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Existing job -> run_job + report."""
        from curator.models.migration import MigrationJob
        from curator.services.migration import MigrationService
        from curator.storage.repositories import MigrationJobRepository

        job = MigrationJob(
            src_source_id="local", src_root="/src",
            dst_source_id="local", dst_root="/dst",
            status="partial", files_total=10,
        )
        monkeypatch.setattr(MigrationJobRepository, "get_job",
                             lambda self, jid: job)
        plan = _make_plan(n_safe=3)
        report = _make_report(plan, moved=3)
        monkeypatch.setattr(MigrationService, "run_job",
                             lambda self, jid, **kw: report)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--resume", str(job.job_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Resuming migration job" in combined
        assert "Migration applied" in combined

    def test_resume_with_failures_exits_1(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.models.migration import MigrationJob
        from curator.services.migration import MigrationService
        from curator.storage.repositories import MigrationJobRepository

        job = MigrationJob(
            src_source_id="local", src_root="/src",
            dst_source_id="local", dst_root="/dst",
            status="failed",
        )
        monkeypatch.setattr(MigrationJobRepository, "get_job",
                             lambda self, jid: job)
        plan = _make_plan(n_safe=2)
        report = _make_report(plan, moved=1, failed=1)
        monkeypatch.setattr(MigrationService, "run_job",
                             lambda self, jid, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--resume", str(job.job_id)],
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# _render_migration_report — failures cap + branches
# ---------------------------------------------------------------------------


class TestRenderMigrationReportBranches:
    def test_more_than_20_failures_caps(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=30)
        report = _make_report(plan, moved=0, failed=25, with_errors=True)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(MigrationService, "apply",
                             lambda self, p, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "... and 5 more" in combined

    def test_report_with_skipped_moves(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=2)
        report = _make_report(plan, moved=1, skipped=1)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        monkeypatch.setattr(MigrationService, "apply",
                             lambda self, p, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "SKIPPED" in combined
