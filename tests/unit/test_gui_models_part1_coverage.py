"""Coverage for ``curator.gui.models`` Part 1 (v1.7.188).

Round 4 Tier 2 sub-ship 4 of 6 — covers the module-level helpers
(``_format_size``, ``_format_dt``) plus the first three Qt table
models (`FileTableModel`, `BundleTableModel`, `TrashTableModel`).
Parts 2 + 3 cover the remaining 6 models + helper.

Each model exposes the same Qt protocol so this file establishes the
canonical test pattern: stub the repo, construct the model, drive
``rowCount`` / ``columnCount`` / ``headerData`` / ``data`` /
``sort`` / accessor directly via the Qt API.
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
# Module-level helpers
# ===========================================================================


class TestFormatSize:
    @pytest.mark.parametrize("inp,expected", [
        (None, ""),
        (0, "0 B"),
        (512, "512 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1024 * 1024, "1.0 MB"),
        (1024 ** 3, "1.0 GB"),
        (1024 ** 4, "1.0 TB"),
        # PB branch: the loop exits after TB without one more division, so
        # 1024^5 reports as "1024.0 PB" — the function's behavior, not a
        # display bug we're fixing here.
        (1024 ** 5, "1024.0 PB"),
    ])
    def test_format_size_buckets(self, inp, expected):
        from curator.gui.models import _format_size
        assert _format_size(inp) == expected


class TestFormatDt:
    def test_none(self):
        from curator.gui.models import _format_dt
        assert _format_dt(None) == ""

    def test_datetime_formatted(self):
        from curator.gui.models import _format_dt
        assert _format_dt(datetime(2026, 5, 13, 14, 30)) == "2026-05-13 14:30"


# ===========================================================================
# FileTableModel
# ===========================================================================


def _make_file_entity(*, path="/p/file.txt", source="local", size=1024,
                     mtime=None, extension="txt", xxhash3=None):
    """Build a FileEntity stub via MagicMock (avoids pydantic
    validate_assignment quirks)."""
    f = MagicMock()
    f.source_id = source
    f.source_path = path
    f.size = size
    f.mtime = mtime if mtime else datetime(2026, 5, 1, 12, 0)
    f.extension = extension
    f.xxhash3_128 = xxhash3
    return f


class TestFileTableModel:
    def test_init_loads_rows(self, qapp):
        from curator.gui.models import FileTableModel
        repo = MagicMock()
        repo.query.return_value = [
            _make_file_entity(path="/a.txt"),
            _make_file_entity(path="/b.txt"),
        ]
        m = FileTableModel(repo)
        assert m.rowCount() == 2
        assert m.columnCount() == 6

    def test_init_failure_falls_back_to_empty(self, qapp):
        from curator.gui.models import FileTableModel
        repo = MagicMock()
        repo.query.side_effect = RuntimeError("db gone")
        m = FileTableModel(repo)
        assert m.rowCount() == 0

    def test_include_deleted_flag(self, qapp):
        from curator.gui.models import FileTableModel
        repo = MagicMock()
        repo.query.return_value = []
        FileTableModel(repo, include_deleted=True)
        # Check the query was called with deleted=None (no filter)
        q_arg = repo.query.call_args[0][0]
        assert q_arg.deleted is None

    def test_include_deleted_default_false(self, qapp):
        from curator.gui.models import FileTableModel
        repo = MagicMock()
        repo.query.return_value = []
        FileTableModel(repo)
        q_arg = repo.query.call_args[0][0]
        assert q_arg.deleted is False

    def test_row_count_with_valid_parent_is_zero(self, qapp):
        """Qt convention: child rows of a non-root parent = 0."""
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import QModelIndex
        repo = MagicMock()
        repo.query.return_value = [_make_file_entity()]
        m = FileTableModel(repo)
        # An invalid parent => rowCount of all rows
        assert m.rowCount(QModelIndex()) == 1
        # Construct a valid index and check that as parent => 0
        idx = m.index(0, 0)
        assert idx.isValid()
        assert m.rowCount(idx) == 0
        assert m.columnCount(idx) == 0

    def test_file_at_in_range_and_out_of_range(self, qapp):
        from curator.gui.models import FileTableModel
        repo = MagicMock()
        repo.query.return_value = [_make_file_entity(path="/x.txt")]
        m = FileTableModel(repo)
        assert m.file_at(0) is not None
        assert m.file_at(99) is None
        assert m.file_at(-1) is None

    def test_header_data_horizontal_returns_column_names(self, qapp):
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = []
        m = FileTableModel(repo)
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.DisplayRole) == "Source"
        assert m.headerData(5, Qt.Orientation.Horizontal, Qt.DisplayRole) == "xxhash3 (short)"
        # Out of range
        assert m.headerData(99, Qt.Orientation.Horizontal, Qt.DisplayRole) is None
        # Vertical orientation (e.g. row numbers) returns None
        assert m.headerData(0, Qt.Orientation.Vertical, Qt.DisplayRole) is None
        # Non-display role returns None
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.ToolTipRole) is None

    def test_data_all_columns_display_role(self, qapp):
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        f = _make_file_entity(
            source="local", path="/foo/bar.pdf", size=2048,
            mtime=datetime(2026, 1, 2, 3, 4), extension="pdf",
            xxhash3="abcdef0123456789longhash",
        )
        repo.query.return_value = [f]
        m = FileTableModel(repo)
        # Source / Path / Size / Modified / Ext / xxhash3
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "local"
        assert m.data(m.index(0, 1), Qt.DisplayRole) == "/foo/bar.pdf"
        assert m.data(m.index(0, 2), Qt.DisplayRole) == "2.0 KB"
        assert m.data(m.index(0, 3), Qt.DisplayRole) == "2026-01-02 03:04"
        assert m.data(m.index(0, 4), Qt.DisplayRole) == "pdf"
        assert m.data(m.index(0, 5), Qt.DisplayRole) == "abcdef012345..."

    def test_data_tooltip_on_path_column(self, qapp):
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        f = _make_file_entity(path="/very/long/full/path.txt")
        repo.query.return_value = [f]
        m = FileTableModel(repo)
        assert m.data(m.index(0, 1), Qt.ToolTipRole) == "/very/long/full/path.txt"
        # Tooltip on non-path column is None
        assert m.data(m.index(0, 0), Qt.ToolTipRole) is None

    def test_data_invalid_index_returns_none(self, qapp):
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import QModelIndex, Qt
        repo = MagicMock()
        repo.query.return_value = []
        m = FileTableModel(repo)
        assert m.data(QModelIndex(), Qt.DisplayRole) is None

    def test_data_unsupported_role(self, qapp):
        """A role that's neither DisplayRole nor ToolTipRole returns None."""
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [_make_file_entity()]
        m = FileTableModel(repo)
        # EditRole isn't supported by this model
        assert m.data(m.index(0, 0), Qt.EditRole) is None

    def test_data_row_out_of_range(self, qapp):
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [_make_file_entity()]
        m = FileTableModel(repo)
        # ``index()`` returns invalid for out-of-range rows; we use
        # ``createIndex`` to bypass that and hit the row-bounds check
        # (line 155-156) directly.
        idx = m.createIndex(99, 0)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_data_column_beyond_known_returns_none(self, qapp):
        """The final `return None` fallback in data() when col is out of range."""
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [_make_file_entity()]
        m = FileTableModel(repo)
        # Column 6 (out of COLUMNS range — model has 6 cols 0-5)
        # createIndex allows col >= columnCount; just check the
        # data() short-circuit at the col-mismatch (the final
        # `return None` in the chain)
        from PySide6.QtCore import QModelIndex
        # We exercise the final fallback by directly invoking data() with
        # an unused-column index. createIndex bypasses validation.
        idx = m.createIndex(0, 7)
        # Should fall through past all branches to return None
        assert m.data(idx, Qt.DisplayRole) is None

    def test_data_xxhash_short_form(self, qapp):
        """xxhash3 column shows first 12 chars + ellipsis if longer."""
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        # Empty hash → empty string
        f_empty = _make_file_entity(xxhash3=None)
        # Short hash (<= 12 chars) → no truncation
        f_short = _make_file_entity(xxhash3="abc")
        # Long hash → first 12 + ellipsis
        f_long = _make_file_entity(xxhash3="0123456789abcdef0123")
        repo.query.return_value = [f_empty, f_short, f_long]
        m = FileTableModel(repo)
        assert m.data(m.index(0, 5), Qt.DisplayRole) == ""
        assert m.data(m.index(1, 5), Qt.DisplayRole) == "abc"
        assert m.data(m.index(2, 5), Qt.DisplayRole) == "0123456789ab..."

    def test_data_extension_none_returns_empty(self, qapp):
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [_make_file_entity(extension=None)]
        m = FileTableModel(repo)
        assert m.data(m.index(0, 4), Qt.DisplayRole) == ""

    def test_sort_each_column(self, qapp):
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [
            _make_file_entity(source="z", path="/z.txt", size=10,
                               mtime=datetime(2026, 5, 3), extension="z",
                               xxhash3="zzz"),
            _make_file_entity(source="a", path="/a.txt", size=200,
                               mtime=datetime(2026, 5, 1), extension="a",
                               xxhash3="aaa"),
        ]
        m = FileTableModel(repo)
        for col in range(6):
            m.sort(col, Qt.SortOrder.AscendingOrder)
        # Reverse order works
        m.sort(0, Qt.SortOrder.DescendingOrder)
        # Sort with column beyond known falls through to source_path
        m.sort(99, Qt.SortOrder.AscendingOrder)

    def test_sort_handles_none_values(self, qapp):
        """Sort keys coerce None values (size=None, mtime=None, etc.)."""
        from curator.gui.models import FileTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [
            _make_file_entity(size=None, mtime=None, extension=None,
                               xxhash3=None, source=None, path=None),
            _make_file_entity(),
        ]
        m = FileTableModel(repo)
        for col in range(6):
            m.sort(col, Qt.SortOrder.AscendingOrder)


