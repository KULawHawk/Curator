"""Coverage for ``curator.gui.dialogs`` helpers + result classes (v1.7.197).

Round 4 Tier 4 sub-ship 2 of ~11 — covers the module-level helpers
(``_make_kv_table``, ``_make_table``, ``_stringify``) and the three
result/data classes (``BundleEditorResult``, ``_CheckResult``,
``HealthCheckResult``).

Per ``docs/DIALOGS_DECOMPOSITION.md``, this is the smallest sub-ship and
validates the test infrastructure before tackling the bigger dialog
classes.
"""

from __future__ import annotations

import os
from datetime import datetime
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


class TestMakeKvTable:
    def test_empty_rows(self, qapp, qtbot):
        from curator.gui.dialogs import _make_kv_table
        t = _make_kv_table([])
        qtbot.addWidget(t)
        assert t.rowCount() == 0
        assert t.columnCount() == 2

    def test_populated_rows(self, qapp, qtbot):
        from curator.gui.dialogs import _make_kv_table
        rows = [("Name", "Alice"), ("Age", "30")]
        t = _make_kv_table(rows)
        qtbot.addWidget(t)
        assert t.rowCount() == 2
        assert t.columnCount() == 2
        # Each cell populated
        assert t.item(0, 0).text() == "Name"
        assert t.item(0, 1).text() == "Alice"
        assert t.item(1, 0).text() == "Age"
        assert t.item(1, 1).text() == "30"

    def test_headers_set(self, qapp, qtbot):
        from curator.gui.dialogs import _make_kv_table
        t = _make_kv_table([])
        qtbot.addWidget(t)
        assert t.horizontalHeaderItem(0).text() == "Field"
        assert t.horizontalHeaderItem(1).text() == "Value"


class TestMakeTable:
    def test_empty_rows(self, qapp, qtbot):
        from curator.gui.dialogs import _make_table
        t = _make_table(["A", "B", "C"], [])
        qtbot.addWidget(t)
        assert t.rowCount() == 0
        assert t.columnCount() == 3

    def test_populated_rows(self, qapp, qtbot):
        from curator.gui.dialogs import _make_table
        headers = ["Col1", "Col2", "Col3"]
        rows = [["a", "b", "c"], ["d", "e", "f"]]
        t = _make_table(headers, rows)
        qtbot.addWidget(t)
        assert t.rowCount() == 2
        assert t.columnCount() == 3
        # Check cells
        assert t.item(0, 0).text() == "a"
        assert t.item(1, 2).text() == "f"

    def test_headers_set(self, qapp, qtbot):
        from curator.gui.dialogs import _make_table
        t = _make_table(["X", "Y"], [])
        qtbot.addWidget(t)
        assert t.horizontalHeaderItem(0).text() == "X"
        assert t.horizontalHeaderItem(1).text() == "Y"


class TestStringify:
    def test_none(self):
        from curator.gui.dialogs import _stringify
        assert _stringify(None) == ""

    def test_datetime(self):
        from curator.gui.dialogs import _stringify
        dt = datetime(2026, 5, 13, 14, 30)
        # _stringify calls _format_dt which formats as "YYYY-MM-DD HH:MM"
        result = _stringify(dt)
        assert "2026-05-13" in result
        assert "14:30" in result

    def test_bool_true(self):
        from curator.gui.dialogs import _stringify
        assert _stringify(True) == "true"

    def test_bool_false(self):
        from curator.gui.dialogs import _stringify
        assert _stringify(False) == "false"

    def test_int(self):
        from curator.gui.dialogs import _stringify
        assert _stringify(42) == "42"

    def test_string(self):
        from curator.gui.dialogs import _stringify
        assert _stringify("hello") == "hello"

    def test_float(self):
        from curator.gui.dialogs import _stringify
        assert _stringify(3.14) == "3.14"


# ===========================================================================
# BundleEditorResult
# ===========================================================================


