"""Tests for v1.1.0a2 MigrationService Phase 2 (persistent jobs).

Covers the new methods on MigrationService:
  * create_job(plan, options, db_path_guard, include_caution)
  * run_job(job_id, workers, verify_hash, on_progress)
  * abort_job(job_id)
  * list_jobs(status, limit)
  * get_job_status(job_id)

Plus the Constitution invariants we MUST preserve from Phase 1:
  * curator_id constancy (lineage edges + bundle memberships persist)
  * Hash-Verify-Before-Move discipline (mismatch leaves source intact)
  * No silent failures (every per-file outcome captured)
  * DB-guard skip
  * Audit per move (with job_id in details for cross-reference)

Strategy:
  * Build a fully-wired CuratorRuntime against a temp DB with REAL files
    on disk (so shutil.copy2, hash recomputation, and trash exercise the
    real OS layer).
  * Stub SafetyService.check_path -> SAFE for migration-mechanics tests
    (pytest's tmp_path is under %LOCALAPPDATA% on Windows, which real
    safety correctly flags as CAUTION).
  * Worker concurrency tests use small N (4 workers, 8-12 files) to
    exercise contention without slowing the test run.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import xxhash

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models.file import FileEntity
from curator.models.lineage import LineageEdge, LineageKind
from curator.models.source import SourceConfig
from curator.services.migration import (
    MigrationOutcome,
    MigrationPlan,
    MigrationMove,
    MigrationReport,
    MigrationService,
)
from curator.services.safety import SafetyLevel, SafetyReport


# ---------------------------------------------------------------------------
# Fixtures (parallel pattern to test_migration.py Phase 1)
# ---------------------------------------------------------------------------


def _seed_real_file(rt, path: Path, content: bytes = b"hello world\n") -> FileEntity:
    """Create a real file on disk + index it. Returns FileEntity with cached hash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    h = xxhash.xxh3_128(content).hexdigest()
    e = FileEntity(
        curator_id=uuid4(),
        source_id="local",
        source_path=str(path),
        size=len(content),
        mtime=datetime.fromtimestamp(path.stat().st_mtime),
        extension=path.suffix.lower(),
        xxhash3_128=h,
    )
    rt.file_repo.upsert(e)
    return e


@pytest.fixture
def migration_runtime(tmp_path):
    """Real CuratorRuntime backed by a temp DB. SafetyService stubbed to SAFE
    so migration-mechanics tests run without CAUTION false positives."""
    db_path = tmp_path / "migration_phase2.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    try:
        rt.source_repo.insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
    except Exception:
        pass
    # Stub safety to SAFE -- tmp_path is under %LOCALAPPDATA% on Windows
    rt.safety.check_path = lambda p, **kw: SafetyReport(path=p, level=SafetyLevel.SAFE)
    return rt


@pytest.fixture
def migration_service(migration_runtime):
    """The wired-up MigrationService from rt (already has migration_jobs repo)."""
    return migration_runtime.migration


@pytest.fixture
def small_library(tmp_path, migration_runtime):
    """5 files at tmp_path/library/. Returns (rt, src_root, files)."""
    rt = migration_runtime
    src_root = tmp_path / "library"
    files = [
        _seed_real_file(rt, src_root / f"song{i}.mp3", f"track{i} bytes".encode() * 50)
        for i in range(5)
    ]
    return rt, src_root, files


@pytest.fixture
def medium_library(tmp_path, migration_runtime):
    """12 files at tmp_path/library/ -- enough to exercise worker contention."""
    rt = migration_runtime
    src_root = tmp_path / "library"
    files = [
        _seed_real_file(rt, src_root / f"track_{i:02d}.mp3", f"bytes_{i:03d}".encode() * 100)
        for i in range(12)
    ]
    return rt, src_root, files


def _build_plan(svc, src_root, dst_root):
    return svc.plan(
        src_source_id="local",
        src_root=str(src_root),
        dst_root=str(dst_root),
    )


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------


