"""GUI dialogs (Phase Beta gate 4 v0.36).

Currently houses :class:`FileInspectDialog` -- the modal shown when a
user double-clicks a row in the Browser tab. It surfaces *everything*
Curator knows about a file in three tabs:

  * **Metadata** -- every fixed-schema field on the FileEntity plus
    every flex attr, in a two-column key / value table.
  * **Lineage Edges** -- every edge where this file appears (either
    side), with the other file's path resolved from the file repo.
  * **Bundle Memberships** -- every bundle this file belongs to with
    role + confidence.

The dialog is read-only: this is the "what does Curator know about
this file" view. Mutations (trash, dissolve) live on the main window
context menus, not in here.

The constructor takes the runtime and the FileEntity; it queries
synchronously at open time. For Curator's typical row counts this is
trivially fast (a single file has at most a few dozen edges + a few
bundle memberships in practice).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
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


__all__ = ["FileInspectDialog", "BundleEditorDialog", "BundleEditorResult"]
