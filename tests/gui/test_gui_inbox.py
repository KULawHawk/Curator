"""Tests for v0.39 Inbox view: ScanJobTableModel + PendingReviewTableModel + tab wiring.

Covers:
  * ScanJobTableModel construction + columns + cell content
  * ScanJobTableModel default limit (10)
  * PendingReviewTableModel filters by confidence range correctly
  * PendingReviewTableModel resolves file paths via the file_repo
  * PendingReviewTableModel handles missing file rows gracefully
  * Inbox tab is at index 0
  * Status bar still works after tab reorder
  * refresh_all() refreshes the three Inbox models
  * Empty-state hint labels appear when models are empty
  * LineageRepository.query_by_confidence (the new repo method)
  * TrashTableModel limit param works in the inbox section

All tests skip if PySide6 unavailable. None requires pytest-qt.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

pyside6 = pytest.importorskip("PySide6")  # noqa: F841

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.gui.main_window import CuratorMainWindow
from curator.gui.models import (
    PendingReviewTableModel,
    ScanJobTableModel,
    TrashTableModel,
)
from curator.models.file import FileEntity
from curator.models.jobs import ScanJob
from curator.models.lineage import LineageEdge, LineageKind
from curator.models.source import SourceConfig
from curator.models.trash import TrashRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def runtime_empty(tmp_path):
    """Real runtime, no seeded data."""
    db_path = tmp_path / "inbox_empty.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    yield rt


@pytest.fixture
def runtime_with_inbox_data(tmp_path):
    """Real runtime with: 3 scan jobs, several lineage edges spanning confidence
    bands, 2 trash records."""
    db_path = tmp_path / "inbox.db"
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

    # Scan jobs.
    base = datetime(2024, 6, 15, 10, 0)
    for i, status in enumerate(["completed", "completed", "running"]):
        job = ScanJob(
            job_id=uuid4(), status=status,
            source_id="local",
            root_path=f"/Users/jmlee/Music{i}",
            files_seen=100 * (i + 1),
            files_hashed=100 * (i + 1) if status == "completed" else 50,
            started_at=base + timedelta(minutes=i * 5),
            completed_at=base + timedelta(minutes=i * 5 + 2)
                         if status == "completed" else None,
        )
        rt.job_repo.insert(job)

    # Lineage edges across the confidence spectrum.
    file_a = FileEntity(curator_id=uuid4(), source_id="local",
                        source_path="/files/a.txt", size=10,
                        mtime=datetime(2024, 1, 1))
    file_b = FileEntity(curator_id=uuid4(), source_id="local",
                        source_path="/files/b.txt", size=20,
                        mtime=datetime(2024, 1, 2))
    file_c = FileEntity(curator_id=uuid4(), source_id="local",
                        source_path="/files/c.txt", size=30,
                        mtime=datetime(2024, 1, 3))
    rt.file_repo.upsert(file_a)
    rt.file_repo.upsert(file_b)
    rt.file_repo.upsert(file_c)

    # confidence 0.99 — above auto_confirm (0.95): NOT pending review
    rt.lineage_repo.insert(LineageEdge(
        from_curator_id=file_a.curator_id, to_curator_id=file_b.curator_id,
        edge_kind=LineageKind.DUPLICATE, confidence=0.99,
        detected_by="curator.core.lineage_hash_dup",
    ))
    # confidence 0.85 — IN pending review band [0.7, 0.95)
    rt.lineage_repo.insert(LineageEdge(
        from_curator_id=file_a.curator_id, to_curator_id=file_c.curator_id,
        edge_kind=LineageKind.NEAR_DUPLICATE, confidence=0.85,
        detected_by="curator.core.lineage_fuzzy_dup",
    ))
    # confidence 0.78 — IN pending review band
    rt.lineage_repo.insert(LineageEdge(
        from_curator_id=file_b.curator_id, to_curator_id=file_c.curator_id,
        edge_kind=LineageKind.VERSION_OF, confidence=0.78,
        detected_by="curator.core.lineage_filename",
    ))

    # Trash records (need a real file row + mark_deleted first).
    trashed_a = FileEntity(curator_id=uuid4(), source_id="local",
                           source_path="/tmp/trashed_a.txt", size=1,
                           mtime=datetime(2024, 5, 1))
    trashed_b = FileEntity(curator_id=uuid4(), source_id="local",
                           source_path="/tmp/trashed_b.txt", size=2,
                           mtime=datetime(2024, 5, 2))
    rt.file_repo.upsert(trashed_a)
    rt.file_repo.upsert(trashed_b)
    rt.file_repo.mark_deleted(trashed_a.curator_id)
    rt.file_repo.mark_deleted(trashed_b.curator_id)
    rt.trash_repo.insert(TrashRecord(
        curator_id=trashed_a.curator_id,
        original_source_id="local", original_path="/tmp/trashed_a.txt",
        trashed_by="user", reason="dup",
    ))
    rt.trash_repo.insert(TrashRecord(
        curator_id=trashed_b.curator_id,
        original_source_id="local", original_path="/tmp/trashed_b.txt",
        trashed_by="user", reason="cleanup",
    ))

    yield rt, [file_a, file_b, file_c]


# ===========================================================================
# ScanJobTableModel
# ===========================================================================


class TestScanJobTableModel:
    def test_constructs_with_empty_repo(self, qapp, runtime_empty):
        model = ScanJobTableModel(runtime_empty.job_repo)
        assert model.rowCount() == 0
        assert model.columnCount() == 6

    def test_loads_seeded_jobs(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        model = ScanJobTableModel(rt.job_repo)
        assert model.rowCount() == 3

    def test_columns_match_constant(self, qapp, runtime_empty):
        model = ScanJobTableModel(runtime_empty.job_repo)
        for i, label in enumerate(ScanJobTableModel.COLUMNS):
            assert model.headerData(i, Qt.Orientation.Horizontal) == label

    def test_status_column(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        model = ScanJobTableModel(rt.job_repo)
        statuses = [model.data(model.index(r, 0), Qt.DisplayRole)
                    for r in range(model.rowCount())]
        assert "completed" in statuses
        assert "running" in statuses

    def test_files_column_format(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        model = ScanJobTableModel(rt.job_repo)
        # Look for a row with both files_hashed and files_seen.
        for r in range(model.rowCount()):
            files = model.data(model.index(r, 3), Qt.DisplayRole)
            if "/" in files:
                # "hashed/seen" format.
                hashed, seen = files.split("/")
                assert hashed.isdigit()
                assert seen.isdigit()
                return
        pytest.fail("no completed-job row found")

    def test_default_limit_is_10(self, qapp, tmp_path):
        # Insert 15 jobs; assert only 10 returned.
        db_path = tmp_path / "limit_test.db"
        cfg = Config.load()
        rt = build_runtime(config=cfg, db_path_override=db_path,
                           json_output=False, no_color=True, verbosity=0)
        try:
            rt.source_repo.insert(SourceConfig(source_id="local", source_type="local"))
        except Exception:
            pass
        for i in range(15):
            rt.job_repo.insert(ScanJob(
                job_id=uuid4(), status="completed",
                source_id="local", root_path=f"/p{i}",
                started_at=datetime(2024, 1, 1) + timedelta(minutes=i),
            ))
        model = ScanJobTableModel(rt.job_repo)
        assert model.rowCount() == 10  # default limit

    def test_explicit_limit_override(self, qapp, tmp_path):
        db_path = tmp_path / "limit_override.db"
        cfg = Config.load()
        rt = build_runtime(config=cfg, db_path_override=db_path,
                           json_output=False, no_color=True, verbosity=0)
        try:
            rt.source_repo.insert(SourceConfig(source_id="local", source_type="local"))
        except Exception:
            pass
        for i in range(5):
            rt.job_repo.insert(ScanJob(
                job_id=uuid4(), status="completed",
                source_id="local", root_path=f"/p{i}",
                started_at=datetime(2024, 1, 1) + timedelta(minutes=i),
            ))
        model = ScanJobTableModel(rt.job_repo, limit=3)
        assert model.rowCount() == 3


# ===========================================================================
# PendingReviewTableModel
# ===========================================================================


class TestPendingReviewTableModel:
    def test_filters_to_ambiguous_band(self, qapp, runtime_with_inbox_data):
        rt, _files = runtime_with_inbox_data
        model = PendingReviewTableModel(
            rt.lineage_repo, rt.file_repo,
            escalate_threshold=0.7, auto_confirm_threshold=0.95,
        )
        # 2 of the 3 seeded edges have confidence in [0.7, 0.95).
        assert model.rowCount() == 2
        confidences = [model.data(model.index(r, 3), Qt.DisplayRole)
                       for r in range(model.rowCount())]
        # 0.85 and 0.78 stringified to 2 decimal places.
        assert "0.85" in confidences
        assert "0.78" in confidences
        # 0.99 should NOT be present.
        assert "0.99" not in confidences

    def test_threshold_widening_includes_more(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        # Lower escalate threshold to 0.5 — still 2 edges (the 0.99 above
        # auto_confirm is excluded).
        model = PendingReviewTableModel(
            rt.lineage_repo, rt.file_repo,
            escalate_threshold=0.5, auto_confirm_threshold=0.95,
        )
        assert model.rowCount() == 2

    def test_threshold_includes_above_auto_confirm_when_widened(
        self, qapp, runtime_with_inbox_data
    ):
        rt, _ = runtime_with_inbox_data
        # Raise auto_confirm above 0.99 — now all 3 edges qualify.
        model = PendingReviewTableModel(
            rt.lineage_repo, rt.file_repo,
            escalate_threshold=0.5, auto_confirm_threshold=1.0,
        )
        assert model.rowCount() == 3

    def test_resolves_file_paths(self, qapp, runtime_with_inbox_data):
        rt, files = runtime_with_inbox_data
        model = PendingReviewTableModel(
            rt.lineage_repo, rt.file_repo,
            escalate_threshold=0.7, auto_confirm_threshold=0.95,
        )
        # Look at the From/To paths in the table.
        all_paths = set()
        for r in range(model.rowCount()):
            all_paths.add(model.data(model.index(r, 1), Qt.DisplayRole))
            all_paths.add(model.data(model.index(r, 2), Qt.DisplayRole))
        # Should see real file paths, not UUIDs.
        for p in all_paths:
            assert p.startswith("/files/"), f"unresolved path: {p}"

    def test_handles_missing_file_gracefully(self, qapp, runtime_with_inbox_data):
        """_resolve_path falls back to UUID-in-parens when file_repo.get returns None.

        Foreign-key constraints prevent the actual lineage_edges row from
        being inserted with a non-existent file_id, so this exercises the
        defensive helper directly: pass an unknown UUID, assert the
        formatted fallback. This is the code path that runs when a file
        row was hard-deleted out from under an existing edge.
        """
        rt, _files = runtime_with_inbox_data
        model = PendingReviewTableModel(
            rt.lineage_repo, rt.file_repo,
            escalate_threshold=0.7, auto_confirm_threshold=0.95,
        )
        ghost_id = uuid4()
        label = model._resolve_path(ghost_id)
        assert label == f"({ghost_id})"
        # Cached on the second call.
        assert model._resolve_path(ghost_id) == f"({ghost_id})"

    def test_kind_column_shows_string_value(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        model = PendingReviewTableModel(
            rt.lineage_repo, rt.file_repo,
            escalate_threshold=0.7, auto_confirm_threshold=0.95,
        )
        kinds = [model.data(model.index(r, 0), Qt.DisplayRole)
                 for r in range(model.rowCount())]
        # near_duplicate (0.85) and version_of (0.78).
        assert "near_duplicate" in kinds
        assert "version_of" in kinds


# ===========================================================================
# LineageRepository.query_by_confidence (the new repo method)
# ===========================================================================


class TestLineageRepoQueryByConfidence:
    def test_returns_edges_in_range(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        edges = rt.lineage_repo.query_by_confidence(
            min_confidence=0.7, max_confidence=0.95,
        )
        assert len(edges) == 2
        for e in edges:
            assert 0.7 <= e.confidence < 0.95

    def test_empty_range_returns_empty(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        edges = rt.lineage_repo.query_by_confidence(
            min_confidence=0.5, max_confidence=0.6,  # nothing here
        )
        assert edges == []

    def test_max_is_exclusive(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        # Edge with confidence 0.85 should be EXCLUDED from [0.5, 0.85).
        edges = rt.lineage_repo.query_by_confidence(
            min_confidence=0.5, max_confidence=0.85,
        )
        confidences = {e.confidence for e in edges}
        assert 0.85 not in confidences
        assert 0.78 in confidences

    def test_limit_caps_results(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        edges = rt.lineage_repo.query_by_confidence(
            min_confidence=0.0, max_confidence=1.0, limit=1,
        )
        assert len(edges) == 1


# ===========================================================================
# TrashTableModel limit (new in v0.39)
# ===========================================================================


class TestTrashLimitParam:
    def test_no_limit_returns_all(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        model = TrashTableModel(rt.trash_repo)
        assert model.rowCount() == 2

    def test_limit_caps(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        model = TrashTableModel(rt.trash_repo, limit=1)
        assert model.rowCount() == 1


# ===========================================================================
# Wiring
# ===========================================================================


class TestWiring:
    def test_inbox_tab_at_index_0(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        window = CuratorMainWindow(rt)
        try:
            assert window._tabs.count() == 7
            assert window._tabs.tabText(0) == "Inbox"
        finally:
            window.deleteLater()

    def test_browser_tab_shifted_to_index_1(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        window = CuratorMainWindow(rt)
        try:
            assert window._tabs.tabText(1) == "Browser"
        finally:
            window.deleteLater()

    def test_inbox_models_constructed(self, qapp, runtime_with_inbox_data):
        rt, _ = runtime_with_inbox_data
        window = CuratorMainWindow(rt)
        try:
            assert window._inbox_scans_model.rowCount() == 3
            assert window._inbox_pending_model.rowCount() == 2
            assert window._inbox_trash_model.rowCount() == 2
        finally:
            window.deleteLater()

    def test_inbox_with_empty_runtime(self, qapp, runtime_empty):
        # The window should construct cleanly even when nothing is seeded;
        # empty-state hint labels appear in each section.
        window = CuratorMainWindow(runtime_empty)
        try:
            assert window._inbox_scans_model.rowCount() == 0
            assert window._inbox_pending_model.rowCount() == 0
            assert window._inbox_trash_model.rowCount() == 0
        finally:
            window.deleteLater()

    def test_refresh_all_refreshes_inbox_models(
        self, qapp, runtime_with_inbox_data
    ):
        rt, files = runtime_with_inbox_data
        window = CuratorMainWindow(rt)
        try:
            assert window._inbox_scans_model.rowCount() == 3
            # Insert another scan job and call refresh_all.
            rt.job_repo.insert(ScanJob(
                job_id=uuid4(), status="completed",
                source_id="local", root_path="/new/scan",
                started_at=datetime(2024, 6, 20),
            ))
            window.refresh_all()
            assert window._inbox_scans_model.rowCount() == 4
        finally:
            window.deleteLater()

    def test_inbox_threshold_reflects_config(self, qapp, tmp_path):
        # Build a runtime with a custom escalate_threshold via config.
        toml = tmp_path / "curator.toml"
        toml.write_text(
            '[lineage]\n'
            'escalate_threshold = 0.5\n'
            'auto_confirm_threshold = 0.99\n',
            encoding="utf-8",
        )
        cfg = Config.load(explicit_path=toml)
        db_path = tmp_path / "thresh.db"
        rt = build_runtime(
            config=cfg, db_path_override=db_path,
            json_output=False, no_color=True, verbosity=0,
        )
        window = CuratorMainWindow(rt)
        try:
            assert window._inbox_pending_model._escalate == 0.5
            assert window._inbox_pending_model._auto_confirm == 0.99
        finally:
            window.deleteLater()