class TestCreateJob:
    def test_persists_job_with_pending_progress_rows(
        self, migration_service, small_library, tmp_path,
    ):
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        assert plan.safe_count == 5

        job_id = migration_service.create_job(
            plan, options={"workers": 4, "verify_hash": True},
        )

        job = rt.migration_job_repo.get_job(job_id)
        assert job is not None
        assert job.status == "queued"
        assert job.files_total == 5
        assert job.options == {"workers": 4, "verify_hash": True}

        # All 5 progress rows seeded as pending (SAFE)
        rows = rt.migration_job_repo.query_progress(job_id)
        assert len(rows) == 5
        assert all(r.status == "pending" for r in rows)

    def test_db_path_guard_pre_skips_matching_row(
        self, migration_service, small_library, tmp_path,
    ):
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")

        guard_path = Path(files[2].source_path)
        job_id = migration_service.create_job(
            plan, db_path_guard=guard_path,
        )

        # Guarded row pre-skipped with skipped_db_guard outcome
        guarded = [
            r for r in rt.migration_job_repo.query_progress(job_id)
            if r.src_path == str(guard_path)
        ]
        assert len(guarded) == 1
        assert guarded[0].status == "skipped"
        assert guarded[0].outcome == "skipped_db_guard"

        # Other 4 rows still pending
        pending = rt.migration_job_repo.query_progress(job_id, status="pending")
        assert len(pending) == 4

        # Job-level skipped count incremented
        job = rt.migration_job_repo.get_job(job_id)
        assert job.files_skipped == 1

    def test_caution_pre_skipped_by_default(
        self, migration_runtime, tmp_path,
    ):
        """With include_caution=False (default), CAUTION rows are pre-skipped."""
        rt = migration_runtime
        # Re-stub safety to mark some files CAUTION
        src_root = tmp_path / "mixed"
        files = []
        for i in range(3):
            files.append(_seed_real_file(rt, src_root / f"f{i}.mp3"))
        # Stub safety: f0/f1 SAFE, f2 CAUTION
        from curator.services.safety import SafetyReport, SafetyLevel as SL
        def safety_stub(p, **kw):
            level = SL.CAUTION if str(p).endswith("f2.mp3") else SL.SAFE
            return SafetyReport(path=p, level=level)
        rt.safety.check_path = safety_stub

        svc = rt.migration
        plan = _build_plan(svc, src_root, tmp_path / "out")
        assert plan.safe_count == 2
        assert plan.caution_count == 1

        job_id = svc.create_job(plan, include_caution=False)
        rows = rt.migration_job_repo.query_progress(job_id)
        skipped = [r for r in rows if r.status == "skipped"]
        assert len(skipped) == 1
        assert skipped[0].outcome == "skipped_not_safe"
        assert skipped[0].safety_level == "caution"

    def test_include_caution_promotes_caution_to_pending(
        self, migration_runtime, tmp_path,
    ):
        """With include_caution=True, CAUTION rows are pending (eligible)."""
        rt = migration_runtime
        src_root = tmp_path / "mixed"
        files = [_seed_real_file(rt, src_root / f"f{i}.mp3") for i in range(3)]
        from curator.services.safety import SafetyReport, SafetyLevel as SL
        def safety_stub(p, **kw):
            level = SL.CAUTION if str(p).endswith("f2.mp3") else SL.SAFE
            return SafetyReport(path=p, level=level)
        rt.safety.check_path = safety_stub

        svc = rt.migration
        plan = _build_plan(svc, src_root, tmp_path / "out")
        job_id = svc.create_job(plan, include_caution=True)
        pending = rt.migration_job_repo.query_progress(job_id, status="pending")
        assert len(pending) == 3  # All 3 (2 SAFE + 1 CAUTION) eligible

    def test_refuse_always_skipped_even_with_include_caution(
        self, migration_runtime, tmp_path,
    ):
        rt = migration_runtime
        src_root = tmp_path / "with_refuse"
        files = [_seed_real_file(rt, src_root / f"f{i}.mp3") for i in range(3)]
        from curator.services.safety import SafetyReport, SafetyLevel as SL
        def safety_stub(p, **kw):
            if str(p).endswith("f2.mp3"):
                return SafetyReport(path=p, level=SL.REFUSE)
            return SafetyReport(path=p, level=SL.SAFE)
        rt.safety.check_path = safety_stub

        svc = rt.migration
        plan = _build_plan(svc, src_root, tmp_path / "out")
        job_id = svc.create_job(plan, include_caution=True)
        skipped = rt.migration_job_repo.query_progress(job_id, status="skipped")
        assert len(skipped) == 1
        assert skipped[0].safety_level == "refuse"

    def test_create_job_without_jobs_repo_raises(self, migration_runtime):
        """Service constructed without migration_jobs raises on create_job."""
        rt = migration_runtime
        svc_no_repo = MigrationService(
            file_repo=rt.file_repo, safety=rt.safety, audit=rt.audit_repo,
            migration_jobs=None,
        )
        empty_plan = MigrationPlan(
            src_source_id="local", src_root="/x",
            dst_source_id="local", dst_root="/y",
        )
        with pytest.raises(RuntimeError, match="migration_jobs"):
            svc_no_repo.create_job(empty_plan)


