"""Coverage for ``curator.gui.main_window`` Part 2 (v1.7.192).

Round 4 Tier 3 sub-ship 2 of 5 — covers the action handlers:

* All seven ``_slot_open_*`` Tools-menu dialog slots (happy + import-error)
* ``_slot_tools_placeholder`` (known key + unknown key)
* ``_slot_run_workflow`` (4 branches: success / not-found / path-resolution exception / startfile exception)
* ``_show_about`` and ``_show_workflows_about``
* ``refresh_all``
* ``_slot_settings_reload`` + ``_perform_settings_reload``
* All four audit slots (refresh_dropdowns, apply_filter, clear_filter, count_label)
* All four lineage slots (slider_changed, clear_time_filter, play_toggle, play_tick)

Parts 3 + 4 cover migrate/sources slots and trash/restore/bundle slots.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Reuse the runtime stub factory from Part 1
from tests.unit.test_gui_main_window_part1_coverage import make_runtime_stub


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def silence_qmessagebox(monkeypatch):
    """Replace QMessageBox.{about, critical, warning, information,
    question} with MagicMocks so tests don't actually pop dialogs.
    Returns a dict so tests can assert on the calls."""
    from PySide6.QtWidgets import QMessageBox

    methods = ("about", "critical", "warning", "information", "question")
    captured = {}
    for name in methods:
        mock = MagicMock()
        monkeypatch.setattr(QMessageBox, name, mock)
        captured[name] = mock
    return captured


@pytest.fixture
def window(qapp, qtbot):
    """A built, qtbot-tracked CuratorMainWindow with default stub runtime."""
    from curator.gui.main_window import CuratorMainWindow
    rt = make_runtime_stub()
    w = CuratorMainWindow(rt)
    qtbot.addWidget(w)
    return w


# ===========================================================================
# Tools-menu open-dialog slots
# ===========================================================================


@pytest.fixture
def stub_dialog_factory(monkeypatch):
    """Returns a factory that, given a dialog class name, replaces
    ``curator.gui.dialogs.<name>`` with a stub that records `.exec()`
    calls. Returns the stub class so tests can verify."""
    captured = {}

    def _stub(class_name: str):
        import curator.gui.dialogs as dialogs

        class _StubDialog:
            instances = []

            def __init__(self, *args, **kwargs):
                _StubDialog.instances.append(self)
                self.exec_called = False

            def exec(self):
                self.exec_called = True
                return 1  # QDialog.Accepted

        _StubDialog.__name__ = class_name
        monkeypatch.setattr(dialogs, class_name, _StubDialog, raising=False)
        captured[class_name] = _StubDialog
        return _StubDialog

    return _stub


class TestOpenDialogSlots:
    """Each Tools-menu slot has the same shape: try-import-dialog,
    construct, exec. Test the happy path + the import-error path."""

    @pytest.mark.parametrize("slot_name,dialog_class_name", [
        ("_slot_open_health_check", "HealthCheckDialog"),
        ("_slot_open_scan_dialog", "ScanDialog"),
        ("_slot_open_group_dialog", "GroupDialog"),
        ("_slot_open_forecast", "ForecastDialog"),
        ("_slot_open_tier_scan", "TierDialog"),
        ("_slot_open_version_stacks", "VersionStackDialog"),
        ("_slot_open_cleanup_dialog", "CleanupDialog"),
    ])
    def test_open_dialog_happy_path(
        self, window, stub_dialog_factory, slot_name, dialog_class_name,
    ):
        stub_cls = stub_dialog_factory(dialog_class_name)
        getattr(window, slot_name)()
        # One instance constructed; exec() called on it
        assert len(stub_cls.instances) == 1
        assert stub_cls.instances[0].exec_called

    @pytest.mark.parametrize("slot_name,error_title_fragment", [
        ("_slot_open_health_check", "Health check"),
        ("_slot_open_scan_dialog", "Scan dialog"),
        ("_slot_open_group_dialog", "Group dialog"),
        ("_slot_open_forecast", "Forecast"),
        ("_slot_open_tier_scan", "Tier dialog"),
        ("_slot_open_version_stacks", "Version stacks"),
        ("_slot_open_cleanup_dialog", "Cleanup dialog"),
    ])
    def test_open_dialog_import_error(
        self, window, monkeypatch, silence_qmessagebox,
        slot_name, error_title_fragment,
    ):
        """When the dialog import fails, QMessageBox.critical is shown."""
        # Patch the dialogs module so attribute access raises
        import curator.gui.dialogs as dialogs
        for cls_name in ("HealthCheckDialog", "ScanDialog", "GroupDialog",
                         "ForecastDialog", "TierDialog", "VersionStackDialog",
                         "CleanupDialog"):
            if hasattr(dialogs, cls_name):
                monkeypatch.delattr(dialogs, cls_name, raising=False)

        # Make any attribute access raise
        def _raising_getattr(name):
            raise ImportError(f"Cannot import {name} (simulated)")

        monkeypatch.setattr(dialogs, "__getattr__", _raising_getattr,
                            raising=False)

        getattr(window, slot_name)()
        # critical was called with a title mentioning the unavailable feature
        silence_qmessagebox["critical"].assert_called_once()
        args, kwargs = silence_qmessagebox["critical"].call_args
        # args = (parent, title, body)
        assert error_title_fragment in args[1]


# ===========================================================================
# Tools placeholder slot
# ===========================================================================


class TestToolsPlaceholder:
    def test_known_key(self, window, silence_qmessagebox):
        window._slot_tools_placeholder("sources")
        silence_qmessagebox["information"].assert_called_once()
        # Body mentions the sources guidance
        args, _ = silence_qmessagebox["information"].call_args
        assert "Sources Manager" in args[2]

    def test_unknown_key(self, window, silence_qmessagebox):
        window._slot_tools_placeholder("totally_bogus_key")
        silence_qmessagebox["information"].assert_called_once()
        args, _ = silence_qmessagebox["information"].call_args
        assert "planned for v1.7" in args[2]


# ===========================================================================
# Workflow runner
# ===========================================================================


class TestRunWorkflow:
    def test_happy_path(self, window, monkeypatch, tmp_path):
        """When the script file exists, os.startfile is called."""
        # Make Path resolution land on tmp_path so we can place a real file
        import curator
        fake_repo_root = tmp_path
        wf_dir = fake_repo_root / "scripts" / "workflows"
        wf_dir.mkdir(parents=True)
        script = wf_dir / "test_script.bat"
        script.write_text("echo hi")

        # Patch curator.__file__ to land at tmp_path/src/curator/__init__.py
        # so the .parent.parent path resolution lands at tmp_path.
        # Actually the code does pkg_root.parent.parent — so we need
        # curator.__file__ = .../src/curator/__init__.py
        fake_pkg = tmp_path / "src" / "curator" / "__init__.py"
        fake_pkg.parent.mkdir(parents=True)
        fake_pkg.write_text("")
        monkeypatch.setattr(curator, "__file__", str(fake_pkg))

        startfile_mock = MagicMock()
        monkeypatch.setattr(os, "startfile", startfile_mock, raising=False)

        window._slot_run_workflow("test_script.bat")
        startfile_mock.assert_called_once()

    def test_path_resolution_exception(
        self, window, monkeypatch, silence_qmessagebox,
    ):
        """If __file__ access raises, show a 'Workflow launch failed' warning."""
        import curator
        # Patch __file__ to raise on access via a property
        class _FakeMod:
            @property
            def __file__(self):
                raise RuntimeError("simulated")
        # Direct attribute-replacement is simpler — set __file__ to a property descriptor
        # by replacing the module-level Path call path. We patch Path itself.
        monkeypatch.setattr(
            "curator.gui.main_window.Path",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        window._slot_run_workflow("any.bat")
        silence_qmessagebox["warning"].assert_called_once()
        args, _ = silence_qmessagebox["warning"].call_args
        assert "Workflow launch failed" in args[1]

    def test_script_not_found(
        self, window, monkeypatch, silence_qmessagebox, tmp_path,
    ):
        """When the script doesn't exist, show 'Workflow not found'."""
        import curator
        fake_pkg = tmp_path / "src" / "curator" / "__init__.py"
        fake_pkg.parent.mkdir(parents=True)
        fake_pkg.write_text("")
        monkeypatch.setattr(curator, "__file__", str(fake_pkg))
        # scripts/workflows/ dir doesn't exist → script_path.exists() False

        window._slot_run_workflow("nonexistent.bat")
        silence_qmessagebox["warning"].assert_called_once()
        args, _ = silence_qmessagebox["warning"].call_args
        assert "Workflow not found" in args[1]

    def test_startfile_exception(
        self, window, monkeypatch, silence_qmessagebox, tmp_path,
    ):
        """When os.startfile raises, show 'Workflow launch failed'."""
        import curator
        fake_repo = tmp_path
        wf_dir = fake_repo / "scripts" / "workflows"
        wf_dir.mkdir(parents=True)
        script = wf_dir / "boom.bat"
        script.write_text("echo")

        fake_pkg = tmp_path / "src" / "curator" / "__init__.py"
        fake_pkg.parent.mkdir(parents=True)
        fake_pkg.write_text("")
        monkeypatch.setattr(curator, "__file__", str(fake_pkg))

        startfile_mock = MagicMock(side_effect=OSError("permission denied"))
        monkeypatch.setattr(os, "startfile", startfile_mock, raising=False)

        window._slot_run_workflow("boom.bat")
        silence_qmessagebox["warning"].assert_called_once()
        args, _ = silence_qmessagebox["warning"].call_args
        assert "Workflow launch failed" in args[1]


