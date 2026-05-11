"""Tests for v0.38 ConfigTableModel + Settings tab wiring.

Covers:
  * Model construction with a default Config (no source TOML)
  * Model construction with a Config loaded from a real TOML file
  * Flattening produces dotted-path rows in alphabetical order
  * Lists are JSON-formatted; primitives stringified
  * set_config() re-points and refreshes
  * 5th tab "Settings" exists at index 4
  * Reload button updates the model from disk
  * Reload handles missing/invalid TOML gracefully
  * Header label correctly reflects source vs defaults vs reloaded state

All tests skip if PySide6 unavailable. None requires pytest-qt event loop.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

pyside6 = pytest.importorskip("PySide6")  # noqa: F841

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from unittest.mock import patch

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.gui.main_window import CuratorMainWindow
from curator.gui.models import ConfigTableModel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def empty_config():
    """Pure defaults; no source_path."""
    return Config()


@pytest.fixture
def config_from_toml(tmp_path):
    """Write a minimal curator.toml and load it; the loaded Config has source_path set."""
    toml_path = tmp_path / "curator.toml"
    toml_path.write_text(
        '[curator]\n'
        'log_level = "DEBUG"\n'
        '\n'
        '[hash]\n'
        'prefix_bytes = 8192\n'
        'fuzzy_for = [".py", ".md"]\n'
        '\n'
        '[trash]\n'
        'purge_older_than_days = 90\n',
        encoding="utf-8",
    )
    return Config.load(explicit_path=toml_path), toml_path


@pytest.fixture
def runtime_with_toml_config(tmp_path):
    """Real runtime built against a Config loaded from a real TOML file."""
    toml_path = tmp_path / "curator.toml"
    toml_path.write_text(
        '[curator]\n'
        'log_level = "DEBUG"\n'
        '\n'
        '[hash]\n'
        'prefix_bytes = 8192\n',
        encoding="utf-8",
    )
    cfg = Config.load(explicit_path=toml_path)
    db_path = tmp_path / "settings_test.db"
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    yield rt, toml_path


# ===========================================================================
# Construction
# ===========================================================================


class TestConstruction:
    def test_constructs_with_default_config(self, qapp, empty_config):
        model = ConfigTableModel(empty_config)
        # Default config has SOMETHING in it (the DEFAULT_CONFIG dict).
        assert model.rowCount() > 0
        assert model.columnCount() == 2

    def test_columns_match_constant(self, qapp, empty_config):
        model = ConfigTableModel(empty_config)
        for i, label in enumerate(ConfigTableModel.COLUMNS):
            assert model.headerData(i, Qt.Orientation.Horizontal) == label

    def test_constructs_with_toml_config(self, qapp, config_from_toml):
        cfg, _path = config_from_toml
        model = ConfigTableModel(cfg)
        assert model.rowCount() > 0


# ===========================================================================
# Flattening
# ===========================================================================


class TestFlattening:
    def test_dotted_path_keys(self, qapp, config_from_toml):
        cfg, _ = config_from_toml
        model = ConfigTableModel(cfg)
        keys = [model.setting_at(r)[0] for r in range(model.rowCount())]
        # All keys are dotted paths (no top-level bare keys, since defaults
        # are sectioned).
        for k in keys:
            assert k != ""
        # We should see hash.prefix_bytes (set in our test TOML).
        assert "hash.prefix_bytes" in keys

    def test_alphabetical_order(self, qapp, config_from_toml):
        cfg, _ = config_from_toml
        model = ConfigTableModel(cfg)
        keys = [model.setting_at(r)[0] for r in range(model.rowCount())]
        assert keys == sorted(keys)

    def test_overridden_value_displayed(self, qapp, config_from_toml):
        cfg, _ = config_from_toml
        model = ConfigTableModel(cfg)
        # We overrode hash.prefix_bytes = 8192 in the test TOML.
        for r in range(model.rowCount()):
            k, v = model.setting_at(r)
            if k == "hash.prefix_bytes":
                assert v == "8192"
                return
        pytest.fail("hash.prefix_bytes row missing")

    def test_log_level_overridden(self, qapp, config_from_toml):
        cfg, _ = config_from_toml
        model = ConfigTableModel(cfg)
        for r in range(model.rowCount()):
            k, v = model.setting_at(r)
            if k == "curator.log_level":
                assert v == "DEBUG"
                return
        pytest.fail("curator.log_level row missing")

    def test_list_value_json_formatted(self, qapp, config_from_toml):
        cfg, _ = config_from_toml
        model = ConfigTableModel(cfg)
        # hash.fuzzy_for should appear as a JSON-formatted list.
        for r in range(model.rowCount()):
            k, v = model.setting_at(r)
            if k == "hash.fuzzy_for":
                assert v.startswith("[") and v.endswith("]")
                assert ".py" in v
                assert ".md" in v
                return
        pytest.fail("hash.fuzzy_for row missing")


# ===========================================================================
# Helpers
# ===========================================================================


class TestHelpers:
    def test_format_value_none(self):
        assert ConfigTableModel._format_value(None) == "(null)"

    def test_format_value_bool(self):
        assert ConfigTableModel._format_value(True) == "true"
        assert ConfigTableModel._format_value(False) == "false"

    def test_format_value_int(self):
        assert ConfigTableModel._format_value(42) == "42"

    def test_format_value_str(self):
        assert ConfigTableModel._format_value("hello") == "hello"

    def test_format_value_list(self):
        result = ConfigTableModel._format_value([1, 2, 3])
        assert result.startswith("[") and result.endswith("]")
        assert "1" in result and "3" in result

    def test_flatten_simple_dict(self):
        rows = list(ConfigTableModel._flatten({"a": 1, "b": 2}))
        # Order is sorted-keys.
        assert rows == [("a", "1"), ("b", "2")]

    def test_flatten_nested_dict(self):
        rows = list(ConfigTableModel._flatten({"section": {"key1": "v1", "key2": "v2"}}))
        # Yields dotted paths.
        assert ("section.key1", "v1") in rows
        assert ("section.key2", "v2") in rows

    def test_flatten_with_list_value(self):
        rows = list(ConfigTableModel._flatten({"items": [1, 2, 3]}))
        # The list is leaf-formatted; no recursion into list items.
        assert len(rows) == 1
        assert rows[0][0] == "items"
        assert "1" in rows[0][1]


# ===========================================================================
# set_config
# ===========================================================================


class TestSetConfig:
    def test_set_config_repoints_and_refreshes(self, qapp, empty_config, config_from_toml):
        model = ConfigTableModel(empty_config)
        original_count = model.rowCount()
        # Find the original log_level value (default is "INFO").
        for r in range(original_count):
            k, v = model.setting_at(r)
            if k == "curator.log_level":
                original_log_level = v
                break
        else:
            original_log_level = None

        # Re-point to the TOML-loaded config (log_level = DEBUG).
        cfg, _ = config_from_toml
        model.set_config(cfg)
        for r in range(model.rowCount()):
            k, v = model.setting_at(r)
            if k == "curator.log_level":
                assert v == "DEBUG"
                if original_log_level:
                    assert v != original_log_level
                return
        pytest.fail("curator.log_level row missing after set_config")


# ===========================================================================
# Wiring
# ===========================================================================


class TestWiring:
    def test_settings_tab_exists_at_index_4(self, qapp, runtime_with_toml_config):
        rt, _ = runtime_with_toml_config
        window = CuratorMainWindow(rt)
        try:
            # v1.1.0: tab count was 8 (Migrate added between Trash and Audit Log);
            # Settings shifted from index 5 to index 6.
            # v1.7-alpha.5: tab count is 9 (Sources tab added AFTER Settings, so
            # Settings stays at index 6 but the total count goes to 9).
            assert window._tabs.count() == 9
            assert window._tabs.tabText(6) == "Settings"
        finally:
            window.deleteLater()

    def test_settings_header_shows_source_path(self, qapp, runtime_with_toml_config):
        rt, toml_path = runtime_with_toml_config
        window = CuratorMainWindow(rt)
        try:
            text = window._settings_header.text()
            assert "Loaded from:" in text
            assert str(toml_path) in text
        finally:
            window.deleteLater()

    def test_settings_header_shows_defaults_when_no_source(self, qapp, tmp_path):
        # Build a runtime whose config has no source_path (Config()).
        cfg = Config()
        # Manually resolve auto paths so build_runtime doesn't choke.
        cfg._resolve_auto_paths()
        db_path = tmp_path / "no_source.db"
        rt = build_runtime(
            config=cfg, db_path_override=db_path,
            json_output=False, no_color=True, verbosity=0,
        )
        window = CuratorMainWindow(rt)
        try:
            text = window._settings_header.text()
            assert "built-in defaults" in text
        finally:
            window.deleteLater()


# ===========================================================================
# _perform_settings_reload
# ===========================================================================


class TestPerformReload:
    def test_reload_succeeds_when_toml_unchanged(self, qapp, runtime_with_toml_config):
        rt, _toml_path = runtime_with_toml_config
        window = CuratorMainWindow(rt)
        try:
            success, message, fresh = window._perform_settings_reload()
            assert success is True
            assert "Reloaded from" in message
            assert fresh is not None
            # The freshly-loaded config has the same overrides.
            assert fresh.get("curator.log_level") == "DEBUG"
        finally:
            window.deleteLater()

    def test_reload_picks_up_disk_changes(self, qapp, runtime_with_toml_config):
        rt, toml_path = runtime_with_toml_config
        window = CuratorMainWindow(rt)
        try:
            # Edit the TOML on disk.
            toml_path.write_text(
                '[curator]\n'
                'log_level = "WARNING"\n',
                encoding="utf-8",
            )
            success, _msg, fresh = window._perform_settings_reload()
            assert success is True
            # Fresh config sees the new value...
            assert fresh.get("curator.log_level") == "WARNING"
            # ...but the LIVE runtime config is unchanged (key v0.38 invariant).
            assert rt.config.get("curator.log_level") == "DEBUG"
        finally:
            window.deleteLater()

    def test_reload_returns_failure_on_invalid_toml(
        self, qapp, runtime_with_toml_config
    ):
        rt, toml_path = runtime_with_toml_config
        window = CuratorMainWindow(rt)
        try:
            # Write deliberately malformed TOML.
            toml_path.write_text("this is not [valid toml\n", encoding="utf-8")
            success, message, fresh = window._perform_settings_reload()
            assert success is False
            assert "Failed to reload" in message
            assert fresh is None
        finally:
            window.deleteLater()

    def test_reload_when_source_missing_still_succeeds(self, qapp, tmp_path):
        # Build a runtime with no source TOML (defaults only).
        cfg = Config()
        cfg._resolve_auto_paths()
        db_path = tmp_path / "no_source_reload.db"
        rt = build_runtime(
            config=cfg, db_path_override=db_path,
            json_output=False, no_color=True, verbosity=0,
        )
        window = CuratorMainWindow(rt)
        try:
            success, message, fresh = window._perform_settings_reload()
            # Reload should succeed (just re-resolving) even with no TOML.
            assert success is True
            assert fresh is not None
        finally:
            window.deleteLater()


# ===========================================================================
# Reload slot (integration of header update + model refresh + status bar)
# ===========================================================================


class TestSlotReload:
    def test_slot_reload_updates_model_and_header(
        self, qapp, runtime_with_toml_config
    ):
        rt, toml_path = runtime_with_toml_config
        window = CuratorMainWindow(rt)
        try:
            # Edit the TOML on disk.
            toml_path.write_text(
                '[curator]\n'
                'log_level = "WARNING"\n',
                encoding="utf-8",
            )
            window._slot_settings_reload()
            # The model now shows the new log level.
            for r in range(window._settings_model.rowCount()):
                k, v = window._settings_model.setting_at(r)
                if k == "curator.log_level":
                    assert v == "WARNING"
                    break
            else:
                pytest.fail("log_level row missing")
            # Header marks reloaded.
            assert "reloaded from disk" in window._settings_header.text()
        finally:
            window.deleteLater()

    def test_slot_reload_shows_warning_on_failure(
        self, qapp, runtime_with_toml_config
    ):
        rt, toml_path = runtime_with_toml_config
        window = CuratorMainWindow(rt)
        try:
            toml_path.write_text("not [valid toml\n", encoding="utf-8")
            with patch.object(QMessageBox, "warning") as mock_warn:
                window._slot_settings_reload()
            assert mock_warn.called
        finally:
            window.deleteLater()
