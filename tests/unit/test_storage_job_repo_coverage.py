"""Coverage closure for ``curator.storage.repositories.job_repo`` (v1.7.134).

Targets the 11 uncovered lines + 1 partial branch:
- Lines 54-55: ``update(job)``
- Lines 88-94: ``update_status`` 'running' + 'other' branches
- Lines 108-109: ``delete(job_id)``
- Lines 124-132: ``list_recent(limit)``
- Lines 135-139: ``list_by_status(status)``
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from curator._compat.datetime import utcnow_naive
from curator.models.jobs import ScanJob


def _mk_job(**overrides) -> ScanJob:
    base = dict(
        source_id="local",
        root_path="/x",
        status="queued",
        files_seen=0,
        files_hashed=0,
    )
    base.update(overrides)
    return ScanJob(**base)


class TestUpdate:
    def test_update_rewrites_row(self, repos, local_source):
        job = _mk_job()
        repos.jobs.insert(job)

        # Mutate + update
        job.status = "running"
        job.files_seen = 42
        job.files_hashed = 30
        job.started_at = utcnow_naive()
        repos.jobs.update(job)

        fetched = repos.jobs.get(job.job_id)
        assert fetched is not None
        assert fetched.status == "running"
        assert fetched.files_seen == 42
        assert fetched.files_hashed == 30


class TestUpdateStatusBranches:
    def test_running_sets_started_at(self, repos, local_source):
        job = _mk_job()
        repos.jobs.insert(job)
        repos.jobs.update_status(job.job_id, "running")
        fetched = repos.jobs.get(job.job_id)
        assert fetched is not None
        assert fetched.status == "running"
        assert fetched.started_at is not None

    def test_other_status_takes_generic_branch(self, repos, local_source):
        """Line 93-97: status not in terminal/'running' hits else branch."""
        job = _mk_job()
        repos.jobs.insert(job)
        repos.jobs.update_status(job.job_id, "paused")
        fetched = repos.jobs.get(job.job_id)
        assert fetched is not None
        assert fetched.status == "paused"

    def test_completed_status_sets_completed_at_and_error(
        self, repos, local_source,
    ):
        job = _mk_job()
        repos.jobs.insert(job)
        repos.jobs.update_status(job.job_id, "failed", error="kaboom")
        fetched = repos.jobs.get(job.job_id)
        assert fetched is not None
        assert fetched.status == "failed"
        assert fetched.completed_at is not None
        assert fetched.error == "kaboom"


class TestDelete:
    def test_delete_removes_row(self, repos, local_source):
        job = _mk_job()
        repos.jobs.insert(job)
        assert repos.jobs.get(job.job_id) is not None
        repos.jobs.delete(job.job_id)
        assert repos.jobs.get(job.job_id) is None


class TestListRecent:
    def test_list_recent_orders_by_started_at_desc(self, repos, local_source):
        old = _mk_job()
        repos.jobs.insert(old)
        repos.jobs.update_status(old.job_id, "running")

        # Insert a fresher job with a later started_at by updating
        new = _mk_job()
        repos.jobs.insert(new)
        repos.jobs.update_status(new.job_id, "running")

        results = repos.jobs.list_recent(limit=10)
        ids = [r.job_id for r in results]
        # newer first
        assert ids.index(new.job_id) <= ids.index(old.job_id)
        assert len(results) == 2

    def test_list_recent_default_limit(self, repos, local_source):
        for _ in range(3):
            j = _mk_job()
            repos.jobs.insert(j)
        results = repos.jobs.list_recent()
        assert len(results) == 3


class TestListByStatus:
    def test_filters_by_status(self, repos, local_source):
        running = _mk_job()
        queued = _mk_job()
        repos.jobs.insert(running)
        repos.jobs.insert(queued)
        repos.jobs.update_status(running.job_id, "running")

        runs = repos.jobs.list_by_status("running")
        queues = repos.jobs.list_by_status("queued")

        assert len(runs) == 1 and runs[0].job_id == running.job_id
        assert len(queues) == 1 and queues[0].job_id == queued.job_id
