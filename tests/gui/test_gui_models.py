"""Tests for the v0.34 PySide6 GUI.

Two layers:

  * **Model tests** (this file in part) -- exercise the Qt table
    models against real (in-memory) repos. They need a single
    QApplication instance to satisfy QObject parent semantics, but
    no event loop is run.
  * **Smoke tests** -- launch the main window via pytest-qt's
    ``qtbot`` fixture, assert it shows + closes cleanly, never
    enter the modal exec loop. Tagged ``slow`` so they're opt-in.

All tests are skipped when PySide6 is unavailable.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

# Skip the entire file if PySide6 isn't available.
pyside6 = pytest.importorskip("PySide6")  # noqa: F841

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from curator.gui.models import (
    BundleTableModel,
    FileTableModel,
    TrashTableModel,
    _format_dt,
    _format_size,
)
from curator.models.bundle import BundleEntity
from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.models.trash import TrashRecord
from curator.storage import CuratorDB
from curator.storage.repositories.bundle_repo import BundleRepository
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.source_repo import SourceRepository
from curator.storage.repositories.trash_repo import TrashRepository


# ---------------------------------------------------------------------------
# Module-scoped QApplication (one per test session)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    """One QApplication per session (Qt's hard requirement)."""
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------------
# DB + repos seeded with realistic data
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "gui_test.db"
    db = CuratorDB(db_path)
    db.init()

    # Seed a source.
    source_repo = SourceRepository(db)
    source_repo.insert(SourceConfig(
        source_id="local", source_type="local", display_name="Local",
    ))

    # Seed three files.
    file_repo = FileRepository(db)
    files = []
    for i, (path, size, ext) in enumerate([
        ("/tmp/alpha.txt", 100, ".txt"),
        ("/tmp/bravo.py", 500, ".py"),
        ("/tmp/charlie.mp3", 5_000_000, ".mp3"),
    ]):
        f = FileEntity(
            curator_id=uuid4(), source_id="local",
            source_path=path, size=size,
            mtime=datetime(2024, 1, i + 1),
            extension=ext,
            xxhash3_128=f"hash{i:04x}" + "0" * 24,
        )
        file_repo.upsert(f)
        files.append(f)

    # Seed two bundles.
    bundle_repo = BundleRepository(db)
    from curator.models.bundle import BundleMembership
    bundle_a = BundleEntity(name="Alpha bundle", bundle_type="manual", confidence=1.0)
    bundle_repo.insert(bundle_a)
    bundle_repo.add_membership(BundleMembership(
        bundle_id=bundle_a.bundle_id, curator_id=files[0].curator_id,
        role="primary",
    ))
    bundle_b = BundleEntity(name="Bravo bundle", bundle_type="manual", confidence=0.85)
    bundle_repo.insert(bundle_b)
    bundle_repo.add_membership(BundleMembership(
        bundle_id=bundle_b.bundle_id, curator_id=files[1].curator_id, role="primary",
    ))
    bundle_repo.add_membership(BundleMembership(
        bundle_id=bundle_b.bundle_id, curator_id=files[2].curator_id, role="member",
    ))

    # Seed one trash record.
    trash_repo = TrashRepository(db)
    # We need a 4th file that's been deleted (trashed) for the trash record.
    deleted_file = FileEntity(
        curator_id=uuid4(), source_id="local",
        source_path="/tmp/deleted.tmp", size=42,
        mtime=datetime(2024, 1, 1),
        extension=".tmp",
    )
    file_repo.upsert(deleted_file)
    file_repo.mark_deleted(deleted_file.curator_id)
    trash_repo.insert(TrashRecord(
        curator_id=deleted_file.curator_id,
        original_source_id="local",
        original_path="/tmp/deleted.tmp",
        trashed_by="user",
        reason="testing",
    ))

    yield {
        "db": db,
        "file_repo": file_repo,
        "bundle_repo": bundle_repo,
        "trash_repo": trash_repo,
        "files": files,
    }


# ===========================================================================
# Helper formatters
# ===========================================================================


class TestFormatters:
    def test_format_size_under_1k(self):
        assert _format_size(500) == "500 B"

    def test_format_size_kb(self):
        assert _format_size(2048) == "2.0 KB"

    def test_format_size_mb(self):
        assert _format_size(5 * 1024 * 1024) == "5.0 MB"

    def test_format_size_none(self):
        assert _format_size(None) == ""

    def test_format_dt_compact(self):
        dt = datetime(2024, 7, 15, 14, 30)
        assert _format_dt(dt) == "2024-07-15 14:30"

    def test_format_dt_none(self):
        assert _format_dt(None) == ""


# ===========================================================================
# FileTableModel
# ===========================================================================


class TestFileTableModel:
    def test_loads_all_non_deleted_files(self, qapp, seeded_db):
        model = FileTableModel(seeded_db["file_repo"])
        # 3 active + 1 trashed; default skips trashed.
        assert model.rowCount() == 3
        # 6 columns.
        assert model.columnCount() == 6

    def test_columns_match_constant(self, qapp, seeded_db):
        model = FileTableModel(seeded_db["file_repo"])
        for i, label in enumerate(FileTableModel.COLUMNS):
            assert model.headerData(i, Qt.Orientation.Horizontal) == label

    def test_data_first_row(self, qapp, seeded_db):
        model = FileTableModel(seeded_db["file_repo"])
        # Sort to guarantee ordering: by path ascending.
        model.sort(1, Qt.SortOrder.AscendingOrder)
        # Row 0 should be alpha.txt.
        idx = model.index(0, 1)
        assert "alpha.txt" in model.data(idx, Qt.DisplayRole)
        # Size column shows formatted value.
        idx_size = model.index(0, 2)
        assert "100 B" == model.data(idx_size, Qt.DisplayRole)

    def test_sort_by_size_descending(self, qapp, seeded_db):
        model = FileTableModel(seeded_db["file_repo"])
        model.sort(2, Qt.SortOrder.DescendingOrder)
        # Largest first: charlie.mp3 (5 MB).
        first_path = model.data(model.index(0, 1), Qt.DisplayRole)
        assert "charlie.mp3" in first_path

    def test_tooltip_returns_full_path(self, qapp, seeded_db):
        model = FileTableModel(seeded_db["file_repo"])
        idx = model.index(0, 1)
        tooltip = model.data(idx, Qt.ToolTipRole)
        assert tooltip is not None
        assert tooltip.startswith("/tmp/")

    def test_invalid_index_returns_none(self, qapp, seeded_db):
        model = FileTableModel(seeded_db["file_repo"])
        # Out-of-range row.
        bad = model.index(99, 0)
        assert model.data(bad, Qt.DisplayRole) is None

    def test_refresh_picks_up_new_file(self, qapp, seeded_db, tmp_path):
        model = FileTableModel(seeded_db["file_repo"])
        original_count = model.rowCount()
        # Add a new file directly.
        new_file = FileEntity(
            curator_id=uuid4(), source_id="local",
            source_path="/tmp/new_one.txt", size=1,
            mtime=datetime(2024, 5, 1),
        )
        seeded_db["file_repo"].upsert(new_file)
        model.refresh()
        assert model.rowCount() == original_count + 1


# ===========================================================================
# BundleTableModel
# ===========================================================================


class TestBundleTableModel:
    def test_loads_all_bundles(self, qapp, seeded_db):
        model = BundleTableModel(seeded_db["bundle_repo"])
        assert model.rowCount() == 2
        assert model.columnCount() == 5

    def test_member_counts_correct(self, qapp, seeded_db):
        model = BundleTableModel(seeded_db["bundle_repo"])
        # Sort by name to make ordering deterministic.
        model.sort(0, Qt.SortOrder.AscendingOrder)
        # Bundle "Alpha bundle" has 1 member; "Bravo bundle" has 2.
        alpha_members = model.data(model.index(0, 2), Qt.DisplayRole)
        bravo_members = model.data(model.index(1, 2), Qt.DisplayRole)
        assert alpha_members == 1
        assert bravo_members == 2

    def test_confidence_formatted(self, qapp, seeded_db):
        model = BundleTableModel(seeded_db["bundle_repo"])
        model.sort(0, Qt.SortOrder.AscendingOrder)
        # Bravo bundle has confidence 0.85.
        bravo_conf = model.data(model.index(1, 3), Qt.DisplayRole)
        assert bravo_conf == "0.85"


# ===========================================================================
# TrashTableModel
# ===========================================================================


class TestTrashTableModel:
    def test_loads_trash_records(self, qapp, seeded_db):
        model = TrashTableModel(seeded_db["trash_repo"])
        assert model.rowCount() == 1
        assert model.columnCount() == 5

    def test_data_includes_reason(self, qapp, seeded_db):
        model = TrashTableModel(seeded_db["trash_repo"])
        reason = model.data(model.index(0, 2), Qt.DisplayRole)
        assert reason == "testing"

    def test_invalid_index(self, qapp, seeded_db):
        model = TrashTableModel(seeded_db["trash_repo"])
        bad = model.index(5, 0)
        assert model.data(bad, Qt.DisplayRole) is None


# ===========================================================================
# Launcher
# ===========================================================================


class TestLauncher:
    def test_is_pyside6_available_returns_true(self):
        # PySide6 is installed in this test env.
        from curator.gui.launcher import is_pyside6_available
        assert is_pyside6_available() is True


# ===========================================================================
# Main window smoke test (opt-in via slow marker)
# ===========================================================================


@pytest.mark.slow
class TestMainWindowSmoke:
    """Boot the QMainWindow but never enter its event loop.

    These tests use pytest-qt's ``qtbot`` fixture which manages the
    QApplication lifecycle and ensures widgets are properly cleaned up.
    """

    def test_window_opens_with_runtime(self, qtbot, seeded_db):
        """Construct, show, close; assert no exceptions raised."""
        from curator.gui.main_window import CuratorMainWindow

        # Build a minimal runtime-shaped object that satisfies what
        # CuratorMainWindow reads (.file_repo, .bundle_repo, .trash_repo, .db).
        class _StubRuntime:
            pass
        rt = _StubRuntime()
        rt.file_repo = seeded_db["file_repo"]
        rt.bundle_repo = seeded_db["bundle_repo"]
        rt.trash_repo = seeded_db["trash_repo"]
        rt.db = seeded_db["db"]

        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        window.show()
        # Sanity: title was set, status bar exists.
        assert "Curator" in window.windowTitle()
        # All three tabs registered.
        assert window._tabs.count() == 3
        # Refresh shouldn't crash.
        window.refresh_all()
        # Status bar got updated.
        assert window._status_db.text().startswith("DB:")