# ===========================================================================
# BundleTableModel
# ===========================================================================


def _make_bundle_entity(*, name="b", bundle_type="duplicates",
                       confidence=0.9, created_at=None):
    b = MagicMock()
    b.bundle_id = uuid4()
    b.name = name
    b.bundle_type = bundle_type
    b.confidence = confidence
    b.created_at = created_at or datetime(2026, 5, 1, 12, 0)
    return b


class TestBundleTableModel:
    def test_init_and_basic_protocol(self, qapp):
        from curator.gui.models import BundleTableModel
        repo = MagicMock()
        bundles = [_make_bundle_entity(name="A"), _make_bundle_entity(name="B")]
        repo.list_all.return_value = bundles
        repo.member_count.side_effect = lambda bid: 5
        m = BundleTableModel(repo)
        assert m.rowCount() == 2
        assert m.columnCount() == 5

    def test_init_failure_falls_back(self, qapp):
        from curator.gui.models import BundleTableModel
        repo = MagicMock()
        repo.list_all.side_effect = RuntimeError("db gone")
        m = BundleTableModel(repo)
        assert m.rowCount() == 0

    def test_bundle_at(self, qapp):
        from curator.gui.models import BundleTableModel
        repo = MagicMock()
        repo.list_all.return_value = [_make_bundle_entity()]
        repo.member_count.return_value = 0
        m = BundleTableModel(repo)
        assert m.bundle_at(0) is not None
        assert m.bundle_at(99) is None

    def test_row_count_with_parent_is_zero(self, qapp):
        from curator.gui.models import BundleTableModel
        repo = MagicMock()
        repo.list_all.return_value = [_make_bundle_entity()]
        repo.member_count.return_value = 0
        m = BundleTableModel(repo)
        idx = m.index(0, 0)
        assert m.rowCount(idx) == 0
        assert m.columnCount(idx) == 0

    def test_header_data_branches(self, qapp):
        from curator.gui.models import BundleTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_all.return_value = []
        m = BundleTableModel(repo)
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.DisplayRole) == "Name"
        assert m.headerData(99, Qt.Orientation.Horizontal, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Vertical, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.EditRole) is None

    def test_data_all_columns(self, qapp):
        from curator.gui.models import BundleTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        bundle = _make_bundle_entity(
            name="MyBundle", bundle_type="duplicates", confidence=0.93,
            created_at=datetime(2026, 4, 1, 10, 0),
        )
        repo.list_all.return_value = [bundle]
        repo.member_count.return_value = 7
        m = BundleTableModel(repo)
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "MyBundle"
        assert m.data(m.index(0, 1), Qt.DisplayRole) == "duplicates"
        assert m.data(m.index(0, 2), Qt.DisplayRole) == 7
        assert m.data(m.index(0, 3), Qt.DisplayRole) == "0.93"
        assert m.data(m.index(0, 4), Qt.DisplayRole) == "2026-04-01 10:00"

    def test_data_unnamed_falls_back(self, qapp):
        from curator.gui.models import BundleTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        b = _make_bundle_entity(name=None)
        repo.list_all.return_value = [b]
        repo.member_count.return_value = 0
        m = BundleTableModel(repo)
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "(unnamed)"

    def test_data_invalid_role_returns_none(self, qapp):
        from curator.gui.models import BundleTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_all.return_value = [_make_bundle_entity()]
        repo.member_count.return_value = 0
        m = BundleTableModel(repo)
        # Non-display roles short-circuit
        assert m.data(m.index(0, 0), Qt.EditRole) is None

    def test_data_invalid_index(self, qapp):
        from curator.gui.models import BundleTableModel
        from PySide6.QtCore import QModelIndex, Qt
        repo = MagicMock()
        repo.list_all.return_value = []
        m = BundleTableModel(repo)
        assert m.data(QModelIndex(), Qt.DisplayRole) is None

    def test_data_row_out_of_range(self, qapp):
        from curator.gui.models import BundleTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_all.return_value = [_make_bundle_entity()]
        repo.member_count.return_value = 0
        m = BundleTableModel(repo)
        idx = m.createIndex(99, 0)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_data_column_fallback(self, qapp):
        from curator.gui.models import BundleTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list_all.return_value = [_make_bundle_entity()]
        repo.member_count.return_value = 0
        m = BundleTableModel(repo)
        idx = m.createIndex(0, 99)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_sort_each_column(self, qapp):
        from curator.gui.models import BundleTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        bundles = [
            _make_bundle_entity(name="Zebra"),
            _make_bundle_entity(name="Apple"),
        ]
        repo.list_all.return_value = bundles
        repo.member_count.side_effect = lambda bid: 1
        m = BundleTableModel(repo)
        for col in range(5):
            m.sort(col, Qt.SortOrder.AscendingOrder)
        m.sort(99, Qt.SortOrder.DescendingOrder)

    def test_sort_handles_none_values(self, qapp):
        from curator.gui.models import BundleTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        b = _make_bundle_entity(name=None, created_at=None)
        repo.list_all.return_value = [b, _make_bundle_entity()]
        repo.member_count.return_value = 1
        m = BundleTableModel(repo)
        for col in range(5):
            m.sort(col, Qt.SortOrder.AscendingOrder)