# ===========================================================================
# About + workflows about
# ===========================================================================


class TestShowAbout:
    def test_show_about_calls_messagebox_about(
        self, window, silence_qmessagebox,
    ):
        window._show_about()
        silence_qmessagebox["about"].assert_called_once()

    def test_show_about_version_import_failure(
        self, window, silence_qmessagebox, monkeypatch,
    ):
        """If `from curator import __version__` raises, falls back to
        'unknown' version string."""
        import curator
        # Force the import to look like it failed
        if hasattr(curator, "__version__"):
            monkeypatch.delattr(curator, "__version__", raising=False)
        window._show_about()
        silence_qmessagebox["about"].assert_called_once()

    def test_show_workflows_about(self, window, silence_qmessagebox):
        window._show_workflows_about()
        silence_qmessagebox["information"].assert_called_once()


# ===========================================================================
# refresh_all
# ===========================================================================


class TestRefreshAll:
    def test_refresh_all_calls_each_model_refresh(self, window):
        # Spy on every model's refresh + the status bar update
        window._files_model.refresh = MagicMock()
        window._bundles_model.refresh = MagicMock()
        window._trash_model.refresh = MagicMock()
        window._audit_model.refresh = MagicMock()
        window._inbox_scans_model.refresh = MagicMock()
        window._inbox_pending_model.refresh = MagicMock()
        window._inbox_trash_model.refresh = MagicMock()
        window._lineage_view.refresh = MagicMock()

        # Spy on the migrate refresh slot
        window._slot_migrate_refresh = MagicMock()

        window.refresh_all()

        window._files_model.refresh.assert_called_once()
        window._bundles_model.refresh.assert_called_once()
        window._trash_model.refresh.assert_called_once()
        window._audit_model.refresh.assert_called_once()
        window._inbox_scans_model.refresh.assert_called_once()
        window._inbox_pending_model.refresh.assert_called_once()
        window._inbox_trash_model.refresh.assert_called_once()
        window._lineage_view.refresh.assert_called_once()
        window._slot_migrate_refresh.assert_called_once()

    def test_refresh_all_without_lineage_view(self, window):
        """If _lineage_view is None or missing, refresh_all still works."""
        # Replace lineage_view with None to trigger the defensive branch
        window._lineage_view = None
        # Should not raise
        window.refresh_all()


