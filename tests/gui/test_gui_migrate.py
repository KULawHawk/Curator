"""Tests for v1.1.0 (Tracer Phase 2 Session C1) Migrate tab.

Two layers, mirroring the existing GUI test suite (test_gui_models /
test_gui_inbox / etc):

  * **Model tests** -- :class:`MigrationJobTableModel` and
    :class:`MigrationProgressTableModel` exercised against a real
    CuratorDB + seeded :class:`MigrationJobRepository`. Need a session
    QApplication for QObject parent semantics; no event loop is run.
  * **Tab wiring tests** -- launch :class:`CuratorMainWindow` against a
    fully-wired :class:`CuratorRuntime` and assert the Migrate tab
    exists at the right index, models are attached to the window, the
    master/detail selection signal works, and ``refresh_all`` triggers
    Migrate refresh.

All tests are skipped when PySide6 is unavailable.

Read-only in C1 -- no mutation actions (Abort, Resume) and no live
progress signal wiring during ``run_job``. Those land in Session C2.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

# Skip the entire file if PySide6 isn't available.
pyside6 = pytest.importorskip("PySide6")  # noqa: F841

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.gui.main_window import CuratorMainWindow
from curator.gui.models import (
    MigrationJobTableModel,
    MigrationProgressTableModel,
    _format_duration,
)
from curator.models.migration import MigrationJob, MigrationProgress
from curator.storage import CuratorDB
from curator.storage.repositories.migration_job_repo import MigrationJobRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    """One QApplication per session (Qt's hard requirement)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def seeded_repo(tmp_path):
    """A real CuratorDB + MigrationJobRepository with three jobs.

    Returns ``(repo, jobs)`` where ``jobs`` is a list of three
    :class:`MigrationJob` records:

      * jobs[0]: completed, 5 files copied, no failures
      * jobs[1]: running, 3 files total, 2 copied so far
      * jobs[2]: failed, 1 file, with an error message
    """
    db_path = tmp_path / "migrate_gui_test.db"
    db = CuratorDB(db_path)
    db.init()
    repo = MigrationJobRepository(db)

    j0 = MigrationJob(
        src_source_id="local", src_root="/data/src",
        dst_source_id="local", dst_root="/data/archive",
        status="completed",
        files_total=5, files_copied=5, files_skipped=0, files_failed=0,
        bytes_copied=2_500_000,
        started_at=datetime(2026, 5, 1, 10, 0),
        completed_at=datetime(2026, 5, 1, 10, 1, 30),
    )
    j1 = MigrationJob(
        src_source_id="local", src_root="/music",
        dst_source_id="local:vault", dst_root="/vault/music",
        status="running",
        files_total=3, files_copied=2, files_skipped=0, files_failed=0,
        bytes_copied=12_000_000,
        started_at=datetime(2026, 5, 1, 11, 0),
    )
    j2 = MigrationJob(
        src_source_id="local", src_root="/photos",
        dst_source_id="gdrive", dst_root="root",
        status="failed",
        files_total=1, files_copied=0, files_skipped=0, files_failed=1,
        bytes_copied=0,
        started_at=datetime(2026, 5, 1, 12, 0),
        completed_at=datetime(2026, 5, 1, 12, 0, 5),
        error="cross-source: no plugin handled curator_source_write",
    )

    # Insert in reverse-time order so list_jobs (newest first) returns j2 first.
    # Actually list_jobs is ORDER BY started_at DESC, so the temporal order of
    # the started_at fields above already controls ordering. Insert order doesn't
    # matter -- the repo reads from DB.
    repo.insert_job(j0)
    repo.insert_job(j1)
    repo.insert_job(j2)

    # Seed three progress rows for j1 (the running job) -- these exercise the
    # progress-model display.
    progress = [
        MigrationProgress(
            job_id=j1.job_id, curator_id=uuid4(),
            src_path="/music/album1/track01.flac",
            dst_path="/vault/music/album1/track01.flac",
            src_xxhash="aabb" * 8, verified_xxhash="aabb" * 8,
            size=4_000_000, safety_level="safe",
            status="completed", outcome="moved",
            completed_at=datetime(2026, 5, 1, 11, 0, 30),
        ),
        MigrationProgress(
            job_id=j1.job_id, curator_id=uuid4(),
            src_path="/music/album1/track02.flac",
            dst_path="/vault/music/album1/track02.flac",
            src_xxhash="bbcc" * 8, verified_xxhash="bbcc" * 8,
            size=3_500_000, safety_level="safe",
            status="completed", outcome="moved",
            completed_at=datetime(2026, 5, 1, 11, 1),
        ),
        MigrationProgress(
            job_id=j1.job_id, curator_id=uuid4(),
            src_path="/music/album2/track01.flac",
            dst_path="/vault/music/album2/track01.flac",
            src_xxhash="ccdd" * 8,
            size=4_500_000, safety_level="safe",
            status="pending",
        ),
    ]
    repo.seed_progress_rows(j1.job_id, progress)

    return repo, [j0, j1, j2]


@pytest.fixture
def runtime_with_migrations(tmp_path, monkeypatch):
    """A fully-wired CuratorRuntime with the same seeded migration data
    as ``seeded_repo`` -- used by the tab wiring tests that need a
    full :class:`CuratorMainWindow`."""
    db_path = tmp_path / "migrate_runtime.db"
    monkeypatch.setenv("CURATOR_DB", str(db_path))

    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )

    repo = rt.migration_job_repo
    j0 = MigrationJob(
        src_source_id="local", src_root="/a", dst_source_id="local", dst_root="/b",
        status="completed",
        files_total=2, files_copied=2,
        bytes_copied=1024,
        started_at=datetime(2026, 5, 1, 9, 0),
        completed_at=datetime(2026, 5, 1, 9, 0, 10),
    )
    j1 = MigrationJob(
        src_source_id="local", src_root="/c",
        dst_source_id="local:vault", dst_root="/d",
        status="running",
        files_total=1, files_copied=0,
        started_at=datetime(2026, 5, 1, 10, 0),
    )
    repo.insert_job(j0)
    repo.insert_job(j1)
    repo.seed_progress_rows(j1.job_id, [
        MigrationProgress(
            job_id=j1.job_id, curator_id=uuid4(),
            src_path="/c/file.txt", dst_path="/d/file.txt",
            size=512, safety_level="safe", status="pending",
        ),
    ])

    return rt, [j0, j1]


