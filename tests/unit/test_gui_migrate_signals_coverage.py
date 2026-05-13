"""Coverage closure for ``curator.gui.migrate_signals`` (v1.7.177).

Round 3 Tier 4 sub-ship 2 of 4. Smallest GUI module (5 statements).

Tests the QObject + Signal bridge by:
1. Instantiating it under a QApplication
2. Connecting a callback to its `progress_updated` signal
3. Emitting and verifying the callback received the payload
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    """Headless QApplication for the test module. Required for any
    QObject construction. Reuses an existing instance if present so
    we play nice with other GUI test fixtures."""
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    return app


class TestMigrationProgressBridge:
    def test_instantiates_under_qapp(self, qapp):
        """Class can be constructed under a QApplication."""
        from curator.gui.migrate_signals import MigrationProgressBridge
        bridge = MigrationProgressBridge()
        assert bridge is not None
        # Verify it's a real QObject (has Qt machinery attached)
        assert hasattr(bridge, "progress_updated")

    def test_signal_emit_invokes_connected_slot(self, qapp):
        """Connecting a slot + emitting delivers the payload."""
        from curator.gui.migrate_signals import MigrationProgressBridge
        bridge = MigrationProgressBridge()
        received = []

        def _slot(payload):
            received.append(payload)

        bridge.progress_updated.connect(_slot)
        # Process direct connection (same thread) emits synchronously
        bridge.progress_updated.emit({"file": "/x.txt", "status": "moved"})

        assert len(received) == 1
        assert received[0] == {"file": "/x.txt", "status": "moved"}

    def test_signal_carries_any_object_payload(self, qapp):
        """Signal(object) accepts arbitrary Python objects without
        forcing a specific type — keeps the bridge decoupled from
        MigrationProgress."""
        from curator.gui.migrate_signals import MigrationProgressBridge
        bridge = MigrationProgressBridge()
        captured = []

        bridge.progress_updated.connect(lambda p: captured.append(p))

        # Emit a variety of payload types
        bridge.progress_updated.emit("a string")
        bridge.progress_updated.emit(42)
        bridge.progress_updated.emit({"dict": True})
        bridge.progress_updated.emit(None)

        assert captured == ["a string", 42, {"dict": True}, None]

    def test_module_exports(self):
        """`__all__` exposes MigrationProgressBridge."""
        from curator.gui import migrate_signals
        assert "MigrationProgressBridge" in migrate_signals.__all__