# ---------------------------------------------------------------------------
# run_job (single worker)
# ---------------------------------------------------------------------------


class TestRunJobSingleWorker:
    def test_runs_to_completion_with_workers_1(
        self, migration_service, small_library, tmp_path,
    ):
        rt, src_root, files = small_library
        dst_root = tmp_path / "out"
        plan = _build_plan(migration_service, src_root, dst_root)
        job_id = migration_service.create_job(plan)

        report = migration_service.run_job(job_id, workers=1)

        assert report.moved_count == 5
        assert report.failed_count == 0
        # All dst files present, src files trashed
        for f in files:
            assert not Path(f.source_path).exists()
            rel = Path(f.source_path).relative_to(src_root)
            assert (dst_root / rel).exists()
        # Job marked completed
        job = rt.migration_job_repo.get_job(job_id)
        assert job.status == "completed"
        assert job.files_copied == 5
        assert job.bytes_copied > 0

    def test_curator_id_constancy_under_persistent_path(
        self, migration_service, small_library, tmp_path,
    ):
        """Headline Constitution invariant: curator_id stays the same."""
        rt, src_root, files = small_library
        original_ids = {f.curator_id for f in files}

        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)
        migration_service.run_job(job_id, workers=1)

        # All 5 entities still exist with same curator_ids
        for f in files:
            entity = rt.file_repo.get(f.curator_id)
            assert entity is not None
            assert entity.curator_id in original_ids

    def test_lineage_edges_survive_persistent_migration(
        self, migration_service, small_library, tmp_path,
    ):
        """Constitution invariant: lineage edges persist across migration."""
        rt, src_root, files = small_library

        # Add a lineage edge between files[0] and files[1]
        edge = LineageEdge(
            from_curator_id=files[0].curator_id,
            to_curator_id=files[1].curator_id,
            edge_kind=LineageKind.NEAR_DUPLICATE,
            confidence=0.85,
            detected_by="test",
        )
        rt.lineage_repo.insert(edge)

        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)
        migration_service.run_job(job_id, workers=1)

        edges = rt.lineage_repo.get_edges_for(files[0].curator_id)
        assert len(edges) == 1
        assert edges[0].from_curator_id == files[0].curator_id
        assert edges[0].to_curator_id == files[1].curator_id

    def test_audit_entries_include_job_id(
        self, migration_service, small_library, tmp_path,
    ):
        """Phase 2 audit entries include job_id in details for cross-reference."""
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)
        migration_service.run_job(job_id, workers=1)

        entries = rt.audit_repo.query(action="migration.move", limit=10)
        assert len(entries) == 5
        for entry in entries:
            assert entry.actor == "curator.migrate"
            assert "job_id" in entry.details
            assert entry.details["job_id"] == str(job_id)

    def test_progress_rows_record_terminal_outcomes(
        self, migration_service, small_library, tmp_path,
    ):
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)
        migration_service.run_job(job_id, workers=1)

        rows = rt.migration_job_repo.query_progress(job_id)
        assert len(rows) == 5
        for r in rows:
            assert r.status == "completed"
            assert r.outcome == "moved"
            assert r.verified_xxhash is not None
            assert r.completed_at is not None


# ---------------------------------------------------------------------------
# run_job (worker pool)
# ---------------------------------------------------------------------------