# ===========================================================================
# Helper tests
# ===========================================================================


class TestFormatDuration:
    @pytest.mark.parametrize("seconds, expected", [
        (None, ""),
        (0.0, "0.0s"),
        (4.2, "4.2s"),
        (59.9, "59.9s"),
        (60, "1m 00s"),
        (90, "1m 30s"),
        (3599, "59m 59s"),
        (3600, "1h 00m"),
        (7325, "2h 02m"),
    ])
    def test_format_duration(self, seconds, expected):
        assert _format_duration(seconds) == expected


# ===========================================================================
# MigrationJobTableModel
# ===========================================================================


class TestMigrationJobTableModel:
    def test_empty_repo_yields_empty_model(self, qapp, tmp_path):
        db_path = tmp_path / "empty.db"
        db = CuratorDB(db_path); db.init()
        repo = MigrationJobRepository(db)
        model = MigrationJobTableModel(repo)
        assert model.rowCount() == 0
        assert model.columnCount() == len(model.COLUMNS)

    def test_columns(self, qapp, seeded_repo):
        repo, _ = seeded_repo
        model = MigrationJobTableModel(repo)
        assert model.columnCount() == 8
        # Header strings expose human-readable labels
        labels = [model.headerData(c, Qt.Orientation.Horizontal) for c in range(8)]
        assert "Status" in labels
        assert any("Src" in label and "Dst" in label for label in labels)
        assert "Files" in labels
        assert "Copied" in labels
        assert "Failed" in labels

    def test_rowcount_matches_seeded_jobs(self, qapp, seeded_repo):
        repo, jobs = seeded_repo
        model = MigrationJobTableModel(repo)
        assert model.rowCount() == len(jobs)

    def test_data_renders_status_and_src_dst(self, qapp, seeded_repo):
        repo, jobs = seeded_repo
        model = MigrationJobTableModel(repo)
        # All rendered statuses appear somewhere in the model
        rendered = [
            model.data(model.index(r, 0)) for r in range(model.rowCount())
        ]
        assert "completed" in rendered
        assert "running" in rendered
        assert "failed" in rendered
        # Src->Dst column shows source IDs
        src_dst = [
            model.data(model.index(r, 1)) for r in range(model.rowCount())
        ]
        # At least one cross-source pair is present
        assert any("local" in s and "local:vault" in s for s in src_dst)

    def test_data_files_columns_are_integers(self, qapp, seeded_repo):
        repo, _ = seeded_repo
        model = MigrationJobTableModel(repo)
        for r in range(model.rowCount()):
            assert isinstance(model.data(model.index(r, 2)), int)  # Files
            assert isinstance(model.data(model.index(r, 3)), int)  # Copied
            assert isinstance(model.data(model.index(r, 4)), int)  # Failed

    def test_data_bytes_formatted_human_readable(self, qapp, seeded_repo):
        repo, _ = seeded_repo
        model = MigrationJobTableModel(repo)
        # The 2_500_000-byte job should render as "2.4 MB" or similar
        any_mb_or_kb = False
        for r in range(model.rowCount()):
            v = model.data(model.index(r, 5))
            if "MB" in str(v) or "KB" in str(v):
                any_mb_or_kb = True
                break
        assert any_mb_or_kb

    def test_tooltip_on_src_dst_includes_full_paths(self, qapp, seeded_repo):
        repo, _ = seeded_repo
        model = MigrationJobTableModel(repo)
        tip = model.data(model.index(0, 1), Qt.ToolTipRole)
        assert tip is not None
        # Tooltip should reference at least one of the seeded src_root paths
        assert any(p in tip for p in ("/data/src", "/music", "/photos"))

    def test_tooltip_on_status_for_failed_job_shows_error(self, qapp, seeded_repo):
        repo, _ = seeded_repo
        model = MigrationJobTableModel(repo)
        # Find the failed row
        failed_row = None
        for r in range(model.rowCount()):
            if model.data(model.index(r, 0)) == "failed":
                failed_row = r
                break
        assert failed_row is not None
        tip = model.data(model.index(failed_row, 0), Qt.ToolTipRole)
        assert tip is not None and "curator_source_write" in tip

    def test_job_at_returns_right_record(self, qapp, seeded_repo):
        repo, jobs = seeded_repo
        model = MigrationJobTableModel(repo)
        # Job IDs of the model rows should be a subset of the seeded jobs
        seeded_ids = {j.job_id for j in jobs}
        for r in range(model.rowCount()):
            j = model.job_at(r)
            assert j is not None
            assert j.job_id in seeded_ids
        assert model.job_at(-1) is None
        assert model.job_at(model.rowCount()) is None  # OOB

    def test_refresh_picks_up_new_job(self, qapp, seeded_repo):
        repo, _ = seeded_repo
        model = MigrationJobTableModel(repo)
        before = model.rowCount()
        new_job = MigrationJob(
            src_source_id="local", src_root="/x",
            dst_source_id="local", dst_root="/y",
            status="queued", files_total=10,
        )
        repo.insert_job(new_job)
        # Model is stale until refresh
        assert model.rowCount() == before
        model.refresh()
        assert model.rowCount() == before + 1


