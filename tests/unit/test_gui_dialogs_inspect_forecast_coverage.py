"""Coverage for FileInspectDialog + ForecastDialog (v1.7.198).

Round 4 Tier 4 sub-ship 3 of ~11. Both are read-only viewers — the
simplest of the 10 dialog classes.
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
# Helpers — file + edge + bundle stubs
# ===========================================================================


def _make_file(
    *, source_path="/p/file.txt", source_id="local",
    size=1024, mtime=None, deleted_at=None, flex=None,
):
    f = MagicMock()
    f.curator_id = uuid4()
    f.source_path = source_path
    f.source_id = source_id
    f.size = size
    f.mtime = mtime or datetime(2026, 5, 1, 12, 0)
    f.ctime = datetime(2026, 4, 1, 10, 0)
    f.inode = 12345
    f.xxhash3_128 = "abc123"
    f.md5 = "def456"
    f.fuzzy_hash = "ghi789"
    f.file_type = "text"
    f.extension = "txt"
    f.file_type_confidence = 0.95
    f.seen_at = datetime(2026, 5, 1, 12, 0)
    f.last_scanned_at = datetime(2026, 5, 1, 12, 0)
    f.deleted_at = deleted_at
    f.flex = flex if flex is not None else {}
    return f


def _make_lineage_edge(*, edge_kind="duplicate", from_id=None, to_id=None,
                       confidence=0.9, detected_by="hash", notes=None):
    e = MagicMock()
    e.from_curator_id = from_id or uuid4()
    e.to_curator_id = to_id or uuid4()
    if isinstance(edge_kind, str):
        kind = MagicMock()
        kind.value = edge_kind
        e.edge_kind = kind
    else:
        e.edge_kind = edge_kind
    e.confidence = confidence
    e.detected_by = detected_by
    e.notes = notes
    return e


def _make_membership(*, bundle_id=None, role="member", confidence=1.0):
    m = MagicMock()
    m.bundle_id = bundle_id or uuid4()
    m.role = role
    m.confidence = confidence
    m.added_at = datetime(2026, 5, 1, 12, 0)
    return m


def _make_bundle(*, name="MyBundle", bundle_type="manual"):
    b = MagicMock()
    b.bundle_id = uuid4()
    b.name = name
    b.bundle_type = bundle_type
    return b


# ===========================================================================
# FileInspectDialog
# ===========================================================================


class TestFileInspectDialog:
    def test_basic_construction(self, qapp, qtbot):
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.return_value = []
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)
        assert dlg.windowTitle() == f"Inspect: {f.source_path}"
        # 3 tabs
        assert dlg._tabs.count() == 3
        assert dlg._tabs.tabText(0) == "Metadata"
        assert dlg._tabs.tabText(1) == "Lineage Edges"
        assert dlg._tabs.tabText(2) == "Bundle Memberships"

    def test_header_no_deleted_at(self, qapp, qtbot):
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.return_value = []
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)
        header_text = dlg._header_text()
        assert f.source_path in header_text
        assert "DELETED" not in header_text

    def test_header_with_deleted_at(self, qapp, qtbot):
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file(deleted_at=datetime(2026, 5, 2))
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.return_value = []
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)
        header_text = dlg._header_text()
        assert "DELETED" in header_text

    def test_metadata_tab_with_flex_attrs(self, qapp, qtbot):
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file(flex={"author": "Alice", "tag": "draft"})
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.return_value = []
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)
        # Metadata tab should have many rows including the flex attrs
        # (rows are >= 17 base + 2 flex)
        # We don't introspect the table; just verify no exception

    def test_metadata_tab_flex_iteration_exception(self, qapp, qtbot):
        """If iterating f.flex raises, the except clause swallows."""
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        # Make iter f.flex raise
        f.flex = MagicMock()
        f.flex.keys.side_effect = RuntimeError("flex broken")
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.return_value = []
        # Should not raise
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_lineage_tab_with_edges(self, qapp, qtbot):
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        # Build 2 edges: one with f as from_curator_id (out), one with f as to_curator_id (in)
        e_out = _make_lineage_edge(from_id=f.curator_id, to_id=uuid4(),
                                    edge_kind="duplicate")
        e_in = _make_lineage_edge(from_id=uuid4(), to_id=f.curator_id,
                                   edge_kind="version_of")
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = [e_out, e_in]
        rt.file_repo.get.return_value = MagicMock(source_path="/other/file.txt")
        rt.bundle_repo.get_memberships_for_file.return_value = []
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_lineage_tab_edges_exception(self, qapp, qtbot):
        """If get_edges_for raises, edges = [] (defensive)."""
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.side_effect = RuntimeError("db gone")
        rt.bundle_repo.get_memberships_for_file.return_value = []
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_lineage_tab_file_repo_exception(self, qapp, qtbot):
        """If file_repo.get raises while resolving the other file, fall
        back to (uuid) placeholder."""
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        e = _make_lineage_edge(from_id=f.curator_id, to_id=uuid4())
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = [e]
        rt.file_repo.get.side_effect = RuntimeError("file gone")
        rt.bundle_repo.get_memberships_for_file.return_value = []
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_lineage_tab_file_repo_returns_none(self, qapp, qtbot):
        """If file_repo.get returns None, fall back to (uuid) placeholder."""
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        e = _make_lineage_edge(from_id=f.curator_id, to_id=uuid4())
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = [e]
        rt.file_repo.get.return_value = None
        rt.bundle_repo.get_memberships_for_file.return_value = []
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_lineage_tab_edge_kind_without_value(self, qapp, qtbot):
        """edge.edge_kind has no .value attr → uses str()."""
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        e = _make_lineage_edge(from_id=f.curator_id, to_id=uuid4())
        e.edge_kind = "raw_string_kind"  # no .value
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = [e]
        rt.file_repo.get.return_value = MagicMock(source_path="/o")
        rt.bundle_repo.get_memberships_for_file.return_value = []
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_bundles_tab_with_memberships(self, qapp, qtbot):
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        m = _make_membership()
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.return_value = [m]
        rt.bundle_repo.get.return_value = _make_bundle(name="B1")
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_bundles_tab_memberships_exception(self, qapp, qtbot):
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.side_effect = RuntimeError("fail")
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_bundles_tab_bundle_get_exception(self, qapp, qtbot):
        """If bundle_repo.get raises while resolving the bundle name, use
        UUID placeholder."""
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        m = _make_membership()
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.return_value = [m]
        rt.bundle_repo.get.side_effect = RuntimeError("bundle gone")
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_bundles_tab_bundle_get_returns_none(self, qapp, qtbot):
        """bundle_repo.get returns None → "(unnamed)"."""
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        m = _make_membership()
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.return_value = [m]
        rt.bundle_repo.get.return_value = None
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)

    def test_bundles_tab_bundle_unnamed(self, qapp, qtbot):
        """Bundle with name=None → "(unnamed)"."""
        from curator.gui.dialogs import FileInspectDialog
        f = _make_file()
        m = _make_membership()
        rt = MagicMock()
        rt.lineage_repo.get_edges_for.return_value = []
        rt.bundle_repo.get_memberships_for_file.return_value = [m]
        rt.bundle_repo.get.return_value = _make_bundle(name=None)
        dlg = FileInspectDialog(f, rt)
        qtbot.addWidget(dlg)


# ===========================================================================
# ForecastDialog
# ===========================================================================


def _make_forecast(
    *, drive_path="C:/", status="fit_ok",
    current_pct=50.0, current_used_gb=500.0,
    current_total_gb=1000.0, current_free_gb=500.0,
    slope_gb_per_day=0.5, fit_r_squared=0.95,
    status_message="OK", days_to_95pct=100, days_to_99pct=200,
    eta_95pct=None, eta_99pct=None, monthly_history=None,
):
    f = MagicMock()
    f.drive_path = drive_path
    f.status = status
    f.current_pct = current_pct
    f.current_used_gb = current_used_gb
    f.current_total_gb = current_total_gb
    f.current_free_gb = current_free_gb
    f.slope_gb_per_day = slope_gb_per_day
    f.fit_r_squared = fit_r_squared
    f.status_message = status_message
    f.days_to_95pct = days_to_95pct
    f.days_to_99pct = days_to_99pct
    f.eta_95pct = eta_95pct or datetime(2026, 8, 1)
    f.eta_99pct = eta_99pct or datetime(2026, 11, 1)
    f.monthly_history = monthly_history or []
    return f


def _make_monthly_bucket(*, month="2026-04", file_count=1000, gb_added=2.5):
    b = MagicMock()
    b.month = month
    b.file_count = file_count
    b.gb_added = gb_added
    return b


class TestForecastDialog:
    def test_basic_construction_no_forecasts(self, qapp, qtbot):
        from curator.gui.dialogs import ForecastDialog
        rt = MagicMock()
        rt.forecast.compute_all_drives.return_value = []
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)
        assert "forecast" in dlg.windowTitle().lower()
        assert dlg.last_forecasts == []

    def test_construction_with_forecasts(self, qapp, qtbot):
        from curator.gui.dialogs import ForecastDialog
        rt = MagicMock()
        f = _make_forecast(status="fit_ok")
        rt.forecast.compute_all_drives.return_value = [f]
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)
        assert dlg.last_forecasts == [f]

    def test_refresh_exception_shows_error(self, qapp, qtbot):
        from curator.gui.dialogs import ForecastDialog
        rt = MagicMock()
        rt.forecast.compute_all_drives.side_effect = RuntimeError("forecast fail")
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)
        # last_forecasts not updated; an Error label inserted
        assert dlg.last_forecasts == []

    @pytest.mark.parametrize("status", [
        "fit_ok", "past_95pct", "past_99pct",
        "insufficient_data", "no_growth", "unknown_status",
    ])
    def test_drive_card_each_status(self, qapp, qtbot, status):
        from curator.gui.dialogs import ForecastDialog
        rt = MagicMock()
        f = _make_forecast(status=status)
        rt.forecast.compute_all_drives.return_value = [f]
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)

    def test_drive_card_no_slope(self, qapp, qtbot):
        """No slope_gb_per_day → no fill rate text."""
        from curator.gui.dialogs import ForecastDialog
        rt = MagicMock()
        f = _make_forecast(slope_gb_per_day=None)
        rt.forecast.compute_all_drives.return_value = [f]
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)

    def test_drive_card_no_projections(self, qapp, qtbot):
        """days_to_95pct + days_to_99pct both None → no projection table."""
        from curator.gui.dialogs import ForecastDialog
        rt = MagicMock()
        f = _make_forecast(days_to_95pct=None, days_to_99pct=None)
        rt.forecast.compute_all_drives.return_value = [f]
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)

    def test_drive_card_only_95pct(self, qapp, qtbot):
        """Only 95pct projection set, not 99pct."""
        from curator.gui.dialogs import ForecastDialog
        rt = MagicMock()
        f = _make_forecast(days_to_99pct=None, eta_99pct=None)
        rt.forecast.compute_all_drives.return_value = [f]
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)

    def test_drive_card_with_history(self, qapp, qtbot):
        """monthly_history populated → history table rendered."""
        from curator.gui.dialogs import ForecastDialog
        rt = MagicMock()
        hist = [_make_monthly_bucket(month=f"2026-0{i}") for i in range(1, 7)]
        f = _make_forecast(monthly_history=hist)
        rt.forecast.compute_all_drives.return_value = [f]
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)

    def test_refresh_clears_old_cards(self, qapp, qtbot):
        """Calling refresh removes the previous cards before adding new ones."""
        from curator.gui.dialogs import ForecastDialog
        rt = MagicMock()
        f1 = _make_forecast(drive_path="C:/")
        rt.forecast.compute_all_drives.return_value = [f1]
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)
        # Now change forecasts and refresh
        f2 = _make_forecast(drive_path="D:/")
        rt.forecast.compute_all_drives.return_value = [f2]
        dlg._refresh()
        assert dlg.last_forecasts == [f2]

    def test_refresh_button_click(self, qapp, qtbot):
        """Refresh button triggers _refresh."""
        from curator.gui.dialogs import ForecastDialog
        from PySide6.QtCore import Qt
        rt = MagicMock()
        rt.forecast.compute_all_drives.return_value = []
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)
        qtbot.mouseClick(dlg._btn_refresh, Qt.MouseButton.LeftButton)
        # compute_all_drives called twice: once in init, once on click
        assert rt.forecast.compute_all_drives.call_count >= 2

    def test_close_button_rejects(self, qapp, qtbot):
        """Close button calls dialog.reject."""
        from curator.gui.dialogs import ForecastDialog
        from PySide6.QtCore import Qt
        rt = MagicMock()
        rt.forecast.compute_all_drives.return_value = []
        dlg = ForecastDialog(rt)
        qtbot.addWidget(dlg)
        # Stub reject so we can verify it's called
        dlg.reject = MagicMock()
        qtbot.mouseClick(dlg._btn_close, Qt.MouseButton.LeftButton)
        dlg.reject.assert_called_once()