# ===========================================================================
# Settings reload
# ===========================================================================


class TestSettingsReload:
    def test_perform_reload_happy_path_no_source(
        self, window, monkeypatch,
    ):
        """When source_path is None and Config.load() succeeds, return
        the friendly 'no TOML file found' message."""
        import curator.config as cfg_mod

        class _FakeConfig:
            source_path = None

        fake_load = MagicMock(return_value=_FakeConfig())
        monkeypatch.setattr(cfg_mod.Config, "load", fake_load)
        window.runtime.config.source_path = None
        success, message, fresh = window._perform_settings_reload()
        assert success
        assert "no TOML file found" in message
        assert fresh is not None

    def test_perform_reload_happy_path_with_source(
        self, window, monkeypatch, tmp_path,
    ):
        """When source_path is set, Config.load(explicit_path=src) returns
        a config with that source_path."""
        import curator.config as cfg_mod

        toml_path = tmp_path / "curator.toml"
        toml_path.write_text("")

        class _FakeConfig:
            source_path = toml_path

        fake_load = MagicMock(return_value=_FakeConfig())
        monkeypatch.setattr(cfg_mod.Config, "load", fake_load)
        window.runtime.config.source_path = str(toml_path)
        success, message, fresh = window._perform_settings_reload()
        assert success
        assert "Reloaded from" in message
        assert str(toml_path) in message

    def test_perform_reload_failure(self, window, monkeypatch):
        """When Config.load raises, return (False, error_msg, None)."""
        import curator.config as cfg_mod
        monkeypatch.setattr(
            cfg_mod.Config, "load",
            MagicMock(side_effect=RuntimeError("bad TOML")),
        )
        success, message, fresh = window._perform_settings_reload()
        assert not success
        assert "Failed to reload" in message
        assert fresh is None

    def test_slot_reload_happy_path(self, window, monkeypatch):
        """Happy path: _perform_settings_reload succeeds → updates model + header."""
        fresh_cfg = MagicMock()
        fresh_cfg.source_path = "/path/to/cfg.toml"
        monkeypatch.setattr(
            window, "_perform_settings_reload",
            lambda: (True, "Reloaded from /path/to/cfg.toml", fresh_cfg),
        )
        window._settings_model.set_config = MagicMock()
        window._slot_settings_reload()
        window._settings_model.set_config.assert_called_once_with(fresh_cfg)

    def test_slot_reload_failure_shows_warning(
        self, window, monkeypatch, silence_qmessagebox,
    ):
        """Failure path: warning dialog is shown, model not touched."""
        monkeypatch.setattr(
            window, "_perform_settings_reload",
            lambda: (False, "Bad TOML", None),
        )
        window._settings_model.set_config = MagicMock()
        window._slot_settings_reload()
        silence_qmessagebox["warning"].assert_called_once()
        # Model was NOT updated
        window._settings_model.set_config.assert_not_called()


