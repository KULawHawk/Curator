"""pytest-qt smoke test (v1.7.184 — Round 4 Tier 1 ship 5 of 5).

Verifies that `pytest-qt`'s `qtbot` fixture is available and that the
three pytest-qt idioms Curator's GUI Coverage Arc will rely on actually
work:

1. ``qtbot.addWidget(w)`` — auto-cleanup of test-owned widgets
2. ``qtbot.mouseClick(w, button)`` — synthesized clicks driving signal
   emission
3. ``qtbot.waitSignal(signal, timeout=...)`` — context-manager-based
   signal wait, used heavily in Tier 2+ widget interaction tests

This file is the *gate ship* for the GUI Coverage Arc — if any of these
three idioms break in a future PySide6 or pytest-qt upgrade, this file
catches it before the bigger module-coverage tests do. Keep it tiny.

See ``docs/GUI_TESTING_STRATEGY.md`` for the full pattern and why
pytest-qt is needed beyond the Lesson #98 Qt headless foundation.
"""

from __future__ import annotations

import os

# Ensure offscreen platform before any Qt import (Lesson #98 / Doctrine #16)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestPytestQtSmoke:
    def test_qtbot_fixture_is_available(self, qtbot):
        """The ``qtbot`` fixture must exist and behave like a pytest-qt
        Qt-bot object.

        ``addWidget`` is required for cleanup; ``mouseClick`` and
        ``waitSignal`` are required for Tier 2+ widget tests. We just
        assert the methods are callable — actual usage happens below.
        """
        assert hasattr(qtbot, "addWidget")
        assert hasattr(qtbot, "mouseClick")
        assert hasattr(qtbot, "waitSignal")
        assert callable(qtbot.addWidget)
        assert callable(qtbot.mouseClick)
        assert callable(qtbot.waitSignal)

    def test_widget_click_drives_signal(self, qtbot):
        """A real QPushButton + qtbot.mouseClick drives the button's
        ``clicked`` signal to fire.

        This is the canonical pattern Tier 2+ will use for menu /
        action handler / dialog button testing.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QPushButton

        button = QPushButton("Test")
        qtbot.addWidget(button)

        # Use waitSignal as a context manager around the click.
        with qtbot.waitSignal(button.clicked, timeout=1000):
            qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    def test_wait_signal_with_existing_qt_signal_class(self, qtbot):
        """Confirms ``qtbot.waitSignal`` works against a custom
        ``Signal(object)`` bridge — the exact pattern used by
        ``curator.gui.scan_signals.ScanProgressBridge`` and the other
        Round 3 Tier 4 bridges.
        """
        from PySide6.QtCore import QObject, Signal

        class Bridge(QObject):
            payload_ready = Signal(object)

        b = Bridge()
        # Emit then wait — emit happens before the wait context, but
        # pytest-qt records the emission. Use the raising=False option
        # so a missed-signal would error explicitly rather than hanging.
        with qtbot.waitSignal(b.payload_ready, timeout=1000, raising=True) as blocker:
            b.payload_ready.emit({"k": "v"})

        assert blocker.signal_triggered
        assert blocker.args == [{"k": "v"}]
