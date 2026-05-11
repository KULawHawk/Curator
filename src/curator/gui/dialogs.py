"""GUI dialogs (Phase Beta gate 4 v0.36, expanded v1.7 alpha).

Houses Curator's modal dialogs:

  * :class:`FileInspectDialog` -- shown when a user double-clicks a row
    in the Browser tab. Surfaces everything Curator knows about a file
    in three tabs (metadata, lineage edges, bundle memberships).

  * :class:`BundleEditorDialog` -- bundle creation + editing.

  * :class:`HealthCheckDialog` (v1.7 alpha) -- the first native PySide6
    dialog replacing a Tools-menu placeholder. Runs a full stack
    diagnostic (filesystem layout, Python + venv versions, package
    versions, GUI dependencies, DB integrity, plugins registered, MCP
    config, real MCP probe) and renders a green/red dashboard. Synchronous
    today; can be made async if any single check goes >100ms.

The dialogs are read-only views over the runtime state; they don't
carry mutation logic. Mutations live on the main window's context menus.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from curator.models.file import FileEntity
from curator.gui.models import _format_dt, _format_size

if TYPE_CHECKING:  # pragma: no cover
    from curator.cli.runtime import CuratorRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kv_table(rows: list[tuple[str, str]]) -> QTableWidget:
    """Two-column key/value table with sensible defaults."""
    t = QTableWidget(len(rows), 2)
    t.setHorizontalHeaderLabels(["Field", "Value"])
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    for r, (k, v) in enumerate(rows):
        t.setItem(r, 0, QTableWidgetItem(k))
        t.setItem(r, 1, QTableWidgetItem(v))
    return t


def _make_table(headers: list[str], rows: list[list[str]]) -> QTableWidget:
    """N-column read-only table."""
    t = QTableWidget(len(rows), len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            t.setItem(r, c, QTableWidgetItem(val))
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    t.resizeColumnsToContents()
    return t


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return _format_dt(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# ---------------------------------------------------------------------------
# FileInspectDialog
# ---------------------------------------------------------------------------


class FileInspectDialog(QDialog):
    """Modal showing everything Curator knows about a single file."""

    def __init__(
        self,
        file: FileEntity,
        runtime: "CuratorRuntime",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.file = file
        self.runtime = runtime
        self.setWindowTitle(f"Inspect: {file.source_path}")
        self.resize(900, 520)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Header label: path + size + mtime, big and obvious.
        header = QLabel(self._header_text())
        header.setWordWrap(True)
        header.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(header)

        # Tabs.
        tabs = QTabWidget(self)
        tabs.addTab(self._build_metadata_tab(), "Metadata")
        tabs.addTab(self._build_lineage_tab(), "Lineage Edges")
        tabs.addTab(self._build_bundles_tab(), "Bundle Memberships")
        layout.addWidget(tabs)
        self._tabs = tabs

        # OK button at the bottom.
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        # The Close button on QDialogButtonBox uses the rejected signal.
        layout.addWidget(button_box)

    def _header_text(self) -> str:
        f = self.file
        return (
            f"<b>{f.source_path}</b><br>"
            f"{_format_size(f.size)} \u2022 "
            f"modified {_format_dt(f.mtime)} \u2022 "
            f"source: {f.source_id}"
            + (f" \u2022 <span style='color: #c44;'>DELETED ({_format_dt(f.deleted_at)})</span>"
               if f.deleted_at else "")
        )

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_metadata_tab(self) -> QWidget:
        f = self.file
        rows: list[tuple[str, str]] = [
            ("Curator ID", str(f.curator_id)),
            ("Source", f.source_id),
            ("Path", f.source_path),
            ("Size (bytes)", str(f.size)),
            ("Size (human)", _format_size(f.size)),
            ("Modified", _format_dt(f.mtime)),
            ("Created", _format_dt(f.ctime)),
            ("Inode", _stringify(f.inode)),
            ("xxhash3_128", f.xxhash3_128 or ""),
            ("MD5", f.md5 or ""),
            ("Fuzzy hash", f.fuzzy_hash or ""),
            ("File type", f.file_type or ""),
            ("Extension", f.extension or ""),
            ("File type confidence", f"{f.file_type_confidence:.2f}"),
            ("Seen at", _format_dt(f.seen_at)),
            ("Last scanned", _format_dt(f.last_scanned_at)),
            ("Deleted at", _format_dt(f.deleted_at)),
        ]
        # Append flex attrs prefixed with "flex: " for clarity.
        try:
            for k in sorted(f.flex.keys()):
                rows.append((f"flex: {k}", _stringify(f.flex[k])))
        except Exception:
            pass

        return _make_kv_table(rows)

    def _build_lineage_tab(self) -> QWidget:
        try:
            edges = self.runtime.lineage_repo.get_edges_for(self.file.curator_id)
        except Exception:
            edges = []

        rows: list[list[str]] = []
        for edge in edges:
            # Resolve "the other file" for readability.
            if edge.from_curator_id == self.file.curator_id:
                other_id = edge.to_curator_id
                direction = "->"
            else:
                other_id = edge.from_curator_id
                direction = "<-"
            try:
                other = self.runtime.file_repo.get(other_id)
                other_label = other.source_path if other else f"({other_id})"
            except Exception:
                other_label = f"({other_id})"

            kind_str = (
                edge.edge_kind.value if hasattr(edge.edge_kind, "value")
                else str(edge.edge_kind)
            )
            rows.append([
                kind_str,
                direction,
                other_label,
                f"{edge.confidence:.2f}",
                edge.detected_by,
                edge.notes or "",
            ])

        return _make_table(
            ["Kind", "Direction", "Other File", "Confidence", "Detected by", "Notes"],
            rows,
        )

    def _build_bundles_tab(self) -> QWidget:
        try:
            memberships = self.runtime.bundle_repo.get_memberships_for_file(
                self.file.curator_id,
            )
        except Exception:
            memberships = []

        rows: list[list[str]] = []
        for m in memberships:
            try:
                bundle = self.runtime.bundle_repo.get(m.bundle_id)
                bundle_label = bundle.name if bundle and bundle.name else "(unnamed)"
                bundle_type = bundle.bundle_type if bundle else ""
            except Exception:
                bundle_label = f"({m.bundle_id})"
                bundle_type = ""
            rows.append([
                bundle_label,
                bundle_type,
                m.role,
                f"{m.confidence:.2f}",
                _format_dt(m.added_at),
            ])

        return _make_table(
            ["Bundle", "Type", "Role", "Confidence", "Added At"],
            rows,
        )


# ---------------------------------------------------------------------------
# BundleEditorDialog (Phase Beta gate 4 polish, v0.43)
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field
from uuid import UUID

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
)

from curator.models.bundle import BundleEntity
from curator.storage.queries import FileQuery


@dataclass
class BundleEditorResult:
    """Outcome of the bundle editor dialog when accepted.

    The caller dispatches on whether ``existing_bundle_id`` is set:
    None means create a new bundle; not-None means apply edits to
    the existing bundle (rename + diff membership).
    """
    name: str
    description: str | None
    member_ids: list[UUID]
    primary_id: UUID | None
    existing_bundle_id: UUID | None = None  # None = create, set = edit
    initial_member_ids: list[UUID] = field(default_factory=list)

    @property
    def added_member_ids(self) -> list[UUID]:
        """Members in the new state but not in the initial state."""
        initial = set(self.initial_member_ids)
        return [m for m in self.member_ids if m not in initial]

    @property
    def removed_member_ids(self) -> list[UUID]:
        """Members in the initial state but not in the new state."""
        target = set(self.member_ids)
        return [m for m in self.initial_member_ids if m not in target]


class BundleEditorDialog(QDialog):
    """Modal dialog for creating new bundles OR editing existing ones.

    Layout: Name + Description fields at the top; dual-list (Available
    files | In bundle) with searchable filters and add/remove buttons
    in the middle; OK / Cancel at the bottom. The primary member is
    indicated with a star prefix in the right list; the "Set as
    Primary" button promotes the currently-selected right-list item.

    The dialog is purely a UI surface -- it does NOT call BundleService.
    On accept, the caller pulls :meth:`get_result` and dispatches to
    the appropriate ``_perform_bundle_*`` method on the main window.
    This keeps the dialog itself testable without DB writes, and keeps
    mutation logic in the testable ``_perform_*`` seams.

    Args:
        runtime: live :class:`CuratorRuntime` for file lookups.
        existing_bundle: if set, the dialog opens in Edit mode
            pre-populated with the bundle's current state.
            If None, opens in Create mode (blank).
        parent: optional QWidget parent.
    """

    PRIMARY_PREFIX = "\u2605 "  # ★ — unicode black star

    def __init__(
        self,
        runtime: "CuratorRuntime",
        *,
        existing_bundle: BundleEntity | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._existing_bundle = existing_bundle
        self._initial_member_ids: list[UUID] = []
        self._primary_id: UUID | None = None
        self._result: BundleEditorResult | None = None
        self._build_ui()
        self._load_files()
        if existing_bundle is not None:
            self._load_existing_state(existing_bundle)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        is_edit = self._existing_bundle is not None
        title = (
            f"Edit bundle: {self._existing_bundle.name or '(unnamed)'}"
            if is_edit else "Create new bundle"
        )
        self.setWindowTitle(title)
        self.resize(900, 600)

        outer = QVBoxLayout(self)

        # --- Header: Name + Description ---------------------------------
        header_label = QLabel(f"<h3>{title}</h3>")
        outer.addWidget(header_label)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., Pink Floyd — The Wall")
        name_row.addWidget(self._name_edit, stretch=1)
        outer.addLayout(name_row)

        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel("Description:"))
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Optional longer text")
        desc_row.addWidget(self._desc_edit, stretch=1)
        outer.addLayout(desc_row)

        # --- Dual list (Available | In bundle) --------------------------
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left side: Available files
        left_wrap = QWidget()
        left_layout = QVBoxLayout(left_wrap)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_label = QLabel("Available files")
        left_layout.addWidget(self._left_label)
        self._left_search = QLineEdit()
        self._left_search.setPlaceholderText("Filter by path or name...")
        self._left_search.textChanged.connect(self._refilter_available)
        left_layout.addWidget(self._left_search)
        self._available_list = QListWidget()
        self._available_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self._available_list.itemDoubleClicked.connect(
            lambda _: self._move_selected_to_bundle()
        )
        left_layout.addWidget(self._available_list, stretch=1)
        splitter.addWidget(left_wrap)

        # Middle: add/remove buttons stacked vertically
        button_wrap = QWidget()
        button_col = QVBoxLayout(button_wrap)
        button_col.setContentsMargins(4, 4, 4, 4)
        button_col.addStretch(1)
        self._add_btn = QPushButton("Add →")
        self._add_btn.setToolTip("Add selected files from Available to the bundle")
        self._add_btn.clicked.connect(self._move_selected_to_bundle)
        button_col.addWidget(self._add_btn)
        self._remove_btn = QPushButton("← Remove")
        self._remove_btn.setToolTip("Remove selected files from the bundle")
        self._remove_btn.clicked.connect(self._move_selected_from_bundle)
        button_col.addWidget(self._remove_btn)
        button_col.addSpacing(20)
        self._set_primary_btn = QPushButton("Set as ★ Primary")
        self._set_primary_btn.setToolTip(
            "Mark the selected bundle member as the primary one. Only one "
            "member can be primary; setting a new primary clears the prior."
        )
        self._set_primary_btn.clicked.connect(self._set_selected_as_primary)
        button_col.addWidget(self._set_primary_btn)
        button_col.addStretch(1)
        splitter.addWidget(button_wrap)

        # Right side: In bundle
        right_wrap = QWidget()
        right_layout = QVBoxLayout(right_wrap)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_label = QLabel("In bundle")
        right_layout.addWidget(self._right_label)
        self._right_search = QLineEdit()
        self._right_search.setPlaceholderText("Filter the bundle...")
        self._right_search.textChanged.connect(self._refilter_bundle)
        right_layout.addWidget(self._right_search)
        self._bundle_list = QListWidget()
        self._bundle_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self._bundle_list.itemDoubleClicked.connect(
            lambda _: self._move_selected_from_bundle()
        )
        right_layout.addWidget(self._bundle_list, stretch=1)
        splitter.addWidget(right_wrap)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 4)
        outer.addWidget(splitter, stretch=1)

        # --- Footer: OK / Cancel ----------------------------------------
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_files(self) -> None:
        """Populate the Available list with every active file.

        Files in the bundle (Edit mode) are filtered out by
        :meth:`_load_existing_state`. Sort by basename for
        scannability; show the full path as tooltip.
        """
        try:
            files = self._runtime.file_repo.query(
                FileQuery(deleted=False, limit=10000)
            )
        except Exception:
            files = []
        # Sort by basename, case-insensitive
        files = sorted(
            files,
            key=lambda f: (self._basename_for_sort(f.source_path), f.source_path),
        )
        for f in files:
            self._add_to_available(f.curator_id, f.source_path)
        self._refresh_count_labels()

    def _load_existing_state(self, bundle: BundleEntity) -> None:
        """Pre-populate the dialog from an existing bundle."""
        self._name_edit.setText(bundle.name or "")
        self._desc_edit.setText(bundle.description or "")
        try:
            memberships = self._runtime.bundle_repo.get_memberships(bundle.bundle_id)
        except Exception:
            memberships = []
        primary_id: UUID | None = None
        for m in memberships:
            self._initial_member_ids.append(m.curator_id)
            if m.role == "primary":
                primary_id = m.curator_id
            # Move from Available -> Bundle
            self._move_id_from_available_to_bundle(m.curator_id)
        self._primary_id = primary_id
        if primary_id is not None:
            self._mark_primary_in_bundle_list(primary_id)
        self._refresh_count_labels()

    @staticmethod
    def _basename_for_sort(path: str) -> str:
        for sep in ("/", "\\"):
            if sep in path:
                return path.rsplit(sep, 1)[-1].lower()
        return path.lower()

    def _add_to_available(self, curator_id: UUID, path: str) -> None:
        """Append a file to the Available list with id stored as item data."""
        item = QListWidgetItem(self._format_file_label(path))
        item.setData(Qt.ItemDataRole.UserRole, curator_id)
        item.setData(Qt.ItemDataRole.UserRole + 1, path)
        item.setToolTip(path)
        self._available_list.addItem(item)

    def _add_to_bundle(self, curator_id: UUID, path: str) -> None:
        """Append a file to the In Bundle list."""
        prefix = self.PRIMARY_PREFIX if curator_id == self._primary_id else "  "
        item = QListWidgetItem(prefix + self._format_file_label(path))
        item.setData(Qt.ItemDataRole.UserRole, curator_id)
        item.setData(Qt.ItemDataRole.UserRole + 1, path)
        item.setToolTip(path)
        self._bundle_list.addItem(item)

    @staticmethod
    def _format_file_label(path: str) -> str:
        """Format `basename  (parent)` for compact display."""
        norm = path.replace("\\", "/")
        if "/" in norm:
            parent, name = norm.rsplit("/", 1)
            # Trim parent to last 2 segments for compactness
            parts = parent.split("/")
            if len(parts) > 2:
                parent_short = ".../" + "/".join(parts[-2:])
            else:
                parent_short = parent
            return f"{name}    ({parent_short})"
        return path

    # ------------------------------------------------------------------
    # List manipulation
    # ------------------------------------------------------------------

    def _move_selected_to_bundle(self) -> None:
        """Move every selected item from Available -> In Bundle."""
        items = self._available_list.selectedItems()
        if not items:
            return
        for item in items:
            cid = item.data(Qt.ItemDataRole.UserRole)
            path = item.data(Qt.ItemDataRole.UserRole + 1)
            row = self._available_list.row(item)
            self._available_list.takeItem(row)
            self._add_to_bundle(cid, path)
        self._refresh_count_labels()

    def _move_selected_from_bundle(self) -> None:
        """Move every selected item from In Bundle -> Available."""
        items = self._bundle_list.selectedItems()
        if not items:
            return
        for item in items:
            cid = item.data(Qt.ItemDataRole.UserRole)
            path = item.data(Qt.ItemDataRole.UserRole + 1)
            row = self._bundle_list.row(item)
            self._bundle_list.takeItem(row)
            self._add_to_available(cid, path)
            # If primary was removed, clear primary state
            if cid == self._primary_id:
                self._primary_id = None
        self._refresh_count_labels()

    def _move_id_from_available_to_bundle(self, curator_id: UUID) -> None:
        """Find the given curator_id in Available and move it to Bundle."""
        for row in range(self._available_list.count()):
            item = self._available_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == curator_id:
                path = item.data(Qt.ItemDataRole.UserRole + 1)
                self._available_list.takeItem(row)
                self._add_to_bundle(curator_id, path)
                return

    def _set_selected_as_primary(self) -> None:
        """Mark the currently-selected bundle member as primary."""
        items = self._bundle_list.selectedItems()
        if len(items) != 1:
            QMessageBox.information(
                self, "Set as Primary",
                "Select exactly one bundle member to mark as primary.",
            )
            return
        new_primary_id = items[0].data(Qt.ItemDataRole.UserRole)
        self._primary_id = new_primary_id
        # Re-render every bundle row's prefix
        for row in range(self._bundle_list.count()):
            item = self._bundle_list.item(row)
            cid = item.data(Qt.ItemDataRole.UserRole)
            path = item.data(Qt.ItemDataRole.UserRole + 1)
            prefix = self.PRIMARY_PREFIX if cid == new_primary_id else "  "
            item.setText(prefix + self._format_file_label(path))

    def _mark_primary_in_bundle_list(self, primary_id: UUID) -> None:
        """Apply the star prefix to the primary item (used during initial load)."""
        for row in range(self._bundle_list.count()):
            item = self._bundle_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == primary_id:
                path = item.data(Qt.ItemDataRole.UserRole + 1)
                item.setText(self.PRIMARY_PREFIX + self._format_file_label(path))
                return

    def _refilter_available(self, query: str) -> None:
        """Hide rows in Available that don't contain ``query`` (case-insensitive)."""
        q = query.strip().lower()
        for row in range(self._available_list.count()):
            item = self._available_list.item(row)
            path = item.data(Qt.ItemDataRole.UserRole + 1) or ""
            item.setHidden(bool(q) and q not in path.lower())

    def _refilter_bundle(self, query: str) -> None:
        """Hide rows in In Bundle that don't contain ``query``."""
        q = query.strip().lower()
        for row in range(self._bundle_list.count()):
            item = self._bundle_list.item(row)
            path = item.data(Qt.ItemDataRole.UserRole + 1) or ""
            item.setHidden(bool(q) and q not in path.lower())

    def _refresh_count_labels(self) -> None:
        self._left_label.setText(f"Available files ({self._available_list.count()})")
        self._right_label.setText(f"In bundle ({self._bundle_list.count()})")

    # ------------------------------------------------------------------
    # Accept / result
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        """Validate inputs; if OK, build the result and accept the dialog."""
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(
                self, "Bundle name required",
                "Please enter a name for the bundle.",
            )
            return
        if self._bundle_list.count() == 0:
            QMessageBox.warning(
                self, "No members",
                "A bundle must have at least one member. Add files from "
                "the Available list using the Add → button or by "
                "double-clicking.",
            )
            return
        # Collect ordered member ids from the In Bundle list.
        member_ids: list[UUID] = []
        for row in range(self._bundle_list.count()):
            item = self._bundle_list.item(row)
            member_ids.append(item.data(Qt.ItemDataRole.UserRole))
        # Default primary: first member if none set.
        primary = self._primary_id if self._primary_id in member_ids else member_ids[0]
        desc = self._desc_edit.text().strip() or None
        existing_id = (
            self._existing_bundle.bundle_id if self._existing_bundle else None
        )
        self._result = BundleEditorResult(
            name=name,
            description=desc,
            member_ids=member_ids,
            primary_id=primary,
            existing_bundle_id=existing_id,
            initial_member_ids=list(self._initial_member_ids),
        )
        self.accept()

    def get_result(self) -> BundleEditorResult | None:
        """Return the result of the dialog; None if cancelled."""
        return self._result