# ===========================================================================
# Audit slots
# ===========================================================================


def _make_audit_entry(actor=None, action=None, entity_type=None):
    e = MagicMock()
    e.actor = actor
    e.action = action
    e.entity_type = entity_type
    return e


class TestAuditDropdowns:
    def test_populates_distinct_values(self, window):
        """Refresh dropdowns reads audit_repo.query(limit=10000) and
        populates the actor / action / entity_type combos with distinct values."""
        window.runtime.audit_repo.query.return_value = [
            _make_audit_entry(actor="cli.scan", action="scan.start", entity_type="scan_job"),
            _make_audit_entry(actor="cli.scan", action="scan.complete", entity_type="scan_job"),
            _make_audit_entry(actor="gui.bundles", action="bundle.create", entity_type="bundle"),
            _make_audit_entry(actor=None, action=None, entity_type=None),  # filtered out
        ]
        window._slot_audit_refresh_dropdowns()
        # actors: cli.scan, gui.bundles → 2 + (any) = 3
        assert window._audit_cb_actor.count() == 3
        # actions: scan.start, scan.complete, bundle.create → 3 + (any) = 4
        assert window._audit_cb_action.count() == 4
        # entity_types: scan_job, bundle → 2 + (any) = 3
        assert window._audit_cb_entity_type.count() == 3

    def test_exception_falls_back_to_empty(self, window):
        """If audit_repo.query raises, entries = []; dropdowns still have (any)."""
        window.runtime.audit_repo.query.side_effect = RuntimeError("db gone")
        window._slot_audit_refresh_dropdowns()
        # Only (any) remains
        assert window._audit_cb_actor.count() == 1

    def test_preserves_selection_on_rebuild(self, window):
        """If a value is selected before refresh and it's still present,
        the selection is preserved."""
        window.runtime.audit_repo.query.return_value = [
            _make_audit_entry(actor="cli.scan", action="scan.start", entity_type="scan_job"),
        ]
        window._slot_audit_refresh_dropdowns()
        # Select "cli.scan" → findData should find it
        idx = window._audit_cb_actor.findData("cli.scan")
        window._audit_cb_actor.setCurrentIndex(idx)
        assert window._audit_cb_actor.currentData() == "cli.scan"
        # Now refresh again — selection should be preserved
        window._slot_audit_refresh_dropdowns()
        assert window._audit_cb_actor.currentData() == "cli.scan"


