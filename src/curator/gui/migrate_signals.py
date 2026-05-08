"""Cross-thread signal bridge for live Migrate-tab progress updates.

DESIGN.md / docs/TRACER_PHASE_2_DESIGN.md §7 (GUI Migrate tab).

Tracer Phase 2 Session C2b. The plumbing:

  * :meth:`MigrationService.run_job` runs workers inside a
    :class:`concurrent.futures.ThreadPoolExecutor`. The workers are NOT
    Qt threads. After each file completes, each worker invokes the
    optional ``on_progress`` callback with a fresh
    :class:`MigrationProgress` row.

  * Qt model updates MUST happen on the main (GUI) thread. Touching a
    :class:`QAbstractTableModel` from a non-Qt thread is undefined
    behavior -- typically a segfault, sometimes a silently-wrong UI.

  * The fix is a small :class:`QObject` that lives on the GUI thread
    and exposes a :class:`Signal`. Calling ``signal.emit(progress)``
    is thread-safe: Qt routes the emission through the GUI thread's
    event loop via ``Qt::QueuedConnection`` (the default
    ``Qt::AutoConnection`` becomes ``QueuedConnection`` automatically
    when the sender and receiver live in different threads).

The :class:`CuratorMainWindow` constructs one of these bridges per
Migrate tab and passes ``bridge.progress_updated.emit`` as the
``on_progress`` parameter to ``run_job``. The slot connected to the
signal then refreshes the relevant Qt models -- which IS safe
because the slot runs on the GUI thread.

This module deliberately stays tiny and focused. The point is to
express the threading boundary in one obvious place.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class MigrationProgressBridge(QObject):
    """Cross-thread signal carrier for migration progress updates.

    Construct one bridge per Migrate tab. Lives on the GUI thread (it
    must be created from the GUI thread or moved there with
    :meth:`QObject.moveToThread`). The :attr:`progress_updated` signal
    is thread-safe to emit from any worker thread.

    The signal carries the freshly-updated :class:`MigrationProgress`
    record as ``object`` so the bridge stays decoupled from the
    domain models (avoids a pluggy-style import cycle and keeps the
    Qt-side type erasure simple).
    """

    #: Emitted once per file as a worker reaches a terminal state.
    #: Payload is the freshly-updated :class:`MigrationProgress`.
    progress_updated = Signal(object)


__all__ = ["MigrationProgressBridge"]
