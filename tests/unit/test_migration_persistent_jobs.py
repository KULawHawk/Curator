"""Focused unit tests for MigrationService persistent-job lifecycle + worker pool.

Sub-ship 5b of the Migration Phase Gamma arc (v1.7.93b — ARC CLOSURE).
Scope plan: docs/MIGRATION_PHASE_GAMMA_SCOPE.md

Group B of the v1.7.93 split. Closes the persistent-job lifecycle code:
the `create_job` plan-persistence path, the `run_job` orchestration
(options resolution, worker spawn, status finalize), the `_worker_loop`
per-row dispatch, the `_execute_one_persistent_*` same-source +
cross-source variants, plus `abort_job`, `list_jobs`, `get_job_status`,
and `_build_report_from_persisted`.

This sub-ship closes the Migration Phase Gamma arc by landing
`services/migration.py` at 100% line + branch.

New stub this ship: `StubMigrationJobRepository` — the entire
MigrationJobRepository surface that the persistent-path code touches,
modeled on `StubAuditRepository`. ~11 methods.

Threading discipline: tests run with `workers=1` and monkeypatch
`concurrent.futures.ThreadPoolExecutor` to a synchronous shim so the
worker pool is deterministic. Per Lesson #82 / #88, this avoids
flakiness while still exercising the same code paths.

Stubs from v1.7.89/90/91/93a reused (Lesson #84 / #87 — pattern
dividends compound across the arc).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from uuid import UUID, uuid4

import pytest

from curator.models.file import FileEntity
from curator.models.migration import MigrationJob, MigrationProgress
from curator.services.migration import (
    MigrationConflictError,
    MigrationMove,
    MigrationOutcome,
    MigrationPlan,
    MigrationReport,
    MigrationService,
)
from curator.services.safety import SafetyLevel

from tests.unit.test_migration_plan_apply import (
    NOW,
    StubAuditRepository,
    StubFileRepository,
    make_service as _base_make_service,
)
from tests.unit.test_migration_cross_source import (
    StubMigrationHooks,
    StubMigrationPluginManager,
)
from tests.unit.test_migration_persistent_progress import (
    make_progress,
    _setup_pm_for_transfer,
)


# ===========================================================================
# StubMigrationJobRepository
# ===========================================================================


@dataclass
class StubMigrationJobRepository:
    """In-memory stand-in for MigrationJobRepository covering the persistent
    path's full surface. Methods mirror the real repo's signatures but
    store everything in dicts for inspection.

    Per Lesson #84: minimal — only the methods the migration service
    actually calls. If a new method becomes needed, add it as a class
    member, not via attribute injection.
    """

    jobs: dict[UUID, MigrationJob] = field(default_factory=dict)
    # progress[(job_id, curator_id)] = MigrationProgress
    progress: dict[tuple[UUID, UUID], MigrationProgress] = field(
        default_factory=dict,
    )
    # Pending queue per job_id (FIFO). Workers pop from the front.
    _pending: dict[UUID, list[UUID]] = field(default_factory=dict)
    # Probes for assertions
    updates_logged: list[tuple[UUID, UUID, dict[str, Any]]] = field(
        default_factory=list,
    )
    count_increments: list[tuple[UUID, dict[str, int]]] = field(
        default_factory=list,
    )
    status_changes: list[tuple[UUID, str]] = field(default_factory=list)

    # ---- read API -----------------------------------------------------------

    def get_job(self, job_id: UUID) -> MigrationJob | None:
        return self.jobs.get(job_id)

    def list_jobs(
        self, *, status: str | None = None, limit: int = 50,
    ) -> list[MigrationJob]:
        out = list(self.jobs.values())
        if status is not None:
            out = [j for j in out if j.status == status]
        return out[:limit]

    def query_progress(self, job_id: UUID) -> list[MigrationProgress]:
        return [p for (jid, _), p in self.progress.items() if jid == job_id]

    def get_progress(
        self, job_id: UUID, curator_id: UUID,
    ) -> MigrationProgress | None:
        return self.progress.get((job_id, curator_id))

    def count_progress_by_status(self, job_id: UUID) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.query_progress(job_id):
            counts[p.status] = counts.get(p.status, 0) + 1
        return counts

    # ---- write API ----------------------------------------------------------

    def insert_job(self, job: MigrationJob) -> None:
        self.jobs[job.job_id] = job

    def seed_progress_rows(
        self, job_id: UUID, rows: list[MigrationProgress],
    ) -> None:
        for row in rows:
            self.progress[(job_id, row.curator_id)] = row
            if row.status == "pending":
                self._pending.setdefault(job_id, []).append(row.curator_id)

    def update_job_status(self, job_id: UUID, status: str) -> None:
        if job_id in self.jobs:
            self.jobs[job_id].status = status
        self.status_changes.append((job_id, status))

    def reset_in_progress_to_pending(self, job_id: UUID) -> None:
        for (jid, cid), p in self.progress.items():
            if jid == job_id and p.status == "in_progress":
                p.status = "pending"
                self._pending.setdefault(job_id, []).append(cid)

    def next_pending_progress(
        self, job_id: UUID,
    ) -> MigrationProgress | None:
        queue = self._pending.get(job_id, [])
        while queue:
            cid = queue.pop(0)
            row = self.progress.get((job_id, cid))
            if row is None:
                continue
            row.status = "in_progress"
            return row
        return None

    def update_progress(
        self,
        job_id: UUID,
        curator_id: UUID,
        *,
        status: str | None = None,
        outcome: str | None = None,
        verified_xxhash: str | None = None,
        error: str | None = None,
    ) -> None:
        key = (job_id, curator_id)
        if key in self.progress:
            row = self.progress[key]
            if status is not None:
                row.status = status
            if outcome is not None:
                row.outcome = outcome
            if verified_xxhash is not None:
                row.verified_xxhash = verified_xxhash
            if error is not None:
                row.error = error
        self.updates_logged.append((job_id, curator_id, {
            "status": status, "outcome": outcome,
            "verified_xxhash": verified_xxhash, "error": error,
        }))

    def increment_job_counts(
        self,
        job_id: UUID,
        *,
        copied: int = 0,
        bytes_copied: int = 0,
        skipped: int = 0,
        failed: int = 0,
    ) -> None:
        if job_id in self.jobs:
            j = self.jobs[job_id]
            j.files_copied += copied
            j.files_skipped += skipped
            j.files_failed += failed
            j.bytes_copied += bytes_copied
        self.count_increments.append((job_id, {
            "copied": copied, "bytes_copied": bytes_copied,
            "skipped": skipped, "failed": failed,
        }))


# ===========================================================================
# Helpers
# ===========================================================================


def make_jobs_service(
    *,
    jobs: StubMigrationJobRepository | None = None,
    audit: StubAuditRepository | None = None,
    file_repo: StubFileRepository | None = None,
    pm: StubMigrationPluginManager | None = None,
) -> MigrationService:
    svc = _base_make_service(audit=audit, file_repo=file_repo)
    svc.migration_jobs = jobs if jobs is not None else StubMigrationJobRepository()
    if pm is not None:
        svc.pm = pm
    return svc


class _SyncFuture:
    """Future-like that runs the callable inline and stores the result."""

    def __init__(self, fn, args, kwargs):
        try:
            self._result = fn(*args, **kwargs)
            self._exc: Exception | None = None
        except Exception as e:
            self._result = None
            self._exc = e

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._result


class _SyncExecutor:
    """Drop-in synchronous replacement for ThreadPoolExecutor.

    Executes submitted callables inline. Used to make `run_job`
    deterministic for tests. Threading semantics (abort_event, etc.) are
    still exercised because the production code's submit/result/loop
    structure is unchanged.
    """

    def __init__(self, max_workers: int = 1):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def submit(self, fn, *args, **kwargs):
        return _SyncFuture(fn, args, kwargs)


@pytest.fixture
def sync_executor(monkeypatch):
    """Replace concurrent.futures.ThreadPoolExecutor inside the migration
    module with a synchronous shim. All run_job tests use this."""
    import curator.services.migration as m
    monkeypatch.setattr(m, "ThreadPoolExecutor", _SyncExecutor)
    return _SyncExecutor


def _make_plan_with_moves(
    moves: list[MigrationMove],
    *,
    src_source_id: str = "local",
    dst_source_id: str = "local",
    src_root: str = "/src",
    dst_root: str = "/dst",
) -> MigrationPlan:
    return MigrationPlan(
        src_source_id=src_source_id, src_root=src_root,
        dst_source_id=dst_source_id, dst_root=dst_root,
        moves=moves,
    )


def _move(
    *,
    src_path: str = "/src/a.txt",
    dst_path: str = "/dst/a.txt",
    safety: SafetyLevel = SafetyLevel.SAFE,
    size: int = 100,
    src_xxhash: str | None = None,
    curator_id: UUID | None = None,
) -> MigrationMove:
    return MigrationMove(
        curator_id=curator_id or uuid4(),
        src_path=src_path, dst_path=dst_path,
        safety_level=safety, size=size, src_xxhash=src_xxhash,
    )


# ===========================================================================
# _require_jobs_repo (lines 2794-2800)
# ===========================================================================


class TestRequireJobsRepo:
    def test_raises_when_jobs_repo_missing(self):
        svc = _base_make_service()
        svc.migration_jobs = None
        with pytest.raises(RuntimeError, match="requires the migration_jobs"):
            svc._require_jobs_repo("any_method")

    def test_does_not_raise_when_jobs_repo_present(self):
        svc = make_jobs_service()
        # Must not raise.
        svc._require_jobs_repo("any_method")


# ===========================================================================
# create_job (lines 2455-2552)
# ===========================================================================


class TestCreateJob:
    def test_create_job_with_safe_rows_seeds_pending(self):
        jobs = StubMigrationJobRepository()
        svc = make_jobs_service(jobs=jobs)
        m1 = _move(safety=SafetyLevel.SAFE)
        plan = _make_plan_with_moves([m1])

        job_id = svc.create_job(plan)
        assert job_id in jobs.jobs
        assert jobs.jobs[job_id].status == "queued"
        assert jobs.jobs[job_id].files_total == plan.total_count
        row = jobs.progress[(job_id, m1.curator_id)]
        assert row.status == "pending"
        assert row.outcome is None

    def test_create_job_with_caution_default_skips_not_safe(self):
        jobs = StubMigrationJobRepository()
        svc = make_jobs_service(jobs=jobs)
        m1 = _move(safety=SafetyLevel.CAUTION)
        plan = _make_plan_with_moves([m1])

        job_id = svc.create_job(plan, include_caution=False)
        row = jobs.progress[(job_id, m1.curator_id)]
        assert row.status == "skipped"
        assert row.outcome == MigrationOutcome.SKIPPED_NOT_SAFE.value
        # pre-skipped counted
        increments = [
            inc for jid, inc in jobs.count_increments if jid == job_id
        ]
        assert any(inc["skipped"] == 1 for inc in increments)

    def test_create_job_with_caution_and_include_seeds_pending(self):
        jobs = StubMigrationJobRepository()
        svc = make_jobs_service(jobs=jobs)
        m1 = _move(safety=SafetyLevel.CAUTION)
        plan = _make_plan_with_moves([m1])

        job_id = svc.create_job(plan, include_caution=True)
        row = jobs.progress[(job_id, m1.curator_id)]
        assert row.status == "pending"

    def test_create_job_refuse_always_skipped(self):
        jobs = StubMigrationJobRepository()
        svc = make_jobs_service(jobs=jobs)
        m1 = _move(safety=SafetyLevel.REFUSE)
        plan = _make_plan_with_moves([m1])

        # Even with include_caution=True, REFUSE stays skipped.
        job_id = svc.create_job(plan, include_caution=True)
        row = jobs.progress[(job_id, m1.curator_id)]
        assert row.status == "skipped"
        assert row.outcome == MigrationOutcome.SKIPPED_NOT_SAFE.value

    def test_create_job_db_path_guard_seeds_skipped(self):
        jobs = StubMigrationJobRepository()
        svc = make_jobs_service(jobs=jobs)
        guarded = Path("/db/curator.db")
        m1 = _move(src_path=str(guarded), safety=SafetyLevel.SAFE)
        plan = _make_plan_with_moves([m1])

        job_id = svc.create_job(plan, db_path_guard=guarded)
        row = jobs.progress[(job_id, m1.curator_id)]
        assert row.status == "skipped"
        assert row.outcome == MigrationOutcome.SKIPPED_DB_GUARD.value

    def test_create_job_options_passed_through(self):
        jobs = StubMigrationJobRepository()
        svc = make_jobs_service(jobs=jobs)
        plan = _make_plan_with_moves([])
        job_id = svc.create_job(
            plan, options={"workers": 8, "verify_hash": False},
        )
        assert jobs.jobs[job_id].options == {
            "workers": 8, "verify_hash": False,
        }

    def test_create_job_with_options_none_defaults_to_empty_dict(self):
        jobs = StubMigrationJobRepository()
        svc = make_jobs_service(jobs=jobs)
        plan = _make_plan_with_moves([])
        job_id = svc.create_job(plan, options=None)
        assert jobs.jobs[job_id].options == {}

    def test_create_job_no_jobs_repo_raises(self):
        svc = _base_make_service()
        svc.migration_jobs = None
        plan = _make_plan_with_moves([])
        with pytest.raises(RuntimeError, match="create_job"):
            svc.create_job(plan)


# ===========================================================================
# abort_job (lines 2725-2743)
# ===========================================================================


class TestAbortJob:
    def test_abort_running_job_sets_event(self):
        import threading
        svc = make_jobs_service()
        job_id = uuid4()
        event = threading.Event()
        with svc._abort_lock:
            svc._abort_events[job_id] = event

        svc.abort_job(job_id)
        assert event.is_set()

    def test_abort_unrunning_job_is_noop(self):
        svc = make_jobs_service()
        # No event registered → silent no-op.
        svc.abort_job(uuid4())

    def test_abort_no_jobs_repo_raises(self):
        svc = _base_make_service()
        svc.migration_jobs = None
        with pytest.raises(RuntimeError, match="abort_job"):
            svc.abort_job(uuid4())


# ===========================================================================
# list_jobs + get_job_status (lines 2745-2788)
# ===========================================================================


class TestListJobsAndStatus:
    def test_list_jobs_passes_through_to_repo(self):
        jobs = StubMigrationJobRepository()
        j1 = MigrationJob(
            src_source_id="local", src_root="/a",
            dst_source_id="local", dst_root="/b",
            status="completed",
        )
        jobs.insert_job(j1)
        svc = make_jobs_service(jobs=jobs)
        result = svc.list_jobs(status="completed", limit=10)
        assert result == [j1]

    def test_list_jobs_no_jobs_repo_raises(self):
        svc = _base_make_service()
        svc.migration_jobs = None
        with pytest.raises(RuntimeError, match="list_jobs"):
            svc.list_jobs()

    def test_get_job_status_returns_full_dict(self):
        jobs = StubMigrationJobRepository()
        job = MigrationJob(
            src_source_id="local", src_root="/a",
            dst_source_id="local", dst_root="/b",
            status="completed", options={"workers": 4},
            files_total=10, files_copied=8, files_skipped=1,
            files_failed=1, bytes_copied=1000,
        )
        jobs.insert_job(job)
        svc = make_jobs_service(jobs=jobs)
        status = svc.get_job_status(job.job_id)
        assert status["job_id"] == str(job.job_id)
        assert status["status"] == "completed"
        assert status["files_total"] == 10
        assert status["files_copied"] == 8
        assert status["bytes_copied"] == 1000
        assert "progress_histogram" in status

    def test_get_job_status_not_found_raises_value_error(self):
        svc = make_jobs_service()
        with pytest.raises(ValueError, match="not found"):
            svc.get_job_status(uuid4())

    def test_get_job_status_no_jobs_repo_raises(self):
        svc = _base_make_service()
        svc.migration_jobs = None
        with pytest.raises(RuntimeError, match="get_job_status"):
            svc.get_job_status(uuid4())


# ===========================================================================
# _build_report_from_persisted (lines 3302-3341)
# ===========================================================================


class TestBuildReportFromPersisted:
    def test_empty_progress_returns_empty_moves(self):
        jobs = StubMigrationJobRepository()
        job = MigrationJob(
            src_source_id="local", src_root="/a",
            dst_source_id="local", dst_root="/b",
        )
        jobs.insert_job(job)
        svc = make_jobs_service(jobs=jobs)
        report = svc._build_report_from_persisted(job)
        assert isinstance(report, MigrationReport)
        assert report.moves == []

    def test_progress_with_outcome_builds_move(self):
        jobs = StubMigrationJobRepository()
        job = MigrationJob(
            src_source_id="local", src_root="/a",
            dst_source_id="local", dst_root="/b",
        )
        jobs.insert_job(job)
        p = MigrationProgress(
            job_id=job.job_id, curator_id=uuid4(),
            src_path="/a/x.txt", dst_path="/b/x.txt",
            size=10, safety_level="safe",
            status="completed", outcome="moved",
            verified_xxhash="h", src_xxhash="s",
        )
        jobs.progress[(job.job_id, p.curator_id)] = p

        svc = make_jobs_service(jobs=jobs)
        report = svc._build_report_from_persisted(job)
        assert len(report.moves) == 1
        assert report.moves[0].outcome == MigrationOutcome.MOVED

    def test_progress_with_invalid_outcome_defaults_to_failed(self):
        # Lines 3322-3324: ValueError → outcome=FAILED.
        jobs = StubMigrationJobRepository()
        job = MigrationJob(
            src_source_id="local", src_root="/a",
            dst_source_id="local", dst_root="/b",
        )
        jobs.insert_job(job)
        p = MigrationProgress(
            job_id=job.job_id, curator_id=uuid4(),
            src_path="/a/x.txt", dst_path="/b/x.txt",
            size=10, safety_level="safe",
            status="failed", outcome="ZZZ_BOGUS_OUTCOME",
        )
        jobs.progress[(job.job_id, p.curator_id)] = p

        svc = make_jobs_service(jobs=jobs)
        report = svc._build_report_from_persisted(job)
        assert report.moves[0].outcome == MigrationOutcome.FAILED

    def test_progress_with_none_outcome_yields_none(self):
        # p.outcome=None → branch 3320 False → outcome stays None.
        jobs = StubMigrationJobRepository()
        job = MigrationJob(
            src_source_id="local", src_root="/a",
            dst_source_id="local", dst_root="/b",
        )
        jobs.insert_job(job)
        p = MigrationProgress(
            job_id=job.job_id, curator_id=uuid4(),
            src_path="/a/x.txt", dst_path="/b/x.txt",
            size=10, safety_level="safe",
            status="pending", outcome=None,
        )
        jobs.progress[(job.job_id, p.curator_id)] = p

        svc = make_jobs_service(jobs=jobs)
        report = svc._build_report_from_persisted(job)
        assert report.moves[0].outcome is None


# ===========================================================================
# _execute_one_persistent (dispatcher, lines 2907-2952)
# ===========================================================================


class TestExecuteOnePersistentDispatch:
    def test_dst_source_id_none_defaults_to_src(self, monkeypatch):
        # Lines 2935-2936: dst_source_id=None → defaults to src_source_id
        # → cross_source=False → dispatches to same-source.
        svc = make_jobs_service()
        progress = make_progress()
        called = {"same": False, "cross": False}

        def fake_same(progress, **kw):
            called["same"] = True
            return (MigrationOutcome.MOVED, "h")

        def fake_cross(progress, **kw):
            called["cross"] = True
            return (MigrationOutcome.MOVED, "h")
        monkeypatch.setattr(svc, "_execute_one_persistent_same_source", fake_same)
        monkeypatch.setattr(svc, "_execute_one_persistent_cross_source", fake_cross)

        svc._execute_one_persistent(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id=None,
        )
        assert called["same"] is True
        assert called["cross"] is False

    def test_cross_source_dispatches_to_cross_handler(self, monkeypatch):
        svc = make_jobs_service()
        progress = make_progress()
        called = {"same": False, "cross": False}
        monkeypatch.setattr(
            svc, "_execute_one_persistent_same_source",
            lambda *a, **kw: called.__setitem__("same", True) or (MigrationOutcome.MOVED, "h"),
        )
        monkeypatch.setattr(
            svc, "_execute_one_persistent_cross_source",
            lambda *a, **kw: called.__setitem__("cross", True) or (MigrationOutcome.MOVED, "h"),
        )

        svc._execute_one_persistent(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert called["same"] is False
        assert called["cross"] is True


# ===========================================================================
# _execute_one_persistent_same_source (lines 2954-3108)
# ===========================================================================


class TestExecuteOnePersistentSameSource:
    def test_happy_path_with_verify(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"hello bytes")
        dst = tmp_path / "subdir" / "dst.txt"
        entity = FileEntity(
            source_id="local", source_path=str(src),
            size=src.stat().st_size, mtime=NOW, xxhash3_128=None,
        )
        file_repo = StubFileRepository(files=[entity])
        audit = StubAuditRepository()
        svc = make_jobs_service(audit=audit, file_repo=file_repo)
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path=str(src), dst_path=str(dst),
            src_xxhash=None,  # compute real hash from disk
        )

        outcome, vhash = svc._execute_one_persistent_same_source(
            progress, verify_hash=True, source_id="local",
        )
        assert outcome == MigrationOutcome.MOVED
        assert vhash is not None
        assert dst.exists()
        assert not src.exists()  # trashed

    def test_collision_skip_mode(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.txt"
        dst.write_bytes(b"existing")
        svc = make_jobs_service()
        svc.set_on_conflict_mode("skip")
        progress = make_progress(src_path=str(src), dst_path=str(dst))

        outcome, vhash = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, source_id="local",
        )
        assert outcome == MigrationOutcome.SKIPPED_COLLISION

    def test_collision_fail_mode_raises(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.txt"
        dst.write_bytes(b"existing")
        svc = make_jobs_service()
        svc.set_on_conflict_mode("fail")
        progress = make_progress(src_path=str(src), dst_path=str(dst))

        with pytest.raises(MigrationConflictError):
            svc._execute_one_persistent_same_source(
                progress, verify_hash=False, source_id="local",
            )

    def test_collision_overwrite_with_backup_mode(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"new data")
        dst = tmp_path / "dst.txt"
        dst.write_bytes(b"existing")
        entity = FileEntity(
            source_id="local", source_path=str(src),
            size=src.stat().st_size, mtime=NOW, xxhash3_128=None,
        )
        file_repo = StubFileRepository(files=[entity])
        svc = make_jobs_service(file_repo=file_repo)
        svc.set_on_conflict_mode("overwrite-with-backup")
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path=str(src), dst_path=str(dst),
        )

        outcome, vhash = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, source_id="local",
        )
        assert outcome == MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP

    def test_collision_rename_with_suffix_mode(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"new data")
        dst = tmp_path / "dst.txt"
        dst.write_bytes(b"existing")
        entity = FileEntity(
            source_id="local", source_path=str(src),
            size=src.stat().st_size, mtime=NOW, xxhash3_128=None,
        )
        file_repo = StubFileRepository(files=[entity])
        svc = make_jobs_service(file_repo=file_repo)
        svc.set_on_conflict_mode("rename-with-suffix")
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path=str(src), dst_path=str(dst),
        )

        outcome, vhash = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, source_id="local",
        )
        assert outcome == MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX
        # dst_path was updated to the suffix variant
        assert "curator-1" in progress.dst_path

    def test_hash_mismatch_cleanups_dst(self, tmp_path, monkeypatch):
        src = tmp_path / "src.txt"
        src.write_bytes(b"good data")
        dst = tmp_path / "dst.txt"
        entity = FileEntity(
            source_id="local", source_path=str(src),
            size=src.stat().st_size, mtime=NOW, xxhash3_128="src_hash_value",
        )
        file_repo = StubFileRepository(files=[entity])
        svc = make_jobs_service(file_repo=file_repo)
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path=str(src), dst_path=str(dst),
            src_xxhash="src_hash_value",  # cached
        )

        # Force xxhash3_128 of dst to differ
        import curator.services.migration as m
        monkeypatch.setattr(m, "_xxhash3_128_of_file", lambda p: "different_hash")

        outcome, vhash = svc._execute_one_persistent_same_source(
            progress, verify_hash=True, source_id="local",
        )
        assert outcome == MigrationOutcome.HASH_MISMATCH
        assert not dst.exists()  # cleaned up
        assert src.exists()  # untouched

    def test_hash_mismatch_unlink_oserror_swallowed(self, tmp_path, monkeypatch):
        src = tmp_path / "src.txt"
        src.write_bytes(b"good data")
        dst = tmp_path / "dst.txt"
        svc = make_jobs_service()
        progress = make_progress(
            src_path=str(src), dst_path=str(dst),
            src_xxhash="src_hash_value",
        )

        import curator.services.migration as m
        monkeypatch.setattr(m, "_xxhash3_128_of_file", lambda p: "different_hash")
        orig_unlink = Path.unlink

        def boom_unlink(self, *args, **kwargs):
            if str(self) == str(dst):
                raise OSError("blocked")
            return orig_unlink(self, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", boom_unlink)

        # Should not raise.
        outcome, vhash = svc._execute_one_persistent_same_source(
            progress, verify_hash=True, source_id="local",
        )
        assert outcome == MigrationOutcome.HASH_MISMATCH

    def test_keep_source_uses_copied_outcome_with_audit(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"hello")
        dst = tmp_path / "dst.txt"
        audit = StubAuditRepository()
        # Make audit.insert work for once — track via dict.
        audit_inserted: list[Any] = []

        def fake_insert(entry):
            audit_inserted.append(entry)
        audit.insert = fake_insert
        svc = make_jobs_service(audit=audit)
        progress = make_progress(
            src_path=str(src), dst_path=str(dst),
        )

        outcome, vhash = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, keep_source=True, source_id="local",
        )
        assert outcome == MigrationOutcome.COPIED
        assert dst.exists()
        assert src.exists()  # untouched
        assert len(audit_inserted) == 1
        assert audit_inserted[0].action == "migration.copy"

    def test_keep_source_audit_none_no_emission(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"hello")
        dst = tmp_path / "dst.txt"
        svc = make_jobs_service(audit=None)
        progress = make_progress(
            src_path=str(src), dst_path=str(dst),
        )

        outcome, _ = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, keep_source=True, source_id="local",
        )
        assert outcome == MigrationOutcome.COPIED

    def test_keep_source_audit_insert_exception_swallowed(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"hello")
        dst = tmp_path / "dst.txt"
        audit = StubAuditRepository()

        def boom(entry):
            raise RuntimeError("audit boom")
        audit.insert = boom
        svc = make_jobs_service(audit=audit)
        progress = make_progress(
            src_path=str(src), dst_path=str(dst),
        )

        # Should not raise.
        outcome, _ = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, keep_source=True, source_id="local",
        )
        assert outcome == MigrationOutcome.COPIED

    def test_keep_source_source_id_none_skips_id_keys(self, tmp_path):
        # Lines 3033-3038 False branch: source_id=None → skip src/dst keys.
        src = tmp_path / "src.txt"
        src.write_bytes(b"hello")
        dst = tmp_path / "dst.txt"
        audit = StubAuditRepository()
        inserted: list[Any] = []
        audit.insert = lambda entry: inserted.append(entry)
        svc = make_jobs_service(audit=audit)
        progress = make_progress(
            src_path=str(src), dst_path=str(dst),
        )

        outcome, _ = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, keep_source=True, source_id=None,
        )
        assert outcome == MigrationOutcome.COPIED
        assert "src_source_id" not in inserted[0].details

    def test_fileentity_vanished_raises(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.txt"
        # Empty file repo → files.get returns None.
        file_repo = StubFileRepository(files=[])
        svc = make_jobs_service(file_repo=file_repo)
        progress = make_progress(src_path=str(src), dst_path=str(dst))

        with pytest.raises(RuntimeError, match="vanished during migration"):
            svc._execute_one_persistent_same_source(
                progress, verify_hash=False, source_id="local",
            )

    def test_trash_failure_is_swallowed(self, tmp_path, monkeypatch):
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.txt"
        entity = FileEntity(
            source_id="local", source_path=str(src),
            size=src.stat().st_size, mtime=NOW, xxhash3_128=None,
        )
        file_repo = StubFileRepository(files=[entity])
        svc = make_jobs_service(file_repo=file_repo)
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path=str(src), dst_path=str(dst),
        )

        from curator._vendored import send2trash as s2t
        monkeypatch.setattr(
            s2t, "send2trash",
            lambda p: (_ for _ in ()).throw(RuntimeError("trash full")),
        )

        outcome, _ = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, source_id="local",
        )
        # Move still succeeds despite trash failure.
        assert outcome == MigrationOutcome.MOVED

    def test_main_audit_none_skips_emission(self, tmp_path):
        # Lines 3075 False branch: audit=None → skip audit.insert block.
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.txt"
        entity = FileEntity(
            source_id="local", source_path=str(src),
            size=src.stat().st_size, mtime=NOW, xxhash3_128=None,
        )
        file_repo = StubFileRepository(files=[entity])
        svc = make_jobs_service(audit=None, file_repo=file_repo)
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path=str(src), dst_path=str(dst),
        )

        outcome, _ = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, source_id="local",
        )
        assert outcome == MigrationOutcome.MOVED

    def test_main_audit_insert_exception_swallowed(self, tmp_path):
        # Lines 3097-3101: audit.insert raises → caught.
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.txt"
        entity = FileEntity(
            source_id="local", source_path=str(src),
            size=src.stat().st_size, mtime=NOW, xxhash3_128=None,
        )
        file_repo = StubFileRepository(files=[entity])
        audit = StubAuditRepository()

        def boom(entry):
            raise RuntimeError("audit boom")
        audit.insert = boom
        svc = make_jobs_service(audit=audit, file_repo=file_repo)
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path=str(src), dst_path=str(dst),
        )

        outcome, _ = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, source_id="local",
        )
        # Move still succeeds despite audit failure.
        assert outcome == MigrationOutcome.MOVED

    def test_main_source_id_none_skips_id_keys(self, tmp_path):
        # Lines 3084-3088 False branch: source_id=None → skip src/dst keys.
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.txt"
        entity = FileEntity(
            source_id="local", source_path=str(src),
            size=src.stat().st_size, mtime=NOW, xxhash3_128=None,
        )
        file_repo = StubFileRepository(files=[entity])
        audit = StubAuditRepository()
        inserted: list[Any] = []
        audit.insert = lambda entry: inserted.append(entry)
        svc = make_jobs_service(audit=audit, file_repo=file_repo)
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path=str(src), dst_path=str(dst),
        )

        outcome, _ = svc._execute_one_persistent_same_source(
            progress, verify_hash=False, source_id=None,
        )
        assert outcome == MigrationOutcome.MOVED
        assert "src_source_id" not in inserted[0].details


# ===========================================================================
# _execute_one_persistent_cross_source (lines 3110-3300)
# ===========================================================================


class TestExecuteOnePersistentCrossSource:
    """Tests use a monkeypatched `_cross_source_transfer` to return canned
    outcomes — the actual transfer body is already covered in v1.7.93a."""

    def _setup(self, monkeypatch, transfer_result: tuple, *,
                audit: StubAuditRepository | None = None,
                file_repo: StubFileRepository | None = None):
        pm = StubMigrationPluginManager()
        # delete hook for src trash
        pm.set_hook("curator_source_delete", lambda **kw: [None])
        svc = make_jobs_service(audit=audit, file_repo=file_repo, pm=pm)

        def fake_transfer(**kwargs):
            return transfer_result
        monkeypatch.setattr(svc, "_cross_source_transfer", fake_transfer)
        return svc, pm

    def test_happy_path(self, monkeypatch):
        entity = FileEntity(
            source_id="local", source_path="/src/x.txt",
            size=100, mtime=NOW,
        )
        file_repo = StubFileRepository(files=[entity])
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.MOVED, "/dst/x.txt", "verified_hash"),
            file_repo=file_repo,
        )
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path="/src/x.txt", dst_path="/dst/x.txt",
        )

        outcome, vhash = svc._execute_one_persistent_cross_source(
            progress, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.MOVED
        assert vhash == "verified_hash"

    def test_hash_mismatch_returns_early(self, monkeypatch):
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.HASH_MISMATCH, "/dst/x.txt", "bad"),
        )
        progress = make_progress()

        outcome, vhash = svc._execute_one_persistent_cross_source(
            progress, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.HASH_MISMATCH

    def test_skipped_collision_skip_mode(self, monkeypatch):
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
        )
        svc.set_on_conflict_mode("skip")
        progress = make_progress()

        outcome, vhash = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.SKIPPED_COLLISION

    def test_skipped_collision_fail_mode_raises_with_audit(self, monkeypatch):
        audit = StubAuditRepository()
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
            audit=audit,
        )
        svc.set_on_conflict_mode("fail")
        progress = make_progress()

        with pytest.raises(MigrationConflictError):
            svc._execute_one_persistent_cross_source(
                progress, verify_hash=False,
                src_source_id="local", dst_source_id="gdrive",
            )
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "fail" in modes

    def test_skipped_collision_fail_mode_no_audit_still_raises(self, monkeypatch):
        # Lines 3158-3174: audit=None → skip audit.log block, still raise.
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
            audit=None,
        )
        svc.set_on_conflict_mode("fail")
        progress = make_progress()

        with pytest.raises(MigrationConflictError):
            svc._execute_one_persistent_cross_source(
                progress, verify_hash=False,
                src_source_id="local", dst_source_id="gdrive",
            )

    def test_skipped_collision_fail_mode_audit_log_raises_swallowed(self, monkeypatch):
        # Lines 3175-3179: audit.log raises → caught.
        audit = StubAuditRepository()

        def boom(**kw):
            raise RuntimeError("audit boom")
        audit.log = boom
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
            audit=audit,
        )
        svc.set_on_conflict_mode("fail")
        progress = make_progress()

        with pytest.raises(MigrationConflictError):
            svc._execute_one_persistent_cross_source(
                progress, verify_hash=False,
                src_source_id="local", dst_source_id="gdrive",
            )

    def test_skipped_collision_overwrite_with_backup_success(self, monkeypatch):
        entity = FileEntity(
            source_id="local", source_path="/src/x.txt", size=100, mtime=NOW,
        )
        file_repo = StubFileRepository(files=[entity])
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
            file_repo=file_repo,
        )
        svc.set_on_conflict_mode("overwrite-with-backup")
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path="/src/x.txt", dst_path="/dst/x.txt",
        )

        monkeypatch.setattr(
            svc, "_cross_source_overwrite_with_backup_for_progress",
            lambda *a, **kw: (MigrationOutcome.MOVED, "/dst/x.txt", "h"),
        )

        outcome, vhash = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP

    def test_skipped_collision_overwrite_degrade_returns_skipped(self, monkeypatch):
        # retry_result is None → return SKIPPED_COLLISION.
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
        )
        svc.set_on_conflict_mode("overwrite-with-backup")
        progress = make_progress()
        monkeypatch.setattr(
            svc, "_cross_source_overwrite_with_backup_for_progress",
            lambda *a, **kw: None,
        )

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.SKIPPED_COLLISION

    def test_skipped_collision_overwrite_retry_hash_mismatch(self, monkeypatch):
        # retry_outcome2 != MOVED → return retry tuple.
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
        )
        svc.set_on_conflict_mode("overwrite-with-backup")
        progress = make_progress()
        monkeypatch.setattr(
            svc, "_cross_source_overwrite_with_backup_for_progress",
            lambda *a, **kw: (MigrationOutcome.HASH_MISMATCH, "/dst/x.txt", "bad"),
        )

        outcome, vhash = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.HASH_MISMATCH

    def test_skipped_collision_rename_with_suffix_success(self, monkeypatch):
        entity = FileEntity(
            source_id="local", source_path="/src/x.txt", size=100, mtime=NOW,
        )
        file_repo = StubFileRepository(files=[entity])
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
            file_repo=file_repo,
        )
        svc.set_on_conflict_mode("rename-with-suffix")
        progress = make_progress(
            curator_id=entity.curator_id,
            src_path="/src/x.txt", dst_path="/dst/x.txt",
        )
        monkeypatch.setattr(
            svc, "_cross_source_rename_with_suffix_for_progress",
            lambda *a, **kw: (MigrationOutcome.MOVED, "/dst/x.curator-1.txt", "h"),
        )

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX
        # progress.dst_path mutated to the suffix variant
        assert progress.dst_path == "/dst/x.curator-1.txt"

    def test_skipped_collision_rename_degrade_returns_skipped(self, monkeypatch):
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
        )
        svc.set_on_conflict_mode("rename-with-suffix")
        progress = make_progress()
        monkeypatch.setattr(
            svc, "_cross_source_rename_with_suffix_for_progress",
            lambda *a, **kw: None,
        )

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.SKIPPED_COLLISION

    def test_skipped_collision_rename_retry_hash_mismatch(self, monkeypatch):
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
        )
        svc.set_on_conflict_mode("rename-with-suffix")
        progress = make_progress()
        monkeypatch.setattr(
            svc, "_cross_source_rename_with_suffix_for_progress",
            lambda *a, **kw: (MigrationOutcome.HASH_MISMATCH, "/dst/x.txt", "bad"),
        )

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.HASH_MISMATCH

    def test_skipped_collision_unknown_mode_defensive(self, monkeypatch):
        # Lines 3217-3219: defensive arm for unreachable-via-validation modes.
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/x.txt", None),
        )
        svc._on_conflict_mode = "BOGUS"  # bypass validation
        progress = make_progress()

        outcome, vhash = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.SKIPPED_COLLISION

    def test_keep_source_copied_with_audit(self, monkeypatch):
        audit = StubAuditRepository()
        inserted: list[Any] = []
        audit.insert = lambda entry: inserted.append(entry)
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.MOVED, "/dst/x.txt", "h"),
            audit=audit,
        )
        progress = make_progress()

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False, keep_source=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.COPIED
        assert inserted[0].action == "migration.copy"
        assert inserted[0].details["cross_source"] is True

    def test_keep_source_audit_none(self, monkeypatch):
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.MOVED, "/dst/x.txt", "h"),
            audit=None,
        )
        progress = make_progress()

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False, keep_source=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.COPIED

    def test_keep_source_audit_insert_raises_swallowed(self, monkeypatch):
        audit = StubAuditRepository()
        audit.insert = lambda entry: (_ for _ in ()).throw(RuntimeError("boom"))
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.MOVED, "/dst/x.txt", "h"),
            audit=audit,
        )
        progress = make_progress()

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False, keep_source=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.COPIED

    def test_fileentity_vanished_raises(self, monkeypatch):
        # Lines 3251-3255: files.get returns None → RuntimeError vanished.
        file_repo = StubFileRepository(files=[])
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.MOVED, "/dst/x.txt", "h"),
            file_repo=file_repo,
        )
        progress = make_progress()

        with pytest.raises(RuntimeError, match="vanished during migration"):
            svc._execute_one_persistent_cross_source(
                progress, verify_hash=False,
                src_source_id="local", dst_source_id="gdrive",
            )

    def test_trash_hook_failure_swallowed(self, monkeypatch):
        # Lines 3268-3272: trash hook raises → caller's except catches.
        # Per Lesson #91: _hook_first_result swallows plugin-raised
        # exceptions internally, so we monkeypatch _hook_first_result
        # itself to propagate — that's the only path to this caller's
        # except clause.
        entity = FileEntity(
            source_id="local", source_path="/src/x.txt", size=100, mtime=NOW,
        )
        file_repo = StubFileRepository(files=[entity])
        svc = make_jobs_service(file_repo=file_repo)
        monkeypatch.setattr(
            svc, "_cross_source_transfer",
            lambda **kw: (MigrationOutcome.MOVED, "/dst/x.txt", "h"),
        )

        def boom_helper(hook_name, **kw):
            if hook_name == "curator_source_delete":
                raise RuntimeError("trash hook propagated")
            return None
        monkeypatch.setattr(svc, "_hook_first_result", boom_helper)
        progress = make_progress(curator_id=entity.curator_id)

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.MOVED

    def test_main_audit_none(self, monkeypatch):
        # Lines 3275-3298: audit=None → skip insert block.
        entity = FileEntity(
            source_id="local", source_path="/src/x.txt", size=100, mtime=NOW,
        )
        file_repo = StubFileRepository(files=[entity])
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.MOVED, "/dst/x.txt", "h"),
            audit=None, file_repo=file_repo,
        )
        progress = make_progress(curator_id=entity.curator_id)

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.MOVED

    def test_main_audit_insert_raises_swallowed(self, monkeypatch):
        # Lines 3294-3298: audit.insert raises → swallow with warning.
        entity = FileEntity(
            source_id="local", source_path="/src/x.txt", size=100, mtime=NOW,
        )
        file_repo = StubFileRepository(files=[entity])
        audit = StubAuditRepository()
        audit.insert = lambda entry: (_ for _ in ()).throw(RuntimeError("boom"))
        svc, _ = self._setup(
            monkeypatch,
            (MigrationOutcome.MOVED, "/dst/x.txt", "h"),
            audit=audit, file_repo=file_repo,
        )
        progress = make_progress(curator_id=entity.curator_id)

        outcome, _ = svc._execute_one_persistent_cross_source(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert outcome == MigrationOutcome.MOVED


# ===========================================================================
# _worker_loop (lines 2802-2905)
# ===========================================================================


class TestWorkerLoop:
    def _setup(self, monkeypatch, *, outcome_seq: list[tuple], raises=None):
        """Set up service + jobs repo with one or more pending rows,
        monkeypatch _execute_one_persistent to yield canned results."""
        jobs = StubMigrationJobRepository()
        job = MigrationJob(
            src_source_id="local", src_root="/a",
            dst_source_id="gdrive", dst_root="/b",
            status="running",
        )
        jobs.insert_job(job)
        # Seed one pending row per outcome
        rows = []
        for i, _ in enumerate(outcome_seq):
            cid = uuid4()
            rows.append(MigrationProgress(
                job_id=job.job_id, curator_id=cid,
                src_path=f"/src/{i}", dst_path=f"/dst/{i}",
                size=10, safety_level="safe", status="pending",
            ))
        jobs.seed_progress_rows(job.job_id, rows)

        svc = make_jobs_service(jobs=jobs)
        iterator = iter(outcome_seq)

        def fake_execute(progress, **kw):
            try:
                item = next(iterator)
            except StopIteration:
                return (MigrationOutcome.MOVED, "h")
            if isinstance(item, Exception):
                raise item
            return item
        monkeypatch.setattr(svc, "_execute_one_persistent", fake_execute)
        return svc, jobs, job

    def test_empty_queue_exits_immediately(self, monkeypatch):
        import threading
        svc, jobs, job = self._setup(monkeypatch, outcome_seq=[])
        abort_event = threading.Event()
        # No pending rows → next_pending_progress returns None → exit.
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event, on_progress=None,
        )

    def test_abort_set_initially_exits(self, monkeypatch):
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[(MigrationOutcome.MOVED, "h")],
        )
        abort_event = threading.Event()
        abort_event.set()  # already aborted
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event, on_progress=None,
        )
        # Nothing got dispatched
        assert len(jobs.updates_logged) == 0

    def test_moved_outcome_updates_progress_and_counts(self, monkeypatch):
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[(MigrationOutcome.MOVED, "verified_h")],
        )
        abort_event = threading.Event()
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event, on_progress=None,
        )
        # update_progress called with status=completed, outcome=moved
        assert any(
            upd[2]["status"] == "completed" and upd[2]["outcome"] == "moved"
            for upd in jobs.updates_logged
        )
        # increment_job_counts called with copied=1
        assert any(
            inc["copied"] == 1 for _, inc in jobs.count_increments
        )

    def test_copied_outcome_treated_as_success(self, monkeypatch):
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[(MigrationOutcome.COPIED, "h")],
        )
        abort_event = threading.Event()
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=True,
            abort_event=abort_event, on_progress=None,
        )
        assert any(
            upd[2]["outcome"] == "copied" for upd in jobs.updates_logged
        )

    def test_hash_mismatch_outcome_records_failed(self, monkeypatch):
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[(MigrationOutcome.HASH_MISMATCH, "bad_h")],
        )
        abort_event = threading.Event()
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=True, keep_source=False,
            abort_event=abort_event, on_progress=None,
        )
        assert any(
            upd[2]["status"] == "failed" and upd[2]["outcome"] == "hash_mismatch"
            for upd in jobs.updates_logged
        )
        assert any(inc["failed"] == 1 for _, inc in jobs.count_increments)

    def test_skipped_collision_outcome_records_skipped(self, monkeypatch):
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[(MigrationOutcome.SKIPPED_COLLISION, None)],
        )
        abort_event = threading.Event()
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event, on_progress=None,
        )
        assert any(
            upd[2]["status"] == "skipped" for upd in jobs.updates_logged
        )

    def test_defensive_unknown_outcome_treated_as_failed(self, monkeypatch):
        # Lines 2865-2871: outcome is something other than the 4 success
        # variants / hash mismatch / skipped collision → failed.
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[(MigrationOutcome.FAILED, None)],
        )
        abort_event = threading.Event()
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event, on_progress=None,
        )
        assert any(
            upd[2]["status"] == "failed" for upd in jobs.updates_logged
        )

    def test_migration_conflict_error_records_failed_due_to_conflict(
        self, monkeypatch,
    ):
        # Lines 2873-2884: MigrationConflictError → FAILED_DUE_TO_CONFLICT.
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[
                MigrationConflictError("/dst/x.txt", src_path="/src/x.txt"),
            ],
        )
        abort_event = threading.Event()
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event, on_progress=None,
        )
        assert any(
            upd[2]["outcome"] == MigrationOutcome.FAILED_DUE_TO_CONFLICT.value
            for upd in jobs.updates_logged
        )

    def test_other_exception_records_failed_with_message(self, monkeypatch):
        # Lines 2885-2892: any other exception → FAILED with type+msg.
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[RuntimeError("worker boom")],
        )
        abort_event = threading.Event()
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event, on_progress=None,
        )
        failed = [u for u in jobs.updates_logged if u[2]["status"] == "failed"]
        assert failed
        assert "RuntimeError" in (failed[0][2]["error"] or "")
        assert "worker boom" in (failed[0][2]["error"] or "")

    def test_on_progress_callback_called(self, monkeypatch):
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[(MigrationOutcome.MOVED, "h")],
        )
        calls: list[MigrationProgress] = []

        def cb(p):
            calls.append(p)

        abort_event = threading.Event()
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event, on_progress=cb,
        )
        assert len(calls) == 1

    def test_on_progress_exception_swallowed(self, monkeypatch):
        # Lines 2902-2905: callback raises → caught with warning, worker
        # does NOT die.
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[(MigrationOutcome.MOVED, "h")],
        )

        def boom(p):
            raise RuntimeError("UI bug")

        abort_event = threading.Event()
        # Must not raise.
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event, on_progress=boom,
        )

    def test_on_progress_skipped_when_get_progress_returns_none(self, monkeypatch):
        # Branch 2900->2821: final is None → skip the callback invocation,
        # loop back. Force by removing the progress row mid-flight.
        import threading
        svc, jobs, job = self._setup(
            monkeypatch,
            outcome_seq=[(MigrationOutcome.MOVED, "h")],
        )
        called: list[Any] = []

        # Override get_progress to return None deliberately
        orig_get = jobs.get_progress
        jobs.get_progress = lambda *a, **kw: None

        abort_event = threading.Event()
        svc._worker_loop(
            job.job_id, "local", "gdrive",
            verify_hash=False, keep_source=False,
            abort_event=abort_event,
            on_progress=lambda p: called.append(p),
        )
        # Callback never fired because final was None
        assert called == []


# ===========================================================================
# run_job (lines 2554-2723)
# ===========================================================================


class TestRunJob:
    def _make_setup_with_job(
        self,
        monkeypatch,
        *,
        options: dict | None = None,
        rows_outcomes: list[tuple] | None = None,
    ):
        """Build a job with N pending rows and monkeypatch
        _execute_one_persistent to yield the requested outcomes."""
        jobs = StubMigrationJobRepository()
        job = MigrationJob(
            src_source_id="local", src_root="/a",
            dst_source_id="gdrive", dst_root="/b",
            status="queued", options=options or {},
        )
        jobs.insert_job(job)
        rows_outcomes = rows_outcomes or []
        rows = []
        for i, _ in enumerate(rows_outcomes):
            rows.append(MigrationProgress(
                job_id=job.job_id, curator_id=uuid4(),
                src_path=f"/src/{i}", dst_path=f"/dst/{i}",
                size=10, safety_level="safe", status="pending",
            ))
        jobs.seed_progress_rows(job.job_id, rows)

        svc = make_jobs_service(jobs=jobs)
        iterator = iter(rows_outcomes)

        def fake_execute(progress, **kw):
            try:
                item = next(iterator)
            except StopIteration:
                return (MigrationOutcome.MOVED, "h")
            if isinstance(item, Exception):
                raise item
            return item
        monkeypatch.setattr(svc, "_execute_one_persistent", fake_execute)
        return svc, jobs, job

    def test_no_jobs_repo_raises(self):
        svc = _base_make_service()
        svc.migration_jobs = None
        with pytest.raises(RuntimeError, match="run_job"):
            svc.run_job(uuid4())

    def test_job_not_found_raises_value_error(self):
        svc = make_jobs_service()
        with pytest.raises(ValueError, match="not found"):
            svc.run_job(uuid4())

    def test_completed_job_is_noop_returns_report(self, monkeypatch):
        jobs = StubMigrationJobRepository()
        job = MigrationJob(
            src_source_id="local", src_root="/a",
            dst_source_id="gdrive", dst_root="/b",
            status="completed",
        )
        jobs.insert_job(job)
        svc = make_jobs_service(jobs=jobs)

        report = svc.run_job(job.job_id)
        assert isinstance(report, MigrationReport)
        # No status changes recorded (returned early)
        assert (job.job_id, "running") not in jobs.status_changes

    def test_max_retries_explicit_kwarg_wins(
        self, monkeypatch, sync_executor,
    ):
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            options={"max_retries": 99},
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        svc.run_job(job.job_id, max_retries=7)
        assert svc._max_retries == 7

    def test_max_retries_from_persisted_options(
        self, monkeypatch, sync_executor,
    ):
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            options={"max_retries": 5},
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        svc.run_job(job.job_id)
        assert svc._max_retries == 5

    def test_max_retries_persisted_invalid_falls_back(
        self, monkeypatch, sync_executor,
    ):
        # Lines 2640-2641: int("garbage") → ValueError → leave unchanged.
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            options={"max_retries": "garbage"},
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        # Capture pre-call value
        before = svc._max_retries
        svc.run_job(job.job_id)
        # _max_retries should still equal `before` (no change)
        assert svc._max_retries == before

    def test_max_retries_persisted_none_unchanged(
        self, monkeypatch, sync_executor,
    ):
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            options={"max_retries": None},
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        before = svc._max_retries
        svc.run_job(job.job_id)
        assert svc._max_retries == before

    def test_max_retries_options_attribute_error_falls_back(
        self, monkeypatch, sync_executor,
    ):
        # Lines 2635-2636: job.options.get raises AttributeError (e.g.
        # options not a dict). Inject by setting options to a non-dict.
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            options={},
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        # Replace job.options with something that raises on .get().
        # Use __dict__ to bypass pydantic's validate_assignment.
        job.__dict__["options"] = SimpleNamespace()
        before = svc._max_retries
        svc.run_job(job.job_id)
        assert svc._max_retries == before

    def test_on_conflict_explicit_kwarg_wins(
        self, monkeypatch, sync_executor,
    ):
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            options={"on_conflict": "rename-with-suffix"},
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        svc.run_job(job.job_id, on_conflict="fail")
        assert svc._on_conflict_mode == "fail"

    def test_on_conflict_explicit_invalid_raises(
        self, monkeypatch, sync_executor,
    ):
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        with pytest.raises(ValueError):
            svc.run_job(job.job_id, on_conflict="totally-bogus")

    def test_on_conflict_persisted_applied(
        self, monkeypatch, sync_executor,
    ):
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            options={"on_conflict": "overwrite-with-backup"},
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        svc.run_job(job.job_id)
        assert svc._on_conflict_mode == "overwrite-with-backup"

    def test_on_conflict_persisted_invalid_falls_back_to_skip(
        self, monkeypatch, sync_executor,
    ):
        # Lines 2663-2671: persisted set_on_conflict_mode raises ValueError
        # → log warning + fall back to "skip".
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            options={"on_conflict": "bogus"},
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        svc.run_job(job.job_id)
        assert svc._on_conflict_mode == "skip"

    def test_on_conflict_options_attribute_error_unchanged(
        self, monkeypatch, sync_executor,
    ):
        # Lines 2658-2659: job.options.get raises AttributeError → unchanged.
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            options={},
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        # Use __dict__ to bypass pydantic's validate_assignment.
        job.__dict__["options"] = SimpleNamespace()
        before = svc._on_conflict_mode
        svc.run_job(job.job_id)
        assert svc._on_conflict_mode == before

    def test_workers_zero_clamps_to_one(
        self, monkeypatch, sync_executor,
    ):
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        report = svc.run_job(job.job_id, workers=0)
        assert isinstance(report, MigrationReport)

    def test_happy_path_status_completed(
        self, monkeypatch, sync_executor,
    ):
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )
        report = svc.run_job(job.job_id, workers=1)
        # Job status finalized to "completed"
        assert (job.job_id, "completed") in jobs.status_changes

    def test_partial_status_when_one_row_failed(
        self, monkeypatch, sync_executor,
    ):
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            rows_outcomes=[
                (MigrationOutcome.MOVED, "h"),
                (MigrationOutcome.FAILED, None),
            ],
        )
        # Force the second row to be classified as failed by the worker.
        # _worker_loop's defensive "any other outcome" path treats FAILED
        # as failed → histogram[failed] > 0 → status=partial.
        svc.run_job(job.job_id, workers=1)
        assert (job.job_id, "partial") in jobs.status_changes

    def test_cancelled_status_when_aborted(
        self, monkeypatch, sync_executor,
    ):
        # Pre-arrange: abort_job before the worker runs. We pre-set the
        # event in _abort_events; run_job re-installs it inside its
        # `with self._abort_lock:` block — so we need to set the event AFTER
        # run_job calls `abort_event = threading.Event()` but BEFORE workers
        # start. Easier: monkeypatch threading.Event so the constructor
        # returns an event that's already set.
        svc, jobs, job = self._make_setup_with_job(
            monkeypatch,
            rows_outcomes=[(MigrationOutcome.MOVED, "h")],
        )

        import threading
        original_event = threading.Event

        class PreSetEvent(original_event):
            def __init__(self):
                super().__init__()
                self.set()
        import curator.services.migration as m
        monkeypatch.setattr(m.threading, "Event", PreSetEvent)

        svc.run_job(job.job_id, workers=1)
        assert (job.job_id, "cancelled") in jobs.status_changes
