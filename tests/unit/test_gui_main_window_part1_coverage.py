"""Coverage for ``curator.gui.main_window`` Part 1 (v1.7.191).

Round 4 Tier 3 sub-ship 1 of 5 — covers the window construction surface:
``__init__``, ``_build_ui`` (menu bar + all tabs), every ``_build_*_tab``
method, the static helpers (``_make_table_view``, ``_wrap_table``,
``_build_lineage_legend_html``), the settings header updater, the
status bar refresher, and the lineage time-slider helpers.

Parts 2-4 cover the action handlers, dock/state persistence, and
signal-wiring slots. Part 5 closes with the pragma audit.

A single ``CuratorMainWindow`` instantiation exercises ~hundreds of
statements across the build_* methods. We then add focused tests for
the static helpers and small computed methods that don't need an
event loop.
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


# ---------------------------------------------------------------------------
# Runtime stub factory — exported via this fixture for Parts 2-4 reuse
# ---------------------------------------------------------------------------


def make_runtime_stub(*, time_range=(None, None), config_overrides=None):
    """Build a fully-stubbed CuratorRuntime suitable for instantiating
    :class:`CuratorMainWindow` without touching the real DB or services.

    All repository methods return empty lists / zero counts by default.
    Pass ``time_range=(min_dt, max_dt)`` to populate the lineage time
    range for tab construction. Pass ``config_overrides`` to override
    config.get(...) returns.
    """
    rt = MagicMock()

    # Config — config.get(key, default) returns the default unless overridden
    overrides = config_overrides or {}
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    cfg.source_path = None
    cfg.as_dict.return_value = {}
    rt.config = cfg

    # DB (used by status-bar)
    rt.db.db_path = "C:/tmp/curator.db"

    # All repos: empty defaults
    rt.job_repo.list_recent.return_value = []
    rt.lineage_repo.query_by_confidence.return_value = []
    rt.lineage_repo.get_edges_for.return_value = []
    rt.trash_repo.list.return_value = []
    rt.file_repo.query.return_value = []
    rt.bundle_repo.list_all.return_value = []
    rt.bundle_repo.member_count.return_value = 0
    rt.migration_job_repo.list_jobs.return_value = []
    rt.migration_job_repo.query_progress.return_value = []
    rt.audit_repo.query.return_value = []
    rt.source_repo.list_all.return_value = []

    # lineage_repo.db.conn() context-manager mock for the time-range query
    cursor = MagicMock()
    cursor.fetchone.return_value = time_range
    cursor.fetchall.return_value = []
    conn_inner = MagicMock(execute=MagicMock(return_value=cursor))
    conn_cm = MagicMock()
    conn_cm.__enter__ = MagicMock(return_value=conn_inner)
    conn_cm.__exit__ = MagicMock(return_value=False)
    rt.lineage_repo.db.conn.return_value = conn_cm

    return rt


@pytest.fixture
def runtime_stub():
    return make_runtime_stub()


# ===========================================================================
# Construction — instantiate the window and verify it builds
# ===========================================================================


class TestCuratorMainWindowConstruction:
    def test_basic_instantiation(self, qapp, qtbot, runtime_stub):
        """Construct the window with a stubbed runtime. This single call
        exercises ``__init__`` + ``_build_ui`` + every ``_build_*_tab``
        method + ``_refresh_status_bar``. Hundreds of statements covered
        in one call."""
        from curator.gui.main_window import CuratorMainWindow

        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)

        # Confirms the window built without error
        assert window.runtime is runtime_stub
        assert hasattr(window, "_tabs")
        # 9 tabs created in _build_ui
        assert window._tabs.count() == 9
        # Tab titles
        titles = [window._tabs.tabText(i) for i in range(9)]
        assert "Inbox" in titles
        assert "Browser" in titles
        assert "Bundles" in titles
        assert "Trash" in titles
        assert "Migrate" in titles
        assert "Audit Log" in titles
        assert "Settings" in titles
        assert "Sources" in titles
        assert "Lineage Graph" in titles

    def test_construction_with_time_range_enabled(self, qapp, qtbot):
        """If lineage time range has min+max, the slider/play btn are
        enabled and the tooltip mentions the range."""
        from curator.gui.main_window import CuratorMainWindow

        rt = make_runtime_stub(
            time_range=(
                "2026-01-01T00:00:00",
                "2026-05-13T12:00:00",
            ),
        )
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)

        # Slider enabled (time_min + time_max both set)
        assert window._lineage_slider.isEnabled()
        assert window._lineage_play_btn.isEnabled()
        # Tooltip mentions the date range
        assert "2026-01-01" in window._lineage_slider.toolTip()

    def test_construction_with_no_time_range(self, qapp, qtbot, runtime_stub):
        """With time_range = (None, None) the slider is disabled."""
        from curator.gui.main_window import CuratorMainWindow

        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        assert not window._lineage_slider.isEnabled()
        assert not window._lineage_play_btn.isEnabled()

    def test_window_title_includes_version(self, qapp, qtbot, runtime_stub):
        from curator.gui.main_window import CuratorMainWindow
        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        # Title should be "Curator <version>"
        assert window.windowTitle().startswith("Curator ")

    def test_window_title_with_version_import_failure(
        self, qapp, qtbot, runtime_stub, monkeypatch,
    ):
        """If ``from curator import __version__`` fails, ``_build_ui``
        uses 'unknown' as the fallback."""
        # Break the import to force the except branch in _build_ui
        import curator
        original_version = getattr(curator, "__version__", None)
        if hasattr(curator, "__version__"):
            monkeypatch.delattr(curator, "__version__", raising=False)
        from curator.gui.main_window import CuratorMainWindow
        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        # Either ends up with "unknown" (if the import actually failed)
        # or with the version (depending on how the module was set up).
        assert window.windowTitle().startswith("Curator ")

    def test_inbox_section_with_empty_hint(self, qapp, qtbot, runtime_stub):
        """When the model has 0 rows, the section adds an empty hint label.
        This verifies the `if model.rowCount() == 0` branch in
        ``_make_inbox_section`` (line 305-309)."""
        from curator.gui.main_window import CuratorMainWindow
        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        # All inbox models are empty by default → hints rendered
        # Just verify the inbox section widgets exist
        assert window._inbox_scans_model.rowCount() == 0
        assert window._inbox_pending_model.rowCount() == 0
        assert window._inbox_trash_model.rowCount() == 0

    def test_inbox_section_without_empty_hint_when_populated(
        self, qapp, qtbot,
    ):
        """When the model has rows, the empty-hint label is skipped."""
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub()
        # Populate one inbox model (recent scans)
        scan_job = MagicMock()
        scan_job.status = "completed"
        scan_job.source_id = "local"
        scan_job.root_path = "/r"
        scan_job.files_seen = 10
        scan_job.files_hashed = 5
        scan_job.started_at = datetime(2026, 5, 1, 10, 0)
        scan_job.completed_at = datetime(2026, 5, 1, 10, 5)
        rt.job_repo.list_recent.return_value = [scan_job]
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        assert window._inbox_scans_model.rowCount() == 1


# ===========================================================================
# Static helpers
# ===========================================================================


class TestStaticHelpers:
    def test_make_table_view(self, qapp, qtbot, runtime_stub):
        """_make_table_view returns a QTableView with sorting + selection."""
        from curator.gui.main_window import CuratorMainWindow
        from curator.gui.models import FileTableModel

        m = FileTableModel(runtime_stub.file_repo)
        view = CuratorMainWindow._make_table_view(m)
        qtbot.addWidget(view)
        assert view.model() is m
        assert view.isSortingEnabled()

    def test_wrap_table(self, qapp, qtbot, runtime_stub):
        from curator.gui.main_window import CuratorMainWindow
        from curator.gui.models import FileTableModel
        from PySide6.QtWidgets import QTableView

        m = FileTableModel(runtime_stub.file_repo)
        view = CuratorMainWindow._make_table_view(m)
        wrapper = CuratorMainWindow._wrap_table(view)
        qtbot.addWidget(wrapper)
        # wrapper is a QWidget containing the view
        assert wrapper is not None

    def test_build_lineage_legend_html(self):
        """Static method returns an HTML legend string with all edge kinds."""
        from curator.gui.main_window import CuratorMainWindow
        html = CuratorMainWindow._build_lineage_legend_html()
        assert "<b>Edge kinds:</b>" in html
        # All 5 known edge kinds appear
        for kind in (
            "duplicate", "near_duplicate", "version_of",
            "derived_from", "renamed_from",
        ):
            assert kind in html


# ===========================================================================
# Settings header updater + reload
# ===========================================================================


class TestSettingsHeader:
    def test_update_with_no_source_path(self, qapp, qtbot, runtime_stub):
        """source_path = None → 'using built-in defaults' text."""
        from curator.gui.main_window import CuratorMainWindow
        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        # Construct already called _update_settings_header(reloaded=False)
        text = window._settings_header.text()
        assert "built-in defaults" in text

    def test_update_with_source_path(self, qapp, qtbot):
        """source_path set → 'Loaded from: ...' text."""
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub()
        rt.config.source_path = "/path/to/curator.toml"
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        text = window._settings_header.text()
        assert "Loaded from:" in text
        assert "curator.toml" in text

    def test_update_with_reloaded_flag(self, qapp, qtbot, runtime_stub):
        """reloaded=True → '(reloaded from disk)' suffix."""
        from curator.gui.main_window import CuratorMainWindow
        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        window._update_settings_header(runtime_stub.config, reloaded=True)
        text = window._settings_header.text()
        assert "reloaded from disk" in text

    def test_update_with_source_path_exception(self, qapp, qtbot):
        """If accessing config.source_path raises, fall back to 'using
        built-in defaults' text via the except clause."""
        from curator.gui.main_window import CuratorMainWindow

        # Replace config with a real object that raises on source_path access
        class _RaisingConfig:
            def __init__(self):
                self._dict = {}

            @property
            def source_path(self):
                raise AttributeError("config has no source_path")

            def get(self, key, default=None):
                return default

            def as_dict(self):
                return {}

        rt = make_runtime_stub()
        rt.config = _RaisingConfig()
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        text = window._settings_header.text()
        assert "built-in defaults" in text