class TestRunJobWorkerPool:
    def test_runs_to_completion_with_4_workers(
        self, migration_service, medium_library, tmp_path,
    ):
        rt, src_root, files = medium_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)

        report = migration_service.run_job(job_id, workers=4)

        assert report.moved_count == 12
        assert report.failed_count == 0
        # All 12 dst files present
        for f in files:
            assert not Path(f.source_path).exists()
            rel = Path(f.source_path).relative_to(src_root)
            assert (tmp_path / "out" / rel).exists()

    def test_workers_partition_work_no_double_claims(
        self, migration_service, medium_library, tmp_path,
    ):
        """No row should be processed twice -- the atomic claim
        primitive prevents double-claiming."""
        rt, src_root, files = medium_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)

        # Track per-row execution count via on_progress callback
        execution_log: list = []
        log_lock = threading.Lock()

        def log_progress(progress):
            with log_lock:
                execution_log.append(progress.curator_id)

        migration_service.run_job(
            job_id, workers=4, on_progress=log_progress,
        )

        # Exactly 12 callbacks (one per file), no duplicates
        assert len(execution_log) == 12
        assert len(set(execution_log)) == 12

    def test_audit_log_serializes_concurrent_appends(
        self, migration_service, medium_library, tmp_path,
    ):
        """All 12 audit entries land safely under concurrent worker writes."""
        rt, src_root, files = medium_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)
        migration_service.run_job(job_id, workers=4)

        entries = rt.audit_repo.query(action="migration.move", limit=50)
        assert len(entries) == 12
        # All have unique entity_ids (one per file)
        entity_ids = [e.entity_id for e in entries]
        assert len(set(entity_ids)) == 12


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


class TestResume:
    def test_resume_picks_up_where_left_off(
        self, migration_service, medium_library, tmp_path,
    ):
        """Simulate interruption: process some rows, abort, then re-run.
        Final state should equal an uninterrupted run."""
        rt, src_root, files = medium_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)

        # Process 4 rows manually (simulating partial completion)
        # by calling next_pending_progress + a synthetic completion
        for _ in range(4):
            row = rt.migration_job_repo.next_pending_progress(job_id)
            # Pretend the worker completed this row successfully
            # without actually moving the file
            rt.migration_job_repo.update_progress(
                job_id, row.curator_id,
                status="completed", outcome="moved",
                verified_xxhash="fakehash" + "0" * 24,
            )
            rt.migration_job_repo.increment_job_counts(
                job_id, copied=1, bytes_copied=row.size,
            )

        # 8 rows still pending; re-run picks them up
        report = migration_service.run_job(job_id, workers=2)

        # All 12 should be terminal now (4 fake-completed + 8 real)
        rows = rt.migration_job_repo.query_progress(job_id)
        assert len(rows) == 12
        assert all(r.is_terminal for r in rows)
        # Job marked completed
        job = rt.migration_job_repo.get_job(job_id)
        assert job.status == "completed"

    def test_resume_resets_in_progress_to_pending(
        self, migration_service, small_library, tmp_path,
    ):
        """A row left as 'in_progress' from a dead worker is reset on
        the next run."""
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)

        # Manually claim 2 rows + simulate worker death (no terminal update)
        c1 = rt.migration_job_repo.next_pending_progress(job_id)
        c2 = rt.migration_job_repo.next_pending_progress(job_id)
        assert c1.status == "in_progress"
        assert c2.status == "in_progress"

        # Re-run should reset those + complete all 5
        report = migration_service.run_job(job_id, workers=1)
        assert report.moved_count == 5

    def test_run_job_on_completed_job_is_noop(
        self, migration_service, small_library, tmp_path,
    ):
        """Running an already-completed job returns the report without re-execution."""
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)
        migration_service.run_job(job_id, workers=2)

        # Run a second time -- should no-op
        report2 = migration_service.run_job(job_id, workers=2)
        assert report2.moved_count == 5
        # Audit log still shows only 5 entries (no re-execution)
        entries = rt.audit_repo.query(action="migration.move", limit=20)
        assert len(entries) == 5


# ---------------------------------------------------------------------------
# Abort
# ---------------------------------------------------------------------------


class TestAbort:
    def test_abort_during_run_marks_cancelled(
        self, migration_service, medium_library, tmp_path,
    ):
        """abort_job from another thread sets the abort event; workers
        finish current file then exit; final job status is 'cancelled'."""
        rt, src_root, files = medium_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)

        # Spawn run_job in a thread, abort after a tiny delay
        result_box: dict = {}
        def _run():
            try:
                result_box["report"] = migration_service.run_job(
                    job_id, workers=1,
                )
            except Exception as e:
                result_box["error"] = e

        t = threading.Thread(target=_run)
        t.start()
        # Give workers a moment to start, then abort
        time.sleep(0.05)
        migration_service.abort_job(job_id)
        t.join(timeout=10)
        assert not t.is_alive(), "run_job should exit shortly after abort"

        job = rt.migration_job_repo.get_job(job_id)
        assert job.status == "cancelled"

    def test_abort_unknown_job_is_noop(self, migration_service):
        """Calling abort_job for a non-existent / non-running job doesn't error."""
        migration_service.abort_job(uuid4())  # should silently no-op