__all__ = [
    "FileInspectDialog",
    "BundleEditorDialog",
    "BundleEditorResult",
    "HealthCheckDialog",
    "HealthCheckResult",
]


# ---------------------------------------------------------------------------
# HealthCheckDialog (v1.7 alpha) -- first native dialog replacing a Tools
# menu placeholder. Runs the same suite of checks as scripts/workflows/
# 05_health_check.ps1 but in-process, with no console window pop-up.
# ---------------------------------------------------------------------------

# Color tokens for status indicators. Avoid hard pure-greens/reds so the
# dialog reads cleanly in both light + dark Qt themes.
_COLOR_PASS = QColor(0x2E, 0x7D, 0x32)   # mid green
_COLOR_FAIL = QColor(0xC6, 0x28, 0x28)   # mid red
_COLOR_WARN = QColor(0xEF, 0x6C, 0x00)   # mid orange (for non-fatal issues)
_COLOR_INFO = QColor(0x5C, 0x6B, 0xC0)   # muted indigo (for informational rows)


@dataclass
class _CheckResult:
    """Outcome of one health check row."""
    label: str
    passed: bool
    detail: str = ""
    severity: str = "fail"  # 'fail' | 'warn' | 'info' -- only used when passed=False


@dataclass
class HealthCheckResult:
    """Aggregate result of the full health-check run.

    Surfaced via :meth:`HealthCheckDialog.last_result` so tests and
    scripts can introspect what the dialog rendered without having to
    parse Qt widgets.
    """
    sections: dict[str, list[_CheckResult]] = field(default_factory=dict)
    started_at: datetime = field(default_factory=datetime.now)
    elapsed_ms: int = 0

    @property
    def total(self) -> int:
        return sum(len(rows) for rows in self.sections.values())

    @property
    def passed(self) -> int:
        return sum(1 for rows in self.sections.values() for r in rows if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for rows in self.sections.values() for r in rows if not r.passed and r.severity == "fail")

    @property
    def all_green(self) -> bool:
        return self.failed == 0


