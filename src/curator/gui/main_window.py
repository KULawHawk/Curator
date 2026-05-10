"""Curator GUI main window (v0.34 → v1.6.2).

Single :class:`QMainWindow` with **8 tabs** (Inbox / Browser / Bundles /
Trash / Migrate / Audit Log / Settings / Lineage Graph), a status bar
showing DB info + row counts, and **5 menus** (File / Edit / Tools /
Workflows / Help).

v0.34 shipped read-only views. v0.35 added three mutations
(trash / restore / dissolve via right-click menus and Edit menu).
v0.43 added bundle creation/editing dialogs.

v1.6.2 (this release) adds discoverability:

  * **Tools menu** — placeholder items for the upcoming v1.7 dialogs
    (Scan, Find Duplicates, Cleanup Junk, Sources Manager, Health
    Check). Today these surface a 'coming in v1.7' notice; in v1.7
    they will open native PySide6 dialogs.
  * **Workflows menu** — launches the PowerShell batch workflows
    shipped at ``Curator/scripts/workflows/`` as separate console
    windows. Click-to-run interface for common multi-step operations
    (initial scan, find duplicates, cleanup junk, audit summary,
    health check) until v1.7's native dialogs ship.

Mutation logic is factored into ``_perform_*`` methods that NEVER raise
and return ``(success: bool, message: str)``. The slots just show the
message in a dialog. This makes the methods unit-testable without an
event loop.

See ``docs/design/GUI_V2_DESIGN.md`` for the full v1.7 / v1.8 / v1.9
implementation roadmap.
"""

from __future__ import annotations

import os
import subprocess  # noqa: F401  (kept for compat with downstream patches)
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QTableView,
    QWidget,
    QVBoxLayout,
)

from curator.gui.migrate_signals import MigrationProgressBridge
from curator.gui.models import (
    AuditLogTableModel,
    BundleTableModel,
    ConfigTableModel,
    FileTableModel,
    MigrationJobTableModel,
    MigrationProgressTableModel,
    PendingReviewTableModel,
    ScanJobTableModel,
    TrashTableModel,
)

if TYPE_CHECKING:  # pragma: no cover
    from curator.cli.runtime import CuratorRuntime