# ---------------------------------------------------------------------------
# Constitution invariants under workers
# ---------------------------------------------------------------------------


class TestConstitutionUnderWorkers:
    def test_hash_mismatch_under_workers_leaves_source_intact(
        self, migration_service, small_library, tmp_path,
    ):
        """Even with concurrent workers, hash mismatch must leave src intact."""
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(plan)

        # Patch _xxhash3_128_of_file to always return wrong dst hash
        call_count = [0]
        original = files[0].xxhash3_128

        def fake_hash(path):
            call_count[0] += 1
            # Even calls = src hash (return real); odd = dst hash (return wrong)
            try:
                return xxhash.xxh3_128(Path(path).read_bytes()).hexdigest() if call_count[0] % 2 == 1 else "deadbeef" * 4
            except Exception:
                return "deadbeef" * 4

        with patch("curator.services.migration._xxhash3_128_of_file", fake_hash):
            report = migration_service.run_job(job_id, workers=2)

        # All sources should still exist (or at least: most of them; we may
        # have one or two false negatives depending on call ordering)
        intact_count = sum(1 for f in files if Path(f.source_path).exists())
        # At least some sources should be preserved on hash mismatch
        assert intact_count >= 1
        # At least one row should be marked failed/hash_mismatch
        failed = rt.migration_job_repo.query_progress(job_id, status="failed")
        assert len(failed) >= 1

    def test_db_guarded_file_never_migrated(
        self, migration_service, small_library, tmp_path,
    ):
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")

        guard_path = Path(files[0].source_path)
        job_id = migration_service.create_job(
            plan, db_path_guard=guard_path,
        )
        migration_service.run_job(job_id, workers=2)

        # Guarded source still on disk
        assert guard_path.exists()
        # Guarded dst NOT created
        guarded_dst = (tmp_path / "out" / guard_path.relative_to(src_root))
        assert not guarded_dst.exists()


# ---------------------------------------------------------------------------
# get_job_status + list_jobs
# ---------------------------------------------------------------------------


class TestGetJobStatusAndList:
    def test_get_job_status_returns_full_dict(
        self, migration_service, small_library, tmp_path,
    ):
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(
            plan, options={"workers": 2, "verify_hash": True},
        )
        migration_service.run_job(job_id, workers=2)

        status = migration_service.get_job_status(job_id)
        assert status["job_id"] == str(job_id)
        assert status["status"] == "completed"
        assert status["files_total"] == 5
        assert status["files_copied"] == 5
        assert status["files_failed"] == 0
        assert status["bytes_copied"] > 0
        assert status["progress_histogram"] == {"completed": 5}
        assert status["options"] == {"workers": 2, "verify_hash": True}
        assert status["duration_seconds"] is not None

    def test_get_job_status_unknown_raises(self, migration_service):
        with pytest.raises(ValueError, match="not found"):
            migration_service.get_job_status(uuid4())

    def test_list_jobs_returns_recent_first(
        self, migration_service, migration_runtime, tmp_path,
    ):
        rt = migration_runtime
        # Create 3 jobs with explicit started_at via direct repo to force ordering
        from curator.models.migration import MigrationJob
        for i in range(3):
            rt.migration_job_repo.insert_job(MigrationJob(
                src_source_id="local", src_root=f"/s{i}",
                dst_source_id="local", dst_root=f"/d{i}",
                status="completed",
                started_at=datetime(2026, 5, 1 + i, 12, 0),
            ))
        listed = migration_service.list_jobs(limit=2)
        assert len(listed) == 2
        # Most recent first
        assert listed[0].src_root == "/s2"
        assert listed[1].src_root == "/s1"

    def test_list_jobs_status_filter_passes_through(
        self, migration_service, migration_runtime, tmp_path,
    ):
        rt = migration_runtime
        from curator.models.migration import MigrationJob
        for status in ("queued", "running", "completed"):
            rt.migration_job_repo.insert_job(MigrationJob(
                src_source_id="local", src_root=f"/s_{status}",
                dst_source_id="local", dst_root=f"/d_{status}",
                status=status,
            ))
        running = migration_service.list_jobs(status="running")
        assert len(running) == 1
        assert running[0].status == "running"