class TestAuditApplyClearFilter:
    def test_apply_filter_with_hours(self, window):
        """Applying filter with hours > 0 builds a `since` datetime."""
        window._audit_sb_hours.setValue(2)
        window._audit_model.set_filter = MagicMock()
        window._audit_model.refresh = MagicMock()
        window._slot_audit_apply_filter()
        window._audit_model.set_filter.assert_called_once()
        kwargs = window._audit_model.set_filter.call_args.kwargs
        assert kwargs["since"] is not None  # Computed from now-2hr
        window._audit_model.refresh.assert_called_once()

    def test_apply_filter_no_hours(self, window):
        """Hours=0 → since=None."""
        window._audit_sb_hours.setValue(0)
        window._audit_model.set_filter = MagicMock()
        window._audit_model.refresh = MagicMock()
        window._slot_audit_apply_filter()
        kwargs = window._audit_model.set_filter.call_args.kwargs
        assert kwargs["since"] is None

    def test_apply_filter_with_entity_id(self, window):
        """Entity_id text is stripped + passed through."""
        window._audit_le_entity_id.setText("  abc-123  ")
        window._audit_model.set_filter = MagicMock()
        window._audit_model.refresh = MagicMock()
        window._slot_audit_apply_filter()
        kwargs = window._audit_model.set_filter.call_args.kwargs
        assert kwargs["entity_id"] == "abc-123"

    def test_apply_filter_empty_entity_id_becomes_none(self, window):
        """Blank or whitespace-only entity_id becomes None."""
        window._audit_le_entity_id.setText("   ")
        window._audit_model.set_filter = MagicMock()
        window._audit_model.refresh = MagicMock()
        window._slot_audit_apply_filter()
        kwargs = window._audit_model.set_filter.call_args.kwargs
        assert kwargs["entity_id"] is None

    def test_clear_filter_resets_widgets_and_model(self, window):
        """Clear resets all toolbar widgets + calls set_filter() (empty)."""
        window._audit_sb_hours.setValue(5)
        window._audit_le_entity_id.setText("xyz")
        window._audit_model.set_filter = MagicMock()
        window._audit_model.refresh = MagicMock()
        window._slot_audit_clear_filter()
        # Widgets reset
        assert window._audit_sb_hours.value() == 0
        assert window._audit_le_entity_id.text() == ""
        # set_filter() called with no args → all None → empty kwargs
        window._audit_model.set_filter.assert_called_once_with()


class TestUpdateAuditCountLabel:
    def test_no_filters(self, window):
        """No filter kwargs → label says '(no filters)'."""
        window._audit_model._filter_kwargs = {}
        window._update_audit_count_label()
        text = window._audit_lbl_count.text()
        assert "no filters" in text

    def test_with_filters(self, window):
        """Some filters set → label lists them."""
        window._audit_model._filter_kwargs = {
            "actor": "cli.scan", "action": "scan.start",
        }
        window._update_audit_count_label()
        text = window._audit_lbl_count.text()
        assert "actor=cli.scan" in text
        assert "action=scan.start" in text

    def test_with_since_filter(self, window):
        """Since filter is formatted to date+time."""
        window._audit_model._filter_kwargs = {
            "since": datetime(2026, 5, 1, 12, 0),
        }
        window._update_audit_count_label()
        text = window._audit_lbl_count.text()
        assert "since 2026-05-01" in text


# ===========================================================================
# Lineage slot handlers
# ===========================================================================


@pytest.fixture
def lineage_window(qapp, qtbot):
    """A window with a populated time range so the lineage slider is enabled."""
    from curator.gui.main_window import CuratorMainWindow
    rt = make_runtime_stub(time_range=(
        "2026-01-01T00:00:00", "2026-12-31T00:00:00",
    ))
    w = CuratorMainWindow(rt)
    qtbot.addWidget(w)
    return w


