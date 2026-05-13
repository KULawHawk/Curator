"""Coverage closure for ``curator.gui.launcher`` (v1.7.176).

Round 3 Tier 4 (stretch) sub-ship 1 of 4.

Targets the 11 uncovered lines (36-43 in `run_gui`, 53-57 in
`is_pyside6_available`). Tests stub `QApplication` and
`CuratorMainWindow` to avoid actually launching a window.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


class TestIsPyside6Available:
    def test_returns_true_when_pyside6_importable(self):
        """Lines 53-57 happy path: PySide6 is installed in the test env."""
        from curator.gui.launcher import is_pyside6_available
        # PySide6 is in [gui] extra; in this env it should be importable
        # (the test env is the full development install).
        assert is_pyside6_available() is True

    def test_returns_false_on_import_error(self, monkeypatch):
        """Lines 55-56: ImportError fallback returns False."""
        # Pop and poison PySide6 so the in-function import re-runs and fails
        monkeypatch.delitem(sys.modules, "PySide6", raising=False)
        monkeypatch.setitem(sys.modules, "PySide6", None)
        from curator.gui.launcher import is_pyside6_available
        assert is_pyside6_available() is False


class TestRunGui:
    def test_run_gui_returns_app_exec_code(self, monkeypatch):
        """Lines 36-43: run_gui boots Qt, builds window, runs event loop.

        Stub QApplication so .exec() returns immediately, stub the
        window class so we don't construct the real (heavyweight) main
        window."""
        # Stub PySide6.QtWidgets.QApplication
        import PySide6.QtWidgets as qtw

        fake_app = MagicMock()
        fake_app.exec.return_value = 0

        class _FakeQApplication:
            @classmethod
            def instance(cls):
                return None

            def __new__(cls, argv):
                return fake_app

        monkeypatch.setattr(qtw, "QApplication", _FakeQApplication)

        # Stub CuratorMainWindow
        import curator.gui.main_window as mw_mod
        fake_window = MagicMock()
        monkeypatch.setattr(
            mw_mod, "CuratorMainWindow",
            lambda rt: fake_window,
        )

        from curator.gui.launcher import run_gui
        result = run_gui(runtime=MagicMock())
        assert result == 0
        fake_window.show.assert_called_once()
        fake_app.exec.assert_called_once()

    def test_run_gui_reuses_existing_qapplication(self, monkeypatch):
        """When QApplication.instance() returns an existing instance,
        run_gui reuses it (the 'or' short-circuits before constructing
        a new QApplication)."""
        import PySide6.QtWidgets as qtw

        existing_app = MagicMock()
        existing_app.exec.return_value = 0

        class _FakeQApplication:
            @classmethod
            def instance(cls):
                return existing_app

            def __new__(cls, argv):
                # Should NOT be called when instance() returns something
                raise AssertionError(
                    "QApplication() should not be constructed when "
                    "instance() returns an existing app"
                )

        monkeypatch.setattr(qtw, "QApplication", _FakeQApplication)

        import curator.gui.main_window as mw_mod
        monkeypatch.setattr(
            mw_mod, "CuratorMainWindow",
            lambda rt: MagicMock(),
        )

        from curator.gui.launcher import run_gui
        result = run_gui(runtime=MagicMock())
        assert result == 0
        existing_app.exec.assert_called_once()

    def test_run_gui_propagates_nonzero_exit_code(self, monkeypatch):
        """run_gui returns whatever app.exec() returns — including
        non-zero exit codes."""
        import PySide6.QtWidgets as qtw

        fake_app = MagicMock()
        fake_app.exec.return_value = 42

        class _FakeQApplication:
            @classmethod
            def instance(cls):
                return None

            def __new__(cls, argv):
                return fake_app

        monkeypatch.setattr(qtw, "QApplication", _FakeQApplication)

        import curator.gui.main_window as mw_mod
        monkeypatch.setattr(
            mw_mod, "CuratorMainWindow",
            lambda rt: MagicMock(),
        )

        from curator.gui.launcher import run_gui
        assert run_gui(runtime=MagicMock()) == 42