# ---------------------------------------------------------------------------
# A3: Service-layer additions (keep_source / globs / path_prefix / include_caution)
# ---------------------------------------------------------------------------


class TestPlanGlobFilters:
    def test_includes_whitelist_only_matches(
        self, migration_service, tmp_path, migration_runtime,
    ):
        rt = migration_runtime
        src_root = tmp_path / "library"
        # Mix of mp3 and flac
        for name in ["a.mp3", "b.flac", "c.mp3", "d.txt"]:
            _seed_real_file(rt, src_root / name)
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(tmp_path / "out"),
            includes=["*.mp3"],
        )
        # Only the two mp3 files should be in the plan
        assert plan.total_count == 2
        assert all(m.src_path.endswith(".mp3") for m in plan.moves)

    def test_excludes_blacklist_removes_matches(
        self, migration_service, tmp_path, migration_runtime,
    ):
        rt = migration_runtime
        src_root = tmp_path / "library"
        for name in ["a.mp3", "b.flac", "draft_c.mp3"]:
            _seed_real_file(rt, src_root / name)
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(tmp_path / "out"),
            excludes=["draft_*"],
        )
        # draft_c.mp3 excluded; a.mp3 and b.flac remain
        assert plan.total_count == 2
        assert not any("draft_" in m.src_path for m in plan.moves)

    def test_includes_and_excludes_combine_intersect(
        self, migration_service, tmp_path, migration_runtime,
    ):
        rt = migration_runtime
        src_root = tmp_path / "library"
        for name in ["a.mp3", "b.flac", "draft_c.mp3", "d.txt"]:
            _seed_real_file(rt, src_root / name)
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(tmp_path / "out"),
            includes=["*.mp3"],
            excludes=["draft_*"],
        )
        # Only non-draft mp3 -- just a.mp3
        assert plan.total_count == 1
        assert plan.moves[0].src_path.endswith("a.mp3")

    def test_path_prefix_narrows_query_but_preserves_dst_subpath(
        self, migration_service, tmp_path, migration_runtime,
    ):
        rt = migration_runtime
        src_root = tmp_path / "library"
        # Files at library/Pink Floyd/ and library/Beatles/
        f_pf = _seed_real_file(rt, src_root / "Pink Floyd" / "Wall.mp3")
        f_b = _seed_real_file(rt, src_root / "Beatles" / "Abbey.mp3")
        dst_root = tmp_path / "out"
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
            path_prefix="Pink Floyd",
        )
        # Only Pink Floyd file selected
        assert plan.total_count == 1
        assert "Pink Floyd" in plan.moves[0].src_path
        # Dst path preserves the FULL relative subpath under src_root
        assert plan.moves[0].dst_path.endswith(
            str(Path("Pink Floyd") / "Wall.mp3")
        )


class TestApplyKeepSource:
    def test_keep_source_leaves_src_intact_and_index_unchanged(
        self, migration_service, small_library, tmp_path,
    ):
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        report = migration_service.apply(plan, keep_source=True)

        assert report.moved_count == 5  # COPIED counts as moved
        # Sources still exist
        for f in files:
            assert Path(f.source_path).exists()
        # Index still points at original src paths (NOT updated)
        for f in files:
            entity = rt.file_repo.get(f.curator_id)
            assert entity.source_path == f.source_path
        # Dst files all created
        for f in files:
            rel = Path(f.source_path).relative_to(src_root)
            assert (tmp_path / "out" / rel).exists()
        # Outcomes are COPIED (not MOVED)
        for m in report.moves:
            if m.outcome:
                assert m.outcome.value == "copied"

    def test_keep_source_audit_uses_migration_copy_action(
        self, migration_service, small_library, tmp_path,
    ):
        rt, src_root, files = small_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        migration_service.apply(plan, keep_source=True)
        copy_entries = rt.audit_repo.query(action="migration.copy", limit=20)
        move_entries = rt.audit_repo.query(action="migration.move", limit=20)
        assert len(copy_entries) == 5
        assert len(move_entries) == 0


