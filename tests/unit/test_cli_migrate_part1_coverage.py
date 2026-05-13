"""Coverage closure for cli/main.py `migrate` command Part 1 (v1.7.166).

Tier 3 sub-ship 12 of the CLI Coverage Arc. First of two migrate ships
per Lesson #88 pre-split (handoff predicted ~550 total uncovered lines).

Scope: `_parse_job_id`, `_migrate_list`, `_migrate_status`, `_migrate_abort`,
`_migrate_resume` (job-lookup branch), `_render_migration_plan`, and the
migrate_cmd validation + lifecycle dispatch + plan-only path.

v1.7.167 covers `_render_migration_report` + the Phase 1/Phase 2 apply
execution paths.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.models.migration import MigrationJob
from curator.services.migration import (
    MigrationMove, MigrationOutcome, MigrationPlan,
)
from curator.services.safety import SafetyLevel


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_migrate.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


def _make_job(*, status="completed", files_total=10, files_copied=8,
               files_skipped=1, files_failed=1, **kwargs) -> MigrationJob:
    base = dict(
        src_source_id="local", src_root="/src",
        dst_source_id="local", dst_root="/dst",
        status=status,
        files_total=files_total, files_copied=files_copied,
        files_skipped=files_skipped, files_failed=files_failed,
        bytes_copied=8 * 1024 * 1024,
        started_at=datetime(2026, 1, 1, 12, 0, 0),
        completed_at=datetime(2026, 1, 1, 12, 5, 0),
    )
    base.update(kwargs)
    return MigrationJob(**base)


def _make_move(*, path: str = "/x.txt", level=SafetyLevel.SAFE,
                size: int = 100) -> MigrationMove:
    return MigrationMove(
        curator_id=uuid4(),
        src_path=path, dst_path=path.replace("src", "dst"),
        safety_level=level, size=size, src_xxhash="h",
    )


def _make_plan(*, n_safe=2, n_caution=1, n_refuse=1) -> MigrationPlan:
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


# ---------------------------------------------------------------------------
# --list lifecycle
# ---------------------------------------------------------------------------


class TestMigrateList:
    def test_empty_human(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.migration import MigrationService
        monkeypatch.setattr(MigrationService, "list_jobs",
                             lambda self, *, status=None, limit=50: [])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "migrate", "--list"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No migration jobs found" in combined

    def test_empty_with_status_filter_human(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        monkeypatch.setattr(MigrationService, "list_jobs",
                             lambda self, *, status=None, limit=50: [])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--list", "--status-filter", "running"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "status=running" in combined

    def test_populated_human_all_status_colors(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Exercise the status-color mapping branches."""
        from curator.services.migration import MigrationService
        jobs = [
            _make_job(status="queued"),
            _make_job(status="running"),
            _make_job(status="completed"),
            _make_job(status="failed"),
            _make_job(status="cancelled"),
            _make_job(status="partial"),
            _make_job(status="other_unknown"),  # default 'white'
        ]
        monkeypatch.setattr(MigrationService, "list_jobs",
                             lambda self, *, status=None, limit=50: jobs)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "migrate", "--list"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "queued" in combined
        assert "running" in combined
        assert "completed" in combined

    def test_populated_json(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.migration import MigrationService
        jobs = [_make_job(status="completed")]
        monkeypatch.setattr(MigrationService, "list_jobs",
                             lambda self, *, status=None, limit=50: jobs)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "migrate", "--list"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"status": "completed"' in combined
        assert '"files_total"' in combined


# ---------------------------------------------------------------------------
# --status lifecycle
# ---------------------------------------------------------------------------


class TestMigrateStatus:
    def test_invalid_uuid_exits_2(self, runner, isolated_cli_db):
        """_parse_job_id: not-a-UUID -> exit 2."""
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--status", "not-a-uuid"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Not a valid job_id" in combined

    def test_job_not_found_exits_1(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.migration import MigrationService

        def _raise(self, job_id):
            raise ValueError("job not found")
        monkeypatch.setattr(MigrationService, "get_job_status", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--status", str(uuid4())],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "job not found" in combined

    def test_human_status_full(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.migration import MigrationService

        def _stub(self, job_id):
            return {
                "job_id": str(job_id),
                "status": "completed",
                "src_source_id": "local",
                "src_root": "/src",
                "dst_source_id": "local",
                "dst_root": "/dst",
                "files_total": 10,
                "files_copied": 8,
                "files_skipped": 1,
                "files_failed": 1,
                "bytes_copied": 1024 * 1024,
                "started_at": "2026-01-01T12:00:00",
                "completed_at": "2026-01-01T12:05:00",
                "duration_seconds": 300.0,
                "progress_histogram": {"moved": 8, "skipped": 1, "failed": 1},
                "options": {"workers": 2, "verify_hash": True},
                "error": "one transient error",
            }
        monkeypatch.setattr(MigrationService, "get_job_status", _stub)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--status", str(uuid4())],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "status:" in combined
        assert "files copied" in combined
        assert "progress:" in combined
        assert "options:" in combined
        assert "one transient error" in combined

    def test_human_status_minimal(self, runner, isolated_cli_db, monkeypatch):
        """Branches: started_at None, completed_at None, no progress, no options, no error."""
        from curator.services.migration import MigrationService

        def _stub(self, job_id):
            return {
                "job_id": str(job_id), "status": "queued",
                "src_source_id": "local", "src_root": "/s",
                "dst_source_id": "local", "dst_root": "/d",
                "files_total": 0, "files_copied": 0,
                "files_skipped": 0, "files_failed": 0,
                "bytes_copied": 0,
                "started_at": None, "completed_at": None,
                "duration_seconds": None,
                "progress_histogram": {},
                "options": {},
                "error": None,
            }
        monkeypatch.setattr(MigrationService, "get_job_status", _stub)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--status", str(uuid4())],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "queued" in combined

    def test_json_status(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.migration import MigrationService

        def _stub(self, job_id):
            return {"job_id": str(job_id), "status": "completed"}
        monkeypatch.setattr(MigrationService, "get_job_status", _stub)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "migrate", "--status", str(uuid4())],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"status": "completed"' in combined


# ---------------------------------------------------------------------------
# --abort lifecycle
# ---------------------------------------------------------------------------


class TestMigrateAbort:
    def test_abort_human(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.migration import MigrationService
        aborted = []
        monkeypatch.setattr(
            MigrationService, "abort_job",
            lambda self, jid: aborted.append(jid),
        )
        jid = uuid4()
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--abort", str(jid)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Abort signal sent" in combined
        assert aborted == [jid]

    def test_abort_json(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.migration import MigrationService
        monkeypatch.setattr(MigrationService, "abort_job",
                             lambda self, jid: None)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "migrate", "--abort", str(uuid4())],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"action": "migrate.abort"' in combined
        assert '"sent": true' in combined

    def test_abort_invalid_uuid(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--abort", "garbage"],
        )
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# --resume lifecycle (job-lookup only; full resume in v1.7.167)
# ---------------------------------------------------------------------------


class TestMigrateResumeLookup:
    def test_resume_invalid_uuid(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--resume", "not-a-uuid"],
        )
        assert result.exit_code == 2

    def test_resume_job_not_found(self, runner, isolated_cli_db, monkeypatch):
        from curator.storage.repositories import MigrationJobRepository
        monkeypatch.setattr(
            MigrationJobRepository, "get_job",
            lambda self, jid: None,
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--resume", str(uuid4())],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "not found" in combined

    def test_resume_repo_lookup_exception(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.storage.repositories import MigrationJobRepository

        def _raise(self, jid):
            raise RuntimeError("db corrupt")
        monkeypatch.setattr(MigrationJobRepository, "get_job", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "--resume", str(uuid4())],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Failed to look up job" in combined


# ---------------------------------------------------------------------------
# migrate_cmd validation
# ---------------------------------------------------------------------------


class TestMigrateValidation:
    def test_missing_positional_args_exits_2(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "migrate"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "required" in combined.lower()

    def test_cross_source_dst_not_writable_exits_2(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Cross-source migration where dst plugin doesn't support write."""
        from curator.services.migration import MigrationService
        monkeypatch.setattr(MigrationService, "_can_write_to_source",
                             lambda self, sid: False)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst",
             "--dst-source-id", "gdrive:nowhere"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Cross-source migration" in combined
        assert "supports_write" in combined

    def test_plan_value_error_exits_2(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService

        def _raise(self, **kw):
            raise ValueError("plan rejected: bad inputs")
        monkeypatch.setattr(MigrationService, "plan", _raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "plan rejected" in combined


# ---------------------------------------------------------------------------
# migrate plan-only mode (_render_migration_plan)
# ---------------------------------------------------------------------------


class TestMigratePlanOnly:
    def test_plan_human_with_safe_moves(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=2, n_caution=1, n_refuse=1)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Migration plan" in combined
        assert "SAFE" in combined
        assert "CAUTION" in combined
        assert "REFUSE" in combined
        assert "Re-run with" in combined  # SAFE > 0 hint
        assert "--apply" in combined

    def test_plan_human_with_more_than_20_safe_moves_caps(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """`... and N more` cap for > 20 safe moves."""
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=25, n_caution=0, n_refuse=0)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "... and 5 more" in combined

    def test_plan_human_empty_safe(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """No SAFE moves -> 'Nothing to do.' hint."""
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=0, n_caution=2, n_refuse=1)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Nothing to do" in combined

    def test_plan_json(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.migration import MigrationService
        plan = _make_plan(n_safe=1)
        monkeypatch.setattr(MigrationService, "plan",
                             lambda self, **kw: plan)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"action": "migrate.plan"' in combined
        assert '"moves"' in combined
        assert '"safety_level"' in combined

    def test_plan_with_extensions_filter(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """--ext is parsed + passed to plan as a list."""
        from curator.services.migration import MigrationService
        captured = {}

        def _stub_plan(self, **kw):
            captured.update(kw)
            return _make_plan()
        monkeypatch.setattr(MigrationService, "plan", _stub_plan)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst",
             "--ext", ".mp3,.flac"],
        )
        assert result.exit_code == 0
        assert captured.get("extensions") == [".mp3", ".flac"]

    def test_plan_with_includes_excludes_path_prefix(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.migration import MigrationService
        captured = {}

        def _stub_plan(self, **kw):
            captured.update(kw)
            return _make_plan()
        monkeypatch.setattr(MigrationService, "plan", _stub_plan)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "migrate", "local", "/src", "/dst",
             "--include", "*.mp3", "--exclude", "*.tmp",
             "--path-prefix", "sub/dir"],
        )
        assert result.exit_code == 0
        assert captured.get("includes") == ["*.mp3"]
        assert captured.get("excludes") == ["*.tmp"]
        assert captured.get("path_prefix") == "sub/dir"
