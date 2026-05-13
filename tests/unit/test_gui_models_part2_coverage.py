"""Coverage for ``curator.gui.models`` Part 2 (v1.7.189).

Round 4 Tier 2 sub-ship 5 of 6 — covers ``AuditLogTableModel`` (with
its filter state + entity/details formatters) and ``ConfigTableModel``
(with its nested-dict ``_flatten`` + ``_format_value`` helpers).

Part 1's canonical pattern (stub-repo + createIndex + role-parameterized
``data`` + sort with None values + 99-column fallback) is reused here.
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
# AuditLogTableModel
# ===========================================================================


def _make_audit_entry(
    *, occurred_at=None, actor="cli.scan", action="scan.complete",
    entity_type="scan_job", entity_id=None, details=None,
):
    """Audit entry stub via MagicMock."""
    e = MagicMock()
    e.occurred_at = occurred_at or datetime(2026, 5, 1, 12, 0)
    e.actor = actor
    e.action = action
    e.entity_type = entity_type
    e.entity_id = entity_id or str(uuid4())
    e.details = details if details is not None else {"files_seen": 42}
    return e


class TestAuditLogTableModelInit:
    def test_default_limit_applied(self, qapp):
        from curator.gui.models import AuditLogTableModel
        repo = MagicMock()
        repo.query.return_value = []
        m = AuditLogTableModel(repo)
        # Default limit = DEFAULT_LIMIT (1000)
        assert m._limit == AuditLogTableModel.DEFAULT_LIMIT
        repo.query.assert_called_with(limit=1000)

    def test_custom_limit(self, qapp):
        from curator.gui.models import AuditLogTableModel
        repo = MagicMock()
        repo.query.return_value = []
        AuditLogTableModel(repo, limit=10)
        repo.query.assert_called_with(limit=10)

    def test_init_failure_falls_back(self, qapp):
        from curator.gui.models import AuditLogTableModel
        repo = MagicMock()
        repo.query.side_effect = RuntimeError("db gone")
        m = AuditLogTableModel(repo)
        assert m.rowCount() == 0

    def test_entry_at(self, qapp):
        from curator.gui.models import AuditLogTableModel
        repo = MagicMock()
        repo.query.return_value = [_make_audit_entry()]
        m = AuditLogTableModel(repo)
        assert m.entry_at(0) is not None
        assert m.entry_at(99) is None


class TestAuditLogTableModelFilter:
    def test_set_filter_with_all_kwargs(self, qapp):
        from curator.gui.models import AuditLogTableModel
        repo = MagicMock()
        repo.query.return_value = []
        m = AuditLogTableModel(repo)

        since = datetime(2026, 4, 1)
        until = datetime(2026, 5, 1)
        m.set_filter(
            since=since, until=until,
            actor="cli.scan", action="scan.start",
            entity_type="file", entity_id="abc-123",
        )
        # Filter kwargs stored
        assert m._filter_kwargs == {
            "since": since, "until": until,
            "actor": "cli.scan", "action": "scan.start",
            "entity_type": "file", "entity_id": "abc-123",
        }
        # refresh() applies the filter
        repo.query.reset_mock()
        m.refresh()
        repo.query.assert_called_with(
            limit=AuditLogTableModel.DEFAULT_LIMIT,
            since=since, until=until,
            actor="cli.scan", action="scan.start",
            entity_type="file", entity_id="abc-123",
        )

    def test_set_filter_with_empty_strings_omits_them(self, qapp):
        """Empty actor/action/entity strings should NOT make it into kwargs."""
        from curator.gui.models import AuditLogTableModel
        repo = MagicMock()
        repo.query.return_value = []
        m = AuditLogTableModel(repo)
        m.set_filter(actor="", action="", entity_type="", entity_id="")
        # All empty strings → no kwargs
        assert m._filter_kwargs == {}

    def test_set_filter_with_none_values_omits_them(self, qapp):
        from curator.gui.models import AuditLogTableModel
        repo = MagicMock()
        repo.query.return_value = []
        m = AuditLogTableModel(repo)
        m.set_filter()
        assert m._filter_kwargs == {}

    def test_set_filter_partial(self, qapp):
        """Only some filters set; refresh uses just those."""
        from curator.gui.models import AuditLogTableModel
        repo = MagicMock()
        repo.query.return_value = []
        m = AuditLogTableModel(repo)
        m.set_filter(actor="cli.tier")
        assert m._filter_kwargs == {"actor": "cli.tier"}


class TestAuditLogTableModelProtocol:
    def test_row_and_column_counts(self, qapp):
        from curator.gui.models import AuditLogTableModel
        repo = MagicMock()
        repo.query.return_value = [
            _make_audit_entry(),
            _make_audit_entry(),
        ]
        m = AuditLogTableModel(repo)
        assert m.rowCount() == 2
        assert m.columnCount() == 5
        # With valid parent → 0
        idx = m.index(0, 0)
        assert m.rowCount(idx) == 0
        assert m.columnCount(idx) == 0

    def test_header_data_branches(self, qapp):
        from curator.gui.models import AuditLogTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = []
        m = AuditLogTableModel(repo)
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.DisplayRole) == "When"
        assert m.headerData(99, Qt.Orientation.Horizontal, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Vertical, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.EditRole) is None

    def test_data_all_columns_display_role(self, qapp):
        from curator.gui.models import AuditLogTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        e = _make_audit_entry(
            occurred_at=datetime(2026, 4, 1, 10, 0),
            actor="cli.bundles", action="bundle.create",
            entity_type="bundle",
            entity_id="abc-12345678-very-long-uuid-here",
            details={"k": "v"},
        )
        repo.query.return_value = [e]
        m = AuditLogTableModel(repo)
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "2026-04-01 10:00"
        assert m.data(m.index(0, 1), Qt.DisplayRole) == "cli.bundles"
        assert m.data(m.index(0, 2), Qt.DisplayRole) == "bundle.create"
        # Entity column: type:short_id
        assert m.data(m.index(0, 3), Qt.DisplayRole).startswith("bundle:")
        assert "..." in m.data(m.index(0, 3), Qt.DisplayRole)
        # Details column shows truncated JSON
        details_val = m.data(m.index(0, 4), Qt.DisplayRole)
        assert '"k":' in details_val and '"v"' in details_val

    def test_data_tooltip_on_details_column(self, qapp):
        from curator.gui.models import AuditLogTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        long_details = {"long_key": "x" * 200}
        e = _make_audit_entry(details=long_details)
        repo.query.return_value = [e]
        m = AuditLogTableModel(repo)
        tip = m.data(m.index(0, 4), Qt.ToolTipRole)
        # Tooltip is non-truncated, so no "..."
        assert tip is not None
        assert "..." not in tip
        # Other columns get None on tooltip
        assert m.data(m.index(0, 0), Qt.ToolTipRole) is None

    def test_data_invalid_index(self, qapp):
        from curator.gui.models import AuditLogTableModel
        from PySide6.QtCore import QModelIndex, Qt
        repo = MagicMock()
        repo.query.return_value = []
        m = AuditLogTableModel(repo)
        assert m.data(QModelIndex(), Qt.DisplayRole) is None

    def test_data_row_out_of_range(self, qapp):
        from curator.gui.models import AuditLogTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [_make_audit_entry()]
        m = AuditLogTableModel(repo)
        idx = m.createIndex(99, 0)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_data_unsupported_role(self, qapp):
        from curator.gui.models import AuditLogTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [_make_audit_entry()]
        m = AuditLogTableModel(repo)
        # EditRole short-circuits in the post-tooltip filter
        assert m.data(m.index(0, 0), Qt.EditRole) is None

    def test_data_column_fallback(self, qapp):
        from curator.gui.models import AuditLogTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [_make_audit_entry()]
        m = AuditLogTableModel(repo)
        idx = m.createIndex(0, 99)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_sort_each_column(self, qapp):
        from curator.gui.models import AuditLogTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        repo.query.return_value = [
            _make_audit_entry(actor="z", occurred_at=datetime(2026, 5, 5)),
            _make_audit_entry(actor="a", occurred_at=datetime(2026, 5, 1)),
        ]
        m = AuditLogTableModel(repo)
        for col in range(5):
            m.sort(col, Qt.SortOrder.AscendingOrder)
        m.sort(99, Qt.SortOrder.DescendingOrder)

    def test_sort_handles_none_values(self, qapp):
        from curator.gui.models import AuditLogTableModel
        from PySide6.QtCore import Qt
        repo = MagicMock()
        e_partial = _make_audit_entry(occurred_at=None, actor=None,
                                       action=None, details=None,
                                       entity_type=None, entity_id=None)
        repo.query.return_value = [e_partial, _make_audit_entry()]
        m = AuditLogTableModel(repo)
        for col in range(5):
            m.sort(col, Qt.SortOrder.AscendingOrder)


class TestAuditLogFormatters:
    """The static/classmethod helpers ``_format_entity`` and
    ``_format_details``."""

    def test_format_entity_both_empty(self, qapp):
        from curator.gui.models import AuditLogTableModel
        e = _make_audit_entry(entity_type=None, entity_id=None)
        # Pydantic-style mocks: ensure the values are truly None/empty
        e.entity_type = ""
        e.entity_id = ""
        assert AuditLogTableModel._format_entity(e) == ""

    def test_format_entity_no_type_returns_just_id(self, qapp):
        from curator.gui.models import AuditLogTableModel
        e = _make_audit_entry(entity_type="", entity_id="abc")
        e.entity_type = ""
        assert AuditLogTableModel._format_entity(e) == "abc"

    def test_format_entity_long_uuid_truncated(self, qapp):
        from curator.gui.models import AuditLogTableModel
        e = _make_audit_entry(entity_type="file",
                              entity_id="abcd1234-5678-90ab-cdef-1234567890ab")
        out = AuditLogTableModel._format_entity(e)
        assert out.startswith("file:")
        assert "..." in out

    def test_format_entity_short_id_kept_whole(self, qapp):
        from curator.gui.models import AuditLogTableModel
        e = _make_audit_entry(entity_type="file", entity_id="short")
        out = AuditLogTableModel._format_entity(e)
        assert out == "file:short"

    def test_format_details_empty(self, qapp):
        from curator.gui.models import AuditLogTableModel
        assert AuditLogTableModel._format_details({}, truncate=True) == ""
        assert AuditLogTableModel._format_details(None, truncate=False) == ""

    def test_format_details_truncate(self, qapp):
        from curator.gui.models import AuditLogTableModel
        # Long details should get truncated to DETAILS_PREVIEW_LEN + "..."
        long = {"k": "x" * 200}
        out = AuditLogTableModel._format_details(long, truncate=True)
        assert out.endswith("...")
        assert len(out) <= AuditLogTableModel.DETAILS_PREVIEW_LEN + 3

    def test_format_details_no_truncate(self, qapp):
        from curator.gui.models import AuditLogTableModel
        long = {"k": "x" * 200}
        out = AuditLogTableModel._format_details(long, truncate=False)
        assert not out.endswith("...")

    def test_format_details_json_serialization_failure_falls_back_to_str(self, qapp):
        """If json.dumps raises (e.g. unserializable obj), str() fallback."""
        from curator.gui.models import AuditLogTableModel

        class _Unserializable:
            def __repr__(self):
                return "<UNSER>"

            def __str__(self):
                # Force str() to be used
                return "{Unserializable}"

        # default=str catches most things; the except path runs if
        # json.dumps itself raises non-TypeError. Force the path by
        # patching json.dumps to raise.
        import json
        original = json.dumps

        def boom(*a, **kw):
            raise ValueError("boom")

        json.dumps = boom
        try:
            out = AuditLogTableModel._format_details(
                {"k": _Unserializable()}, truncate=False,
            )
            assert out == str({"k": _Unserializable()})
        finally:
            json.dumps = original


# ===========================================================================
# ConfigTableModel
# ===========================================================================


class _StubConfig:
    """Minimal Config-like stub with .as_dict() method."""

    def __init__(self, data: dict):
        self._data = data

    def as_dict(self):
        return self._data


class TestConfigTableModelInit:
    def test_basic_init(self, qapp):
        from curator.gui.models import ConfigTableModel
        cfg = _StubConfig({"a": 1, "nested": {"x": 2}})
        m = ConfigTableModel(cfg)
        assert m.rowCount() == 2  # 'a' and 'nested.x'
        assert m.columnCount() == 2

    def test_init_failure_falls_back(self, qapp):
        from curator.gui.models import ConfigTableModel
        cfg = MagicMock()
        cfg.as_dict.side_effect = RuntimeError("bad config")
        m = ConfigTableModel(cfg)
        assert m.rowCount() == 0

    def test_setting_at(self, qapp):
        from curator.gui.models import ConfigTableModel
        cfg = _StubConfig({"k1": "v1"})
        m = ConfigTableModel(cfg)
        assert m.setting_at(0) == ("k1", "v1")
        assert m.setting_at(99) is None

    def test_set_config_refreshes(self, qapp):
        from curator.gui.models import ConfigTableModel
        cfg1 = _StubConfig({"a": 1})
        cfg2 = _StubConfig({"b": 2, "c": 3})
        m = ConfigTableModel(cfg1)
        assert m.rowCount() == 1
        m.set_config(cfg2)
        assert m.rowCount() == 2


class TestConfigTableModelProtocol:
    def test_row_and_column_counts(self, qapp):
        from curator.gui.models import ConfigTableModel
        cfg = _StubConfig({"a": 1, "b": 2})
        m = ConfigTableModel(cfg)
        assert m.rowCount() == 2
        assert m.columnCount() == 2
        idx = m.index(0, 0)
        assert m.rowCount(idx) == 0
        assert m.columnCount(idx) == 0

    def test_header_data_branches(self, qapp):
        from curator.gui.models import ConfigTableModel
        from PySide6.QtCore import Qt
        cfg = _StubConfig({})
        m = ConfigTableModel(cfg)
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.DisplayRole) == "Setting"
        assert m.headerData(99, Qt.Orientation.Horizontal, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Vertical, Qt.DisplayRole) is None
        assert m.headerData(0, Qt.Orientation.Horizontal, Qt.EditRole) is None

    def test_data_all_columns(self, qapp):
        from curator.gui.models import ConfigTableModel
        from PySide6.QtCore import Qt
        cfg = _StubConfig({"foo": "bar"})
        m = ConfigTableModel(cfg)
        # Sorted: "foo" is the only key
        assert m.data(m.index(0, 0), Qt.DisplayRole) == "foo"
        assert m.data(m.index(0, 1), Qt.DisplayRole) == "bar"
        # Tooltip on value column shows the full value
        assert m.data(m.index(0, 1), Qt.ToolTipRole) == "bar"
        # Tooltip on key column is None
        assert m.data(m.index(0, 0), Qt.ToolTipRole) is None

    def test_data_unsupported_role(self, qapp):
        from curator.gui.models import ConfigTableModel
        from PySide6.QtCore import Qt
        cfg = _StubConfig({"x": "y"})
        m = ConfigTableModel(cfg)
        assert m.data(m.index(0, 0), Qt.EditRole) is None

    def test_data_invalid_index(self, qapp):
        from curator.gui.models import ConfigTableModel
        from PySide6.QtCore import QModelIndex, Qt
        cfg = _StubConfig({})
        m = ConfigTableModel(cfg)
        assert m.data(QModelIndex(), Qt.DisplayRole) is None

    def test_data_row_out_of_range(self, qapp):
        from curator.gui.models import ConfigTableModel
        from PySide6.QtCore import Qt
        cfg = _StubConfig({"x": "y"})
        m = ConfigTableModel(cfg)
        idx = m.createIndex(99, 0)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_data_column_fallback(self, qapp):
        from curator.gui.models import ConfigTableModel
        from PySide6.QtCore import Qt
        cfg = _StubConfig({"x": "y"})
        m = ConfigTableModel(cfg)
        idx = m.createIndex(0, 99)
        assert m.data(idx, Qt.DisplayRole) is None

    def test_sort_each_column_with_fallback(self, qapp):
        from curator.gui.models import ConfigTableModel
        from PySide6.QtCore import Qt
        cfg = _StubConfig({"zeta": 1, "alpha": 2})
        m = ConfigTableModel(cfg)
        m.sort(0, Qt.SortOrder.AscendingOrder)
        m.sort(1, Qt.SortOrder.DescendingOrder)
        # 99-column falls through to key (row[0])
        m.sort(99, Qt.SortOrder.AscendingOrder)


class TestConfigFlatten:
    """The `_flatten` classmethod and `_format_value` static method."""

    def test_flatten_simple_dict(self, qapp):
        from curator.gui.models import ConfigTableModel
        result = list(ConfigTableModel._flatten({"a": 1, "b": 2}))
        # Sorted alphabetically (the flatten emits in sorted key order)
        assert result == [("a", "1"), ("b", "2")]

    def test_flatten_nested(self, qapp):
        from curator.gui.models import ConfigTableModel
        result = list(ConfigTableModel._flatten({"section": {"key": "v"}}))
        assert result == [("section.key", "v")]

    def test_flatten_list_value(self, qapp):
        from curator.gui.models import ConfigTableModel
        result = list(ConfigTableModel._flatten({"items": [1, 2, 3]}))
        assert len(result) == 1
        key, val = result[0]
        assert key == "items"
        assert "1" in val and "2" in val and "3" in val

    def test_flatten_tuple_value(self, qapp):
        from curator.gui.models import ConfigTableModel
        result = list(ConfigTableModel._flatten({"pair": (1, 2)}))
        assert len(result) == 1
        key, val = result[0]
        assert key == "pair"

    def test_flatten_scalar_at_root(self, qapp):
        """If the input is a scalar (not a dict/list), yields one row
        with the prefix as key (empty for top-level scalar)."""
        from curator.gui.models import ConfigTableModel
        result = list(ConfigTableModel._flatten("scalar"))
        assert result == [("", "scalar")]

    def test_format_value_none(self, qapp):
        from curator.gui.models import ConfigTableModel
        assert ConfigTableModel._format_value(None) == "(null)"

    def test_format_value_bool(self, qapp):
        from curator.gui.models import ConfigTableModel
        assert ConfigTableModel._format_value(True) == "true"
        assert ConfigTableModel._format_value(False) == "false"

    def test_format_value_int(self, qapp):
        from curator.gui.models import ConfigTableModel
        assert ConfigTableModel._format_value(42) == "42"

    def test_format_value_string(self, qapp):
        from curator.gui.models import ConfigTableModel
        assert ConfigTableModel._format_value("hello") == "hello"

    def test_format_value_list(self, qapp):
        from curator.gui.models import ConfigTableModel
        out = ConfigTableModel._format_value([1, 2, 3])
        assert "1" in out and "2" in out and "3" in out

    def test_format_value_tuple(self, qapp):
        from curator.gui.models import ConfigTableModel
        out = ConfigTableModel._format_value((1, 2))
        # Tuples get json-listed
        assert "1" in out and "2" in out

    def test_format_value_dict(self, qapp):
        from curator.gui.models import ConfigTableModel
        out = ConfigTableModel._format_value({"k": "v"})
        assert '"k"' in out and '"v"' in out

    def test_format_value_list_json_failure_falls_back_to_str(self, qapp):
        """If json.dumps raises on a list, fall back to str()."""
        from curator.gui.models import ConfigTableModel
        import json
        original = json.dumps
        json.dumps = lambda *a, **kw: (_ for _ in ()).throw(ValueError("nope"))
        try:
            out = ConfigTableModel._format_value([1, 2])
            assert out == str([1, 2])
        finally:
            json.dumps = original

    def test_format_value_dict_json_failure_falls_back_to_str(self, qapp):
        """If json.dumps raises on a dict, fall back to str()."""
        from curator.gui.models import ConfigTableModel
        import json
        original = json.dumps
        json.dumps = lambda *a, **kw: (_ for _ in ()).throw(ValueError("nope"))
        try:
            out = ConfigTableModel._format_value({"k": "v"})
            assert out == str({"k": "v"})
        finally:
            json.dumps = original
