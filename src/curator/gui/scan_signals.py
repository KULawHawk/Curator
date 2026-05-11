"""Cross-thread signal bridge + worker for the v1.7 alpha Scan dialog.

DESIGN.md §15 (GUI Scan dialog).

v1.7 alpha approach
-------------------

:meth:`curator.services.scan.ScanService.scan` is **synchronous** and
takes no progress callback in v1.6.5. Calling it from the GUI thread
would freeze the window for the duration of the scan (potentially
minutes for large trees).

The v1.7 alpha workaround:

  * Run :meth:`ScanService.scan` inside a :class:`QThread`
    (:class:`ScanWorker`).
  * Show **indeterminate** progress (spinner + "Scanning..." status)
    while it runs -- granular per-file progress requires a callback
    parameter in ScanService that's queued for v1.7.x.
  * On completion, emit the full :class:`ScanReport` (or the raised
    exception) via a :class:`QObject` signal bridge that lives on the
    GUI thread.

The pattern mirrors :class:`curator.gui.migrate_signals.MigrationProgressBridge`:
the bridge is just plumbing for Qt's automatic ``QueuedConnection``
mechanism (when sender and receiver live in different threads, signals
get routed through the GUI thread's event loop -- which is what makes
touching the model from the slot safe).

When ScanService gains a progress callback (v1.7.x), the QThread will
forward each callback invocation through :attr:`ScanProgressBridge.scan_progress`
and the dialog can switch from indeterminate to a real percentage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QThread, Signal

if TYPE_CHECKING:  # pragma: no cover
    from curator.cli.runtime import CuratorRuntime
    from curator.services.scan import ScanReport


class ScanProgressBridge(QObject):
    """Cross-thread signal carrier for ScanDialog updates.

    Three signals cover the full scan lifecycle. The dialog connects
    to all three; the worker emits them at the appropriate transitions.

    All payloads are passed as ``object`` so the bridge stays decoupled
    from the domain models (mirrors the
    :class:`MigrationProgressBridge` pattern in ``migrate_signals.py``).
    """

    #: Emitted exactly once when the scan starts. Payload: (source_id, root).
    scan_started = Signal(object)

    #: Emitted exactly once on successful completion. Payload: ScanReport.
    scan_completed = Signal(object)

    #: Emitted exactly once if the scan raises. Payload: the exception.
    scan_failed = Signal(object)

    #: Reserved for v1.7.x when ScanService gains a progress callback.
    #: Payload shape TBD -- likely (files_seen, files_hashed, current_path).
    scan_progress = Signal(object)


class ScanWorker(QThread):
    """QThread wrapper that runs ``ScanService.scan`` and emits via a bridge.

    Construct with the runtime, source_id, root, optional options dict,
    and a :class:`ScanProgressBridge`. Call :meth:`start` to begin --
    the thread will:

      1. Emit ``bridge.scan_started.emit((source_id, root))``
      2. Call ``runtime.scan_service.scan(...)``
      3. Emit ``bridge.scan_completed.emit(scan_report)`` on success,
         or ``bridge.scan_failed.emit(exception)`` on failure.

    The thread auto-finishes after step 3; the dialog's slot decides
    what to display next.

    No cancellation support in v1.7 alpha -- ScanService doesn't have
    a cancel flag yet. Closing the dialog while a scan is running
    will orphan the worker (it'll finish but its emit will land on a
    dead bridge slot, which Qt handles gracefully).
    """

    def __init__(
        self,
        runtime: "CuratorRuntime",
        source_id: str,
        root: str,
        options: dict[str, Any] | None,
        bridge: ScanProgressBridge,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._source_id = source_id
        self._root = root
        self._options = options
        self._bridge = bridge

    def run(self) -> None:  # noqa: D401 -- QThread API
        """Run the scan. Emits via the bridge; never raises."""
        self._bridge.scan_started.emit((self._source_id, self._root))
        try:
            report = self._runtime.scan.scan(
                source_id=self._source_id,
                root=self._root,
                options=self._options,
            )
        except Exception as e:  # noqa: BLE001
            # Surface the full exception object -- the dialog's slot
            # can render the type, message, and traceback as it sees fit.
            self._bridge.scan_failed.emit(e)
            return
        self._bridge.scan_completed.emit(report)


__all__ = ["ScanProgressBridge", "ScanWorker"]
