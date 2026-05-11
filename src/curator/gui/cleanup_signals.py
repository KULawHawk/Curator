"""Cross-thread signal bridges + workers for the v1.7 alpha cleanup-family dialogs.

DESIGN.md §15.4 (GroupDialog) and §15.5 (CleanupDialog).

Houses the QThread plumbing for the two dialogs that share a backing service
(``CleanupService``):

  * :class:`GroupProgressBridge` + :class:`GroupFindWorker` + :class:`GroupApplyWorker`
    -- power the v1.7-alpha :class:`GroupDialog` (duplicate finder).
  * Reserved namespace for the CleanupDialog workers (v1.7-alpha.4 next).

The pattern mirrors :class:`MigrationProgressBridge` and :class:`ScanProgressBridge`:
a QObject lives on the GUI thread and exposes ``Signal`` instances that worker
QThreads emit. Signals carry domain objects as ``object`` to keep the bridge
decoupled from the model layer (no pluggy-style import cycles).

The two-worker split (find vs apply) reflects the two-phase UX:

  1. The user clicks **Find** -- :class:`GroupFindWorker` runs
     ``CleanupService.find_duplicates`` and emits the ``CleanupReport``.
  2. The dialog renders the report and (optionally) the user clicks **Apply** --
     :class:`GroupApplyWorker` runs ``CleanupService.apply`` and emits the
     ``ApplyReport``.

Either phase can fail independently; either phase can be skipped (the user
might click Find, review, and close without applying).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QThread, Signal

if TYPE_CHECKING:  # pragma: no cover
    from curator.cli.runtime import CuratorRuntime
    from curator.services.cleanup import CleanupReport


class GroupProgressBridge(QObject):
    """Cross-thread signal carrier for GroupDialog (find + apply phases).

    Six signals cover the full duplicate-finding lifecycle. The dialog
    connects to all six; each worker emits the appropriate pair.

    All payloads are passed as ``object`` so the bridge stays decoupled
    from the domain models.
    """

    # --- Find phase --------------------------------------------------------
    #: Emitted when find_duplicates starts. Payload: (source_id, match_kind).
    find_started = Signal(object)

    #: Emitted on successful find. Payload: CleanupReport.
    find_completed = Signal(object)

    #: Emitted if find raises. Payload: the exception.
    find_failed = Signal(object)

    # --- Apply phase -------------------------------------------------------
    #: Emitted when apply starts. Payload: number of findings to be applied.
    apply_started = Signal(object)

    #: Emitted on successful apply. Payload: ApplyReport.
    apply_completed = Signal(object)

    #: Emitted if apply raises. Payload: the exception.
    apply_failed = Signal(object)


class GroupFindWorker(QThread):
    """QThread wrapper that runs ``CleanupService.find_duplicates``.

    Emits via the bridge:

      1. ``bridge.find_started.emit((source_id, match_kind))``
      2. On success: ``bridge.find_completed.emit(cleanup_report)``
      3. On failure: ``bridge.find_failed.emit(exception)``

    No cancellation support in v1.7-alpha (the underlying query is a single
    DB pass and not interruptible). Closing the dialog mid-find orphans the
    worker; Qt handles the dead-bridge emit gracefully.
    """

    def __init__(
        self,
        runtime: "CuratorRuntime",
        *,
        source_id: str | None,
        root_prefix: str | None,
        keep_strategy: str,
        keep_under: str | None,
        match_kind: str,
        similarity_threshold: float,
        bridge: GroupProgressBridge,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._source_id = source_id
        self._root_prefix = root_prefix
        self._keep_strategy = keep_strategy
        self._keep_under = keep_under
        self._match_kind = match_kind
        self._similarity_threshold = similarity_threshold
        self._bridge = bridge

    def run(self) -> None:  # noqa: D401 -- QThread API
        """Run the find. Emits via the bridge; never raises."""
        self._bridge.find_started.emit((self._source_id, self._match_kind))
        try:
            report = self._runtime.cleanup.find_duplicates(
                source_id=self._source_id,
                root_prefix=self._root_prefix,
                keep_strategy=self._keep_strategy,
                keep_under=self._keep_under,
                match_kind=self._match_kind,
                similarity_threshold=self._similarity_threshold,
            )
        except Exception as e:  # noqa: BLE001
            self._bridge.find_failed.emit(e)
            return
        self._bridge.find_completed.emit(report)


class GroupApplyWorker(QThread):
    """QThread wrapper that runs ``CleanupService.apply`` on a duplicate report.

    Emits via the bridge:

      1. ``bridge.apply_started.emit(len(report.findings))``
      2. On success: ``bridge.apply_completed.emit(apply_report)``
      3. On failure: ``bridge.apply_failed.emit(exception)``
    """

    def __init__(
        self,
        runtime: "CuratorRuntime",
        report: "CleanupReport",
        *,
        use_trash: bool,
        bridge: GroupProgressBridge,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._report = report
        self._use_trash = use_trash
        self._bridge = bridge

    def run(self) -> None:  # noqa: D401 -- QThread API
        """Run the apply. Emits via the bridge; never raises."""
        self._bridge.apply_started.emit(len(self._report.findings))
        try:
            apply_report = self._runtime.cleanup.apply(
                self._report, use_trash=self._use_trash,
            )
        except Exception as e:  # noqa: BLE001
            self._bridge.apply_failed.emit(e)
            return
        self._bridge.apply_completed.emit(apply_report)


__all__ = [
    "GroupProgressBridge",
    "GroupFindWorker",
    "GroupApplyWorker",
    "CleanupProgressBridge",
    "CleanupFindWorker",
    "CleanupApplyWorker",
]


# ---------------------------------------------------------------------------
# CleanupDialog workers (v1.7-alpha.4) -- mode-dispatched find + apply
# ---------------------------------------------------------------------------
#
# Where GroupFindWorker is specific to find_duplicates, CleanupFindWorker
# dispatches on a mode string to one of the 3 non-duplicate find methods:
#
#   mode='junk'             -> CleanupService.find_junk_files
#   mode='empty_dirs'       -> CleanupService.find_empty_dirs
#   mode='broken_symlinks'  -> CleanupService.find_broken_symlinks
#
# (Duplicates is excluded from CleanupDialog because GroupDialog gives
# the richer 2-phase UI for that case -- the CleanupDialog explains this
# and provides a shortcut to open GroupDialog instead.)


class CleanupProgressBridge(QObject):
    """Cross-thread signal carrier for CleanupDialog.

    Identical 6-signal shape to :class:`GroupProgressBridge`; separated
    so the two dialogs don't share state. Each dialog instantiates its
    own bridge.
    """

    find_started = Signal(object)       # payload: (mode, root)
    find_completed = Signal(object)     # payload: CleanupReport
    find_failed = Signal(object)        # payload: exception

    apply_started = Signal(object)      # payload: number of findings
    apply_completed = Signal(object)    # payload: ApplyReport
    apply_failed = Signal(object)       # payload: exception


class CleanupFindWorker(QThread):
    """QThread that dispatches to one of the 3 non-duplicate find_* methods.

    The ``mode`` parameter selects which CleanupService method runs:

      * ``mode='junk'``            -> ``find_junk_files(root, patterns=patterns)``
      * ``mode='empty_dirs'``      -> ``find_empty_dirs(root, ignore_system_junk=...)``
      * ``mode='broken_symlinks'`` -> ``find_broken_symlinks(root)``

    Emits via the bridge:

      1. ``bridge.find_started.emit((mode, root))``
      2. On success: ``bridge.find_completed.emit(cleanup_report)``
      3. On failure: ``bridge.find_failed.emit(exception)``
    """

    SUPPORTED_MODES = ("junk", "empty_dirs", "broken_symlinks")

    def __init__(
        self,
        runtime: "CuratorRuntime",
        *,
        mode: str,
        root: str,
        # Junk-specific:
        patterns: list[str] | None = None,
        # Empty-dirs-specific:
        ignore_system_junk: bool = True,
        bridge: "CleanupProgressBridge",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"CleanupFindWorker: unknown mode {mode!r}. "
                f"Supported: {self.SUPPORTED_MODES}"
            )
        self._runtime = runtime
        self._mode = mode
        self._root = root
        self._patterns = patterns
        self._ignore_system_junk = ignore_system_junk
        self._bridge = bridge

    def run(self) -> None:  # noqa: D401 -- QThread API
        """Dispatch to the right find_* method based on mode."""
        self._bridge.find_started.emit((self._mode, self._root))
        try:
            cleanup = self._runtime.cleanup
            if self._mode == "junk":
                report = cleanup.find_junk_files(
                    self._root, patterns=self._patterns,
                )
            elif self._mode == "empty_dirs":
                report = cleanup.find_empty_dirs(
                    self._root, ignore_system_junk=self._ignore_system_junk,
                )
            elif self._mode == "broken_symlinks":
                report = cleanup.find_broken_symlinks(self._root)
            else:
                # Defensive; __init__ validates but be paranoid.
                raise ValueError(f"unreachable: mode {self._mode!r}")
        except Exception as e:  # noqa: BLE001
            self._bridge.find_failed.emit(e)
            return
        self._bridge.find_completed.emit(report)


class CleanupApplyWorker(QThread):
    """QThread that runs ``CleanupService.apply`` for the CleanupDialog.

    Functionally identical to :class:`GroupApplyWorker` but uses
    :class:`CleanupProgressBridge` signals so the two dialogs stay
    decoupled.
    """

    def __init__(
        self,
        runtime: "CuratorRuntime",
        report: "CleanupReport",
        *,
        use_trash: bool,
        bridge: "CleanupProgressBridge",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._report = report
        self._use_trash = use_trash
        self._bridge = bridge

    def run(self) -> None:  # noqa: D401 -- QThread API
        """Run apply, emit via the bridge; never raise."""
        self._bridge.apply_started.emit(len(self._report.findings))
        try:
            apply_report = self._runtime.cleanup.apply(
                self._report, use_trash=self._use_trash,
            )
        except Exception as e:  # noqa: BLE001
            self._bridge.apply_failed.emit(e)
            return
        self._bridge.apply_completed.emit(apply_report)