class TestLineageSliderSlots:
    def test_slot_slider_changed_at_max_clears_filter(self, lineage_window):
        """value=100 → clear_time_filter, no refresh-with-arg call."""
        lineage_window._lineage_view.clear_time_filter = MagicMock()
        lineage_window._lineage_view.refresh = MagicMock()
        lineage_window._slot_lineage_slider_changed(100)
        lineage_window._lineage_view.clear_time_filter.assert_called_once()
        lineage_window._lineage_view.refresh.assert_not_called()

    def test_slot_slider_changed_below_max_refreshes(self, lineage_window):
        """value<100 → refresh(max_detected_at=<computed datetime>)."""
        lineage_window._lineage_view.refresh = MagicMock()
        lineage_window._slot_lineage_slider_changed(50)
        lineage_window._lineage_view.refresh.assert_called_once()
        kwargs = lineage_window._lineage_view.refresh.call_args.kwargs
        assert kwargs["max_detected_at"] is not None

    def test_slot_slider_changed_dt_none_returns_early(
        self, lineage_window, monkeypatch,
    ):
        """If _slider_to_datetime returns None at value<100, return without
        calling refresh."""
        lineage_window._lineage_view.refresh = MagicMock()
        monkeypatch.setattr(
            lineage_window, "_slider_to_datetime", lambda pct: None,
        )
        lineage_window._slot_lineage_slider_changed(50)
        lineage_window._lineage_view.refresh.assert_not_called()

    def test_slot_clear_time_filter_resets_and_stops_playing(
        self, lineage_window,
    ):
        """clear_time_filter: slider→100, label updated, view filter cleared,
        play timer stopped if running."""
        from PySide6.QtCore import QTimer
        lineage_window._lineage_view.clear_time_filter = MagicMock()
        # Simulate a running play timer
        lineage_window._lineage_play_timer = QTimer(lineage_window)
        lineage_window._lineage_play_timer.start()
        lineage_window._slot_lineage_clear_time_filter()
        assert lineage_window._lineage_slider.value() == 100
        lineage_window._lineage_view.clear_time_filter.assert_called_once()
        # Play timer was stopped + cleared
        assert lineage_window._lineage_play_timer is None
        assert "Play" in lineage_window._lineage_play_btn.text()

    def test_slot_clear_time_filter_no_play_timer(self, lineage_window):
        """clear_time_filter works when no play timer is running."""
        lineage_window._lineage_view.clear_time_filter = MagicMock()
        lineage_window._lineage_play_timer = None
        # Should not raise
        lineage_window._slot_lineage_clear_time_filter()
        assert lineage_window._lineage_slider.value() == 100

    def test_slot_play_toggle_starts_when_idle(self, lineage_window):
        """Play toggle from idle → creates a QTimer + starts it + relabels btn."""
        lineage_window._lineage_play_timer = None
        lineage_window._lineage_slider.setValue(50)  # not at end
        lineage_window._slot_lineage_play_toggle()
        assert lineage_window._lineage_play_timer is not None
        # Stop the timer so it doesn't fire during teardown
        lineage_window._lineage_play_timer.stop()
        assert "Pause" in lineage_window._lineage_play_btn.text()

    def test_slot_play_toggle_pauses_when_playing(self, lineage_window):
        """Play toggle while playing → stops + clears timer + relabels."""
        from PySide6.QtCore import QTimer
        lineage_window._lineage_play_timer = QTimer(lineage_window)
        lineage_window._lineage_play_timer.start()
        lineage_window._slot_lineage_play_toggle()
        assert lineage_window._lineage_play_timer is None
        assert "Play" in lineage_window._lineage_play_btn.text()

    def test_slot_play_toggle_restarts_from_end(self, lineage_window):
        """Play toggle from value==100 → resets to 0 first."""
        lineage_window._lineage_play_timer = None
        lineage_window._lineage_slider.setValue(100)
        lineage_window._slot_lineage_play_toggle()
        # Slider was reset to 0
        assert lineage_window._lineage_slider.value() == 0
        # Stop timer so it doesn't keep firing
        lineage_window._lineage_play_timer.stop()

    def test_lineage_play_tick_increments(self, lineage_window):
        """Each tick increments the slider by 1."""
        lineage_window._lineage_slider.setValue(50)
        lineage_window._lineage_play_tick()
        assert lineage_window._lineage_slider.value() == 51

    def test_lineage_play_tick_stops_at_max(self, lineage_window):
        """When slider is at 100, tick stops the timer."""
        from PySide6.QtCore import QTimer
        lineage_window._lineage_play_timer = QTimer(lineage_window)
        lineage_window._lineage_play_timer.start()
        lineage_window._lineage_slider.setValue(100)
        lineage_window._lineage_play_tick()
        assert lineage_window._lineage_play_timer is None
        assert "Play" in lineage_window._lineage_play_btn.text()

    def test_lineage_play_tick_at_max_no_timer(self, lineage_window):
        """Defensive: tick at max even when timer is None doesn't raise."""
        lineage_window._lineage_play_timer = None
        lineage_window._lineage_slider.setValue(100)
        # Should not raise
        lineage_window._lineage_play_tick()
        assert lineage_window._lineage_play_timer is None
