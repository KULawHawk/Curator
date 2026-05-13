"""Coverage closure for ``curator.gui.cleanup_signals`` (v1.7.179).

Round 3 Tier 4 sub-ship 4 of 4 — FINAL Tier 4 ship.

Covers two bridges (GroupProgressBridge + CleanupProgressBridge) and
four QThread workers (GroupFindWorker, GroupApplyWorker, CleanupFindWorker,
CleanupApplyWorker). Calls `run()` directly for synchronous testing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# Bridges
# ---------------------------------------------------------------------------


class TestBridges:
    def test_group_progress_bridge_signals(self, qapp):
        from curator.gui.cleanup_signals import GroupProgressBridge
        bridge = GroupProgressBridge()
        # 6 signals: find_started/completed/failed + apply_started/completed/failed
        for name in ("find_started", "find_completed", "find_failed",
                     "apply_started", "apply_completed", "apply_failed"):
            assert hasattr(bridge, name)
        # Emit each + verify delivery
        received = {n: [] for n in ("find_started", "find_completed",
                                     "find_failed", "apply_started",
                                     "apply_completed", "apply_failed")}
        for name in received:
            getattr(bridge, name).connect(
                lambda p, n=name: received[n].append(p),
            )
        bridge.find_started.emit(("local", "exact"))
        bridge.find_completed.emit({"report": "x"})
        bridge.find_failed.emit(RuntimeError("boom"))
        bridge.apply_started.emit(5)
        bridge.apply_completed.emit({"apply": "x"})
        bridge.apply_failed.emit(RuntimeError("apply boom"))
        for name in received:
            assert len(received[name]) == 1

    def test_cleanup_progress_bridge_signals(self, qapp):
        from curator.gui.cleanup_signals import CleanupProgressBridge
        bridge = CleanupProgressBridge()
        for name in ("find_started", "find_completed", "find_failed",
                     "apply_started", "apply_completed", "apply_failed"):
            assert hasattr(bridge, name)


# ---------------------------------------------------------------------------
# GroupFindWorker
# ---------------------------------------------------------------------------


class TestGroupFindWorker:
    def test_run_success(self, qapp):
        from curator.gui.cleanup_signals import (
            GroupFindWorker, GroupProgressBridge,
        )
        bridge = GroupProgressBridge()
        events = []
        bridge.find_started.connect(lambda p: events.append(("started", p)))
        bridge.find_completed.connect(lambda p: events.append(("completed", p)))
        bridge.find_failed.connect(lambda p: events.append(("failed", p)))

        runtime = MagicMock()
        runtime.cleanup.find_duplicates.return_value = {"report": "ok"}
        worker = GroupFindWorker(
            runtime=runtime,
            source_id="local", root_prefix="/sub",
            keep_strategy="shortest_path", keep_under=None,
            match_kind="exact", similarity_threshold=0.85,
            bridge=bridge,
        )
        worker.run()
        runtime.cleanup.find_duplicates.assert_called_once()
        assert ("started", ("local", "exact")) in events
        assert ("completed", {"report": "ok"}) in events
        assert not any(e[0] == "failed" for e in events)

    def test_run_failure(self, qapp):
        from curator.gui.cleanup_signals import (
            GroupFindWorker, GroupProgressBridge,
        )
        bridge = GroupProgressBridge()
        events = []
        bridge.find_started.connect(lambda p: events.append(("started", p)))
        bridge.find_completed.connect(lambda p: events.append(("completed", p)))
        bridge.find_failed.connect(lambda p: events.append(("failed", p)))

        runtime = MagicMock()
        runtime.cleanup.find_duplicates.side_effect = RuntimeError("db gone")
        worker = GroupFindWorker(
            runtime=runtime,
            source_id=None, root_prefix=None,
            keep_strategy="newest", keep_under="/k",
            match_kind="fuzzy", similarity_threshold=0.9,
            bridge=bridge,
        )
        worker.run()
        assert events[0][0] == "started"
        assert events[-1][0] == "failed"
        assert isinstance(events[-1][1], RuntimeError)
        assert not any(e[0] == "completed" for e in events)


# ---------------------------------------------------------------------------
# GroupApplyWorker
# ---------------------------------------------------------------------------


class TestGroupApplyWorker:
    def test_run_success(self, qapp):
        from curator.gui.cleanup_signals import (
            GroupApplyWorker, GroupProgressBridge,
        )
        bridge = GroupProgressBridge()
        events = []
        bridge.apply_started.connect(lambda p: events.append(("started", p)))
        bridge.apply_completed.connect(lambda p: events.append(("completed", p)))
        bridge.apply_failed.connect(lambda p: events.append(("failed", p)))

        runtime = MagicMock()
        runtime.cleanup.apply.return_value = {"apply_report": "ok"}
        # Report with 3 findings
        report = MagicMock(findings=[1, 2, 3])
        worker = GroupApplyWorker(
            runtime=runtime, report=report,
            use_trash=True, bridge=bridge,
        )
        worker.run()
        runtime.cleanup.apply.assert_called_once_with(
            report, use_trash=True,
        )
        assert ("started", 3) in events
        assert ("completed", {"apply_report": "ok"}) in events

    def test_run_failure(self, qapp):
        from curator.gui.cleanup_signals import (
            GroupApplyWorker, GroupProgressBridge,
        )
        bridge = GroupProgressBridge()
        events = []
        bridge.apply_started.connect(lambda p: events.append(("started", p)))
        bridge.apply_completed.connect(lambda p: events.append(("completed", p)))
        bridge.apply_failed.connect(lambda p: events.append(("failed", p)))

        runtime = MagicMock()
        runtime.cleanup.apply.side_effect = OSError("permission denied")
        report = MagicMock(findings=[1])
        worker = GroupApplyWorker(
            runtime=runtime, report=report,
            use_trash=False, bridge=bridge,
        )
        worker.run()
        assert events[0] == ("started", 1)
        assert events[-1][0] == "failed"
        assert isinstance(events[-1][1], OSError)


# ---------------------------------------------------------------------------
# CleanupFindWorker (mode dispatch)
# ---------------------------------------------------------------------------


class TestCleanupFindWorker:
    def test_init_rejects_unknown_mode(self, qapp):
        from curator.gui.cleanup_signals import (
            CleanupFindWorker, CleanupProgressBridge,
        )
        bridge = CleanupProgressBridge()
        with pytest.raises(ValueError, match="unknown mode"):
            CleanupFindWorker(
                runtime=MagicMock(), mode="bogus", root="/x",
                bridge=bridge,
            )

    def test_run_junk_mode(self, qapp):
        from curator.gui.cleanup_signals import (
            CleanupFindWorker, CleanupProgressBridge,
        )
        bridge = CleanupProgressBridge()
        events = []
        bridge.find_completed.connect(lambda p: events.append(("completed", p)))
        bridge.find_failed.connect(lambda p: events.append(("failed", p)))

        runtime = MagicMock()
        runtime.cleanup.find_junk_files.return_value = {"junk": "report"}
        worker = CleanupFindWorker(
            runtime=runtime, mode="junk", root="/r",
            patterns=["*.tmp"], bridge=bridge,
        )
        worker.run()
        runtime.cleanup.find_junk_files.assert_called_once_with(
            "/r", patterns=["*.tmp"],
        )
        assert events == [("completed", {"junk": "report"})]

    def test_run_empty_dirs_mode(self, qapp):
        from curator.gui.cleanup_signals import (
            CleanupFindWorker, CleanupProgressBridge,
        )
        bridge = CleanupProgressBridge()
        events = []
        bridge.find_completed.connect(lambda p: events.append(("completed", p)))

        runtime = MagicMock()
        runtime.cleanup.find_empty_dirs.return_value = {"empty": "report"}
        worker = CleanupFindWorker(
            runtime=runtime, mode="empty_dirs", root="/r",
            ignore_system_junk=False, bridge=bridge,
        )
        worker.run()
        runtime.cleanup.find_empty_dirs.assert_called_once_with(
            "/r", ignore_system_junk=False,
        )
        assert events == [("completed", {"empty": "report"})]

    def test_run_broken_symlinks_mode(self, qapp):
        from curator.gui.cleanup_signals import (
            CleanupFindWorker, CleanupProgressBridge,
        )
        bridge = CleanupProgressBridge()
        events = []
        bridge.find_completed.connect(lambda p: events.append(("completed", p)))

        runtime = MagicMock()
        runtime.cleanup.find_broken_symlinks.return_value = {"symlinks": "report"}
        worker = CleanupFindWorker(
            runtime=runtime, mode="broken_symlinks", root="/r",
            bridge=bridge,
        )
        worker.run()
        runtime.cleanup.find_broken_symlinks.assert_called_once_with("/r")

    def test_run_unreachable_mode_raises(self, qapp, monkeypatch):
        """Lines 268: defensive ValueError when mode bypasses __init__
        validation (defensive boundary per docstring)."""
        from curator.gui.cleanup_signals import (
            CleanupFindWorker, CleanupProgressBridge,
        )
        bridge = CleanupProgressBridge()
        events = []
        bridge.find_failed.connect(lambda p: events.append(("failed", p)))

        runtime = MagicMock()
        # Construct legally then forcibly mutate _mode to bypass __init__
        worker = CleanupFindWorker(
            runtime=runtime, mode="junk", root="/r", bridge=bridge,
        )
        worker._mode = "unreachable_value"  # bypass __init__ validation
        worker.run()
        # The unreachable raise is caught by the broad except and emitted as failed
        assert events[-1][0] == "failed"
        assert isinstance(events[-1][1], ValueError)
        assert "unreachable" in str(events[-1][1])

    def test_run_failure_emits_find_failed(self, qapp):
        from curator.gui.cleanup_signals import (
            CleanupFindWorker, CleanupProgressBridge,
        )
        bridge = CleanupProgressBridge()
        events = []
        bridge.find_started.connect(lambda p: events.append(("started", p)))
        bridge.find_failed.connect(lambda p: events.append(("failed", p)))

        runtime = MagicMock()
        runtime.cleanup.find_junk_files.side_effect = RuntimeError("scan failed")
        worker = CleanupFindWorker(
            runtime=runtime, mode="junk", root="/r", bridge=bridge,
        )
        worker.run()
        assert events[0] == ("started", ("junk", "/r"))
        assert events[-1][0] == "failed"


# ---------------------------------------------------------------------------
# CleanupApplyWorker
# ---------------------------------------------------------------------------


class TestCleanupApplyWorker:
    def test_run_success(self, qapp):
        from curator.gui.cleanup_signals import (
            CleanupApplyWorker, CleanupProgressBridge,
        )
        bridge = CleanupProgressBridge()
        events = []
        bridge.apply_started.connect(lambda p: events.append(("started", p)))
        bridge.apply_completed.connect(lambda p: events.append(("completed", p)))

        runtime = MagicMock()
        runtime.cleanup.apply.return_value = {"ok": True}
        report = MagicMock(findings=[1, 2])
        worker = CleanupApplyWorker(
            runtime=runtime, report=report, use_trash=True, bridge=bridge,
        )
        worker.run()
        assert events == [("started", 2), ("completed", {"ok": True})]

    def test_run_failure(self, qapp):
        from curator.gui.cleanup_signals import (
            CleanupApplyWorker, CleanupProgressBridge,
        )
        bridge = CleanupProgressBridge()
        events = []
        bridge.apply_failed.connect(lambda p: events.append(("failed", p)))

        runtime = MagicMock()
        runtime.cleanup.apply.side_effect = RuntimeError("apply boom")
        worker = CleanupApplyWorker(
            runtime=runtime, report=MagicMock(findings=[]),
            use_trash=False, bridge=bridge,
        )
        worker.run()
        assert events[-1][0] == "failed"
        assert isinstance(events[-1][1], RuntimeError)


class TestModuleExports:
    def test_all_exports(self):
        from curator.gui import cleanup_signals
        for name in ("GroupProgressBridge", "GroupFindWorker",
                     "GroupApplyWorker", "CleanupProgressBridge",
                     "CleanupFindWorker", "CleanupApplyWorker"):
            assert name in cleanup_signals.__all__
