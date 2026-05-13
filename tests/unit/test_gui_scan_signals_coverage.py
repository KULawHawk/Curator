"""Coverage closure for ``curator.gui.scan_signals`` (v1.7.178).

Round 3 Tier 4 sub-ship 3 of 4. Tests ScanProgressBridge (4 signals)
+ ScanWorker (QThread that wraps ScanService.scan and emits via bridge).

The worker's `run()` method is exercised by calling it directly (not
via QThread.start) so the test stays synchronous — Qt's event loop
isn't required to exercise the call/emit/return logic.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)


class TestScanProgressBridge:
    def test_instantiates_under_qapp(self, qapp):
        from curator.gui.scan_signals import ScanProgressBridge
        bridge = ScanProgressBridge()
        # Verify all 4 signals exist
        assert hasattr(bridge, "scan_started")
        assert hasattr(bridge, "scan_completed")
        assert hasattr(bridge, "scan_failed")
        assert hasattr(bridge, "scan_progress")

    def test_all_signals_emit_and_deliver(self, qapp):
        """Connect a callback per signal, emit each, verify delivery."""
        from curator.gui.scan_signals import ScanProgressBridge
        bridge = ScanProgressBridge()
        received = {"started": [], "completed": [], "failed": [], "progress": []}

        bridge.scan_started.connect(lambda p: received["started"].append(p))
        bridge.scan_completed.connect(lambda p: received["completed"].append(p))
        bridge.scan_failed.connect(lambda p: received["failed"].append(p))
        bridge.scan_progress.connect(lambda p: received["progress"].append(p))

        bridge.scan_started.emit(("local", "/x"))
        bridge.scan_completed.emit({"files_seen": 10})
        bridge.scan_failed.emit(RuntimeError("boom"))
        bridge.scan_progress.emit({"files_seen": 5})

        assert received["started"] == [("local", "/x")]
        assert received["completed"] == [{"files_seen": 10}]
        assert len(received["failed"]) == 1
        assert isinstance(received["failed"][0], RuntimeError)
        assert received["progress"] == [{"files_seen": 5}]


class TestScanWorker:
    def test_init_stores_args(self, qapp):
        from curator.gui.scan_signals import (
            ScanProgressBridge, ScanWorker,
        )
        bridge = ScanProgressBridge()
        runtime = MagicMock()
        worker = ScanWorker(
            runtime=runtime, source_id="local", root="/src",
            options={"k": "v"}, bridge=bridge,
        )
        assert worker._runtime is runtime
        assert worker._source_id == "local"
        assert worker._root == "/src"
        assert worker._options == {"k": "v"}
        assert worker._bridge is bridge

    def test_run_success_emits_started_then_completed(self, qapp):
        """run() emits scan_started + scan_completed on success."""
        from curator.gui.scan_signals import (
            ScanProgressBridge, ScanWorker,
        )
        from curator.services.scan import ScanReport

        bridge = ScanProgressBridge()
        events = []
        bridge.scan_started.connect(lambda p: events.append(("started", p)))
        bridge.scan_completed.connect(lambda p: events.append(("completed", p)))
        bridge.scan_failed.connect(lambda p: events.append(("failed", p)))

        # Build a stub runtime whose .scan.scan() returns a real ScanReport
        report = ScanReport(
            job_id=uuid4(), source_id="local", root="/src",
            started_at=datetime(2026, 1, 1, 12, 0, 0),
            completed_at=datetime(2026, 1, 1, 12, 0, 1),
            files_seen=5,
        )
        runtime = MagicMock()
        runtime.scan.scan.return_value = report

        worker = ScanWorker(
            runtime=runtime, source_id="local", root="/src",
            options={"verbose": True}, bridge=bridge,
        )
        # Call run() directly (synchronous) instead of start() (async)
        worker.run()

        # Verify call + emit sequence
        runtime.scan.scan.assert_called_once_with(
            source_id="local", root="/src", options={"verbose": True},
        )
        # Both started + completed emitted (failed not emitted)
        assert ("started", ("local", "/src")) in events
        assert ("completed", report) in events
        assert not any(e[0] == "failed" for e in events)

    def test_run_exception_emits_started_then_failed(self, qapp):
        """run() emits scan_started + scan_failed when scan raises."""
        from curator.gui.scan_signals import (
            ScanProgressBridge, ScanWorker,
        )
        bridge = ScanProgressBridge()
        events = []
        bridge.scan_started.connect(lambda p: events.append(("started", p)))
        bridge.scan_completed.connect(lambda p: events.append(("completed", p)))
        bridge.scan_failed.connect(lambda p: events.append(("failed", p)))

        runtime = MagicMock()
        runtime.scan.scan.side_effect = RuntimeError("scan exploded")

        worker = ScanWorker(
            runtime=runtime, source_id="local", root="/src",
            options=None, bridge=bridge,
        )
        worker.run()

        # started + failed (NOT completed)
        assert events[0][0] == "started"
        assert events[-1][0] == "failed"
        assert isinstance(events[-1][1], RuntimeError)
        assert "scan exploded" in str(events[-1][1])
        # Completed must NOT have fired
        assert not any(e[0] == "completed" for e in events)


class TestModuleExports:
    def test_all_exports(self):
        from curator.gui import scan_signals
        assert "ScanProgressBridge" in scan_signals.__all__
        assert "ScanWorker" in scan_signals.__all__
