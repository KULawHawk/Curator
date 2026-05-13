"""Coverage closure for ``curator.gui.main_window`` Part 5 (v1.7.195).

Round 4 Tier 3 sub-ship 5 of 5 (FINAL) — Pragma audit close. The 6
partial branches surfaced at the end of Part 4 are all reachable in
principle; this file closes them with real tests rather than pragmas
(apex-accuracy doctrine: prefer tests to pragmas).

Branches closed here:

* 490→488 — ``_slot_migrate_refresh`` loop completes without finding
  the same job_id (j.job_id != current_job_id)
* 547→exit — ``_show_migrate_context_menu`` user dismisses (no action
  chosen)
* 974→976 — ``_slot_audit_refresh_dropdowns`` selection-data not found
  in rebuilt dropdown
* 1613→1617 — ``refresh_all`` ``not hasattr(self, "_lineage_view")``
  defensive branch
* 1647→exit — browser context menu user dismisses
* 1657→exit — trash context menu user dismisses
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.unit.test_gui_main_window_part1_coverage import make_runtime_stub


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(qapp, qtbot):
    from curator.gui.main_window import CuratorMainWindow
    rt = make_runtime_stub()
    w = CuratorMainWindow(rt)
    qtbot.addWidget(w)
    return w


def _stub_menu_picking_none(monkeypatch):
    """Patch QMenu so exec() returns None (user dismissed)."""

    class _StubMenu:
        def __init__(self, *a, **kw):
            self.actions = []

        def addAction(self, text):
            a = MagicMock(text_=text)
            self.actions.append(a)
            return a

        def addSeparator(self): ...

        def exec(self, *args, **kw):
            return None

    monkeypatch.setattr("curator.gui.main_window.QMenu", _StubMenu)


def _make_migration_job(*, job_id=None, status="running"):
    j = MagicMock()
    j.job_id = job_id or uuid4()
    j.status = status
    j.files_total = 1
    j.files_copied = 0
    j.files_failed = 0
    j.bytes_copied = 0
    j.started_at = datetime(2026, 5, 1, 12, 0)
    j.duration_seconds = 1.0
    j.src_source_id = "local"
    j.dst_source_id = "gdrive"
    j.src_root = "/src"
    j.dst_root = "/dst"
    j.error = None
    return j


class TestRefreshLoopsWithoutFinding:
    """Branch 490→488: the for loop iterates a row where j.job_id !=
    current_job_id, then continues to the next iteration (or exits the
    loop). My existing tests in Part 3 covered the 'found match'
    (return statement) and 'no rows' (loop never entered) cases."""

    def test_refresh_loops_past_non_matching_row(self, window):
        # Seed two jobs; the user has the FIRST selected, then on refresh
        # the FIRST has vanished but the SECOND has a different ID.
        original = _make_migration_job(job_id=uuid4())
        window.runtime.migration_job_repo.list_jobs.return_value = [original]
        window._migrate_jobs_model.refresh()
        window._migrate_jobs_view.selectRow(0)
        # Now make the model return a different job entirely
        other = _make_migration_job(job_id=uuid4())
        window.runtime.migration_job_repo.list_jobs.return_value = [other]
        # Spy refresh — should be called once (fallback path)
        window._migrate_progress_model.refresh = MagicMock()
        window._slot_migrate_refresh()
        # The loop iterated over 'other', found j.job_id != original.job_id,
        # didn't match, fell through to fallback. The fallback path
        # invokes progress.refresh.
        window._migrate_progress_model.refresh.assert_called_once()


class TestContextMenuDismissedBranches:
    """Branches 1647→exit and 1657→exit: user right-clicks but dismisses
    the menu without picking an action. Existing Part 4 tests verified
    picking actions; these verify the dismissed case."""

    def test_browser_context_menu_dismissed(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        # Seed file + force valid index
        f = MagicMock()
        f.curator_id = uuid4()
        f.source_path = "/p/f.txt"
        f.source_id = "local"
        f.size = 1
        f.mtime = datetime(2026, 5, 1)
        f.extension = "txt"
        f.xxhash3_128 = "abc"
        window.runtime.file_repo.query.return_value = [f]
        window._files_model.refresh()
        valid_idx = window._files_model.index(0, 0)
        window._files_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_inspect_at_index = MagicMock()
        window._slot_trash_at_row = MagicMock()
        _stub_menu_picking_none(monkeypatch)  # User dismisses
        window._show_browser_context_menu(QPoint(0, 0))
        # Neither slot fires
        window._slot_inspect_at_index.assert_not_called()
        window._slot_trash_at_row.assert_not_called()

    def test_trash_context_menu_dismissed(self, window, monkeypatch):
        from PySide6.QtCore import QPoint
        r = MagicMock()
        r.curator_id = uuid4()
        r.original_path = "/p/f.txt"
        r.original_source_id = "local"
        r.reason = "dup"
        r.trashed_by = "user"
        r.trashed_at = datetime(2026, 5, 1)
        window.runtime.trash_repo.list.return_value = [r]
        window._trash_model.refresh()
        valid_idx = window._trash_model.index(0, 0)
        window._trash_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_restore_at_row = MagicMock()
        _stub_menu_picking_none(monkeypatch)
        window._show_trash_context_menu(QPoint(0, 0))
        window._slot_restore_at_row.assert_not_called()

    def test_migrate_context_menu_dismissed(self, window, monkeypatch):
        """Branch 547→exit: user dismisses the migrate context menu."""
        from PySide6.QtCore import QPoint
        job = _make_migration_job(status="running")
        window.runtime.migration_job_repo.list_jobs.return_value = [job]
        window._migrate_jobs_model.refresh()
        valid_idx = window._migrate_jobs_model.index(0, 0)
        window._migrate_jobs_view.indexAt = MagicMock(return_value=valid_idx)
        window._slot_migrate_abort_at_row = MagicMock()
        window._slot_migrate_resume_at_row = MagicMock()
        _stub_menu_picking_none(monkeypatch)
        window._show_migrate_context_menu(QPoint(0, 0))
        window._slot_migrate_abort_at_row.assert_not_called()
        window._slot_migrate_resume_at_row.assert_not_called()


class TestAuditDropdownsSelectionNotFound:
    """Branch 974→976: after rebuilding the dropdown, the previously-
    selected value isn't in the new options list (e.g. the audit
    entry whose actor was selected has been deleted), so
    ``cb.findData(current_data)`` returns -1 and the if branch is
    skipped. Existing test_preserves_selection_on_rebuild covered the
    found case."""

    def test_selection_lost_after_rebuild(self, window):
        # Seed initial actor "old_actor"
        e_old = MagicMock(actor="old_actor", action="x", entity_type="t")
        window.runtime.audit_repo.query.return_value = [e_old]
        window._slot_audit_refresh_dropdowns()
        # Select it
        idx = window._audit_cb_actor.findData("old_actor")
        window._audit_cb_actor.setCurrentIndex(idx)
        assert window._audit_cb_actor.currentData() == "old_actor"
        # Now make the dropdown options change so "old_actor" is gone
        e_new = MagicMock(actor="new_actor", action="x", entity_type="t")
        window.runtime.audit_repo.query.return_value = [e_new]
        window._slot_audit_refresh_dropdowns()
        # "old_actor" is no longer in the dropdown — selection silently lost
        # The branch where findData returns -1 fired (idx >= 0 was False)
        assert window._audit_cb_actor.findData("old_actor") == -1


class TestRefreshAllNoLineageViewAttr:
    """Branch 1613→1617: ``not hasattr(self, "_migrate_jobs_model")``
    defensive branch in ``refresh_all``. Plus the lineage_view
    missing-attr case (1610's `and` short-circuit)."""

    def test_no_lineage_view_attribute(self, window):
        # Delete the attribute entirely
        if hasattr(window, "_lineage_view"):
            delattr(window, "_lineage_view")
        # refresh_all should not raise — the `hasattr` check skips the
        # lineage refresh entirely
        window.refresh_all()

    def test_no_migrate_jobs_model_attribute(self, window):
        """Branch 1613→1617: when ``_migrate_jobs_model`` attribute is
        missing, skip the migrate refresh."""
        if hasattr(window, "_migrate_jobs_model"):
            delattr(window, "_migrate_jobs_model")
        # refresh_all should not raise
        window.refresh_all()