class CuratorMainWindow(QMainWindow):
    """Main GUI window. Constructed with a fully-wired :class:`CuratorRuntime`."""

    def __init__(self, runtime: "CuratorRuntime", parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._build_ui()
        self._refresh_status_bar()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        try:
            from curator import __version__ as _version
        except Exception:
            _version = "unknown"
        self.setWindowTitle(f"Curator {_version}")
        self.resize(1100, 650)

        # --- Tabs --------------------------------------------------------
        # Inbox is the landing tab (DESIGN.md §15.2 lists it first).
        tabs = QTabWidget(self)
        tabs.addTab(self._build_inbox_tab(), "Inbox")
        tabs.addTab(self._build_browser_tab(), "Browser")
        tabs.addTab(self._build_bundles_tab(), "Bundles")
        tabs.addTab(self._build_trash_tab(), "Trash")
        tabs.addTab(self._build_migrate_tab(), "Migrate")
        tabs.addTab(self._build_audit_tab(), "Audit Log")
        tabs.addTab(self._build_settings_tab(), "Settings")
        tabs.addTab(self._build_lineage_tab(), "Lineage Graph")
        self.setCentralWidget(tabs)
        self._tabs = tabs

        # --- Menu bar ----------------------------------------------------
        menu_file = self.menuBar().addMenu("&File")
        act_refresh = QAction("&Refresh", self)
        act_refresh.setShortcut(QKeySequence.StandardKey.Refresh)
        act_refresh.triggered.connect(self.refresh_all)
        menu_file.addAction(act_refresh)
        menu_file.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        menu_file.addAction(act_quit)

        # v0.35: Edit menu with mutation actions.
        menu_edit = self.menuBar().addMenu("&Edit")
        self._act_trash = QAction("Send selected file to &Trash...", self)
        self._act_trash.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_T))
        self._act_trash.triggered.connect(self._slot_trash_selected)
        menu_edit.addAction(self._act_trash)

        self._act_restore = QAction("&Restore selected trash record...", self)
        self._act_restore.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_R))
        self._act_restore.triggered.connect(self._slot_restore_selected)
        menu_edit.addAction(self._act_restore)

        self._act_dissolve = QAction("&Dissolve selected bundle...", self)
        self._act_dissolve.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_D))
        self._act_dissolve.triggered.connect(self._slot_dissolve_selected)
        menu_edit.addAction(self._act_dissolve)

        # v0.43: bundle creation + editing.
        menu_edit.addSeparator()
        self._act_bundle_new = QAction("&New bundle...", self)
        self._act_bundle_new.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_N))
        self._act_bundle_new.triggered.connect(self._slot_bundle_new)
        menu_edit.addAction(self._act_bundle_new)

        self._act_bundle_edit = QAction("&Edit selected bundle...", self)
        self._act_bundle_edit.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_E))
        self._act_bundle_edit.triggered.connect(self._slot_bundle_edit_selected)
        menu_edit.addAction(self._act_bundle_edit)

        # v1.6.2: Tools menu — placeholders for v1.7 native dialogs.
        menu_tools = self.menuBar().addMenu("&Tools")
        for label, key in [
            ("&Scan folder...", "scan"),
            ("Find &duplicates...", "group"),
            ("&Cleanup junk...", "cleanup"),
            ("&Sources manager...", "sources"),
            ("&Health check", "health"),
        ]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked=False, k=key: self._slot_tools_placeholder(k))
            menu_tools.addAction(act)

        # v1.6.2: Workflows menu — spawns the PowerShell .bat scripts
        # shipped at scripts/workflows/ as separate console windows.
        menu_wf = self.menuBar().addMenu("&Workflows")
        for label, script in [
            ("&Initial scan...",            "01_initial_scan.bat"),
            ("Find &duplicates...",         "02_find_duplicates.bat"),
            ("Cleanup &junk...",            "03_cleanup_junk.bat"),
            ("&Audit summary (24h)",        "04_audit_summary.bat"),
            ("&Health check",               "05_health_check.bat"),
        ]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked=False, s=script: self._slot_run_workflow(s))
            menu_wf.addAction(act)
        menu_wf.addSeparator()
        act_wf_about = QAction("&About these workflows...", self)
        act_wf_about.triggered.connect(self._show_workflows_about)
        menu_wf.addAction(act_wf_about)

        menu_help = self.menuBar().addMenu("&Help")
        act_about = QAction("&About Curator", self)
        act_about.triggered.connect(self._show_about)
        menu_help.addAction(act_about)

        # --- Status bar --------------------------------------------------
        sb = QStatusBar(self)
        self.setStatusBar(sb)
        self._status_db = QLabel()
        self._status_counts = QLabel()
        sb.addWidget(self._status_db, 1)
        sb.addPermanentWidget(self._status_counts)

    def _build_inbox_tab(self) -> QWidget:
        """v0.39: landing dashboard tab.

        Three stacked sections:
          * **Recent scans** — last 10 scan jobs.
          * **Pending review** — lineage edges with confidence in the
            ``[escalate, auto_confirm)`` ambiguous middle band.
          * **Recent trash** — last 10 trashed items.

        Each section is a QGroupBox with a header label + small
        QTableView. Read-only; mutations live on the dedicated tabs.
        The thresholds for the middle section come from the runtime's
        Config so users can tune them via curator.toml.
        """
        # Pull thresholds from config (DESIGN.md §8.2).
        cfg = self.runtime.config
        escalate = float(cfg.get("lineage.escalate_threshold", 0.7))
        auto_confirm = float(cfg.get("lineage.auto_confirm_threshold", 0.95))

        # Build the three small models.
        self._inbox_scans_model = ScanJobTableModel(self.runtime.job_repo)
        self._inbox_pending_model = PendingReviewTableModel(
            self.runtime.lineage_repo, self.runtime.file_repo,
            escalate_threshold=escalate,
            auto_confirm_threshold=auto_confirm,
        )
        self._inbox_trash_model = TrashTableModel(
            self.runtime.trash_repo, limit=10,
        )

        # Build the three small views.
        self._inbox_scans_view = self._make_table_view(self._inbox_scans_model)
        self._inbox_pending_view = self._make_table_view(self._inbox_pending_model)
        self._inbox_trash_view = self._make_table_view(self._inbox_trash_model)

        # Tighten each section's height to a sensible default.
        for view in (self._inbox_scans_view, self._inbox_pending_view, self._inbox_trash_view):
            view.setMinimumHeight(120)
            view.setMaximumHeight(220)

        # Compose the tab.
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._make_inbox_section(
            "Recent scans", self._inbox_scans_view, self._inbox_scans_model,
            empty_hint="No scans yet. Run `curator scan <root>` to get started.",
        ))
        layout.addWidget(self._make_inbox_section(
            f"Pending review (confidence in [{escalate:.2f}, {auto_confirm:.2f}))",
            self._inbox_pending_view, self._inbox_pending_model,
            empty_hint="No lineage edges in the ambiguous middle band. Curator "
                      "is either certain or quiet.",
        ))
        layout.addWidget(self._make_inbox_section(
            "Recent trash", self._inbox_trash_view, self._inbox_trash_model,
            empty_hint="Nothing in the trash registry.",
        ))
        layout.addStretch(1)
        return wrapper

    def _make_inbox_section(
        self, title: str, view: QTableView, model, *, empty_hint: str,
    ) -> QWidget:
        """Wrap a section header + table view + empty-hint label."""
        from PySide6.QtWidgets import QGroupBox
        group = QGroupBox(f"{title} \u2014 {model.rowCount()}")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(6, 6, 6, 6)
        group_layout.addWidget(view)
        if model.rowCount() == 0:
            empty = QLabel(empty_hint)
            empty.setStyleSheet("color: #888; font-style: italic; padding: 4px;")
            empty.setWordWrap(True)
            group_layout.addWidget(empty)
        return group

    def _build_browser_tab(self) -> QWidget:
        self._files_model = FileTableModel(self.runtime.file_repo)
        self._files_view = self._make_table_view(self._files_model)
        self._files_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._files_view.customContextMenuRequested.connect(
            self._show_browser_context_menu,
        )
        # v0.36: double-click opens the inspect dialog.
        self._files_view.doubleClicked.connect(self._slot_inspect_at_index)
        return self._wrap_table(self._files_view)

    def _build_bundles_tab(self) -> QWidget:
        self._bundles_model = BundleTableModel(self.runtime.bundle_repo)
        self._bundles_view = self._make_table_view(self._bundles_model)
        self._bundles_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._bundles_view.customContextMenuRequested.connect(
            self._show_bundles_context_menu,
        )
        return self._wrap_table(self._bundles_view)

    def _build_trash_tab(self) -> QWidget:
        self._trash_model = TrashTableModel(self.runtime.trash_repo)
        self._trash_view = self._make_table_view(self._trash_model)
        self._trash_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._trash_view.customContextMenuRequested.connect(
            self._show_trash_context_menu,
        )
        return self._wrap_table(self._trash_view)

    def _build_migrate_tab(self) -> QWidget:
        """v1.1.0 Tracer Phase 2 Session C1: read-only Migrate tab.

        Master/detail layout via :class:`QSplitter`:

          * **Top:** list of recent migration jobs (most-recent first),
            via :class:`MigrationJobTableModel`.
          * **Bottom:** per-file progress for the currently-selected
            job, via :class:`MigrationProgressTableModel`.

        Selecting a job row populates the progress table; the label
        above the progress table shows the short job_id + file count
        + status. A Refresh button at the bottom re-queries both
        models without losing the current selection.

        Read-only in Session C1; mutations (Abort, Resume) and live
        progress signal wiring during ``run_job`` come in Session C2.
        For now, refresh is manual via the button or File > Refresh / F5.
        """
        from PySide6.QtWidgets import QSplitter

        # Models.
        self._migrate_jobs_model = MigrationJobTableModel(
            self.runtime.migration_job_repo,
        )
        self._migrate_progress_model = MigrationProgressTableModel(
            self.runtime.migration_job_repo,
        )

        # Views.
        self._migrate_jobs_view = self._make_table_view(self._migrate_jobs_model)
        self._migrate_progress_view = self._make_table_view(
            self._migrate_progress_model,
        )

        # Wire selection: clicking a job row populates the progress table.
        self._migrate_jobs_view.selectionModel().selectionChanged.connect(
            self._slot_migrate_job_selected,
        )

        # v1.1.0 Tracer Phase 2 Session C2: right-click context menu for
        # Abort / Resume mutations on individual jobs.
        self._migrate_jobs_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu,
        )
        self._migrate_jobs_view.customContextMenuRequested.connect(
            self._show_migrate_context_menu,
        )
        # Track background resume threads so tests can join them and
        # the GUI's Refresh can prefer fresh state over stale cache.
        self._migrate_resume_threads: list[threading.Thread] = []

        # v1.1.0 Tracer Phase 2 Session C2b: cross-thread bridge for live
        # progress updates. Worker threads inside MigrationService.run_job
        # call ``bridge.progress_updated.emit(progress)`` per file; Qt
        # delivers the signal across thread boundaries via QueuedConnection,
        # so the connected slot fires on the GUI thread (where touching
        # Qt models is safe).
        self._migrate_progress_bridge = MigrationProgressBridge(parent=self)
        self._migrate_progress_bridge.progress_updated.connect(
            self._slot_migrate_apply_progress_update,
        )

        # Top half: jobs label + jobs view.
        jobs_wrapper = QWidget()
        jobs_layout = QVBoxLayout(jobs_wrapper)
        jobs_layout.setContentsMargins(0, 0, 0, 0)
        jobs_layout.addWidget(QLabel("<b>Migration jobs</b> (most recent first)"))
        jobs_layout.addWidget(self._migrate_jobs_view)

        # Bottom half: progress label + progress view.
        progress_wrapper = QWidget()
        progress_layout = QVBoxLayout(progress_wrapper)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self._migrate_progress_label = QLabel(
            "<b>Per-file progress</b> \u2014 select a job above"
        )
        progress_layout.addWidget(self._migrate_progress_label)
        progress_layout.addWidget(self._migrate_progress_view)

        # Master/detail via QSplitter.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(jobs_wrapper)
        splitter.addWidget(progress_wrapper)
        splitter.setSizes([200, 400])  # progress gets the larger pane
        self._migrate_splitter = splitter

        # Refresh button row.
        self._migrate_refresh_btn = QPushButton("Refresh")
        self._migrate_refresh_btn.setToolTip(
            "Re-query both job and progress tables. Useful after a CLI "
            "`curator migrate --apply` run completes in another shell."
        )
        self._migrate_refresh_btn.clicked.connect(self._slot_migrate_refresh)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._migrate_refresh_btn)

        # Compose tab.
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)
        layout.addLayout(btn_row)
        return wrapper

    def _slot_migrate_job_selected(self, *args, **kwargs) -> None:
        """Master\u2192detail wire-up: populate progress for the selected job.

        Called whenever the jobs view's selection changes. If nothing
        is selected (e.g. after a refresh that cleared rows), clears
        the progress table back to the empty state.
        """
        indexes = self._migrate_jobs_view.selectionModel().selectedRows()
        if not indexes:
            self._migrate_progress_model.set_job_id(None)
            self._migrate_progress_label.setText(
                "<b>Per-file progress</b> \u2014 select a job above"
            )
            return
        row = indexes[0].row()
        job = self._migrate_jobs_model.job_at(row)
        if job is None:
            return
        self._migrate_progress_model.set_job_id(job.job_id)
        short_id = str(job.job_id)[:8]
        self._migrate_progress_label.setText(
            f"<b>Per-file progress</b> \u2014 job <code>{short_id}\u2026</code> "
            f"({job.files_total} files; status: {job.status})"
        )

    def _slot_migrate_refresh(self) -> None:
        """Refresh both Migrate-tab models, preserving the current
        job selection if the same job_id is still present after
        re-querying the jobs list."""
        # Remember current selection so we can re-apply it post-refresh.
        current_job_id = None
        indexes = self._migrate_jobs_view.selectionModel().selectedRows()
        if indexes:
            j = self._migrate_jobs_model.job_at(indexes[0].row())
            if j is not None:
                current_job_id = j.job_id

        self._migrate_jobs_model.refresh()
        # Try to restore selection on the same job_id.
        if current_job_id is not None:
            for r in range(self._migrate_jobs_model.rowCount()):
                j = self._migrate_jobs_model.job_at(r)
                if j is not None and j.job_id == current_job_id:
                    self._migrate_jobs_view.selectRow(r)
                    return  # selectionChanged slot already refreshed progress
        # No prior selection (or job vanished) -> just refresh progress
        # for whatever the model currently shows.
        self._migrate_progress_model.refresh()

    # ------------------------------------------------------------------
    # v1.1.0 Tracer Phase 2 Session C2: Migrate tab mutations
    # (right-click context menu -> Abort / Resume)
    # ------------------------------------------------------------------

    # Statuses for which Resume makes sense (queued, paused-by-abort,
    # finished-with-failures, partial). Excluded: 'running' (already
    # in flight) and 'completed' (no work left).
    _MIGRATE_RESUMABLE_STATUSES = frozenset(
        {"queued", "cancelled", "partial", "failed"}
    )

    def _show_migrate_context_menu(self, pos: QPoint) -> None:
        """Right-click context menu on the migration jobs table.

        Builds a menu with Abort + Resume actions. Each is enabled only
        when the job's current status admits the action -- abort only
        for ``running`` jobs, resume only for jobs in
        :attr:`_MIGRATE_RESUMABLE_STATUSES`.
        """
        idx = self._migrate_jobs_view.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        job = self._migrate_jobs_model.job_at(row)
        if job is None:
            return

        menu = QMenu(self._migrate_jobs_view)
        act_abort = menu.addAction("Abort job\u2026")
        act_abort.setEnabled(job.status == "running")
        act_abort.setToolTip(
            "Signal a running job to stop. Workers finish their current "
            "file (per-file atomicity is preserved), then exit."
        )

        act_resume = menu.addAction("Resume job (background)\u2026")
        act_resume.setEnabled(
            job.status in self._MIGRATE_RESUMABLE_STATUSES
        )
        act_resume.setToolTip(
            "Start (or resume) the job in a background thread. The GUI "
            "stays responsive; click Refresh to see updated progress."
        )

        chosen = menu.exec(
            self._migrate_jobs_view.viewport().mapToGlobal(pos)
        )
        if chosen is act_abort:
            self._slot_migrate_abort_at_row(row)
        elif chosen is act_resume:
            self._slot_migrate_resume_at_row(row)

    def _slot_migrate_abort_at_row(self, row: int) -> None:
        """Confirmation dialog -> :meth:`_perform_migrate_abort` -> result dialog."""
        job = self._migrate_jobs_model.job_at(row)
        if job is None:
            return
        short_id = str(job.job_id)[:8]
        confirm = QMessageBox.question(
            self,
            "Abort migration job",
            (
                f"Abort job <code>{short_id}\u2026</code>? Workers will "
                "finish their current file (per-file atomicity is "
                "preserved) and then exit."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        success, message = self._perform_migrate_abort(job.job_id)
        icon = (
            QMessageBox.Icon.Information if success
            else QMessageBox.Icon.Warning
        )
        box = QMessageBox(icon, "Abort job", message, parent=self)
        box.exec()
        # Refresh so the user sees the status update sooner rather than
        # later -- abort_job is fire-and-forget; the actual status flip
        # to 'cancelled' happens when workers return.
        self._slot_migrate_refresh()

    def _slot_migrate_resume_at_row(self, row: int) -> None:
        """Confirmation dialog -> :meth:`_perform_migrate_resume` -> result dialog."""
        job = self._migrate_jobs_model.job_at(row)
        if job is None:
            return
        short_id = str(job.job_id)[:8]
        confirm = QMessageBox.question(
            self,
            "Resume migration job",
            (
                f"Resume job <code>{short_id}\u2026</code> in the "
                "background? The GUI will stay responsive; click "
                "Refresh to see updated progress."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        success, message = self._perform_migrate_resume(job.job_id)
        icon = (
            QMessageBox.Icon.Information if success
            else QMessageBox.Icon.Warning
        )
        box = QMessageBox(icon, "Resume job", message, parent=self)
        box.exec()
        # Refresh once now so the user sees the status flip from queued
        # /cancelled/etc to 'running' as the worker thread starts.
        self._slot_migrate_refresh()

    def _perform_migrate_abort(
        self, job_id: UUID,
    ) -> tuple[bool, str]:
        """Signal a running job to stop. Best-effort. Never raises.

        Returns ``(success, message)`` suitable to display in a dialog.
        Aborting a job that isn't currently running is a no-op at the
        service layer, but we still surface that as ``success=True``
        because the user's intent (job not running) is satisfied.
        """
        try:
            self.runtime.migration.abort_job(job_id)
        except Exception as e:  # noqa: BLE001 -- boundary catch
            return False, f"Failed to abort job: {type(e).__name__}: {e}"
        return True, (
            f"Abort signaled for job {str(job_id)[:8]}\u2026\n\n"
            "Workers finish their current files atomically. The job's "
            "status will flip to 'cancelled' once all workers have "
            "returned. Click Refresh to see the updated status."
        )

    def _perform_migrate_resume(
        self, job_id: UUID, *, workers: int = 4,
    ) -> tuple[bool, str]:
        """Start a background thread running ``migration.run_job``. Best-effort.

        Returns ``(success, message)`` indicating whether the thread was
        started. Does NOT wait for run_job to complete -- that's the
        whole point of running it in the background; the GUI stays
        responsive.

        Refuses if the job is currently 'running' (would race-condition
        with the existing run) or 'completed' (no work left). Never
        raises; refusal returns ``(False, message)``.
        """
        try:
            status = self.runtime.migration.get_job_status(job_id)
        except Exception as e:  # noqa: BLE001 -- boundary catch
            return False, (
                f"Failed to query job status: {type(e).__name__}: {e}"
            )

        cur = status.get("status", "")
        if cur == "running":
            return False, (
                f"Job {str(job_id)[:8]}\u2026 is already running. Use Abort "
                "first if you want to interrupt and re-start it."
            )
        if cur == "completed":
            return False, (
                f"Job {str(job_id)[:8]}\u2026 is already completed; "
                "nothing to resume."
            )

        def _runner() -> None:
            try:
                self.runtime.migration.run_job(
                    job_id,
                    workers=workers,
                    on_progress=self._migrate_progress_bridge.progress_updated.emit,
                )
            except Exception as e:  # noqa: BLE001 -- non-Qt thread boundary
                # The failure surfaces in the GUI on next Refresh via
                # the job's status field. Log here so it's not silent.
                from loguru import logger
                logger.warning(
                    "MigrationService.run_job (GUI background) raised: {e}",
                    e=e,
                )

        thread = threading.Thread(
            target=_runner,
            name=f"curator-gui-resume-{str(job_id)[:8]}",
            daemon=True,
        )
        thread.start()
        # Track the thread so refresh / shutdown can prefer fresh state.
        if not hasattr(self, "_migrate_resume_threads"):
            self._migrate_resume_threads = []
        self._migrate_resume_threads.append(thread)

        return True, (
            f"Resume started in the background.\n\n"
            f"Job {str(job_id)[:8]}\u2026 will run with {workers} "
            "workers. Click Refresh to see progress."
        )

    def _slot_migrate_apply_progress_update(self, progress) -> None:
        """Slot for ``MigrationProgressBridge.progress_updated``.

        Runs on the GUI thread (Qt routes the cross-thread emission via
        ``QueuedConnection``). Refreshes the affected models so the user
        sees live progress without clicking Refresh.

        Strategy: full ``refresh()`` of the jobs model (cheap; <=50 rows)
        plus full refresh of the progress model IF the in-flight job is
        the one currently displayed. Per-row update would be more
        efficient for thousand-file jobs, but full refresh is simpler
        and correct for the typical job size (dozens to low hundreds of
        files).

        Defensive: if the model attributes don't exist (window torn down,
        Migrate tab not built yet), this is a silent no-op.
        """
        # Defensive: window may be in tear-down
        if not hasattr(self, "_migrate_jobs_model"):
            return
        if not hasattr(self, "_migrate_progress_model"):
            return

        # Always refresh the jobs model so rollup counters
        # (files_copied, files_failed, bytes_copied, status) update.
        try:
            self._migrate_jobs_model.refresh()
        except Exception:  # noqa: BLE001 -- defensive
            return

        # Refresh the progress model only if it's pointed at the
        # job that just produced this update. Avoids re-querying for
        # unrelated jobs the user may be viewing instead.
        try:
            current_job_id = self._migrate_progress_model.job_id
        except Exception:  # noqa: BLE001
            return
        progress_job_id = getattr(progress, "job_id", None)
        if (current_job_id is not None
                and progress_job_id is not None
                and current_job_id == progress_job_id):
            try:
                self._migrate_progress_model.refresh()
            except Exception:  # noqa: BLE001
                pass

    def _build_audit_tab(self) -> QWidget:
        """v0.37: audit log view. Read-only, no context menu.

        The audit log is intentionally append-only at the storage layer
        (see :class:`AuditRepository`); there's no GUI mutation path
        because there's no model-level mutation path.
        """
        self._audit_model = AuditLogTableModel(self.runtime.audit_repo)
        self._audit_view = self._make_table_view(self._audit_model)
        return self._wrap_table(self._audit_view)

    def _build_settings_tab(self) -> QWidget:
        """v0.38: settings view (curator.toml display + reload).

        The runtime's services were constructed with the loaded config
        and won't pick up changes without restarting Curator. The
        Reload button in this view re-parses the source TOML file and
        updates the *display* only; the live runtime keeps using its
        original config. This lets users verify their TOML edits are
        valid without restarting, and see what the new values would
        be when they do restart.
        """
        cfg = self.runtime.config
        self._settings_model = ConfigTableModel(cfg)
        self._settings_view = self._make_table_view(self._settings_model)

        # Header showing source path + active-vs-disk state.
        self._settings_header = QLabel()
        self._settings_header.setWordWrap(True)
        self._settings_header.setStyleSheet("padding: 4px;")
        self._update_settings_header(cfg, reloaded=False)

        # Reload button + help text in a horizontal row.
        self._settings_reload_btn = QPushButton("Reload from disk")
        self._settings_reload_btn.setToolTip(
            "Re-parse the TOML file and refresh the table. Note: this only "
            "updates the display — the live runtime's services keep using "
            "the original config until Curator is restarted."
        )
        self._settings_reload_btn.clicked.connect(self._slot_settings_reload)

        help_label = QLabel(
            "<i>Edit the file directly with your text editor. Changes apply "
            "on next Curator restart.</i>"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("padding: 4px; color: #888;")

        button_row = QHBoxLayout()
        button_row.addWidget(self._settings_reload_btn)
        button_row.addStretch(1)

        # Compose the tab.
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._settings_header)
        layout.addWidget(self._settings_view)
        layout.addLayout(button_row)
        layout.addWidget(help_label)
        return wrapper

    def _update_settings_header(self, config, *, reloaded: bool) -> None:
        """Refresh the settings tab's source-path header label."""
        try:
            src = config.source_path
        except Exception:
            src = None
        if src is None:
            text = "<b>(using built-in defaults)</b> — no curator.toml found."
        else:
            text = f"<b>Loaded from:</b> {src}"
        if reloaded:
            text += "  <span style='color: #6a6;'>(reloaded from disk)</span>"
        self._settings_header.setText(text)

    def _slot_settings_reload(self) -> None:
        """Re-parse the source TOML and refresh the Settings table.

        Falls back to a friendly error dialog if the file is missing
        or contains invalid TOML.
        """
        success, message, fresh_config = self._perform_settings_reload()
        if not success:
            QMessageBox.warning(self, "Reload from disk", message)
            return
        # Update the model + header.
        self._settings_model.set_config(fresh_config)
        self._update_settings_header(fresh_config, reloaded=True)
        # Brief confirmation in the status bar (transient).
        self.statusBar().showMessage(message, 4000)

    def _perform_settings_reload(self):
        """Re-parse curator.toml. Never raises.

        Returns:
            ``(success: bool, message: str, fresh_config: Config | None)``.
            On failure, ``fresh_config`` is None.
        """
        try:
            from curator.config import Config as _Config
            src = self.runtime.config.source_path
            fresh = _Config.load(explicit_path=src) if src else _Config.load()
        except Exception as e:  # noqa: BLE001
            return False, f"Failed to reload curator.toml: {e}", None
        if fresh.source_path is None:
            return True, "Reloaded (no TOML file found; using built-in defaults).", fresh
        return True, f"Reloaded from {fresh.source_path}", fresh

    def _build_lineage_tab(self) -> QWidget:
        """v0.41: Lineage Graph view (final DESIGN.md §15.2 view).

        Renders all files with at least one lineage edge as a graph,
        with nodes = files, edges = lineage relationships colored by
        edge kind. Read-only for v0.41; focus-mode (centered on a
        selected file) is a v0.42 follow-up.
        """
        from curator.gui.lineage_view import (
            LineageGraphBuilder,
            _make_lineage_graph_view,
        )
        self._lineage_builder = LineageGraphBuilder(
            self.runtime.file_repo,
            self.runtime.lineage_repo,
        )
        self._lineage_view = _make_lineage_graph_view(self._lineage_builder)

        # Wrap with a header showing edge-kind legend.
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QLabel(self._build_lineage_legend_html())
        header.setWordWrap(True)
        header.setStyleSheet("padding: 4px; font-size: 10pt;")
        layout.addWidget(header)

        layout.addWidget(self._lineage_view)
        return wrapper

    @staticmethod
    def _build_lineage_legend_html() -> str:
        """Build a colored-swatch HTML legend for edge kinds."""
        from curator.gui.lineage_view import EDGE_KIND_COLORS
        parts = ["<b>Edge kinds:</b> "]
        for kind, color in EDGE_KIND_COLORS.items():
            parts.append(
                f"<span style='color: {color}; font-weight: bold;'>●</span> "
                f"{kind}"
            )
        return " &nbsp;&nbsp; ".join(parts)

    @staticmethod
    def _make_table_view(model) -> QTableView:
        view = QTableView()
        view.setModel(model)
        view.setSortingEnabled(True)
        view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        view.setAlternatingRowColors(True)
        view.verticalHeader().setVisible(False)
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        view.horizontalHeader().setStretchLastSection(True)
        return view

    @staticmethod
    def _wrap_table(view: QTableView) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)
        return w

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        """Re-query every model. Bound to File > Refresh / F5."""
        self._files_model.refresh()
        self._bundles_model.refresh()
        self._trash_model.refresh()
        self._audit_model.refresh()
        # v0.39: Inbox tab models.
        self._inbox_scans_model.refresh()
        self._inbox_pending_model.refresh()
        self._inbox_trash_model.refresh()
        # v0.41: Lineage graph view (refresh re-queries the builder).
        if hasattr(self, "_lineage_view") and self._lineage_view is not None:
            self._lineage_view.refresh()
        # v1.1.0 (Tracer Phase 2 Session C1): Migrate tab.
        if hasattr(self, "_migrate_jobs_model"):
            self._slot_migrate_refresh()
        # Settings model points at the runtime config; nothing to re-query
        # there — the explicit Reload button is the path for that.
        self._refresh_status_bar()

    def _refresh_status_bar(self) -> None:
        try:
            db_path = str(self.runtime.db.db_path)
        except Exception:
            db_path = "(unknown)"
        self._status_db.setText(f"DB: {db_path}")
        self._status_counts.setText(
            f"Files: {self._files_model.rowCount()}    "
            f"Bundles: {self._bundles_model.rowCount()}    "
            f"Trash: {self._trash_model.rowCount()}    "
            f"Audit: {self._audit_model.rowCount()}"
        )

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------

    def _show_browser_context_menu(self, pos: QPoint) -> None:
        idx = self._files_view.indexAt(pos)
        if not idx.isValid():
            return
        menu = QMenu(self._files_view)
        act_inspect = menu.addAction("Inspect...")
        menu.addSeparator()
        act_trash = menu.addAction("Send to Trash...")
        chosen = menu.exec(self._files_view.viewport().mapToGlobal(pos))
        if chosen is act_inspect:
            self._slot_inspect_at_index(idx)
        elif chosen is act_trash:
            self._slot_trash_at_row(idx.row())

    def _show_trash_context_menu(self, pos: QPoint) -> None:
        idx = self._trash_view.indexAt(pos)
        if not idx.isValid():
            return
        menu = QMenu(self._trash_view)
        act = menu.addAction("Restore...")
        chosen = menu.exec(self._trash_view.viewport().mapToGlobal(pos))
        if chosen is act:
            self._slot_restore_at_row(idx.row())

    def _show_bundles_context_menu(self, pos: QPoint) -> None:
        idx = self._bundles_view.indexAt(pos)
        menu = QMenu(self._bundles_view)
        # "New bundle..." is always available, even when right-clicking on empty space.
        act_new = menu.addAction("New bundle...")
        act_edit = None
        act_dissolve = None
        if idx.isValid():
            menu.addSeparator()
            act_edit = menu.addAction("Edit bundle...")
            act_dissolve = menu.addAction("Dissolve bundle...")
        chosen = menu.exec(self._bundles_view.viewport().mapToGlobal(pos))
        if chosen is act_new:
            self._slot_bundle_new()
        elif chosen is act_edit and idx.isValid():
            self._slot_bundle_edit_at_row(idx.row())
        elif chosen is act_dissolve and idx.isValid():
            self._slot_dissolve_at_row(idx.row())

    # ------------------------------------------------------------------
    # Slots (Edit menu + context menu both call these)
    # ------------------------------------------------------------------

    def _slot_trash_selected(self) -> None:
        idx = self._files_view.currentIndex()
        if not idx.isValid():
            QMessageBox.information(
                self, "No selection",
                "Select a file in the Browser tab first.",
            )
            return
        self._slot_trash_at_row(idx.row())

    def _slot_restore_selected(self) -> None:
        idx = self._trash_view.currentIndex()
        if not idx.isValid():
            QMessageBox.information(
                self, "No selection",
                "Select a trashed file in the Trash tab first.",
            )
            return
        self._slot_restore_at_row(idx.row())

    def _slot_inspect_at_index(self, index) -> None:
        """Open the per-file inspect dialog for the row at ``index``.

        Wired to the Browser table's ``doubleClicked`` signal. Also
        callable directly from tests via the row index.
        """
        if not index.isValid():
            return
        f = self._files_model.file_at(index.row())
        if f is None:
            return
        self._open_inspect_dialog(f)

    def _open_inspect_dialog(self, file) -> None:
        """Construct + show the inspect dialog modally. Testable seam.

        Factored out so tests can patch this to capture the dialog
        construction without entering the modal exec loop.
        """
        from curator.gui.dialogs import FileInspectDialog
        dlg = FileInspectDialog(file, self.runtime, parent=self)
        dlg.exec()

    def _slot_dissolve_selected(self) -> None:
        idx = self._bundles_view.currentIndex()
        if not idx.isValid():
            QMessageBox.information(
                self, "No selection",
                "Select a bundle in the Bundles tab first.",
            )
            return
        self._slot_dissolve_at_row(idx.row())

    def _slot_trash_at_row(self, row: int) -> None:
        f = self._files_model.file_at(row)
        if f is None:
            return
        ok = QMessageBox.question(
            self,
            "Send to Trash?",
            f"Send this file to the OS Recycle Bin?\n\n{f.source_path}\n\n"
            "Curator records the move so you can restore later from the "
            "Trash tab (best effort -- on some platforms manual restoration "
            "from the OS Recycle Bin may be required).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        success, message = self._perform_trash(
            f.curator_id, reason="user via GUI",
        )
        self._show_result_dialog("Send to Trash", success, message)
        self.refresh_all()

    def _slot_restore_at_row(self, row: int) -> None:
        record = self._trash_model.trash_at(row)
        if record is None:
            return
        ok = QMessageBox.question(
            self,
            "Restore from Trash?",
            f"Restore this file?\n\n{record.original_path}\n\n"
            "Curator will attempt to move the file back from the OS "
            "Recycle Bin. On some platforms (notably Windows) this is "
            "not possible automatically -- you may need to restore "
            "manually from the Recycle Bin.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        success, message = self._perform_restore(record.curator_id)
        self._show_result_dialog("Restore", success, message)
        self.refresh_all()

    def _slot_dissolve_at_row(self, row: int) -> None:
        b = self._bundles_model.bundle_at(row)
        if b is None:
            return
        ok = QMessageBox.question(
            self,
            "Dissolve bundle?",
            f"Dissolve this bundle?\n\n"
            f"Name: {b.name or '(unnamed)'}\n"
            f"Type: {b.bundle_type}\n\n"
            "The bundle row and its membership rows will be removed. "
            "The member files themselves are NOT affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        success, message = self._perform_dissolve(b.bundle_id)
        self._show_result_dialog("Dissolve bundle", success, message)
        self.refresh_all()

    # ------------------------------------------------------------------
    # _perform_* methods (testable; never raise; return (success, message))
    # ------------------------------------------------------------------

    def _perform_trash(self, curator_id: UUID, *, reason: str) -> tuple[bool, str]:
        """Send a file to the OS trash via TrashService. Best-effort.

        Returns ``(success, message)``. Never raises. ``message`` is
        suitable to display in a dialog.
        """
        try:
            record = self.runtime.trash.send_to_trash(
                curator_id, reason=reason, actor="user.gui",
            )
        except Exception as e:  # noqa: BLE001 -- boundary catch
            return False, f"Failed to send to Trash: {e}"
        return True, (
            f"Sent to OS Recycle Bin.\n\n"
            f"Original: {record.original_path}\n"
            f"Trash record id: {record.curator_id}"
        )

    def _perform_restore(self, curator_id: UUID) -> tuple[bool, str]:
        """Restore a file from trash via TrashService. Best-effort.

        Returns ``(success, message)``. Never raises. On Windows, this
        will typically fail with ``RestoreImpossibleError`` because
        ``send2trash`` doesn't return the OS trash location -- the
        message tells the user to restore manually from the Recycle
        Bin. The trash record stays in place so they can re-attempt.
        """
        try:
            file = self.runtime.trash.restore(curator_id, actor="user.gui")
        except Exception as e:  # noqa: BLE001
            # RestoreImpossibleError gets a friendlier message; everything
            # else falls through with a generic prefix.
            cls = type(e).__name__
            if cls == "RestoreImpossibleError":
                return False, (
                    "Curator can't restore this file automatically (the OS "
                    "trash location wasn't recorded at trash time). Please "
                    "open your Recycle Bin and restore it manually. The "
                    "Curator trash record will remain so you can refer to "
                    "it for the original path."
                )
            return False, f"Failed to restore: {e}"
        return True, f"Restored to:\n{file.source_path}"

    def _perform_dissolve(self, bundle_id: UUID) -> tuple[bool, str]:
        """Dissolve a bundle via BundleService. Best-effort.

        Returns ``(success, message)``. Never raises.
        """
        try:
            self.runtime.bundle.dissolve(bundle_id)
        except Exception as e:  # noqa: BLE001
            return False, f"Failed to dissolve bundle: {e}"
        return True, "Bundle dissolved. Member files were preserved."

    # ------------------------------------------------------------------
    # v0.43: Bundle create + edit
    # ------------------------------------------------------------------

    def _slot_bundle_new(self) -> None:
        """Open the bundle editor in Create mode and apply the result if accepted."""
        result = self._open_bundle_editor(existing_bundle=None)
        if result is None:
            return
        success, message = self._perform_bundle_create(
            name=result.name,
            description=result.description,
            member_ids=result.member_ids,
            primary_id=result.primary_id,
        )
        self._show_result_dialog("New bundle", success, message)
        self.refresh_all()

    def _slot_bundle_edit_selected(self) -> None:
        idx = self._bundles_view.currentIndex()
        if not idx.isValid():
            QMessageBox.information(
                self, "No selection",
                "Select a bundle in the Bundles tab first.",
            )
            return
        self._slot_bundle_edit_at_row(idx.row())

    def _slot_bundle_edit_at_row(self, row: int) -> None:
        b = self._bundles_model.bundle_at(row)
        if b is None:
            return
        result = self._open_bundle_editor(existing_bundle=b)
        if result is None:
            return
        success, message = self._perform_bundle_apply_edits(
            bundle_id=b.bundle_id,
            name=result.name,
            description=result.description,
            target_member_ids=result.member_ids,
            primary_id=result.primary_id,
            initial_member_ids=result.initial_member_ids,
        )
        self._show_result_dialog("Edit bundle", success, message)
        self.refresh_all()

    def _open_bundle_editor(self, *, existing_bundle):
        """Construct + show the bundle editor dialog modally. Testable seam.

        Tests patch this attribute on the window to inject a synthetic
        :class:`BundleEditorResult` (or ``None``) without booting the
        Qt event loop.

        Returns:
            :class:`BundleEditorResult` or ``None`` if cancelled.
        """
        from curator.gui.dialogs import BundleEditorDialog
        dlg = BundleEditorDialog(
            self.runtime, existing_bundle=existing_bundle, parent=self,
        )
        accepted = dlg.exec()
        if not accepted:
            return None
        return dlg.get_result()

    def _perform_bundle_create(
        self,
        *,
        name: str,
        description,
        member_ids,
        primary_id,
    ) -> tuple[bool, str]:
        """Create a new manual bundle via BundleService. Best-effort.

        Returns ``(success, message)``. Never raises.
        """
        try:
            bundle = self.runtime.bundle.create_manual(
                name=name,
                member_ids=list(member_ids),
                description=description,
                primary_id=primary_id,
            )
        except Exception as e:  # noqa: BLE001
            return False, f"Failed to create bundle: {e}"
        return True, (
            f"Created bundle: {bundle.name}\n"
            f"Members: {len(member_ids)}\n"
            f"Bundle id: {bundle.bundle_id}"
        )

    def _perform_bundle_apply_edits(
        self,
        *,
        bundle_id: UUID,
        name: str,
        description,
        target_member_ids,
        primary_id,
        initial_member_ids,
    ) -> tuple[bool, str]:
        """Apply edits to an existing bundle: rename + diff membership.

        Best-effort. Returns ``(success, message)``. Never raises.

        Strategy:
          1. Update bundle metadata (name + description) via the repo.
          2. For each member to add: ``bundle.add_member``.
          3. For each member to remove: ``bundle.remove_member``.
          4. Reset the primary marker: remove + re-add the primary as
             role='primary'. (Other members are role='member'.)

        We compute add/remove by set diff; we don't try to reorder.
        """
        try:
            existing = self.runtime.bundle.get(bundle_id)
            if existing is None:
                return False, f"Bundle {bundle_id} no longer exists."
            # Update name + description if changed
            if existing.name != name or existing.description != description:
                existing.name = name
                existing.description = description
                self.runtime.bundle_repo.update(existing)
            # Diff membership
            initial_set = set(initial_member_ids)
            target_set = set(target_member_ids)
            to_add = target_set - initial_set
            to_remove = initial_set - target_set
            for cid in to_add:
                self.runtime.bundle.add_member(
                    bundle_id, cid,
                    role=("primary" if cid == primary_id else "member"),
                )
            for cid in to_remove:
                self.runtime.bundle.remove_member(bundle_id, cid)
            # Re-set the primary: if the primary changed OR was previously
            # in the set as non-primary, we update its role.
            self._reset_primary(bundle_id, primary_id, target_member_ids)
        except Exception as e:  # noqa: BLE001
            return False, f"Failed to apply bundle edits: {e}"
        added = len(target_set - initial_set)
        removed = len(initial_set - target_set)
        return True, (
            f"Bundle updated.\n"
            f"Added: {added}    Removed: {removed}    Total members: {len(target_member_ids)}"
        )

    def _reset_primary(self, bundle_id, primary_id, target_member_ids) -> None:
        """Ensure exactly one member of the bundle has role='primary'.

        Strategy: re-add every membership with the correct role. The
        BundleRepository's add_membership uses INSERT OR REPLACE so
        this is idempotent and safe. Done as a separate pass after
        adds/removes so we can be sure the in-memory primary is in
        the persisted state.
        """
        if primary_id is None or primary_id not in target_member_ids:
            return
        from curator.models.bundle import BundleMembership
        for cid in target_member_ids:
            role = "primary" if cid == primary_id else "member"
            self.runtime.bundle_repo.add_membership(
                BundleMembership(
                    bundle_id=bundle_id,
                    curator_id=cid,
                    role=role,
                    confidence=1.0,
                )
            )

    # ------------------------------------------------------------------
    # Result + about dialogs
    # ------------------------------------------------------------------

    def _show_result_dialog(self, title: str, success: bool, message: str) -> None:
        if success:
            QMessageBox.information(self, title, message)
        else:
            QMessageBox.warning(self, title, message)

    def _show_about(self) -> None:
        try:
            from curator import __version__ as _version
        except Exception:
            _version = "unknown"
        QMessageBox.about(
            self,
            "About Curator",
            f"<h3>Curator {_version}</h3>"
            "<p>Content-aware artifact intelligence layer for files.</p>"
            "<p>v0.34 read-only GUI; v0.35 adds Trash / Restore / Dissolve "
            "mutations via right-click context menu or the Edit menu. "
            "v0.43 adds bundle creation + editing via the Bundles tab. "
            "v1.6.2 adds Tools and Workflows menus for discoverability.</p>"
            "<p>See BUILD_TRACKER.md and DESIGN.md for details.</p>",
        )

    # ------------------------------------------------------------------
    # v1.6.2: Tools menu placeholders + Workflows menu launchers
    # ------------------------------------------------------------------

    def _slot_tools_placeholder(self, key: str) -> None:
        """Show 'coming in v1.7' notice for a Tools menu item.

        Each Tools menu action will be replaced with a real PySide6
        dialog in v1.7 per docs/design/GUI_V2_DESIGN.md. Until then,
        this surfaces what the dialog will do and points the user at
        the closest CLI / Workflows alternative they can use today.
        """
        guidance = {
            "scan": (
                "<b>Scan folder</b> dialog will let you pick a source + folder + ignore"
                " globs and watch progress. Coming in v1.7.<br><br>"
                "<b>Today:</b> use Workflows → Initial scan, or run"
                " <code>curator scan &lt;source&gt; &lt;folder&gt;</code> in PowerShell."
            ),
            "group": (
                "<b>Find duplicates</b> dialog will show a duplicate-set browser with"
                " --keep strategy picker + apply. Coming in v1.7.<br><br>"
                "<b>Today:</b> use Workflows → Find duplicates."
            ),
            "cleanup": (
                "<b>Cleanup</b> dialog will let you pick categories"
                " (junk / empty-dirs / broken-symlinks) and preview before applying."
                " Coming in v1.7.<br><br>"
                "<b>Today:</b> use Workflows → Cleanup junk."
            ),
            "sources": (
                "<b>Sources Manager</b> dialog will let you add / enable / disable / remove"
                " sources and edit their config. Coming in v1.7.<br><br>"
                "<b>Today:</b> use <code>curator sources add|list|show|enable|disable|remove</code>"
                " in PowerShell."
            ),
            "health": (
                "<b>Health Check</b> dialog will show a live green/red dashboard."
                " Coming in v1.7.<br><br>"
                "<b>Today:</b> use Workflows → Health check."
            ),
        }
        msg = guidance.get(key, "This dialog is planned for v1.7.")
        QMessageBox.information(self, "Coming in v1.7", msg)

    def _slot_run_workflow(self, script_name: str) -> None:
        """Spawn a workflow .bat from scripts/workflows/ as a separate console window.

        Resolves the script path relative to the curator package source
        tree (works in both editable installs and packaged installs).
        Falls back to a friendly error dialog if the script can't be
        found, or if launching fails.

        The .bat opens its own console window so the GUI stays responsive
        and the user can interact with the workflow's prompts.
        """
        # Resolve scripts/workflows/ relative to the curator source tree.
        # In an editable install this is curator/<repo>/scripts/workflows.
        try:
            import curator as _curator_pkg
            pkg_root = Path(_curator_pkg.__file__).resolve().parent
            # pkg_root = .../src/curator; repo root is .../  (two up from src)
            repo_root = pkg_root.parent.parent
            script_path = repo_root / "scripts" / "workflows" / script_name
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(
                self, "Workflow launch failed",
                f"Could not locate workflow scripts directory: {e}",
            )
            return

        if not script_path.exists():
            QMessageBox.warning(
                self, "Workflow not found",
                f"Expected workflow script at:<br><code>{script_path}</code><br><br>"
                "This usually means the Curator install is incomplete or the"
                " repo wasn't cloned with the scripts directory. Re-run the"
                " installer:<br><code>installer\\Install-Curator.bat</code>",
            )
            return

        # Spawn the .bat as Windows would on double-click — os.startfile
        # is the proper Win32 ShellExecute path. The .bat opens its own
        # console window so the GUI stays responsive and the user can
        # interact with the workflow's prompts.
        try:
            os.startfile(str(script_path))  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(
                self, "Workflow launch failed",
                f"Could not launch <code>{script_name}</code>:<br>{e}",
            )

    def _show_workflows_about(self) -> None:
        """About-dialog for the Workflows menu."""
        QMessageBox.information(
            self,
            "About workflows",
            "<h3>Curator batch workflows</h3>"
            "<p>Each workflow combines several <code>curator</code> CLI commands"
            " into one click. They live as PowerShell .bat scripts at:</p>"
            "<p><code>Curator/scripts/workflows/</code></p>"
            "<p>Every destructive workflow is cautious-by-default: plan-mode preview,"
            " explicit user confirmation before any changes, and everything routes"
            " through the OS Recycle Bin (reversible).</p>"
            "<p>Native PySide6 dialogs replacing these scripts are planned for v1.7."
            " See <code>docs/design/GUI_V2_DESIGN.md</code>.</p>",
        )


__all__ = ["CuratorMainWindow"]