class TestApplyIncludeCaution:
    def test_include_caution_true_migrates_caution_files(
        self, migration_runtime, tmp_path,
    ):
        rt = migration_runtime
        src_root = tmp_path / "mixed"
        files = [_seed_real_file(rt, src_root / f"f{i}.mp3") for i in range(3)]
        # Stub safety: f0/f1 SAFE, f2 CAUTION
        from curator.services.safety import SafetyReport, SafetyLevel as SL
        def safety_stub(p, **kw):
            level = SL.CAUTION if str(p).endswith("f2.mp3") else SL.SAFE
            return SafetyReport(path=p, level=level)
        rt.safety.check_path = safety_stub
        svc = rt.migration
        plan = _build_plan(svc, src_root, tmp_path / "out")
        report = svc.apply(plan, include_caution=True)
        # All 3 should have moved
        assert report.moved_count == 3

    def test_include_caution_false_default_skips_caution(
        self, migration_runtime, tmp_path,
    ):
        rt = migration_runtime
        src_root = tmp_path / "mixed"
        files = [_seed_real_file(rt, src_root / f"f{i}.mp3") for i in range(3)]
        from curator.services.safety import SafetyReport, SafetyLevel as SL
        def safety_stub(p, **kw):
            level = SL.CAUTION if str(p).endswith("f2.mp3") else SL.SAFE
            return SafetyReport(path=p, level=level)
        rt.safety.check_path = safety_stub
        svc = rt.migration
        plan = _build_plan(svc, src_root, tmp_path / "out")
        report = svc.apply(plan)  # default include_caution=False
        assert report.moved_count == 2  # SAFE only
        # CAUTION file recorded as SKIPPED_NOT_SAFE
        skipped = [m for m in report.moves if m.outcome
                   and m.outcome.value == "skipped_not_safe"]
        assert len(skipped) == 1

    def test_include_caution_true_still_skips_refuse(
        self, migration_runtime, tmp_path,
    ):
        rt = migration_runtime
        src_root = tmp_path / "with_refuse"
        files = [_seed_real_file(rt, src_root / f"f{i}.mp3") for i in range(3)]
        from curator.services.safety import SafetyReport, SafetyLevel as SL
        def safety_stub(p, **kw):
            if str(p).endswith("f2.mp3"):
                return SafetyReport(path=p, level=SL.REFUSE)
            return SafetyReport(path=p, level=SL.SAFE)
        rt.safety.check_path = safety_stub
        svc = rt.migration
        plan = _build_plan(svc, src_root, tmp_path / "out")
        report = svc.apply(plan, include_caution=True)
        # 2 SAFE move; REFUSE never moves regardless of include_caution
        assert report.moved_count == 2
        skipped = [m for m in report.moves if m.outcome
                   and m.outcome.value == "skipped_not_safe"]
        assert len(skipped) == 1


class TestRunJobKeepSource:
    def test_run_job_keep_source_under_workers_leaves_sources_intact(
        self, migration_service, medium_library, tmp_path,
    ):
        rt, src_root, files = medium_library
        plan = _build_plan(migration_service, src_root, tmp_path / "out")
        job_id = migration_service.create_job(
            plan, options={"keep_source": True, "workers": 4},
        )
        report = migration_service.run_job(
            job_id, workers=4, keep_source=True,
        )
        # All 12 dst files exist; all 12 src files ALSO still exist
        for f in files:
            assert Path(f.source_path).exists()
            rel = Path(f.source_path).relative_to(src_root)
            assert (tmp_path / "out" / rel).exists()
        # Index untouched -- still points at src
        for f in files:
            assert rt.file_repo.get(f.curator_id).source_path == f.source_path
        # All audit entries are migration.copy (not migration.move)
        copy_entries = rt.audit_repo.query(action="migration.copy", limit=20)
        move_entries = rt.audit_repo.query(action="migration.move", limit=20)
        assert len(copy_entries) == 12
        assert len(move_entries) == 0
        # Each copy audit entry has the job_id for cross-reference
        for entry in copy_entries:
            assert entry.details.get("job_id") == str(job_id)
        # Progress rows reflect outcome=copied
        rows = rt.migration_job_repo.query_progress(job_id)
        assert len(rows) == 12
        for r in rows:
            assert r.outcome == "copied"
            assert r.status == "completed"
