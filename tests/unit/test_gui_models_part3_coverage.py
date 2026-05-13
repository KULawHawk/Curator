"""Coverage for ``curator.gui.models`` Part 3 + close (v1.7.190).

Round 4 Tier 2 sub-ship 6 of 6 — covers the 4 remaining Qt table
models (``ScanJobTableModel``, ``PendingReviewTableModel``,
``MigrationJobTableModel``, ``MigrationProgressTableModel``) and the
``_format_duration`` helper. Combined with Parts 1 + 2 this closes
``gui/models.py`` at 100% line + branch.

Pattern from Parts 1 + 2 reused (Lesson #84): stub-repo + createIndex
+ role-parameterized data + sort with None values + 99-column fallback.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)


# ===========================================================================
# _format_duration helper
# ===========================================================================


class TestFormatDuration:
    @pytest.mark.parametrize("inp,expected", [
        (None, ""),
        (0.0, "0.0s"),
        (4.2, "4.2s"),
        (59.9, "59.9s"),
        (60.0, "1m 00s"),
        (83.0, "1m 23s"),
        (3599.0, "59m 59s"),
        (3600.0, "1h 00m"),
        (7565.0, "2h 06m"),
    ])
    def test_format_duration_buckets(self, inp, expected):
        from curator.gui.models import _format_duration
        assert _format_duration(inp) == expected


# ===========================================================================
# ScanJobTableModel
# ===========================================================================


def _make_scan_job(
    *, status="completed", source_id="local", root_path="/r",
    files_seen=100, files_hashed=80, started_at=None, completed_at=None,
):
    j = MagicMock()
    j.status = status
    j.source_id = source_id
    j.root_path = root_path
    j.files_seen = files_seen
    j.files_hashed = files_hashed
    j.started_at = started_at or datetime(2026, 5, 1, 10, 0)
    j.completed_at = completed_at or datetime(2026, 5, 1, 10, 5)
    return j


class TestScanJobTableModel:
    def test_init_uses_default_limit(self, qapp):
        from curator.gui.models import ScanJobTableModel
        repo = MagicMock()
        repo.list_recent.return_value = []
        ScanJobTableModel(repo)
        repo.list_recent.assert_called_with(limit=ScanJobTableModel.DEFAULT_LIMIT)

    def test_init_custom_limit(self, qapp):
        from curator.gui.models import ScanJobTableModel
        repo = MagicMock()
        repo.list_recent.return_value = []
        ScanJobTableModel(repo, limit=5)
        repo.list_recent.assert_called_with(limit=5)

    def test_init_failure_falls_back(self, qapp):
        from curator.gui.models import ScanJobTableModel
        repo = MagicMock()
        repo.list_recent.side_effect = RuntimeError("db gone")
        m = ScanJobTableModel(repo)
        assert m.rowCount() == 0

    def test_job_at(self, qapp):
        from curator.gui.models import ScanJobTableModel
        repo = MagicMock()
        repo.list_recent.return_value = [_make_scan_job()]
        m = ScanJobTableModel(repo)
        assert m.job_at(0) is not None
        assert m.job_at(99) is None

    def test_row_and_column_counts(self, qapp):
        from curator.gui.models import ScanJobTableModel
        repo = MagicMock()
        repo.list_recent.return_value = [_make_scan_job()]
        m = ScanJobTableModel(repo)
        assert m.rowCount() == 1
        assert m.columnCount() == 6
        idx = m.index(0, 0)
        assert m.rowCount(idx) == 0
        assert m.columnCount(idx) == 0

    def test_header_data_branches(self, qapp):
        from curator.gui.models import ScanJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_recent.return_value = []
        m = ScanJobTableModel(repo)
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.DisplayRole) == "Status"
        assert m.headerData(99, Qt.Orientation.Horizontal, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Vertical, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.EditRole) is None

    def test_data_all_columns_with_both_counts(self, qapp):
        """files_hashed + files_seen both populated → '80/100' display."""
        from curator.gui.models import ScanJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        j = _make_scan_job(
            status="completed", source_id="local", root_path="/r",
            files_seen=100, files_hashed=80,
            started_at=datetime(2026, 5, 1, 10, 0),
            completed_at=datetime(2026, 5, 1, 10, 5),
        )
        repo.list_recent.return_value = [j]
        m = ScanJobTableModel(repo)
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "completed"
        assert m.data(m.index(0, 1), Qt.DisplayRole) == "local"
        assert m.data(m.index(0, 2), Qt.DisplayRole) == "/r"
        assert m.data(m.index(0, 3), Qt.DisplayRole) == "80/100"
        assert m.data(m.index(0, 4), Qt.DisplayRole) == "2026-05-01 10:00"
        assert m.data(m.index(0, 5), Qt.DisplayRole) == "2026-05-01 10:05"

    def test_data_files_only_seen(self, qapp):
        """files_hashed = 0/None but files_seen present → just the seen count."""
        from curator.gui.models import ScanJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        j = _make_scan_job(files_seen=50, files_hashed=0)
        repo.list_recent.return_value = [j]
        m = ScanJobTableModel(repo)
        assert m.data(m.index(0, 3), Qt.DisplayRole) == "50"

    def test_data_files_neither_set(self, qapp):
        """Both files_seen and files_hashed = 0 → empty."""
        from curator.gui.models import ScanJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        j = _make_scan_job(files_seen=0, files_hashed=0)
        repo.list_recent.return_value = [j]
        m = ScanJobTableModel(repo)
        assert m.data(m.index(0, 3), Qt.DisplayRole) == ""

    def test_data_invalid_and_role_short_circuits(self, qapp):
        from curator.gui.models import ScanJobTableModel
        from PySide6.QtCore import QModelIndex, Qt
        repo = MagicMock()
        repo.list_recent.return_value = [_make_scan_job()]
        m = ScanJobTableModel(repo)
        assert m.data(QModelIndex(), Qt.DisplayRole) is None
        assert m.data(m.index(0, 0), Qt.EditRole) is None

    def test_data_row_out_of_range(self, qapp):
        from curator.gui.models import ScanJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_recent.return_value = [_make_scan_job()]
        m = ScanJobTableModel(repo)
        idx = m.createIndex(99, 0)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_data_column_fallback(self, qapp):
        from curator.gui.models import ScanJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_recent.return_value = [_make_scan_job()]
        m = ScanJobTableModel(repo)
        idx = m.createIndex(0, 99)
        assert m.data(idx, Qt.DisplayRole) is None


# ===========================================================================
# PendingReviewTableModel
# ===========================================================================


def _make_lineage_edge(
    *, from_id=None, to_id=None, edge_kind="version_of",
    confidence=0.80, detected_by="hash",
):
    e = MagicMock()
    e.from_curator_id = from_id or uuid4()
    e.to_curator_id = to_id or uuid4()
    # edge_kind: real lineage uses an Enum; emulate via simple .value
    if isinstance(edge_kind, str):
        kind = MagicMock()
        kind.value = edge_kind
        e.edge_kind = kind
    else:
        e.edge_kind = edge_kind
    e.confidence = confidence
    e.detected_by = detected_by
    return e


class TestPendingReviewTableModel:
    def test_init_with_default_thresholds(self, qapp):
        from curator.gui.models import PendingReviewTableModel
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = []
        file_repo = MagicMock()
        m = PendingReviewTableModel(lineage_repo, file_repo)
        # Default thresholds + default limit
        lineage_repo.query_by_confidence.assert_called_with(
            min_confidence=0.7, max_confidence=0.95, limit=50,
        )
        assert m.rowCount() == 0

    def test_init_custom_thresholds_and_limit(self, qapp):
        from curator.gui.models import PendingReviewTableModel
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = []
        file_repo = MagicMock()
        PendingReviewTableModel(
            lineage_repo, file_repo,
            escalate_threshold=0.5, auto_confirm_threshold=0.99, limit=10,
        )
        lineage_repo.query_by_confidence.assert_called_with(
            min_confidence=0.5, max_confidence=0.99, limit=10,
        )

    def test_init_failure_falls_back(self, qapp):
        from curator.gui.models import PendingReviewTableModel
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.side_effect = RuntimeError("db gone")
        file_repo = MagicMock()
        m = PendingReviewTableModel(lineage_repo, file_repo)
        assert m.rowCount() == 0

    def test_edge_at(self, qapp):
        from curator.gui.models import PendingReviewTableModel
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = [_make_lineage_edge()]
        file_repo = MagicMock()
        m = PendingReviewTableModel(lineage_repo, file_repo)
        assert m.edge_at(0) is not None
        assert m.edge_at(99) is None

    def test_row_and_column_counts(self, qapp):
        from curator.gui.models import PendingReviewTableModel
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = [_make_lineage_edge()]
        file_repo = MagicMock()
        m = PendingReviewTableModel(lineage_repo, file_repo)
        assert m.rowCount() == 1
        assert m.columnCount() == 5
        idx = m.index(0, 0)
        assert m.rowCount(idx) == 0
        assert m.columnCount(idx) == 0

    def test_header_data_branches(self, qapp):
        from curator.gui.models import PendingReviewTableModel
        from PySide6.QtCore import Qt
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = []
        file_repo = MagicMock()
        m = PendingReviewTableModel(lineage_repo, file_repo)
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.DisplayRole) == "Kind"
        assert m.headerData(99, Qt.Orientation.Horizontal, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Vertical, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.EditRole) is None

    def test_data_all_columns_with_value_enum(self, qapp):
        """edge_kind has .value (Enum-like) → uses it."""
        from curator.gui.models import PendingReviewTableModel
        from PySide6.QtCore import Qt
        from_id, to_id = uuid4(), uuid4()
        edge = _make_lineage_edge(
            from_id=from_id, to_id=to_id,
            edge_kind="version_of", confidence=0.80, detected_by="hash",
        )
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = [edge]
        file_repo = MagicMock()
        # file_repo.get returns a file with source_path
        file_repo.get.side_effect = lambda fid: MagicMock(
            source_path=f"/p/{fid.hex[:6]}.txt"
        )
        m = PendingReviewTableModel(lineage_repo, file_repo)
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "version_of"
        # From and To columns resolve to file paths
        assert m.data(m.index(0, 1), Qt.DisplayRole).startswith("/p/")
        assert m.data(m.index(0, 2), Qt.DisplayRole).startswith("/p/")
        assert m.data(m.index(0, 3), Qt.DisplayRole) == "0.80"
        assert m.data(m.index(0, 4), Qt.DisplayRole) == "hash"

    def test_data_edge_kind_without_value_uses_str(self, qapp):
        """edge_kind is a plain string (no .value) → uses str()."""
        from curator.gui.models import PendingReviewTableModel
        from PySide6.QtCore import Qt
        edge = _make_lineage_edge()
        edge.edge_kind = "literal_string_kind"  # no .value attribute
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = [edge]
        file_repo = MagicMock()
        file_repo.get.return_value = MagicMock(source_path="/p")
        m = PendingReviewTableModel(lineage_repo, file_repo)
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "literal_string_kind"

    def test_data_file_unresolvable_uses_uuid_string(self, qapp):
        """file_repo.get returns None → label is `(uuid)`."""
        from curator.gui.models import PendingReviewTableModel
        from PySide6.QtCore import Qt
        edge = _make_lineage_edge()
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = [edge]
        file_repo = MagicMock()
        file_repo.get.return_value = None
        m = PendingReviewTableModel(lineage_repo, file_repo)
        out = m.data(m.index(0, 1), Qt.DisplayRole)
        assert out.startswith("(") and out.endswith(")")

    def test_data_file_repo_exception_falls_back_to_uuid(self, qapp):
        """file_repo.get raises → label is `(uuid)`."""
        from curator.gui.models import PendingReviewTableModel
        from PySide6.QtCore import Qt
        edge = _make_lineage_edge()
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = [edge]
        file_repo = MagicMock()
        file_repo.get.side_effect = RuntimeError("db lost")
        m = PendingReviewTableModel(lineage_repo, file_repo)
        out = m.data(m.index(0, 1), Qt.DisplayRole)
        assert out.startswith("(") and out.endswith(")")

    def test_data_invalid_and_role_short_circuits(self, qapp):
        from curator.gui.models import PendingReviewTableModel
        from PySide6.QtCore import QModelIndex, Qt
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = [_make_lineage_edge()]
        file_repo = MagicMock()
        m = PendingReviewTableModel(lineage_repo, file_repo)
        assert m.data(QModelIndex(), Qt.DisplayRole) is None
        assert m.data(m.index(0, 0), Qt.EditRole) is None

    def test_data_row_out_of_range_and_column_fallback(self, qapp):
        from curator.gui.models import PendingReviewTableModel
        from PySide6.QtCore import Qt
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = [_make_lineage_edge()]
        file_repo = MagicMock()
        file_repo.get.return_value = MagicMock(source_path="/p")
        m = PendingReviewTableModel(lineage_repo, file_repo)
        assert m.data(m.createIndex(99, 0), Qt.DisplayRole) is None
        assert m.data(m.createIndex(0, 99), Qt.DisplayRole) is None

    def test_resolve_path_caches(self, qapp):
        """_resolve_path stores results in _path_cache to avoid re-fetch."""
        from curator.gui.models import PendingReviewTableModel
        from PySide6.QtCore import Qt
        same_id = uuid4()
        edge1 = _make_lineage_edge(from_id=same_id, to_id=uuid4())
        edge2 = _make_lineage_edge(from_id=uuid4(), to_id=same_id)
        lineage_repo = MagicMock()
        lineage_repo.query_by_confidence.return_value = [edge1, edge2]
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: MagicMock(
            source_path=f"/p/{fid.hex[:6]}",
        )
        m = PendingReviewTableModel(lineage_repo, file_repo)
        # Reading row 0 col 1 + row 1 col 2 = both same_id
        m.data(m.index(0, 1), Qt.DisplayRole)
        m.data(m.index(1, 2), Qt.DisplayRole)
        # file_repo.get is called once for same_id (plus once for each
        # other unique UUID in the edges)
        ids_seen = {c.args[0] for c in file_repo.get.call_args_list}
        assert same_id in ids_seen
        # Cached: count for same_id is 1
        same_id_calls = sum(1 for c in file_repo.get.call_args_list
                            if c.args[0] == same_id)
        assert same_id_calls == 1


# ===========================================================================
# MigrationJobTableModel
# ===========================================================================


def _make_migration_job(
    *, status="completed",
    src_source_id="local", dst_source_id="gdrive",
    src_root="/src", dst_root="/dst",
    files_total=100, files_copied=80, files_failed=2,
    bytes_copied=2048, started_at=None,
    duration_seconds=120.0, error=None,
):
    j = MagicMock()
    j.status = status
    j.src_source_id = src_source_id
    j.dst_source_id = dst_source_id
    j.src_root = src_root
    j.dst_root = dst_root
    j.files_total = files_total
    j.files_copied = files_copied
    j.files_failed = files_failed
    j.bytes_copied = bytes_copied
    j.started_at = started_at or datetime(2026, 5, 1, 10, 0)
    j.duration_seconds = duration_seconds
    j.error = error
    return j


class TestMigrationJobTableModel:
    def test_init_default_limit(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        repo = MagicMock()
        repo.list_jobs.return_value = []
        MigrationJobTableModel(repo)
        repo.list_jobs.assert_called_with(limit=MigrationJobTableModel.DEFAULT_LIMIT)

    def test_init_custom_limit(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        repo = MagicMock()
        repo.list_jobs.return_value = []
        MigrationJobTableModel(repo, limit=5)
        repo.list_jobs.assert_called_with(limit=5)

    def test_init_failure_falls_back(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        repo = MagicMock()
        repo.list_jobs.side_effect = RuntimeError("db gone")
        m = MigrationJobTableModel(repo)
        assert m.rowCount() == 0

    def test_job_at(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        repo = MagicMock()
        repo.list_jobs.return_value = [_make_migration_job()]
        m = MigrationJobTableModel(repo)
        assert m.job_at(0) is not None
        assert m.job_at(99) is None

    def test_row_and_column_counts(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        repo = MagicMock()
        repo.list_jobs.return_value = [_make_migration_job()]
        m = MigrationJobTableModel(repo)
        assert m.rowCount() == 1
        assert m.columnCount() == 8
        idx = m.index(0, 0)
        assert m.rowCount(idx) == 0
        assert m.columnCount(idx) == 0

    def test_header_data_branches(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_jobs.return_value = []
        m = MigrationJobTableModel(repo)
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.DisplayRole) == "Status"
        assert m.headerData(99, Qt.Orientation.Horizontal, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Vertical, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.EditRole) is None

    def test_data_display_role_all_columns(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        j = _make_migration_job(
            status="completed",
            src_source_id="local", dst_source_id="gdrive",
            files_total=10, files_copied=8, files_failed=1,
            bytes_copied=2048, duration_seconds=83.0,
            started_at=datetime(2026, 5, 1, 10, 0),
        )
        repo.list_jobs.return_value = [j]
        m = MigrationJobTableModel(repo)
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "completed"
        assert m.data(m.index(0, 1), Qt.DisplayRole) == "local → gdrive"
        assert m.data(m.index(0, 2), Qt.DisplayRole) == 10
        assert m.data(m.index(0, 3), Qt.DisplayRole) == 8
        assert m.data(m.index(0, 4), Qt.DisplayRole) == 1
        assert m.data(m.index(0, 5), Qt.DisplayRole) == "2.0 KB"
        assert m.data(m.index(0, 6), Qt.DisplayRole) == "2026-05-01 10:00"
        assert m.data(m.index(0, 7), Qt.DisplayRole) == "1m 23s"

    def test_data_tooltip_on_src_dst_column(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        j = _make_migration_job(
            src_source_id="A", dst_source_id="B",
            src_root="/srcroot", dst_root="/dstroot",
        )
        repo.list_jobs.return_value = [j]
        m = MigrationJobTableModel(repo)
        tip = m.data(m.index(0, 1), Qt.ToolTipRole)
        assert tip is not None
        assert "/srcroot" in tip
        assert "/dstroot" in tip

    def test_data_tooltip_on_status_when_error(self, qapp):
        """Tooltip on Status col returns the error if set."""
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        j = _make_migration_job(error="disk full")
        repo.list_jobs.return_value = [j]
        m = MigrationJobTableModel(repo)
        assert m.data(m.index(0, 0), Qt.ToolTipRole) == "disk full"

    def test_data_tooltip_on_status_no_error(self, qapp):
        """Tooltip on Status col returns None if no error set."""
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        j = _make_migration_job(error=None)
        repo.list_jobs.return_value = [j]
        m = MigrationJobTableModel(repo)
        assert m.data(m.index(0, 0), Qt.ToolTipRole) is None

    def test_data_tooltip_other_columns_none(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_jobs.return_value = [_make_migration_job()]
        m = MigrationJobTableModel(repo)
        # Tooltip on columns other than 0/1 returns None
        assert m.data(m.index(0, 2), Qt.ToolTipRole) is None

    def test_data_invalid_and_role_short_circuits(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import QModelIndex, Qt
        repo = MagicMock()
        repo.list_jobs.return_value = [_make_migration_job()]
        m = MigrationJobTableModel(repo)
        assert m.data(QModelIndex(), Qt.DisplayRole) is None
        # EditRole short-circuits after the tooltip branch
        assert m.data(m.index(0, 0), Qt.EditRole) is None

    def test_data_row_out_of_range_and_column_fallback(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_jobs.return_value = [_make_migration_job()]
        m = MigrationJobTableModel(repo)
        assert m.data(m.createIndex(99, 0), Qt.DisplayRole) is None
        assert m.data(m.createIndex(0, 99), Qt.DisplayRole) is None

    def test_sort_each_column(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_jobs.return_value = [
            _make_migration_job(status="b", files_total=100, started_at=datetime(2026, 5, 5)),
            _make_migration_job(status="a", files_total=50, started_at=datetime(2026, 5, 1)),
        ]
        m = MigrationJobTableModel(repo)
        for col in range(8):
            m.sort(col, Qt.SortOrder.AscendingOrder)
        m.sort(99, Qt.SortOrder.DescendingOrder)

    def test_sort_handles_none_values(self, qapp):
        from curator.gui.models import MigrationJobTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        j_partial = _make_migration_job(
            status=None, started_at=None, duration_seconds=None,
        )
        repo.list_jobs.return_value = [j_partial, _make_migration_job()]
        m = MigrationJobTableModel(repo)
        for col in range(8):
            m.sort(col, Qt.SortOrder.AscendingOrder)


# ===========================================================================
# MigrationProgressTableModel
# ===========================================================================


def _make_migration_progress(
    *, status="copied", outcome="ok",
    src_path="/src/f.txt", dst_path="/dst/f.txt",
    size=1024, verified_xxhash=None, error=None,
):
    p = MagicMock()
    p.status = status
    p.outcome = outcome
    p.src_path = src_path
    p.dst_path = dst_path
    p.size = size
    p.verified_xxhash = verified_xxhash
    p.error = error
    return p


class TestMigrationProgressTableModel:
    def test_init_with_job_id(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        repo = MagicMock()
        repo.query_progress.return_value = [_make_migration_progress()]
        job_id = uuid4()
        m = MigrationProgressTableModel(repo, job_id=job_id)
        assert m.job_id == job_id
        assert m.rowCount() == 1
        repo.query_progress.assert_called_with(job_id)

    def test_init_without_job_id_loads_empty(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        repo = MagicMock()
        m = MigrationProgressTableModel(repo)
        # No job_id → no query, empty rows
        assert m.rowCount() == 0
        repo.query_progress.assert_not_called()

    def test_init_failure_falls_back(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        repo = MagicMock()
        repo.query_progress.side_effect = RuntimeError("db gone")
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.rowCount() == 0

    def test_set_job_id(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        repo = MagicMock()
        repo.query_progress.return_value = [_make_migration_progress()]
        m = MigrationProgressTableModel(repo)
        # Initially empty
        assert m.rowCount() == 0
        # Set job_id triggers refresh
        new_id = uuid4()
        m.set_job_id(new_id)
        assert m.job_id == new_id
        assert m.rowCount() == 1

    def test_set_job_id_to_none_clears(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        repo = MagicMock()
        repo.query_progress.return_value = [_make_migration_progress()]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.rowCount() == 1
        # Clear via set_job_id(None)
        m.set_job_id(None)
        assert m.job_id is None
        assert m.rowCount() == 0

    def test_progress_at(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        repo = MagicMock()
        repo.query_progress.return_value = [_make_migration_progress()]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.progress_at(0) is not None
        assert m.progress_at(99) is None

    def test_row_and_column_counts(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        repo = MagicMock()
        repo.query_progress.return_value = [_make_migration_progress()]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.rowCount() == 1
        assert m.columnCount() == 5
        idx = m.index(0, 0)
        assert m.rowCount(idx) == 0
        assert m.columnCount(idx) == 0

    def test_header_data_branches(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        m = MigrationProgressTableModel(repo)
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.DisplayRole) == "Status"
        assert m.headerData(99, Qt.Orientation.Horizontal, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Vertical, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.EditRole) is None

    def test_data_display_role_all_columns(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        p = _make_migration_progress(
            status="copied", outcome="ok",
            src_path="/src/file.txt", size=4096,
            verified_xxhash="abcdef0123456789long",
        )
        repo.query_progress.return_value = [p]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "copied"
        assert m.data(m.index(0, 1), Qt.DisplayRole) == "ok"
        assert m.data(m.index(0, 2), Qt.DisplayRole) == "/src/file.txt"
        assert m.data(m.index(0, 3), Qt.DisplayRole) == "4.0 KB"
        # Hash truncated to 12 chars + ellipsis (uses … char)
        result = m.data(m.index(0, 4), Qt.DisplayRole)
        assert result == "abcdef012345…"

    def test_data_outcome_none_returns_empty(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        p = _make_migration_progress(outcome=None)
        repo.query_progress.return_value = [p]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.data(m.index(0, 1), Qt.DisplayRole) == ""

    def test_data_verified_hash_short_form(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        p_short = _make_migration_progress(verified_xxhash="abc")
        p_empty = _make_migration_progress(verified_xxhash=None)
        repo.query_progress.return_value = [p_short, p_empty]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.data(m.index(0, 4), Qt.DisplayRole) == "abc"
        assert m.data(m.index(1, 4), Qt.DisplayRole) == ""

    def test_data_tooltip_on_src_path_col(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        p = _make_migration_progress(
            src_path="/src/full.txt", dst_path="/dst/full.txt",
        )
        repo.query_progress.return_value = [p]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        tip = m.data(m.index(0, 2), Qt.ToolTipRole)
        assert tip is not None
        assert "/src/full.txt" in tip
        assert "/dst/full.txt" in tip

    def test_data_tooltip_on_outcome_when_error(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        p = _make_migration_progress(error="boom")
        repo.query_progress.return_value = [p]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.data(m.index(0, 1), Qt.ToolTipRole) == "boom"

    def test_data_tooltip_outcome_no_error_returns_none(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        p = _make_migration_progress(error=None)
        repo.query_progress.return_value = [p]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.data(m.index(0, 1), Qt.ToolTipRole) is None

    def test_data_tooltip_other_columns_none(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query_progress.return_value = [_make_migration_progress()]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.data(m.index(0, 0), Qt.ToolTipRole) is None

    def test_data_invalid_and_role_short_circuits(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import QModelIndex, Qt
        repo = MagicMock()
        repo.query_progress.return_value = [_make_migration_progress()]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.data(QModelIndex(), Qt.DisplayRole) is None
        assert m.data(m.index(0, 0), Qt.EditRole) is None

    def test_data_row_out_of_range_and_column_fallback(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query_progress.return_value = [_make_migration_progress()]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        assert m.data(m.createIndex(99, 0), Qt.DisplayRole) is None
        assert m.data(m.createIndex(0, 99), Qt.DisplayRole) is None

    def test_sort_each_column(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query_progress.return_value = [
            _make_migration_progress(src_path="/z"),
            _make_migration_progress(src_path="/a"),
        ]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        for col in range(5):
            m.sort(col, Qt.SortOrder.AscendingOrder)
        m.sort(99, Qt.SortOrder.DescendingOrder)

    def test_sort_handles_none_values(self, qapp):
        from curator.gui.models import MigrationProgressTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        p_partial = _make_migration_progress(
            status=None, outcome=None, src_path=None, verified_xxhash=None,
        )
        repo.query_progress.return_value = [p_partial, _make_migration_progress()]
        m = MigrationProgressTableModel(repo, job_id=uuid4())
        for col in range(5):
            m.sort(col, Qt.SortOrder.AscendingOrder)


# ===========================================================================
# Module __all__ sanity (arc-close check)
# ===========================================================================


class TestModuleExports:
    def test_all_models_exported(self):
        from curator.gui import models
        for name in (
            "FileTableModel", "BundleTableModel", "TrashTableModel",
            "AuditLogTableModel", "ConfigTableModel",
            "ScanJobTableModel", "PendingReviewTableModel",
            "MigrationJobTableModel", "MigrationProgressTableModel",
        ):
            assert name in models.__all__