class TestBundleEditorResult:
    def test_construction_defaults(self):
        from curator.gui.dialogs import BundleEditorResult
        r = BundleEditorResult(
            name="b", description=None,
            member_ids=[], primary_id=None,
        )
        assert r.name == "b"
        assert r.description is None
        assert r.member_ids == []
        assert r.primary_id is None
        assert r.existing_bundle_id is None
        assert r.initial_member_ids == []

    def test_full_construction(self):
        from curator.gui.dialogs import BundleEditorResult
        m1, m2 = uuid4(), uuid4()
        ex = uuid4()
        r = BundleEditorResult(
            name="bundle",
            description="desc",
            member_ids=[m1, m2],
            primary_id=m1,
            existing_bundle_id=ex,
            initial_member_ids=[m1],
        )
        assert r.member_ids == [m1, m2]
        assert r.primary_id == m1
        assert r.existing_bundle_id == ex

    def test_added_member_ids(self):
        """Members in member_ids but not initial_member_ids."""
        from curator.gui.dialogs import BundleEditorResult
        m1, m2, m3 = uuid4(), uuid4(), uuid4()
        r = BundleEditorResult(
            name="b", description=None,
            member_ids=[m1, m2, m3], primary_id=m1,
            initial_member_ids=[m1],
        )
        added = r.added_member_ids
        assert m1 not in added
        assert m2 in added
        assert m3 in added

    def test_removed_member_ids(self):
        from curator.gui.dialogs import BundleEditorResult
        m1, m2, m3 = uuid4(), uuid4(), uuid4()
        r = BundleEditorResult(
            name="b", description=None,
            member_ids=[m1], primary_id=m1,
            initial_member_ids=[m1, m2, m3],
        )
        removed = r.removed_member_ids
        assert m1 not in removed
        assert m2 in removed
        assert m3 in removed


# ===========================================================================
# _CheckResult
# ===========================================================================


class TestCheckResult:
    def test_defaults(self):
        from curator.gui.dialogs import _CheckResult
        r = _CheckResult(label="check1", passed=True)
        assert r.label == "check1"
        assert r.passed is True
        assert r.detail == ""
        assert r.severity == "fail"

    def test_full(self):
        from curator.gui.dialogs import _CheckResult
        r = _CheckResult(
            label="check2", passed=False,
            detail="something went wrong",
            severity="warn",
        )
        assert r.passed is False
        assert r.detail == "something went wrong"
        assert r.severity == "warn"


# ===========================================================================
# HealthCheckResult
# ===========================================================================


class TestHealthCheckResult:
    def test_empty_defaults(self):
        from curator.gui.dialogs import HealthCheckResult
        r = HealthCheckResult()
        assert r.sections == {}
        assert r.elapsed_ms == 0
        # started_at defaults to a datetime
        assert isinstance(r.started_at, datetime)

    def test_total_count(self):
        from curator.gui.dialogs import HealthCheckResult, _CheckResult
        r = HealthCheckResult(sections={
            "A": [_CheckResult("a1", True), _CheckResult("a2", True)],
            "B": [_CheckResult("b1", False)],
        })
        assert r.total == 3

    def test_passed_count(self):
        from curator.gui.dialogs import HealthCheckResult, _CheckResult
        r = HealthCheckResult(sections={
            "A": [_CheckResult("a1", True), _CheckResult("a2", False)],
            "B": [_CheckResult("b1", True)],
        })
        assert r.passed == 2

    def test_failed_count_only_severity_fail(self):
        """failed property only counts severity='fail'; warn/info excluded."""
        from curator.gui.dialogs import HealthCheckResult, _CheckResult
        r = HealthCheckResult(sections={
            "A": [
                _CheckResult("a1", False, severity="fail"),
                _CheckResult("a2", False, severity="warn"),
                _CheckResult("a3", False, severity="info"),
            ],
        })
        # Only a1 (passed=False AND severity=fail) is counted
        assert r.failed == 1

    def test_all_green_true_when_no_failures(self):
        from curator.gui.dialogs import HealthCheckResult, _CheckResult
        r = HealthCheckResult(sections={
            "A": [_CheckResult("a1", True), _CheckResult("a2", True)],
        })
        assert r.all_green is True

    def test_all_green_false_with_failure(self):
        from curator.gui.dialogs import HealthCheckResult, _CheckResult
        r = HealthCheckResult(sections={
            "A": [_CheckResult("a1", False, severity="fail")],
        })
        assert r.all_green is False

    def test_all_green_true_with_only_warnings(self):
        """Warnings don't affect all_green."""
        from curator.gui.dialogs import HealthCheckResult, _CheckResult
        r = HealthCheckResult(sections={
            "A": [_CheckResult("a1", False, severity="warn")],
        })
        # failed (count of severity=fail) == 0 → all_green
        assert r.all_green is True


# ===========================================================================
# Module exports / imports sanity
# ===========================================================================


class TestModuleStructure:
    def test_helpers_callable(self):
        from curator.gui.dialogs import _make_kv_table, _make_table, _stringify
        assert callable(_make_kv_table)
        assert callable(_make_table)
        assert callable(_stringify)

    def test_dataclasses_accessible(self):
        from curator.gui.dialogs import (
            BundleEditorResult, _CheckResult, HealthCheckResult,
        )
        # Constructors all callable
        BundleEditorResult(name="x", description=None,
                            member_ids=[], primary_id=None)
        _CheckResult(label="x", passed=True)
        HealthCheckResult()