# ===========================================================================
# MigrationProgressTableModel
# ===========================================================================


class TestMigrationProgressTableModel:
    def test_empty_when_no_job_id(self, qapp, seeded_repo):
        repo, _ = seeded_repo
        model = MigrationProgressTableModel(repo)
        assert model.rowCount() == 0
        assert model.job_id is None

    def test_set_job_id_populates_rows(self, qapp, seeded_repo):
        repo, jobs = seeded_repo
        # j1 is the seeded running job with 3 progress rows
        running_job = next(j for j in jobs if j.status == "running")
        model = MigrationProgressTableModel(repo)
        model.set_job_id(running_job.job_id)
        assert model.rowCount() == 3
        assert model.job_id == running_job.job_id

    def test_set_job_id_none_clears_rows(self, qapp, seeded_repo):
        repo, jobs = seeded_repo
        running_job = next(j for j in jobs if j.status == "running")
        model = MigrationProgressTableModel(repo, job_id=running_job.job_id)
        assert model.rowCount() == 3
        model.set_job_id(None)
        assert model.rowCount() == 0
        assert model.job_id is None

    def test_data_renders_status_outcome_path(self, qapp, seeded_repo):
        repo, jobs = seeded_repo
        running_job = next(j for j in jobs if j.status == "running")
        model = MigrationProgressTableModel(repo, job_id=running_job.job_id)
        statuses = [model.data(model.index(r, 0)) for r in range(model.rowCount())]
        assert "completed" in statuses or "pending" in statuses
        # Outcome column shows 'moved' for terminal rows, '' for pending
        outcomes = [model.data(model.index(r, 1)) for r in range(model.rowCount())]
        assert "moved" in outcomes
        # Src Path column has filesystem paths
        paths = [model.data(model.index(r, 2)) for r in range(model.rowCount())]
        assert any("track01.flac" in str(p) for p in paths)

    def test_verified_hash_truncated(self, qapp, seeded_repo):
        repo, jobs = seeded_repo
        running_job = next(j for j in jobs if j.status == "running")
        model = MigrationProgressTableModel(repo, job_id=running_job.job_id)
        # The seeded verified_xxhash values are 32 chars; cell should
        # show the first 12 + ellipsis
        for r in range(model.rowCount()):
            v = model.data(model.index(r, 4))
            if v:  # pending rows have no verified hash
                assert len(v) <= 13  # 12 chars + 1-char ellipsis
                assert v.endswith("\u2026")

    def test_tooltip_on_src_path_shows_dst_too(self, qapp, seeded_repo):
        repo, jobs = seeded_repo
        running_job = next(j for j in jobs if j.status == "running")
        model = MigrationProgressTableModel(repo, job_id=running_job.job_id)
        tip = model.data(model.index(0, 2), Qt.ToolTipRole)
        assert tip is not None
        assert "src:" in tip and "dst:" in tip

    def test_progress_at_returns_right_record(self, qapp, seeded_repo):
        repo, jobs = seeded_repo
        running_job = next(j for j in jobs if j.status == "running")
        model = MigrationProgressTableModel(repo, job_id=running_job.job_id)
        for r in range(model.rowCount()):
            p = model.progress_at(r)
            assert p is not None
            assert p.job_id == running_job.job_id
        assert model.progress_at(-1) is None
        assert model.progress_at(model.rowCount()) is None