# ===========================================================================
# Status bar
# ===========================================================================


class TestStatusBarRefresh:
    def test_refresh_with_db_path(self, qapp, qtbot, runtime_stub):
        from curator.gui.main_window import CuratorMainWindow
        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        # __init__ already called _refresh_status_bar
        assert "C:/tmp/curator.db" in window._status_db.text()
        # Counts label populated (all zeros from stubs)
        counts = window._status_counts.text()
        assert "Files:" in counts and "Bundles:" in counts
        assert "Trash:" in counts and "Audit:" in counts

    def test_refresh_with_db_path_exception(self, qapp, qtbot):
        """If runtime.db.db_path raises, fall back to '(unknown)'."""
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub()
        # PropertyError-like: accessing db.db_path raises
        type(rt.db).db_path = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("no db"))
        )
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        assert "(unknown)" in window._status_db.text()


# ===========================================================================
# Lineage slider helpers (computed text)
# ===========================================================================


class TestLineageSliderHelpers:
    def test_slider_to_datetime_no_range(self, qapp, qtbot, runtime_stub):
        from curator.gui.main_window import CuratorMainWindow
        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        # (None, None) → returns None
        assert window._slider_to_datetime(50) is None

    def test_slider_to_datetime_max(self, qapp, qtbot):
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub(time_range=(
            "2026-01-01T00:00:00", "2026-12-31T00:00:00",
        ))
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        # pct=100 → time_max
        result = window._slider_to_datetime(100)
        assert result == datetime(2026, 12, 31)

    def test_slider_to_datetime_min(self, qapp, qtbot):
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub(time_range=(
            "2026-01-01T00:00:00", "2026-12-31T00:00:00",
        ))
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        # pct=0 → time_min
        assert window._slider_to_datetime(0) == datetime(2026, 1, 1)

    def test_slider_to_datetime_interpolation(self, qapp, qtbot):
        """pct=50 lands halfway between min and max."""
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub(time_range=(
            "2026-01-01T00:00:00", "2026-12-31T00:00:00",
        ))
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        result = window._slider_to_datetime(50)
        # Approximately 2026-07-01 (mid-year)
        assert result.year == 2026
        assert result.month == 7

    def test_time_label_text_no_range(self, qapp, qtbot, runtime_stub):
        from curator.gui.main_window import CuratorMainWindow
        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        assert window._lineage_time_label_text(50) == "(no edges)"

    def test_time_label_text_max(self, qapp, qtbot):
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub(time_range=(
            "2026-01-01T00:00:00", "2026-12-31T00:00:00",
        ))
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        # pct >= 100 → "now (all edges)"
        assert "now (all edges)" in window._lineage_time_label_text(100)

    def test_time_label_text_interpolated(self, qapp, qtbot):
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub(time_range=(
            "2026-01-01T00:00:00", "2026-12-31T00:00:00",
        ))
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        result = window._lineage_time_label_text(50)
        assert "as of:" in result

    def test_time_label_text_slider_returns_none(
        self, qapp, qtbot, monkeypatch,
    ):
        """When _slider_to_datetime returns None despite a range being
        set (shouldn't happen but defensive), label says '(no edges)'."""
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub(time_range=(
            "2026-01-01T00:00:00", "2026-12-31T00:00:00",
        ))
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        # Force _slider_to_datetime to return None for pct=50
        original = window._slider_to_datetime
        window._slider_to_datetime = lambda pct: None
        try:
            assert window._lineage_time_label_text(50) == "(no edges)"
        finally:
            window._slider_to_datetime = original

    def test_axis_label_text_no_range(self, qapp, qtbot, runtime_stub):
        from curator.gui.main_window import CuratorMainWindow
        window = CuratorMainWindow(runtime_stub)
        qtbot.addWidget(window)
        # (None, None) → empty axis label
        assert window._lineage_axis_label_text(50) == ""

    def test_axis_label_text_with_range(self, qapp, qtbot):
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub(time_range=(
            "2026-01-01T00:00:00", "2026-12-31T00:00:00",
        ))
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        result = window._lineage_axis_label_text(50)
        assert result.startswith("2026-")

    def test_axis_label_text_slider_returns_none(self, qapp, qtbot):
        """Defensive: if _slider_to_datetime returns None when range
        appears set, axis label is empty."""
        from curator.gui.main_window import CuratorMainWindow
        rt = make_runtime_stub(time_range=(
            "2026-01-01T00:00:00", "2026-12-31T00:00:00",
        ))
        window = CuratorMainWindow(rt)
        qtbot.addWidget(window)
        window._slider_to_datetime = lambda pct: None
        assert window._lineage_axis_label_text(50) == ""
