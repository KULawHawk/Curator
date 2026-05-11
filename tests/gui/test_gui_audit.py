"""Tests for v0.37 AuditLogTableModel + 4th tab wiring.

Covers:
  * Model construction with empty + populated audit logs
  * Newest-first default ordering
  * Column count + header labels
  * _format_entity helper (UUID truncation + missing fields)
  * _format_details helper (JSON-stringify + truncation + tooltip)
  * Limit parameter respected
  * Wiring: 4th tab exists with title "Audit Log"
  * refresh_all() refreshes the audit model too

All tests skip if PySide6 unavailable. None requires pytest-qt event
loop driving.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

pyside6 = pytest.importorskip("PySide6")  # noqa: F841

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.gui.main_window import CuratorMainWindow
from curator.gui.models import AuditLogTableModel
from curator.models.audit import AuditEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def runtime_empty(tmp_path):
    """Real runtime with an empty audit_log table."""
    db_path = tmp_path / "audit_empty.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    yield rt


@pytest.fixture
def runtime_with_audit_entries(tmp_path):
    """Real runtime with 5 seeded audit entries spanning different actors / actions."""
    db_path = tmp_path / "audit_seeded.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )

    # Seed 5 entries with explicit timestamps (older to newer).
    base = datetime(2024, 6, 1, 9, 0)
    entries = [
        ("user.cli", "scan", "scan_job", str(uuid4()),
         {"root": "/Music", "files_seen": 1234}),
        ("user.gui", "trash", "file", str(uuid4()),
         {"reason": "duplicate", "original_path": "/Music/dup.mp3"}),
        ("curator.organize.apply", "organize.apply.move", "file", str(uuid4()),
         {"from": "/Music/x.mp3", "to": "/Library/Music/Artist/x.mp3"}),
        ("user.gui", "bundle.dissolve", "bundle", str(uuid4()),
         {"name": "Old playlist"}),
        ("user.cli", "config.reload", None, None,
         {"path": "/etc/curator.toml"}),
    ]
    for i, (actor, action, etype, eid, details) in enumerate(entries):
        rt.audit_repo.log(
            actor=actor, action=action,
            entity_type=etype, entity_id=eid,
            details=details,
            when=base + timedelta(minutes=i * 5),
        )

    yield rt, entries


# ===========================================================================
# Construction + empty state
# ===========================================================================


class TestConstruction:
    def test_empty_audit_log(self, qapp, runtime_empty):
        model = AuditLogTableModel(runtime_empty.audit_repo)
        assert model.rowCount() == 0
        assert model.columnCount() == 5

    def test_columns_match_constant(self, qapp, runtime_empty):
        model = AuditLogTableModel(runtime_empty.audit_repo)
        for i, label in enumerate(AuditLogTableModel.COLUMNS):
            assert model.headerData(i, Qt.Orientation.Horizontal) == label

    def test_loads_seeded_entries(self, qapp, runtime_with_audit_entries):
        rt, entries = runtime_with_audit_entries
        model = AuditLogTableModel(rt.audit_repo)
        assert model.rowCount() == len(entries)


# ===========================================================================
# Newest-first ordering
# ===========================================================================


class TestNewestFirst:
    def test_default_order_is_newest_first(self, qapp, runtime_with_audit_entries):
        rt, _entries = runtime_with_audit_entries
        model = AuditLogTableModel(rt.audit_repo)
        # Row 0 should be the most recent entry (config.reload at +20 min).
        first = model.entry_at(0)
        last = model.entry_at(model.rowCount() - 1)
        assert first.action == "config.reload"
        assert last.action == "scan"
        # And the timestamps should descend.
        assert first.occurred_at > last.occurred_at


# ===========================================================================
# Cell content
# ===========================================================================


class TestCellContent:
    def test_when_column_formatted(self, qapp, runtime_with_audit_entries):
        rt, _ = runtime_with_audit_entries
        model = AuditLogTableModel(rt.audit_repo)
        when = model.data(model.index(0, 0), Qt.DisplayRole)
        # Format is "YYYY-MM-DD HH:MM"
        assert when.startswith("2024-06-01 09:")

    def test_actor_column(self, qapp, runtime_with_audit_entries):
        rt, _ = runtime_with_audit_entries
        model = AuditLogTableModel(rt.audit_repo)
        # Newest entry was config.reload by user.cli.
        assert model.data(model.index(0, 1), Qt.DisplayRole) == "user.cli"

    def test_action_column(self, qapp, runtime_with_audit_entries):
        rt, _ = runtime_with_audit_entries
        model = AuditLogTableModel(rt.audit_repo)
        assert model.data(model.index(0, 2), Qt.DisplayRole) == "config.reload"

    def test_entity_column_with_uuid(self, qapp, runtime_with_audit_entries):
        rt, _ = runtime_with_audit_entries
        model = AuditLogTableModel(rt.audit_repo)
        # Find the trash entry row (entity_type=file, entity_id is UUID).
        for r in range(model.rowCount()):
            if model.data(model.index(r, 2), Qt.DisplayRole) == "trash":
                ent = model.data(model.index(r, 3), Qt.DisplayRole)
                # Should be "file:<8-char-prefix>..."
                assert ent.startswith("file:")
                assert ent.endswith("...")
                return
        pytest.fail("trash entry not found")

    def test_entity_column_with_no_entity(self, qapp, runtime_with_audit_entries):
        rt, _ = runtime_with_audit_entries
        model = AuditLogTableModel(rt.audit_repo)
        # The config.reload entry has no entity_type / entity_id.
        for r in range(model.rowCount()):
            if model.data(model.index(r, 2), Qt.DisplayRole) == "config.reload":
                ent = model.data(model.index(r, 3), Qt.DisplayRole)
                assert ent == ""
                return
        pytest.fail("config.reload entry not found")

    def test_details_column_truncated(self, qapp, runtime_with_audit_entries):
        rt, _ = runtime_with_audit_entries
        # Insert one entry with details large enough to require truncation.
        rt.audit_repo.log(
            actor="user.test", action="big.details",
            details={"long_field": "x" * 200},
            when=datetime(2024, 6, 2, 12, 0),
        )
        model = AuditLogTableModel(rt.audit_repo)
        row0 = model.data(model.index(0, 4), Qt.DisplayRole)
        # Should end with "..." since the JSON exceeds DETAILS_PREVIEW_LEN.
        assert row0.endswith("...")
        assert len(row0) <= AuditLogTableModel.DETAILS_PREVIEW_LEN + 3

    def test_details_column_empty_when_no_details(self, qapp, runtime_empty):
        rt = runtime_empty
        rt.audit_repo.log(actor="x", action="y")  # no details
        model = AuditLogTableModel(rt.audit_repo)
        assert model.data(model.index(0, 4), Qt.DisplayRole) == ""

    def test_details_tooltip_returns_full_json(self, qapp, runtime_with_audit_entries):
        rt, _ = runtime_with_audit_entries
        rt.audit_repo.log(
            actor="user.test", action="tip.details",
            details={"a": "x" * 200},
            when=datetime(2024, 6, 2, 12, 0),
        )
        model = AuditLogTableModel(rt.audit_repo)
        # ToolTip on Details column shows full JSON (no truncation).
        tip = model.data(model.index(0, 4), Qt.ToolTipRole)
        assert tip is not None
        assert not tip.endswith("...")
        assert "x" * 200 in tip


# ===========================================================================
# Limit parameter
# ===========================================================================


class TestLimit:
    def test_explicit_limit_caps_rows(self, qapp, runtime_with_audit_entries):
        rt, _entries = runtime_with_audit_entries
        # 5 seeded; ask for only 3.
        model = AuditLogTableModel(rt.audit_repo, limit=3)
        assert model.rowCount() == 3
        # Newest 3 returned.
        first = model.entry_at(0)
        assert first.action == "config.reload"


# ===========================================================================
# Helpers
# ===========================================================================


class TestHelpers:
    def test_format_entity_truncates_uuid(self):
        # Real UUID-shaped entity_id.
        e = AuditEntry(
            actor="x", action="y",
            entity_type="file", entity_id="12345678-1234-5678-1234-567812345678",
        )
        result = AuditLogTableModel._format_entity(e)
        assert result == "file:12345678..."

    def test_format_entity_short_id_kept(self):
        # Short non-UUID entity_id stays whole.
        e = AuditEntry(
            actor="x", action="y", entity_type="job", entity_id="42",
        )
        assert AuditLogTableModel._format_entity(e) == "job:42"

    def test_format_entity_only_id(self):
        e = AuditEntry(actor="x", action="y", entity_id="raw_id_value")
        # No entity_type, just the id.
        assert AuditLogTableModel._format_entity(e) == "raw_id_value"

    def test_format_details_empty_dict(self):
        assert AuditLogTableModel._format_details({}, truncate=True) == ""
        assert AuditLogTableModel._format_details({}, truncate=False) == ""

    def test_format_details_short(self):
        s = AuditLogTableModel._format_details({"k": "v"}, truncate=True)
        assert "k" in s and "v" in s
        assert not s.endswith("...")

    def test_format_details_truncate_long(self):
        long = {"x": "a" * 200}
        s = AuditLogTableModel._format_details(long, truncate=True)
        assert s.endswith("...")
        assert len(s) <= AuditLogTableModel.DETAILS_PREVIEW_LEN + 3

    def test_format_details_no_truncate(self):
        long = {"x": "a" * 200}
        s = AuditLogTableModel._format_details(long, truncate=False)
        assert not s.endswith("...")
        assert "a" * 200 in s


# ===========================================================================
# Wiring
# ===========================================================================


class TestWiring:
    def test_audit_tab_exists_with_title(self, qapp, runtime_with_audit_entries):
        rt, _ = runtime_with_audit_entries
        window = CuratorMainWindow(rt)
        try:
            # v1.7-alpha.6: refactored to name-based assertion. Was previously
            # asserting count >= 6 and tabText(5) == "Audit Log" — the latter
            # breaks if any tab is reordered before Audit Log.
            tab_names = [window._tabs.tabText(i) for i in range(window._tabs.count())]
            assert "Audit Log" in tab_names
        finally:
            window.deleteLater()

    def test_status_bar_includes_audit_count(self, qapp, runtime_with_audit_entries):
        rt, _ = runtime_with_audit_entries
        window = CuratorMainWindow(rt)
        try:
            counts = window._status_counts.text()
            assert "Audit:" in counts
            # 5 seeded entries.
            assert "Audit: 5" in counts
        finally:
            window.deleteLater()

    def test_refresh_all_refreshes_audit_model(
        self, qapp, runtime_with_audit_entries
    ):
        rt, _ = runtime_with_audit_entries
        window = CuratorMainWindow(rt)
        try:
            assert window._audit_model.rowCount() == 5
            # Add another entry directly.
            rt.audit_repo.log(actor="x", action="post.refresh.test")
            window.refresh_all()
            assert window._audit_model.rowCount() == 6
            # Status bar updated too.
            assert "Audit: 6" in window._status_counts.text()
        finally:
            window.deleteLater()