class HealthCheckDialog(QDialog):
    """Curator stack health diagnostic, in-process.

    Sections (matching scripts/workflows/05_health_check.ps1):
      1. Filesystem layout (canonical paths exist)
      2. Python + venv detection
      3. Curator + plugin versions
      4. GUI dependencies (PySide6 + networkx)
      5. DB integrity check (PRAGMA integrity_check)
      6. Plugins registered (from runtime.pm)
      7. Claude Desktop MCP config (file present + curator entry valid)
      8. Real MCP probe (spawn curator-mcp, initialize, tools/list)

    The dialog runs all checks synchronously at construction (and on
    Refresh). Each section takes well under 100 ms except the MCP probe
    which spawns a subprocess (~500 ms). Tolerable for a button click;
    can be moved off the main thread if it ever degrades.

    The dialog never raises -- every check is wrapped in a try/except
    that converts unexpected errors into a `_CheckResult(passed=False)`
    with the exception text in the detail.
    """

    def __init__(self, runtime: "CuratorRuntime", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._last_result: HealthCheckResult | None = None
        self.setWindowTitle("Curator Health Check")
        self.setMinimumSize(640, 560)
        self._build_ui()
        self.refresh()

    # ---- public API ----

    @property
    def last_result(self) -> HealthCheckResult | None:
        """The result of the most recent check run, or None if none."""
        return self._last_result

    def refresh(self) -> None:
        """Re-run all health checks and re-render the UI."""
        started = datetime.now()
        result = HealthCheckResult(started_at=started)

        result.sections["Filesystem layout"] = self._check_filesystem()
        result.sections["Python + venv"] = self._check_python()
        result.sections["Curator + plugin versions"] = self._check_versions()
        result.sections["GUI dependencies"] = self._check_gui_deps()
        result.sections["DB integrity"] = self._check_db_integrity()
        result.sections["Plugins registered"] = self._check_plugins()
        result.sections["Claude Desktop MCP config"] = self._check_mcp_config()
        result.sections["Real MCP probe"] = self._check_mcp_probe()

        result.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        self._last_result = result
        self._render(result)

    # ---- check implementations ----
    # Each returns list[_CheckResult]; never raises.

    def _check_filesystem(self) -> list[_CheckResult]:
        out: list[_CheckResult] = []
        try:
            db_path = Path(self.runtime.config.db_path)
            repo_root = db_path.parent.parent  # .curator/curator.db -> AL/
            out.append(_CheckResult(
                "Canonical DB exists",
                db_path.exists(),
                str(db_path),
            ))
            out.append(_CheckResult(
                "Repo root accessible",
                repo_root.exists() and os.access(repo_root, os.R_OK),
                str(repo_root),
            ))
            curator_repo = repo_root / "Curator"
            out.append(_CheckResult(
                "Curator source tree present",
                curator_repo.exists(),
                str(curator_repo),
            ))
        except Exception as e:  # noqa: BLE001
            out.append(_CheckResult("Filesystem check raised", False, str(e)))
        return out

    def _check_python(self) -> list[_CheckResult]:
        out: list[_CheckResult] = []
        out.append(_CheckResult(
            "Python version",
            sys.version_info >= (3, 11),
            f"{sys.version.split()[0]} ({sys.executable})",
            severity="fail" if sys.version_info < (3, 11) else "info",
        ))
        # Venv check: is sys.executable inside a venv?
        in_venv = hasattr(sys, "real_prefix") or (
            hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
        )
        out.append(_CheckResult(
            "Running in venv",
            in_venv,
            sys.prefix,
        ))
        return out

    def _check_versions(self) -> list[_CheckResult]:
        out: list[_CheckResult] = []
        try:
            import curator
            out.append(_CheckResult("curator package", True, curator.__version__, severity="info"))
        except Exception as e:  # noqa: BLE001
            out.append(_CheckResult("curator package", False, str(e)))
        for mod_name, label in [
            ("curatorplug.atrium_citation", "atrium-citation"),
            ("curatorplug.atrium_safety", "atrium-safety"),
        ]:
            try:
                mod = __import__(mod_name, fromlist=["__version__"])
                out.append(_CheckResult(label, True, getattr(mod, "__version__", "?"), severity="info"))
            except Exception as e:  # noqa: BLE001
                out.append(_CheckResult(label, False, str(e)))
        return out

    def _check_gui_deps(self) -> list[_CheckResult]:
        out: list[_CheckResult] = []
        try:
            import PySide6
            out.append(_CheckResult("PySide6", True, PySide6.__version__, severity="info"))
        except Exception as e:  # noqa: BLE001
            out.append(_CheckResult("PySide6", False, str(e)))
        try:
            import networkx
            out.append(_CheckResult("networkx (lineage graph)", True, networkx.__version__, severity="info"))
        except ImportError:
            out.append(_CheckResult(
                "networkx (lineage graph)",
                False,
                "not installed; lineage graph tab will show 'unavailable'",
                severity="warn",
            ))
        return out

    def _check_db_integrity(self) -> list[_CheckResult]:
        out: list[_CheckResult] = []
        try:
            db_path = Path(self.runtime.config.db_path)
            c = sqlite3.connect(str(db_path))
            try:
                row = c.execute("PRAGMA integrity_check").fetchone()
                ok = row is not None and row[0] == "ok"
                out.append(_CheckResult(
                    "PRAGMA integrity_check",
                    ok,
                    row[0] if row else "no result",
                ))
                # File count for context (info row)
                fc = c.execute("SELECT COUNT(*) FROM files WHERE deleted_at IS NULL").fetchone()[0]
                out.append(_CheckResult(
                    "Indexed files (active)",
                    True,
                    f"{fc:,}",
                    severity="info",
                ))
                sc = c.execute("SELECT COUNT(*) FROM sources WHERE enabled=1").fetchone()[0]
                out.append(_CheckResult(
                    "Sources enabled",
                    True,
                    str(sc),
                    severity="info",
                ))
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001
            out.append(_CheckResult("DB integrity check raised", False, str(e)))
        return out

    def _check_plugins(self) -> list[_CheckResult]:
        out: list[_CheckResult] = []
        try:
            plugin_names = [name for name, _p in self.runtime.pm.list_name_plugin()]
            # We expect at least 9 plugins (audit + classify + 3 lineage + 2 sources + 2 atrium)
            out.append(_CheckResult(
                f"Total plugins registered",
                len(plugin_names) >= 9,
                f"{len(plugin_names)} plugins (expected ≥ 9)",
            ))
            # Spot-check the critical ones
            for required in [
                "curator.core.local_source",
                "curator.core.gdrive_source",
                "curator.core.audit_writer",
            ]:
                present = required in plugin_names
                out.append(_CheckResult(required, present, severity="info" if present else "fail"))
        except Exception as e:  # noqa: BLE001
            out.append(_CheckResult("Plugin enumeration raised", False, str(e)))
        return out

    def _check_mcp_config(self) -> list[_CheckResult]:
        out: list[_CheckResult] = []
        cfg_path = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
        if not cfg_path.exists():
            out.append(_CheckResult(
                "claude_desktop_config.json",
                False,
                f"not found at {cfg_path}",
                severity="warn",
            ))
            return out
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", {})
            curator_entry = servers.get("curator")
            out.append(_CheckResult("Config file exists", True, str(cfg_path), severity="info"))
            out.append(_CheckResult("Has curator MCP entry", curator_entry is not None))
            if curator_entry:
                cmd = curator_entry.get("command", "")
                env = curator_entry.get("env", {})
                out.append(_CheckResult(
                    "Command points at curator-mcp.exe",
                    "curator-mcp" in cmd.lower(),
                    cmd,
                ))
                cfg_env_target = env.get("CURATOR_CONFIG", "")
                expected_toml = Path(self.runtime.config.db_path).parent / "curator.toml"
                # Resolve both for fair comparison
                env_ok = bool(cfg_env_target) and Path(cfg_env_target).resolve() == expected_toml.resolve()
                out.append(_CheckResult(
                    "CURATOR_CONFIG env var set",
                    env_ok,
                    cfg_env_target or "<not set>",
                    severity="warn" if not env_ok else "info",
                ))
        except json.JSONDecodeError as e:
            out.append(_CheckResult("Config JSON valid", False, f"parse error: {e}"))
        except Exception as e:  # noqa: BLE001
            out.append(_CheckResult("Config check raised", False, str(e)))
        return out

    def _check_mcp_probe(self) -> list[_CheckResult]:
        """Spawn curator-mcp and complete MCP handshake; assert >=9 tools.

        This is the same probe used by Install-Curator.ps1 Step 9.
        Done synchronously; takes ~500ms.
        """
        out: list[_CheckResult] = []
        # Locate curator-mcp.exe in the venv
        venv_scripts = Path(sys.prefix) / "Scripts"
        mcp_exe = venv_scripts / "curator-mcp.exe"
        if not mcp_exe.exists():
            mcp_exe = venv_scripts / "curator-mcp"  # POSIX fallback
        if not mcp_exe.exists():
            out.append(_CheckResult(
                "curator-mcp executable found",
                False,
                f"missing at {mcp_exe}",
            ))
            return out

        # Build env with CURATOR_CONFIG pinned
        env = os.environ.copy()
        canonical_toml = Path(self.runtime.config.db_path).parent / "curator.toml"
        if canonical_toml.exists():
            env["CURATOR_CONFIG"] = str(canonical_toml)

        # Spawn + handshake
        try:
            proc = subprocess.Popen(
                [str(mcp_exe)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, text=True, encoding="utf-8",
            )
            try:
                # MCP initialize
                init_msg = {
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "HealthCheckDialog", "version": "1.0"},
                    },
                }
                proc.stdin.write(json.dumps(init_msg) + "\n")
                proc.stdin.flush()
                proc.stdout.readline()  # init response
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                proc.stdin.flush()
                # tools/list
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
                proc.stdin.flush()
                tools_resp = proc.stdout.readline()
                if not tools_resp:
                    out.append(_CheckResult("MCP handshake", False, "no tools/list response"))
                    return out
                tools = json.loads(tools_resp).get("result", {}).get("tools", [])
                out.append(_CheckResult(
                    f"MCP tools advertised",
                    len(tools) >= 9,
                    f"{len(tools)} tools (expected ≥ 9)",
                ))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception as e:  # noqa: BLE001
            out.append(_CheckResult("MCP probe raised", False, str(e)))
        return out

    # ---- UI construction ----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Header label + summary row
        self._header = QLabel("Running checks…")
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        self._header.setFont(font)
        layout.addWidget(self._header)

        self._summary = QLabel("")
        self._summary.setStyleSheet("color: #666; padding-bottom: 6px;")
        layout.addWidget(self._summary)

        # Scrollable area for the check sections
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_inner = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_inner)
        self._scroll_layout.setSpacing(8)
        self._scroll.setWidget(self._scroll_inner)
        layout.addWidget(self._scroll, stretch=1)

        # Button row: Refresh + Copy to clipboard + Close
        btn_row = QHBoxLayout()
        self._refresh_btn = QPushButton("&Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self._refresh_btn)

        self._copy_btn = QPushButton("&Copy to clipboard")
        self._copy_btn.clicked.connect(self._copy_result)
        btn_row.addWidget(self._copy_btn)

        btn_row.addStretch(1)

        close_btn = QPushButton("&Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _render(self, result: HealthCheckResult) -> None:
        # Clear existing section widgets
        while self._scroll_layout.count():
            item = self._scroll_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # Header + summary
        if result.all_green:
            self._header.setText(f"✓ All checks passed ({result.passed} of {result.total})")
            self._header.setStyleSheet(f"color: {_COLOR_PASS.name()};")
        else:
            self._header.setText(
                f"✗ {result.failed} of {result.total} checks failed"
            )
            self._header.setStyleSheet(f"color: {_COLOR_FAIL.name()};")
        self._summary.setText(
            f"Curator stack diagnostic • ran in {result.elapsed_ms} ms • {result.started_at:%Y-%m-%d %H:%M:%S}"
        )

        # One QGroupBox per section
        for section_name, rows in result.sections.items():
            box = QGroupBox(section_name)
            box_layout = QVBoxLayout(box)
            box_layout.setSpacing(4)
            box_layout.setContentsMargins(10, 10, 10, 10)
            for r in rows:
                box_layout.addLayout(self._render_check_row(r))
            self._scroll_layout.addWidget(box)

        self._scroll_layout.addStretch(1)

    def _render_check_row(self, r: _CheckResult) -> QHBoxLayout:
        """One check row: [icon] [label] [detail]."""
        row = QHBoxLayout()
        row.setSpacing(8)

        # Status icon (colored bullet)
        icon = QLabel()
        if r.passed:
            color = _COLOR_PASS if r.severity == "fail" else _COLOR_INFO
            text = "●"  # filled circle
        else:
            color = {"warn": _COLOR_WARN, "info": _COLOR_INFO}.get(r.severity, _COLOR_FAIL)
            text = "●"
        icon.setText(text)
        icon.setStyleSheet(f"color: {color.name()}; font-size: 14pt; padding-right: 6px;")
        icon.setFixedWidth(20)
        row.addWidget(icon)

        # Label
        label = QLabel(r.label)
        label.setMinimumWidth(220)
        row.addWidget(label)

        # Detail (italic, smaller, gray)
        if r.detail:
            detail = QLabel(r.detail)
            detail.setStyleSheet("color: #555; font-style: italic;")
            detail.setWordWrap(True)
            detail.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row.addWidget(detail, stretch=1)
        else:
            row.addStretch(1)
        return row

    def _copy_result(self) -> None:
        """Copy a plain-text rendering of the check results to clipboard."""
        if self._last_result is None:
            return
        lines = [
            f"Curator Health Check  ({self._last_result.started_at:%Y-%m-%d %H:%M:%S})",
            f"{self._last_result.passed} of {self._last_result.total} checks passed"
            f" -- ran in {self._last_result.elapsed_ms} ms",
            "",
        ]
        for section, rows in self._last_result.sections.items():
            lines.append(f"## {section}")
            for r in rows:
                marker = "[ OK ]" if r.passed else f"[{r.severity.upper():4}]"
                lines.append(f"  {marker} {r.label}" + (f": {r.detail}" if r.detail else ""))
            lines.append("")
        QApplication.clipboard().setText("\n".join(lines))


# ---------------------------------------------------------------------------
# ScanDialog (v1.7 alpha) -- second native dialog after HealthCheckDialog
# ---------------------------------------------------------------------------
#
# Lets the user pick a source + root path + (optional) ignore globs,
# kicks off a scan in a QThread, and shows progress + final ScanReport.
#
# v1.7 alpha limitations (tracked for v1.7.x):
#   * Progress is INDETERMINATE -- ScanService.scan() has no progress
#     callback in v1.6.5. The dialog shows a spinner + "Scanning..."
#     label while the worker thread runs.
#   * No cancellation -- ScanService doesn't support cancel mid-scan.
#     Closing the dialog while scanning orphans the worker (it'll
#     finish; its terminal emit lands on a dead bridge slot, which
#     Qt handles gracefully).
#   * No ignore-glob input yet -- ScanService takes a generic options
#     dict but we don't have a stable schema for ignore patterns yet.


class ScanDialog(QDialog):
    """Run a scan from the GUI without spawning a console window.

    Replaces the v1.6.2 "Scan folder..." placeholder under the Tools
    menu. Closes the most-cited gaps from the v1.6.4 smoke test:

      1. Live progress feedback (indeterminate today, real progress
         once ScanService gains a callback).
      2. Native directory picker (was: copy-paste path into PowerShell).
      3. In-app modal (was: separate console window via .bat).

    Reads its source list from ``runtime.source_repo.list_sources()``
    and the scan service from ``runtime.scan``.
    """

    def __init__(self, runtime: "CuratorRuntime", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._worker: Any = None        # ScanWorker, set when scanning
        self._bridge: Any = None        # ScanProgressBridge, set when scanning
        self._last_report: Any = None   # ScanReport on completion

        self.setWindowTitle("Curator - Scan folder")
        self.setMinimumWidth(620)
        self.resize(720, 540)
        self._build_ui()
        self._populate_sources()
        self._update_scan_enabled()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Lazy import to keep file headers tidy and follow the
        # HealthCheckDialog precedent.
        from PySide6.QtWidgets import (
            QComboBox,
            QFileDialog,
            QLineEdit,
            QProgressBar,
        )

        layout = QVBoxLayout(self)

        # --- Inputs group ----------------------------------------------
        grp_inputs = QGroupBox("Scan inputs")
        gi = QVBoxLayout(grp_inputs)

        # Source picker
        row_src = QHBoxLayout()
        row_src.addWidget(QLabel("Source:"))
        self._cb_source = QComboBox()
        self._cb_source.setMinimumWidth(220)
        row_src.addWidget(self._cb_source)
        row_src.addStretch(1)
        gi.addLayout(row_src)

        # Path picker
        row_path = QHBoxLayout()
        row_path.addWidget(QLabel("Folder:"))
        self._le_path = QLineEdit()
        self._le_path.setPlaceholderText("(pick a folder to scan)")
        self._le_path.textChanged.connect(self._update_scan_enabled)
        row_path.addWidget(self._le_path, 1)
        self._btn_browse = QPushButton("Browse...")
        self._btn_browse.clicked.connect(self._on_browse_clicked)
        row_path.addWidget(self._btn_browse)
        gi.addLayout(row_path)

        # Hint label
        hint = QLabel(
            "<i>Tip: scan a small folder (say ~100 files) before running on a"
            " whole drive. Progress is indeterminate in v1.7 alpha -- the dialog"
            " will appear to hang on large scans until the report comes back.</i>"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5C6BC0; padding: 4px;")
        gi.addWidget(hint)

        layout.addWidget(grp_inputs)

        # --- Progress group --------------------------------------------
        grp_progress = QGroupBox("Progress")
        gp = QVBoxLayout(grp_progress)

        self._lbl_status = QLabel("Idle. Pick a source + folder, then click Scan.")
        self._lbl_status.setWordWrap(True)
        gp.addWidget(self._lbl_status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)       # determinate-by-default with 1 max
        self._progress.setValue(0)
        self._progress.setFormat("")
        gp.addWidget(self._progress)

        layout.addWidget(grp_progress)

        # --- Results group (built once; rows added on completion) ------
        self._grp_results = QGroupBox("Results")
        self._gr_layout = QVBoxLayout(self._grp_results)
        self._lbl_no_results = QLabel("<i>No scan run yet.</i>")
        self._lbl_no_results.setStyleSheet("color: gray; padding: 8px;")
        self._gr_layout.addWidget(self._lbl_no_results)
        layout.addWidget(self._grp_results, 1)

        # --- Buttons row ------------------------------------------------
        row_btn = QHBoxLayout()
        self._btn_scan = QPushButton("Scan")
        self._btn_scan.setDefault(True)
        self._btn_scan.clicked.connect(self._on_scan_clicked)
        row_btn.addWidget(self._btn_scan)

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.reject)
        row_btn.addWidget(self._btn_close)
        row_btn.addStretch(1)

        layout.addLayout(row_btn)

    # ------------------------------------------------------------------
    # Source population
    # ------------------------------------------------------------------

    def _populate_sources(self) -> None:
        """Load registered sources from runtime.source_repo into the dropdown.

        Uses :meth:`SourceRepository.list_all` (the actual method name on
        the repo as of v1.6.5; earlier drafts of this dialog mistakenly
        referenced ``list_sources`` which doesn't exist).
        """
        try:
            sources = self.runtime.source_repo.list_all()
        except Exception as e:  # noqa: BLE001
            self._cb_source.addItem(f"(error loading sources: {e})")
            self._cb_source.setEnabled(False)
            return
        if not sources:
            self._cb_source.addItem("(no sources configured)")
            self._cb_source.setEnabled(False)
            return
        for s in sources:
            label = f"{s.source_id} ({s.source_type})"
            self._cb_source.addItem(label, s.source_id)

    def _selected_source_id(self) -> str | None:
        if not self._cb_source.isEnabled():
            return None
        sid = self._cb_source.currentData()
        return sid if isinstance(sid, str) and sid else None

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    def _on_browse_clicked(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start_dir = self._le_path.text().strip()
        if not start_dir or not Path(start_dir).is_dir():
            start_dir = str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select folder to scan",
            start_dir,
        )
        if chosen:
            self._le_path.setText(chosen)

    def _update_scan_enabled(self) -> None:
        """Enable the Scan button only when source + path are both valid."""
        path = self._le_path.text().strip()
        path_ok = bool(path) and Path(path).is_dir()
        src_ok = self._selected_source_id() is not None
        in_flight = self._worker is not None and self._worker.isRunning()
        self._btn_scan.setEnabled(path_ok and src_ok and not in_flight)

    def _on_scan_clicked(self) -> None:
        sid = self._selected_source_id()
        root = self._le_path.text().strip()
        if not sid or not root:
            return

        try:
            from curator.gui.scan_signals import ScanProgressBridge, ScanWorker
        except Exception as e:  # noqa: BLE001
            self._lbl_status.setText(
                f"<span style='color: #C62828;'>Could not load ScanWorker: {e}</span>"
            )
            return

        self._clear_results()
        self._set_indeterminate(True)
        self._lbl_status.setText(
            f"Scanning <b>{root}</b> as source <b>{sid}</b>..."
        )
        self._btn_scan.setEnabled(False)
        self._btn_browse.setEnabled(False)
        self._cb_source.setEnabled(False)
        self._le_path.setEnabled(False)

        self._bridge = ScanProgressBridge(self)
        self._bridge.scan_started.connect(self._on_scan_started)
        self._bridge.scan_completed.connect(self._on_scan_completed)
        self._bridge.scan_failed.connect(self._on_scan_failed)

        self._worker = ScanWorker(
            runtime=self.runtime,
            source_id=sid,
            root=root,
            options=None,
            bridge=self._bridge,
            parent=self,
        )
        self._worker.start()

    # ------------------------------------------------------------------
    # Slots -- run on the GUI thread via QueuedConnection
    # ------------------------------------------------------------------

    def _on_scan_started(self, payload: object) -> None:
        # payload is (source_id, root); status text already set in click handler.
        pass

    def _on_scan_completed(self, report: object) -> None:
        self._last_report = report
        self._set_indeterminate(False)
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._lbl_status.setText(
            f"<span style='color: #2E7D32;'><b>Scan complete</b></span>"
            f" -- {getattr(report, 'files_seen', '?')} files seen,"
            f" {getattr(report, 'files_new', '?')} new,"
            f" {getattr(report, 'files_updated', '?')} updated,"
            f" {getattr(report, 'errors', '?')} errors."
        )
        self._render_report(report)
        self._reenable_controls()

    def _on_scan_failed(self, exc: object) -> None:
        self._set_indeterminate(False)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        msg = f"{type(exc).__name__}: {exc}"
        self._lbl_status.setText(
            f"<span style='color: #C62828;'><b>Scan failed:</b></span> {msg}"
        )
        self._reenable_controls()

    def _reenable_controls(self) -> None:
        self._btn_browse.setEnabled(True)
        if self._cb_source.count() > 0:
            first_text = self._cb_source.itemText(0)
            if "no sources" not in first_text and "error" not in first_text:
                self._cb_source.setEnabled(True)
        self._le_path.setEnabled(True)
        self._update_scan_enabled()

    # ------------------------------------------------------------------
    # Progress + results rendering
    # ------------------------------------------------------------------

    def _set_indeterminate(self, on: bool) -> None:
        if on:
            self._progress.setRange(0, 0)  # busy-spinner mode
            self._progress.setFormat("Scanning...")
        else:
            self._progress.setFormat("")

    def _clear_results(self) -> None:
        while self._gr_layout.count() > 0:
            item = self._gr_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._lbl_no_results = QLabel("<i>Scan in progress...</i>")
        self._lbl_no_results.setStyleSheet("color: gray; padding: 8px;")
        self._gr_layout.addWidget(self._lbl_no_results)

    def _render_report(self, report: object) -> None:
        """Render every ScanReport field in a structured table."""
        while self._gr_layout.count() > 0:
            item = self._gr_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        rows: list[tuple[str, str]] = []

        def _get(attr: str, default: str = "") -> str:
            v = getattr(report, attr, default)
            return "" if v is None else str(v)

        rows.append(("Job ID",                  _get("job_id")))
        rows.append(("Source",                  _get("source_id")))
        rows.append(("Root",                    _get("root")))
        rows.append(("Started",                 _format_dt(getattr(report, "started_at", None))))
        rows.append(("Completed",               _format_dt(getattr(report, "completed_at", None))))
        rows.append(("Files seen",              _get("files_seen", "0")))
        rows.append(("Files new",               _get("files_new", "0")))
        rows.append(("Files updated",           _get("files_updated", "0")))
        rows.append(("Files unchanged",         _get("files_unchanged", "0")))
        rows.append(("Files hashed",            _get("files_hashed", "0")))
        rows.append(("Cache hits",              _get("cache_hits", "0")))
        bytes_read = getattr(report, "bytes_read", 0) or 0
        rows.append(("Bytes read",              f"{bytes_read:,} ({_format_size(bytes_read)})"))
        rows.append(("Fuzzy hashes computed",   _get("fuzzy_hashes_computed", "0")))
        rows.append(("Classifications assigned",_get("classifications_assigned", "0")))
        rows.append(("Lineage edges created",   _get("lineage_edges_created", "0")))
        rows.append(("Files deleted (gone)",    _get("files_deleted", "0")))
        rows.append(("Errors",                  _get("errors", "0")))

        tbl = _make_kv_table(rows)
        err_count = getattr(report, "errors", 0) or 0
        if err_count > 0:
            for r in range(tbl.rowCount()):
                if tbl.item(r, 0) and tbl.item(r, 0).text() == "Errors":
                    tbl.item(r, 1).setForeground(QColor("#C62828"))
                    f = QFont()
                    f.setBold(True)
                    tbl.item(r, 1).setFont(f)
                    break
        self._gr_layout.addWidget(tbl)

        error_paths = getattr(report, "error_paths", None) or []
        if error_paths:
            lbl_ep = QLabel(f"<b>Error paths ({len(error_paths)}):</b>")
            lbl_ep.setStyleSheet("color: #C62828; padding-top: 6px;")
            self._gr_layout.addWidget(lbl_ep)
            ep_tbl = _make_table(["#", "Path"],
                                 [[str(i + 1), p] for i, p in enumerate(error_paths[:50])])
            self._gr_layout.addWidget(ep_tbl)
            if len(error_paths) > 50:
                lbl_more = QLabel(f"<i>... and {len(error_paths) - 50} more (see audit log)</i>")
                lbl_more.setStyleSheet("color: gray;")
                self._gr_layout.addWidget(lbl_more)

    # ------------------------------------------------------------------
    # Test / introspection helpers
    # ------------------------------------------------------------------

    @property
    def last_report(self) -> Any:
        """Return the ScanReport from the most recent scan, or None."""
        return self._last_report


# ---------------------------------------------------------------------------
# GroupDialog (v1.7 alpha) -- third native dialog after Health + Scan
# ---------------------------------------------------------------------------
#
# Two-phase duplicate finder:
#
#   1. **Find**: pick a source + path prefix + keep strategy + match kind,
#      click Find. Worker runs CleanupService.find_duplicates() and emits
#      a CleanupReport. Dialog renders one row per duplicate group with
#      its keeper marked.
#
#   2. **Apply**: click Apply (disabled until findings exist). Confirm
#      modal then run CleanupService.apply() -- moves non-keepers to trash
#      (reversible) by default, or hard-deletes if --use-trash unchecked.
#      Renders the ApplyReport stats (deleted / skipped / failed).
#
# v1.7 alpha limitations (tracked in FEATURE_TODO):
#   * No tree-style expansion in the duplicate group view -- shows a flat
#     table grouped by dupset_id with the keeper highlighted in green.
#     Full nested tree comes in v1.7.x.
#   * No per-group "ungroup" or "change keeper" actions yet -- you re-run
#     Find with a different keep_strategy to change what's marked as keeper.


class GroupDialog(QDialog):
    """Find + apply duplicate cleanup from the GUI without console output.

    Replaces the v1.6.2 "Find duplicates..." Tools-menu placeholder.
    Reads sources from ``runtime.source_repo.list_all()`` and runs all
    cleanup operations through ``runtime.cleanup``.
    """

    def __init__(self, runtime: "CuratorRuntime", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._find_worker: Any = None
        self._apply_worker: Any = None
        self._bridge: Any = None
        self._last_find_report: Any = None      # CleanupReport from find phase
        self._last_apply_report: Any = None     # ApplyReport from apply phase

        self.setWindowTitle("Curator - Find duplicates")
        self.setMinimumWidth(780)
        self.resize(880, 640)
        self._build_ui()
        self._populate_sources()
        self._update_button_states()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QLineEdit,
            QProgressBar,
            QRadioButton,
            QButtonGroup,
        )

        layout = QVBoxLayout(self)

        # --- Inputs group ----------------------------------------------
        grp_inputs = QGroupBox("Find parameters")
        gi = QVBoxLayout(grp_inputs)

        # Source picker + path prefix (same row)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Source:"))
        self._cb_source = QComboBox()
        self._cb_source.setMinimumWidth(180)
        row1.addWidget(self._cb_source)
        row1.addSpacing(20)
        row1.addWidget(QLabel("Path prefix:"))
        self._le_prefix = QLineEdit()
        self._le_prefix.setPlaceholderText("(optional, e.g. C:\\Users\\jmlee\\Pictures)")
        row1.addWidget(self._le_prefix, 1)
        gi.addLayout(row1)

        # Keep strategy + keep_under (same row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Keep strategy:"))
        self._cb_strategy = QComboBox()
        for s in ("shortest_path", "longest_path", "oldest", "newest"):
            self._cb_strategy.addItem(s, s)
        row2.addWidget(self._cb_strategy)
        row2.addSpacing(20)
        row2.addWidget(QLabel("Keep under:"))
        self._le_keep_under = QLineEdit()
        self._le_keep_under.setPlaceholderText("(optional path prefix; overrides strategy)")
        row2.addWidget(self._le_keep_under, 1)
        gi.addLayout(row2)

        # Match kind + similarity threshold (same row)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Match kind:"))
        self._rb_exact = QRadioButton("exact (xxhash3_128)")
        self._rb_exact.setChecked(True)
        self._rb_fuzzy = QRadioButton("fuzzy (MinHash-LSH)")
        self._rb_exact.toggled.connect(self._on_match_kind_changed)
        self._rb_group = QButtonGroup(self)
        self._rb_group.addButton(self._rb_exact)
        self._rb_group.addButton(self._rb_fuzzy)
        row3.addWidget(self._rb_exact)
        row3.addWidget(self._rb_fuzzy)
        row3.addSpacing(20)
        row3.addWidget(QLabel("Similarity:"))
        self._sb_threshold = QDoubleSpinBox()
        self._sb_threshold.setRange(0.5, 1.0)
        self._sb_threshold.setSingleStep(0.05)
        self._sb_threshold.setValue(0.85)
        self._sb_threshold.setDecimals(2)
        self._sb_threshold.setEnabled(False)  # fuzzy off by default
        row3.addWidget(self._sb_threshold)
        row3.addStretch(1)
        gi.addLayout(row3)

        # Hint
        hint = QLabel(
            "<i>Tip: leave Source empty to search ALL sources. Fuzzy match"
            " catches re-encoded JPEGs / re-compressed MP3s but has false"
            " positives -- always review before Apply. Default keep strategy"
            " (shortest_path) usually keeps the file closest to the source root.</i>"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5C6BC0; padding: 4px;")
        gi.addWidget(hint)

        layout.addWidget(grp_inputs)

        # --- Progress + status ----------------------------------------
        grp_status = QGroupBox("Status")
        gs = QVBoxLayout(grp_status)

        self._lbl_status = QLabel("Idle. Set parameters, then click Find.")
        self._lbl_status.setWordWrap(True)
        gs.addWidget(self._lbl_status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("")
        gs.addWidget(self._progress)

        layout.addWidget(grp_status)

        # --- Results group --------------------------------------------
        self._grp_results = QGroupBox("Duplicate groups")
        self._gr_layout = QVBoxLayout(self._grp_results)
        self._lbl_no_results = QLabel("<i>No find run yet.</i>")
        self._lbl_no_results.setStyleSheet("color: gray; padding: 8px;")
        self._gr_layout.addWidget(self._lbl_no_results)
        layout.addWidget(self._grp_results, 1)

        # --- Buttons + use-trash toggle -------------------------------
        row_btn = QHBoxLayout()
        self._btn_find = QPushButton("Find duplicates")
        self._btn_find.setDefault(True)
        self._btn_find.clicked.connect(self._on_find_clicked)
        row_btn.addWidget(self._btn_find)

        self._cb_use_trash = QCheckBox("Move to trash (reversible)")
        self._cb_use_trash.setChecked(True)
        self._cb_use_trash.setToolTip(
            "When checked: applying sends duplicates to OS Recycle Bin + "
            "Curator's trash registry (restorable). When unchecked: hard delete."
        )
        row_btn.addWidget(self._cb_use_trash)

        self._btn_apply = QPushButton("Apply (trash duplicates)")
        self._btn_apply.clicked.connect(self._on_apply_clicked)
        self._btn_apply.setEnabled(False)
        row_btn.addWidget(self._btn_apply)

        row_btn.addStretch(1)
        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.reject)
        row_btn.addWidget(self._btn_close)
        layout.addLayout(row_btn)

    # ------------------------------------------------------------------
    # Source population
    # ------------------------------------------------------------------

    def _populate_sources(self) -> None:
        """Load sources into the dropdown. Includes '(all sources)' option."""
        self._cb_source.addItem("(all sources)", None)
        try:
            sources = self.runtime.source_repo.list_all()
        except Exception as e:  # noqa: BLE001
            self._cb_source.addItem(f"(error: {e})")
            return
        for s in sources:
            label = f"{s.source_id} ({s.source_type})"
            self._cb_source.addItem(label, s.source_id)

    def _selected_source_id(self) -> str | None:
        return self._cb_source.currentData()

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _on_match_kind_changed(self, _checked: bool) -> None:
        self._sb_threshold.setEnabled(self._rb_fuzzy.isChecked())

    def _update_button_states(self) -> None:
        find_in_flight = self._find_worker is not None and self._find_worker.isRunning()
        apply_in_flight = self._apply_worker is not None and self._apply_worker.isRunning()
        any_in_flight = find_in_flight or apply_in_flight
        self._btn_find.setEnabled(not any_in_flight)
        # Apply only enabled if we have findings AND not currently scanning/applying
        has_findings = (self._last_find_report is not None
                        and len(self._last_find_report.findings) > 0)
        self._btn_apply.setEnabled(has_findings and not any_in_flight)

    def _set_indeterminate(self, on: bool, label: str = "Working...") -> None:
        if on:
            self._progress.setRange(0, 0)
            self._progress.setFormat(label)
        else:
            self._progress.setRange(0, 1)
            self._progress.setFormat("")

    def _clear_results(self) -> None:
        while self._gr_layout.count() > 0:
            item = self._gr_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ------------------------------------------------------------------
    # Find phase
    # ------------------------------------------------------------------

    def _on_find_clicked(self) -> None:
        # Lazy import
        try:
            from curator.gui.cleanup_signals import GroupProgressBridge, GroupFindWorker
        except Exception as e:  # noqa: BLE001
            self._lbl_status.setText(
                f"<span style='color: #C62828;'>Could not load GroupFindWorker: {e}</span>"
            )
            return

        # Collect inputs
        source_id = self._selected_source_id()
        prefix = self._le_prefix.text().strip() or None
        keep_strategy = self._cb_strategy.currentData()
        keep_under = self._le_keep_under.text().strip() or None
        match_kind = "fuzzy" if self._rb_fuzzy.isChecked() else "exact"
        threshold = self._sb_threshold.value()

        # Reset state
        self._last_find_report = None
        self._last_apply_report = None
        self._clear_results()
        lbl = QLabel("<i>Finding duplicates...</i>")
        lbl.setStyleSheet("color: gray; padding: 8px;")
        self._gr_layout.addWidget(lbl)
        self._set_indeterminate(True, "Searching for duplicates...")
        scope = source_id if source_id else "ALL sources"
        self._lbl_status.setText(
            f"Searching {scope} (match: <b>{match_kind}</b>, keep: <b>{keep_strategy}</b>)..."
        )

        # Set up bridge + worker
        self._bridge = GroupProgressBridge(self)
        self._bridge.find_started.connect(self._on_find_started)
        self._bridge.find_completed.connect(self._on_find_completed)
        self._bridge.find_failed.connect(self._on_find_failed)
        self._bridge.apply_started.connect(self._on_apply_started)
        self._bridge.apply_completed.connect(self._on_apply_completed)
        self._bridge.apply_failed.connect(self._on_apply_failed)

        self._find_worker = GroupFindWorker(
            runtime=self.runtime,
            source_id=source_id,
            root_prefix=prefix,
            keep_strategy=keep_strategy,
            keep_under=keep_under,
            match_kind=match_kind,
            similarity_threshold=threshold,
            bridge=self._bridge,
            parent=self,
        )
        self._find_worker.start()
        self._update_button_states()

    def _on_find_started(self, _payload: object) -> None:
        pass  # status already set in click handler

    def _on_find_completed(self, report: object) -> None:
        self._last_find_report = report
        self._set_indeterminate(False)
        n_findings = len(report.findings)

        # Compute group count + reclaimable bytes
        dupset_ids = set()
        total_bytes = 0
        for f in report.findings:
            ds = f.details.get("dupset_id") if isinstance(f.details, dict) else None
            if ds:
                dupset_ids.add(ds)
            total_bytes += f.size

        if n_findings == 0:
            self._lbl_status.setText(
                "<span style='color: #2E7D32;'><b>No duplicates found.</b></span>"
                " The index is clean for the parameters you chose."
            )
        else:
            self._lbl_status.setText(
                f"<span style='color: #2E7D32;'><b>Found</b></span>"
                f" {len(dupset_ids)} duplicate group(s), {n_findings} non-keeper file(s),"
                f" <b>{_format_size(total_bytes)}</b> reclaimable."
            )
        self._render_find_report(report, dupset_ids, total_bytes)
        self._update_button_states()

    def _on_find_failed(self, exc: object) -> None:
        self._set_indeterminate(False)
        self._lbl_status.setText(
            f"<span style='color: #C62828;'><b>Find failed:</b></span>"
            f" {type(exc).__name__}: {exc}"
        )
        self._update_button_states()

    def _render_find_report(self, report: object, dupset_ids: set, total_bytes: int) -> None:
        """Render duplicate findings grouped by dupset_id."""
        self._clear_results()

        if not report.findings:
            lbl = QLabel("<i>No duplicates found for these parameters.</i>")
            lbl.setStyleSheet("color: gray; padding: 8px;")
            self._gr_layout.addWidget(lbl)
            return

        # Group findings by dupset_id
        groups: dict = {}
        for f in report.findings:
            ds = f.details.get("dupset_id") if isinstance(f.details, dict) else "unknown"
            groups.setdefault(ds, []).append(f)

        # Build flat table: one row per file (keeper first per group)
        # Columns: Group hash (short) | File path | Size | Status (KEEPER / dup)
        headers = ["Group (xxhash3 prefix)", "Path", "Size", "Status"]
        rows: list[list[str]] = []
        for ds_id, findings in sorted(groups.items(),
                                       key=lambda kv: -sum(f.size for f in kv[1])):
            short_hash = ds_id[:12] + "..." if ds_id and len(ds_id) > 12 else (ds_id or "?")
            # Keeper row (synthesize from details — keeper itself isn't in findings list)
            first = findings[0]
            kept_path = first.details.get("kept_path", "(unknown)") if isinstance(first.details, dict) else "(?)"
            kept_reason = first.details.get("kept_reason", "?") if isinstance(first.details, dict) else "?"
            rows.append([short_hash, kept_path, _format_size(first.size), f"KEEPER ({kept_reason})"])
            for f in findings:
                rows.append(["", f.path, _format_size(f.size), "duplicate"])

        tbl = _make_table(headers, rows)
        # Highlight keeper rows green, duplicate rows yellow-ish
        for r in range(tbl.rowCount()):
            status_item = tbl.item(r, 3)
            if status_item is None:
                continue
            if "KEEPER" in status_item.text():
                for c in range(tbl.columnCount()):
                    cell = tbl.item(r, c)
                    if cell:
                        cell.setForeground(QColor("#2E7D32"))
                        f = QFont()
                        f.setBold(True)
                        cell.setFont(f)
            elif status_item.text() == "duplicate":
                for c in range(tbl.columnCount()):
                    cell = tbl.item(r, c)
                    if cell:
                        cell.setForeground(QColor("#EF6C00"))
        # Cap the visible row count via min height so the dialog stays usable
        tbl.setMinimumHeight(220)
        self._gr_layout.addWidget(tbl)

    # ------------------------------------------------------------------
    # Apply phase
    # ------------------------------------------------------------------

    def _on_apply_clicked(self) -> None:
        if self._last_find_report is None or not self._last_find_report.findings:
            return  # button shouldn't have been enabled

        # Lazy import
        try:
            from curator.gui.cleanup_signals import GroupApplyWorker
        except Exception as e:  # noqa: BLE001
            self._lbl_status.setText(
                f"<span style='color: #C62828;'>Could not load GroupApplyWorker: {e}</span>"
            )
            return

        # Lazy import QMessageBox for the confirm dialog
        from PySide6.QtWidgets import QMessageBox

        n = len(self._last_find_report.findings)
        use_trash = self._cb_use_trash.isChecked()
        action_word = "move to trash" if use_trash else "HARD DELETE"
        reply = QMessageBox.question(
            self,
            "Confirm apply",
            f"This will {action_word} <b>{n}</b> non-keeper file(s).<br><br>"
            f"<b>Use trash:</b> {'yes (reversible)' if use_trash else 'NO (irreversible)'}<br><br>"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_indeterminate(True, "Applying...")
        self._lbl_status.setText(
            f"Applying: {action_word} {n} file(s)..."
        )

        # Reuse the same bridge from find phase (signals already wired)
        self._apply_worker = GroupApplyWorker(
            runtime=self.runtime,
            report=self._last_find_report,
            use_trash=use_trash,
            bridge=self._bridge,
            parent=self,
        )
        self._apply_worker.start()
        self._update_button_states()

    def _on_apply_started(self, _n: object) -> None:
        pass  # status already set

    def _on_apply_completed(self, apply_report: object) -> None:
        self._last_apply_report = apply_report
        self._set_indeterminate(False)

        # Tally outcomes
        from collections import Counter
        outcomes = Counter()
        errors: list[str] = []
        for result in apply_report.results:
            # outcome is ApplyOutcome enum: DELETED / SKIPPED_REFUSE / SKIPPED_MISSING / FAILED
            outcome_name = getattr(result.outcome, "name", str(result.outcome))
            outcomes[outcome_name] += 1
            if result.error:
                errors.append(f"{result.finding.path}: {result.error}")

        deleted = outcomes.get("DELETED", 0)
        skipped = outcomes.get("SKIPPED_REFUSE", 0) + outcomes.get("SKIPPED_MISSING", 0)
        failed = outcomes.get("FAILED", 0)

        color = "#2E7D32" if failed == 0 else "#EF6C00"
        self._lbl_status.setText(
            f"<span style='color: {color};'><b>Apply complete:</b></span>"
            f" {deleted} deleted, {skipped} skipped, {failed} failed."
        )

        # Append apply summary below the existing find table
        from PySide6.QtWidgets import QGroupBox as _QGB
        sub = _QGB("Apply results")
        sub_layout = QVBoxLayout(sub)
        sub_rows = [
            ("Deleted",        str(deleted)),
            ("Skipped (refused safety)",  str(outcomes.get("SKIPPED_REFUSE", 0))),
            ("Skipped (missing on disk)", str(outcomes.get("SKIPPED_MISSING", 0))),
            ("Failed",         str(failed)),
            ("Started",        _format_dt(getattr(apply_report, "started_at", None))),
            ("Completed",      _format_dt(getattr(apply_report, "completed_at", None))),
        ]
        sub_layout.addWidget(_make_kv_table(sub_rows))
        if errors:
            sub_layout.addWidget(QLabel(f"<b>Errors ({len(errors)}):</b>"))
            err_tbl = _make_table(["Path", "Error"],
                                  [[e.split(": ", 1)[0], e.split(": ", 1)[1] if ": " in e else ""]
                                   for e in errors[:30]])
            sub_layout.addWidget(err_tbl)
        self._gr_layout.addWidget(sub)
        self._update_button_states()

    def _on_apply_failed(self, exc: object) -> None:
        self._set_indeterminate(False)
        self._lbl_status.setText(
            f"<span style='color: #C62828;'><b>Apply failed:</b></span>"
            f" {type(exc).__name__}: {exc}"
        )
        self._update_button_states()

    # ------------------------------------------------------------------
    # Test / introspection helpers
    # ------------------------------------------------------------------

    @property
    def last_find_report(self) -> Any:
        """Return the CleanupReport from the most recent find, or None."""
        return self._last_find_report

    @property
    def last_apply_report(self) -> Any:
        """Return the ApplyReport from the most recent apply, or None."""
        return self._last_apply_report