# ===========================================================================
# TrashTableModel
# ===========================================================================


def _make_trash_record(
    *, original_path="/p/old.txt", original_source_id="local",
    reason="dup", trashed_by="user", trashed_at=None,
):
    r = MagicMock()
    r.original_path = original_path
    r.original_source_id = original_source_id
    r.reason = reason
    r.trashed_by = trashed_by
    r.trashed_at = trashed_at or datetime(2026, 5, 1, 12, 0)
    return r


class TestTrashTableModel:
    def test_init_loads_rows(self, qapp):
        from curator.gui.models import TrashTableModel
        repo = MagicMock()
        repo.list.return_value = [
            _make_trash_record(original_path="/a.txt"),
            _make_trash_record(original_path="/b.txt"),
        ]
        m = TrashTableModel(repo)
        assert m.rowCount() == 2
        assert m.columnCount() == 5

    def test_with_limit_passed(self, qapp):
        from curator.gui.models import TrashTableModel
        repo = MagicMock()
        repo.list.return_value = []
        TrashTableModel(repo, limit=25)
        repo.list.assert_called_with(limit=25)

    def test_init_failure_falls_back(self, qapp):
        from curator.gui.models import TrashTableModel
        repo = MagicMock()
        repo.list.side_effect = RuntimeError("db gone")
        m = TrashTableModel(repo)
        assert m.rowCount() == 0

    def test_trash_at(self, qapp):
        from curator.gui.models import TrashTableModel
        repo = MagicMock()
        repo.list.return_value = [_make_trash_record()]
        m = TrashTableModel(repo)
        assert m.trash_at(0) is not None
        assert m.trash_at(99) is None

    def test_row_count_with_parent_is_zero(self, qapp):
        from curator.gui.models import TrashTableModel
        repo = MagicMock()
        repo.list.return_value = [_make_trash_record()]
        m = TrashTableModel(repo)
        idx = m.index(0, 0)
        assert m.rowCount(idx) == 0
        assert m.columnCount(idx) == 0

    def test_header_data_branches(self, qapp):
        from curator.gui.models import TrashTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list.return_value = []
        m = TrashTableModel(repo)
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.DisplayRole) == "Original Path"
        assert m.headerData(99, Qt.Orientation.Horizontal, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Vertical, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.EditRole) is None

    def test_data_all_columns(self, qapp):
        from curator.gui.models import TrashTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        r = _make_trash_record(
            original_path="/old/file.txt", original_source_id="local",
            reason="duplicate", trashed_by="cli.cleanup",
            trashed_at=datetime(2026, 4, 1, 10, 0),
        )
        repo.list.return_value = [r]
        m = TrashTableModel(repo)
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "/old/file.txt"
        assert m.data(m.index(0, 1), Qt.DisplayRole) == "local"
        assert m.data(m.index(0, 2), Qt.DisplayRole) == "duplicate"
        assert m.data(m.index(0, 3), Qt.DisplayRole) == "cli.cleanup"
        assert m.data(m.index(0, 4), Qt.DisplayRole) == "2026-04-01 10:00"

    def test_data_unsupported_role(self, qapp):
        from curator.gui.models import TrashTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list.return_value = [_make_trash_record()]
        m = TrashTableModel(repo)
        assert m.data(m.index(0, 0), Qt.EditRole) is None

    def test_data_invalid_index(self, qapp):
        from curator.gui.models import TrashTableModel
        from PySide6.QtCore import QModelIndex, Qt
        repo = MagicMock()
        repo.list.return_value = []
        m = TrashTableModel(repo)
        assert m.data(QModelIndex(), Qt.DisplayRole) is None

    def test_data_row_out_of_range(self, qapp):
        from curator.gui.models import TrashTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list.return_value = [_make_trash_record()]
        m = TrashTableModel(repo)
        idx = m.createIndex(99, 0)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_data_column_fallback(self, qapp):
        from curator.gui.models import TrashTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list.return_value = [_make_trash_record()]
        m = TrashTableModel(repo)
        idx = m.createIndex(0, 99)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_sort_each_column(self, qapp):
        from curator.gui.models import TrashTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.list.return_value = [
            _make_trash_record(original_path="/z.txt"),
            _make_trash_record(original_path="/a.txt"),
        ]
        m = TrashTableModel(repo)
        for col in range(5):
            m.sort(col, Qt.SortOrder.AscendingOrder)
        m.sort(99, Qt.SortOrder.DescendingOrder)

    def test_sort_handles_none_values(self, qapp):
        from curator.gui.models import TrashTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        r = _make_trash_record(
            original_path=None, original_source_id=None,
            reason=None, trashed_by=None, trashed_at=None,
        )
        repo.list.return_value = [r, _make_trash_record()]
        m = TrashTableModel(repo)
        for col in range(5):
            m.sort(col, Qt.SortOrder.AscendingOrder)
