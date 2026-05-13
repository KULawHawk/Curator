"""Coverage closure for ``curator.storage.repositories.migration_job_repo`` (v1.7.132).

Targets:
- Line 103: ``update_job_status`` "other" branch (status not in
  terminal/'running')
- Lines 266-269: ``next_pending_progress`` ``BEGIN IMMEDIATE`` exception
  (already in transaction)
- Branch 324->326: ``update_progress`` outcome=None skips outcome SET
- Line 331: ``update_progress`` src_xxhash set
- Branch 333->336: ``update_progress`` non-terminal status skips
  completed_at SET
- Lines 401-402: ``query_progress`` with limit
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock
from uuid import uuid4

from curator._compat.datetime import utcnow_naive
from curator.models import FileEntity
from curator.models.migration import MigrationJob, MigrationProgress
from curator.storage.repositories._helpers import uuid_to_str


def _mk_job() -> MigrationJob:
    return MigrationJob(
        src_source_id="local",
        src_root="/src",
        dst_source_id="local",
        dst_root="/dst",
    )


def _mk_progress(job_id, curator_id, src_path: str) -> MigrationProgress:
    return MigrationProgress(
        job_id=job_id,
        curator_id=curator_id,
        src_path=src_path,
        dst_path=src_path.replace("src", "dst"),
        size=10,
        safety_level="safe",
    )


def _file_for(repos, source_id: str, path: str) -> FileEntity:
    f = FileEntity(
        source_id=source_id, source_path=path,
        size=10, mtime=utcnow_naive(),
    )
    repos.files.insert(f)
    return f


class TestUpdateJobStatusOtherBranch:
    def test_status_queued_takes_else_branch(self, repos, local_source):
        """Line 103: ``update_job_status(status='queued')`` (not terminal,
        not 'running') hits the catchall else branch."""
        job = _mk_job()
        repos.migration_jobs.insert_job(job)
        # 'queued' is the default; transition to 'queued' via the
        # generic UPDATE arm. This exercises line 103.
        repos.migration_jobs.update_job_status(job.job_id, "queued")
        # Verify the row still exists
        fetched = repos.migration_jobs.get_job(job.job_id)
        assert fetched is not None
        assert fetched.status == "queued"


class _ExecuteRaisingConn:
    """Wrapper around a real sqlite3.Connection that raises on the FIRST
    ``BEGIN IMMEDIATE`` call but delegates everything else to the real
    connection. Used to simulate "already in a transaction" without
    actually nesting transactions."""

    def __init__(self, real_conn):
        self._real = real_conn
        self._raised_begin_immediate = False

    def execute(self, sql, params=()):
        if not self._raised_begin_immediate and "BEGIN IMMEDIATE" in sql:
            self._raised_begin_immediate = True
            raise RuntimeError("already in transaction (simulated)")
        return self._real.execute(sql, params)

    def __enter__(self):
        # Mirror the real conn's context-manager protocol
        self._real.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._real.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestNextPendingProgressAlreadyInTransaction:
    def test_begin_immediate_exception_is_swallowed(
        self, repos, local_source, monkeypatch,
    ):
        """Lines 266-269: BEGIN IMMEDIATE raises if already in tx;
        the except clause swallows it and proceeds."""
        job = _mk_job()
        repos.migration_jobs.insert_job(job)
        f = _file_for(repos, "local", "/src/a")
        repos.migration_jobs.seed_progress_rows(job.job_id, [
            _mk_progress(job.job_id, f.curator_id, "/src/a"),
        ])

        # Wrap the db.conn() to return a fake conn that raises on
        # BEGIN IMMEDIATE the first time. The real conn handles
        # everything else.
        real_db = repos.migration_jobs.db
        real_conn = real_db.conn()
        wrapped = _ExecuteRaisingConn(real_conn)
        monkeypatch.setattr(real_db, "conn", lambda: wrapped)

        # The method should still succeed despite BEGIN IMMEDIATE raising
        claimed = repos.migration_jobs.next_pending_progress(job.job_id)
        assert claimed is not None
        assert claimed.curator_id == f.curator_id


class TestUpdateProgressBranches:
    def test_outcome_none_skips_outcome_set(self, repos, local_source):
        """Branch 324->326: outcome=None means the if-block is skipped."""
        job = _mk_job()
        repos.migration_jobs.insert_job(job)
        f = _file_for(repos, "local", "/src/b")
        repos.migration_jobs.seed_progress_rows(job.job_id, [
            _mk_progress(job.job_id, f.curator_id, "/src/b"),
        ])

        repos.migration_jobs.update_progress(
            job.job_id, f.curator_id,
            status="in_progress",  # non-terminal
            outcome=None,           # skip the if outcome branch
            # Don't pass src_xxhash either — but the test for line 331 is below
        )
        # No assertion needed beyond "did not crash"

    def test_src_xxhash_set_appends_clause(self, repos, local_source):
        """Line 331: ``src_xxhash`` set when value is not None."""
        job = _mk_job()
        repos.migration_jobs.insert_job(job)
        f = _file_for(repos, "local", "/src/c")
        repos.migration_jobs.seed_progress_rows(job.job_id, [
            _mk_progress(job.job_id, f.curator_id, "/src/c"),
        ])

        repos.migration_jobs.update_progress(
            job.job_id, f.curator_id,
            status="completed",
            src_xxhash="hash_xyz",  # exercises line 331
            verified_xxhash="hash_xyz",
        )
        prog = repos.migration_jobs.get_progress(job.job_id, f.curator_id)
        assert prog is not None
        assert prog.src_xxhash == "hash_xyz"
        assert prog.status == "completed"

    def test_non_terminal_status_skips_completed_at_set(
        self, repos, local_source,
    ):
        """Branch 333->336: non-terminal status (e.g. 'in_progress')
        means the if-block is skipped (no completed_at appended)."""
        job = _mk_job()
        repos.migration_jobs.insert_job(job)
        f = _file_for(repos, "local", "/src/d")
        repos.migration_jobs.seed_progress_rows(job.job_id, [
            _mk_progress(job.job_id, f.curator_id, "/src/d"),
        ])

        repos.migration_jobs.update_progress(
            job.job_id, f.curator_id,
            status="in_progress",  # not terminal
        )
        prog = repos.migration_jobs.get_progress(job.job_id, f.curator_id)
        assert prog is not None
        assert prog.completed_at is None
        assert prog.status == "in_progress"


class TestQueryProgressWithLimit:
    def test_limit_clauses(self, repos, local_source):
        """Lines 401-402: ``query_progress(limit=N)`` appends LIMIT."""
        job = _mk_job()
        repos.migration_jobs.insert_job(job)
        rows = []
        for i in range(5):
            f = _file_for(repos, "local", f"/src/q{i}")
            rows.append(_mk_progress(job.job_id, f.curator_id, f"/src/q{i}"))
        repos.migration_jobs.seed_progress_rows(job.job_id, rows)

        # No limit
        all_rows = repos.migration_jobs.query_progress(job.job_id)
        assert len(all_rows) == 5

        # With limit
        limited = repos.migration_jobs.query_progress(job.job_id, limit=2)
        assert len(limited) == 2