# ===========================================================================
# Migrate tab wiring (full window)
# ===========================================================================


class TestMigrateTabWiring:
    def test_migrate_tab_at_index_4(self, qapp, runtime_with_migrations):
        rt, _ = runtime_with_migrations
        window = CuratorMainWindow(rt)
        try:
            assert window._tabs.tabText(4) == "Migrate"
        finally:
            window.deleteLater()

    def test_models_attached_to_window(self, qapp, runtime_with_migrations):
        rt, jobs = runtime_with_migrations
        window = CuratorMainWindow(rt)
        try:
            assert isinstance(window._migrate_jobs_model, MigrationJobTableModel)
            assert isinstance(
                window._migrate_progress_model, MigrationProgressTableModel,
            )
            # Jobs model populated by initial refresh
            assert window._migrate_jobs_model.rowCount() == len(jobs)
            # Progress model is empty until a job is selected
            assert window._migrate_progress_model.rowCount() == 0
            assert window._migrate_progress_model.job_id is None
        finally:
            window.deleteLater()

    def test_selecting_job_populates_progress(self, qapp, runtime_with_migrations):
        rt, jobs = runtime_with_migrations
        window = CuratorMainWindow(rt)
        try:
            # Find the row whose job_id matches the running job (which has
            # progress rows seeded).
            running_id = next(j.job_id for j in jobs if j.status == "running")
            target_row = None
            for r in range(window._migrate_jobs_model.rowCount()):
                if window._migrate_jobs_model.job_at(r).job_id == running_id:
                    target_row = r
                    break
            assert target_row is not None
            # Programmatically select that row -- this fires selectionChanged
            window._migrate_jobs_view.selectRow(target_row)
            # Progress model should now be pointed at the running job
            assert window._migrate_progress_model.job_id == running_id
            assert window._migrate_progress_model.rowCount() == 1  # 1 progress row
            # Progress label updated to show the short job_id
            label_text = window._migrate_progress_label.text()
            assert "Per-file progress" in label_text
            assert str(running_id)[:8] in label_text
        finally:
            window.deleteLater()

    def test_refresh_button_triggers_refresh(self, qapp, runtime_with_migrations):
        rt, jobs = runtime_with_migrations
        window = CuratorMainWindow(rt)
        try:
            # Insert a new job directly into the repo
            new_job = MigrationJob(
                src_source_id="local", src_root="/new",
                dst_source_id="local", dst_root="/dest",
                status="queued", files_total=99,
            )
            rt.migration_job_repo.insert_job(new_job)
            # Model is stale until refresh
            stale_count = window._migrate_jobs_model.rowCount()
            assert stale_count == len(jobs)
            # Click refresh
            window._migrate_refresh_btn.click()
            assert window._migrate_jobs_model.rowCount() == stale_count + 1
        finally:
            window.deleteLater()

    def test_refresh_all_includes_migrate(self, qapp, runtime_with_migrations):
        rt, jobs = runtime_with_migrations
        window = CuratorMainWindow(rt)
        try:
            new_job = MigrationJob(
                src_source_id="local", src_root="/x",
                dst_source_id="local", dst_root="/y",
                status="queued", files_total=1,
            )
            rt.migration_job_repo.insert_job(new_job)
            window.refresh_all()
            assert window._migrate_jobs_model.rowCount() == len(jobs) + 1
        finally:
            window.deleteLater()

    def test_clearing_selection_clears_progress_label(
        self, qapp, runtime_with_migrations,
    ):
        rt, jobs = runtime_with_migrations
        window = CuratorMainWindow(rt)
        try:
            running_id = next(j.job_id for j in jobs if j.status == "running")
            for r in range(window._migrate_jobs_model.rowCount()):
                if window._migrate_jobs_model.job_at(r).job_id == running_id:
                    window._migrate_jobs_view.selectRow(r)
                    break
            assert window._migrate_progress_model.job_id == running_id
            # Clear selection
            window._migrate_jobs_view.clearSelection()
            # The slot fires; progress should be cleared
            assert window._migrate_progress_model.job_id is None
            assert "select a job above" in window._migrate_progress_label.text()
        finally:
            window.deleteLater()
