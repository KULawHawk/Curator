"""Qt table models for the Curator GUI (v0.34).

Each model is a thin :class:`QAbstractTableModel` wrapping a Curator
repository. They:

  * load data once from the repo on construction
  * expose columns matching what's most useful to see at-a-glance
  * implement ``rowCount``, ``columnCount``, ``data``, ``headerData``,
    and ``sort`` so QTableView's built-in sort indicator works

These models are kept simple on purpose: re-querying the DB on every
:meth:`refresh` keeps the GUI honest about what's actually indexed,
and the row counts in a personal-scale Curator install (low thousands
of files) are well within "load it all" territory. If we ever hit
50k-row tables we'll add lazy fetch semantics.

All three models follow the same shape so the views can treat them
uniformly: load via ``refresh()``, read via Qt's index-based API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    Qt,
)

from uuid import UUID

from curator.models.file import FileEntity
from curator.models.bundle import BundleEntity
from curator.models.audit import AuditEntry
from curator.models.jobs import ScanJob
from curator.models.lineage import LineageEdge
from curator.models.migration import MigrationJob, MigrationProgress
from curator.storage.queries import FileQuery
from curator.storage.repositories.audit_repo import AuditRepository
from curator.storage.repositories.bundle_repo import BundleRepository
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.job_repo import ScanJobRepository
from curator.storage.repositories.lineage_repo import LineageRepository
from curator.storage.repositories.migration_job_repo import MigrationJobRepository
from curator.storage.repositories.trash_repo import TrashRepository

# Type-only import for Config (avoid hard-importing in headless tests).
from typing import TYPE_CHECKING
if TYPE_CHECKING:  # pragma: no cover
    from curator.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_size(n: int | None) -> str:
    """Human-readable size: 1024 -> '1.0 KB' etc."""
    if n is None:
        return ""
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0  # type: ignore[assignment]
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PB"


def _format_dt(dt: datetime | None) -> str:
    """Compact ISO-ish datetime string for table cells."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# FileTableModel — Browser view
# ---------------------------------------------------------------------------


class FileTableModel(QAbstractTableModel):
    """Table model over :class:`FileEntity` rows.

    Columns: source / path / size / mtime / extension / xxhash3 (short)
    """

    COLUMNS: tuple[str, ...] = (
        "Source", "Path", "Size", "Modified", "Ext", "xxhash3 (short)",
    )

    def __init__(
        self,
        file_repo: FileRepository,
        *,
        include_deleted: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._file_repo = file_repo
        self._include_deleted = include_deleted
        self._rows: list[FileEntity] = []
        self.refresh()

    # -- public API -----------------------------------------------------

    def refresh(self) -> None:
        """Re-query the DB and reset the view."""
        self.beginResetModel()
        try:
            query = FileQuery(deleted=False if not self._include_deleted else None)
            self._rows = self._file_repo.query(query)
        except Exception:
            # Keep the GUI alive on a failed refresh; the status bar
            # can show the error elsewhere.
            self._rows = []
        self.endResetModel()

    def file_at(self, row: int) -> FileEntity | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- Qt protocol ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole,  # type: ignore[name-defined]
    ) -> Any:
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:  # type: ignore[name-defined]
        if not index.isValid():
            return None
        if role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        row = index.row()
        if not (0 <= row < len(self._rows)):
            return None
        f = self._rows[row]
        col = index.column()
        if role == Qt.ToolTipRole:
            # Full path on tooltip (the column may be elided).
            if col == 1:
                return f.source_path
            return None
        if col == 0:
            return f.source_id
        if col == 1:
            return f.source_path
        if col == 2:
            return _format_size(f.size)
        if col == 3:
            return _format_dt(f.mtime)
        if col == 4:
            return f.extension or ""
        if col == 5:
            h = f.xxhash3_128
            return (h[:12] + "...") if h and len(h) > 12 else (h or "")
        return None

    def sort(  # noqa: D401 -- Qt protocol name
        self,
        column: int,
        order: Qt.SortOrder = Qt.AscendingOrder,  # type: ignore[name-defined]
    ) -> None:
        """Sort by the given column. Stable; falls back to source_path."""
        reverse = order == Qt.SortOrder.DescendingOrder

        def key(f: FileEntity) -> Any:
            if column == 0:
                return f.source_id or ""
            if column == 1:
                return f.source_path or ""
            if column == 2:
                return f.size or 0
            if column == 3:
                # datetime may be None; coerce
                return f.mtime or datetime.min
            if column == 4:
                return f.extension or ""
            if column == 5:
                return f.xxhash3_128 or ""
            return f.source_path or ""

        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key, reverse=reverse)
        self.layoutChanged.emit()


# ---------------------------------------------------------------------------
# BundleTableModel — Bundles view
# ---------------------------------------------------------------------------


class BundleTableModel(QAbstractTableModel):
    """Table model over :class:`BundleEntity` rows."""

    COLUMNS: tuple[str, ...] = (
        "Name", "Type", "Members", "Confidence", "Created",
    )

    def __init__(
        self,
        bundle_repo: BundleRepository,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._bundle_repo = bundle_repo
        self._rows: list[BundleEntity] = []
        self._member_counts: dict = {}
        self.refresh()

    # -- public API -----------------------------------------------------

    def refresh(self) -> None:
        self.beginResetModel()
        try:
            self._rows = self._bundle_repo.list_all()
            self._member_counts = {
                b.bundle_id: self._bundle_repo.member_count(b.bundle_id)
                for b in self._rows
            }
        except Exception:
            self._rows = []
            self._member_counts = {}
        self.endResetModel()

    def bundle_at(self, row: int) -> BundleEntity | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- Qt protocol ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = index.row()
        if not (0 <= row < len(self._rows)):
            return None
        b = self._rows[row]
        col = index.column()
        if col == 0:
            return b.name or "(unnamed)"
        if col == 1:
            return b.bundle_type
        if col == 2:
            return self._member_counts.get(b.bundle_id, 0)
        if col == 3:
            return f"{b.confidence:.2f}"
        if col == 4:
            return _format_dt(b.created_at)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        reverse = order == Qt.SortOrder.DescendingOrder

        def key(b: BundleEntity) -> Any:
            if column == 0:
                return (b.name or "").lower()
            if column == 1:
                return b.bundle_type
            if column == 2:
                return self._member_counts.get(b.bundle_id, 0)
            if column == 3:
                return b.confidence
            if column == 4:
                return b.created_at or datetime.min
            return b.name or ""

        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key, reverse=reverse)
        self.layoutChanged.emit()


# ---------------------------------------------------------------------------
# TrashTableModel — Trash view
# ---------------------------------------------------------------------------


class TrashTableModel(QAbstractTableModel):
    """Table model over the trash registry."""

    COLUMNS: tuple[str, ...] = (
        "Original Path", "Source", "Reason", "Trashed By", "Trashed At",
    )

    def __init__(
        self,
        trash_repo: TrashRepository,
        *,
        limit: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._trash_repo = trash_repo
        self._limit = limit
        self._rows: list = []
        self.refresh()

    # -- public API -----------------------------------------------------

    def refresh(self) -> None:
        self.beginResetModel()
        try:
            self._rows = list(self._trash_repo.list(limit=self._limit))
        except Exception:
            self._rows = []
        self.endResetModel()

    def trash_at(self, row: int):
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- Qt protocol ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = index.row()
        if not (0 <= row < len(self._rows)):
            return None
        r = self._rows[row]
        col = index.column()
        if col == 0:
            return r.original_path
        if col == 1:
            return r.original_source_id
        if col == 2:
            return r.reason
        if col == 3:
            return r.trashed_by
        if col == 4:
            return _format_dt(r.trashed_at)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        reverse = order == Qt.SortOrder.DescendingOrder

        def key(r) -> Any:
            if column == 0:
                return r.original_path or ""
            if column == 1:
                return r.original_source_id or ""
            if column == 2:
                return r.reason or ""
            if column == 3:
                return r.trashed_by or ""
            if column == 4:
                return r.trashed_at or datetime.min
            return r.original_path or ""

        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key, reverse=reverse)
        self.layoutChanged.emit()


# ---------------------------------------------------------------------------
# AuditLogTableModel — Audit Log view (v0.37)
# ---------------------------------------------------------------------------


class AuditLogTableModel(QAbstractTableModel):
    """Table model over the audit log (append-only).

    Columns: When / Actor / Action / Entity / Details (JSON-truncated)

    The audit log can grow large in long-running deployments, so the
    repository query is capped at ``DEFAULT_LIMIT`` rows newest-first.
    For larger histories (forensic investigations, etc.), users should
    filter via the CLI; the GUI is for at-a-glance "what just happened".
    """

    DEFAULT_LIMIT: int = 1000
    DETAILS_PREVIEW_LEN: int = 80

    COLUMNS: tuple[str, ...] = (
        "When", "Actor", "Action", "Entity", "Details",
    )

    def __init__(
        self,
        audit_repo: AuditRepository,
        *,
        limit: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._audit_repo = audit_repo
        self._limit = limit if limit is not None else self.DEFAULT_LIMIT
        self._rows: list[AuditEntry] = []
        self.refresh()

    # -- public API -----------------------------------------------------

    def refresh(self) -> None:
        """Re-query newest-first up to ``limit`` rows."""
        self.beginResetModel()
        try:
            self._rows = self._audit_repo.query(limit=self._limit)
        except Exception:
            self._rows = []
        self.endResetModel()

    def entry_at(self, row: int) -> AuditEntry | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- Qt protocol ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self._rows)):
            return None
        e = self._rows[row]
        col = index.column()
        if role == Qt.ToolTipRole:
            # On the Details column, show the full JSON in a tooltip
            # since the cell will truncate.
            if col == 4:
                return self._format_details(e.details, truncate=False)
            return None
        if role != Qt.DisplayRole:
            return None
        if col == 0:
            return _format_dt(e.occurred_at)
        if col == 1:
            return e.actor
        if col == 2:
            return e.action
        if col == 3:
            return self._format_entity(e)
        if col == 4:
            return self._format_details(e.details, truncate=True)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        reverse = order == Qt.SortOrder.DescendingOrder

        def key(e: AuditEntry) -> Any:
            if column == 0:
                return e.occurred_at or datetime.min
            if column == 1:
                return e.actor or ""
            if column == 2:
                return e.action or ""
            if column == 3:
                return self._format_entity(e)
            if column == 4:
                return self._format_details(e.details, truncate=True)
            return e.occurred_at or datetime.min

        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key, reverse=reverse)
        self.layoutChanged.emit()

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _format_entity(entry: AuditEntry) -> str:
        """Compact 'type:short_id' or 'type:(none)' display."""
        et = entry.entity_type or ""
        eid = entry.entity_id or ""
        if not et and not eid:
            return ""
        # Truncate UUID-shaped IDs to first 8 chars + ellipsis for table compactness.
        if eid and len(eid) > 12 and "-" in eid:
            eid = eid[:8] + "..."
        return f"{et}:{eid}" if et else eid

    @classmethod
    def _format_details(cls, details: dict, *, truncate: bool) -> str:
        """JSON-stringify the details dict for display."""
        if not details:
            return ""
        try:
            import json
            s = json.dumps(details, default=str, sort_keys=True)
        except Exception:
            s = str(details)
        if truncate and len(s) > cls.DETAILS_PREVIEW_LEN:
            s = s[:cls.DETAILS_PREVIEW_LEN] + "..."
        return s


# ---------------------------------------------------------------------------
# ConfigTableModel — Settings view (v0.38)
# ---------------------------------------------------------------------------


class ConfigTableModel(QAbstractTableModel):
    """Table model over a flattened :class:`Config` dict.

    Each row is a dotted-path key + its value. Nested sections are
    recursively flattened so users see ``hash.prefix_bytes`` rather
    than having to expand a tree. Lists are JSON-formatted; primitives
    are str()'d.

    The model can be re-pointed at a different :class:`Config` via
    :meth:`set_config` (used by the Settings view's Reload button to
    refresh from disk without touching the live runtime config).
    """

    COLUMNS: tuple[str, ...] = ("Setting", "Value")

    def __init__(self, config: "Config", parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._rows: list[tuple[str, str]] = []
        self.refresh()

    # -- public API -----------------------------------------------------

    def set_config(self, config: "Config") -> None:
        """Re-point at a different Config (used by Settings view's Reload)."""
        self._config = config
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the flattened rows from the current config."""
        self.beginResetModel()
        try:
            data = self._config.as_dict()
            self._rows = list(self._flatten(data))
            self._rows.sort(key=lambda r: r[0])
        except Exception:
            self._rows = []
        self.endResetModel()

    def setting_at(self, row: int) -> tuple[str, str] | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- Qt protocol ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self._rows)):
            return None
        if role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        key, value = self._rows[row]
        col = index.column()
        if role == Qt.ToolTipRole:
            # Full value on tooltip in case the cell elides.
            if col == 1:
                return value
            return None
        if col == 0:
            return key
        if col == 1:
            return value
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        reverse = order == Qt.SortOrder.DescendingOrder

        def key(r: tuple[str, str]) -> Any:
            return r[column] if 0 <= column < 2 else r[0]

        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key, reverse=reverse)
        self.layoutChanged.emit()

    # -- helpers --------------------------------------------------------

    @classmethod
    def _flatten(cls, data: Any, prefix: str = ""):
        """Yield (dotted_key, value_str) pairs for a nested dict."""
        if isinstance(data, dict):
            for k in sorted(data.keys()):
                new_prefix = f"{prefix}.{k}" if prefix else str(k)
                yield from cls._flatten(data[k], new_prefix)
        elif isinstance(data, (list, tuple)):
            yield (prefix, cls._format_value(data))
        else:
            yield (prefix, cls._format_value(data))

    @staticmethod
    def _format_value(value: Any) -> str:
        if value is None:
            return "(null)"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (list, tuple)):
            try:
                import json
                return json.dumps(list(value), default=str)
            except Exception:
                return str(value)
        if isinstance(value, dict):
            # Should be flattened earlier; but be defensive.
            try:
                import json
                return json.dumps(value, default=str, sort_keys=True)
            except Exception:
                return str(value)
        return str(value)


# ---------------------------------------------------------------------------
# ScanJobTableModel — Inbox "Recent scans" section (v0.39)
# ---------------------------------------------------------------------------


class ScanJobTableModel(QAbstractTableModel):
    """Table model over recent scan jobs.

    Columns: Status / Source / Root / Files / Started / Completed

    Used by the Inbox tab's "Recent scans" section. The model wraps
    :meth:`ScanJobRepository.list_recent` and is intentionally simple:
    no sort customization beyond Qt's defaults, no filtering. The
    Inbox is a glanceable summary, not a forensic tool.
    """

    DEFAULT_LIMIT: int = 10

    COLUMNS: tuple[str, ...] = (
        "Status", "Source", "Root", "Files", "Started", "Completed",
    )

    def __init__(
        self,
        job_repo: ScanJobRepository,
        *,
        limit: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._job_repo = job_repo
        self._limit = limit if limit is not None else self.DEFAULT_LIMIT
        self._rows: list[ScanJob] = []
        self.refresh()

    # -- public API -----------------------------------------------------

    def refresh(self) -> None:
        self.beginResetModel()
        try:
            self._rows = self._job_repo.list_recent(limit=self._limit)
        except Exception:
            self._rows = []
        self.endResetModel()

    def job_at(self, row: int) -> ScanJob | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- Qt protocol ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = index.row()
        if not (0 <= row < len(self._rows)):
            return None
        j = self._rows[row]
        col = index.column()
        if col == 0:
            return j.status
        if col == 1:
            return j.source_id
        if col == 2:
            return j.root_path
        if col == 3:
            # Show "hashed/seen" if both populated, else just seen.
            if j.files_hashed and j.files_seen:
                return f"{j.files_hashed}/{j.files_seen}"
            return str(j.files_seen) if j.files_seen else ""
        if col == 4:
            return _format_dt(j.started_at)
        if col == 5:
            return _format_dt(j.completed_at)
        return None


# ---------------------------------------------------------------------------
# PendingReviewTableModel — Inbox "Pending review" section (v0.39)
# ---------------------------------------------------------------------------


class PendingReviewTableModel(QAbstractTableModel):
    """Table model over lineage edges that need human review.

    "Pending review" is defined here as: lineage edges with confidence
    in the [escalate_threshold, auto_confirm_threshold) ambiguous middle
    band. Above auto_confirm: Curator just acts. Below escalate: nothing
    is stored. Between: the user should look at it.

    Both thresholds come from the runtime's Config (``lineage.escalate_threshold``
    and ``lineage.auto_confirm_threshold``); this model accepts them as
    constructor args so it stays cheap to test in isolation.

    Columns: Kind / From -> To / Confidence / Detected by

    Resolves the from/to file paths via :class:`FileRepository.get` for
    readability. If a file row is missing (rare; could happen if the
    file was hard-deleted), the column shows the UUID instead.
    """

    DEFAULT_LIMIT: int = 50

    COLUMNS: tuple[str, ...] = (
        "Kind", "From", "To", "Confidence", "Detected by",
    )

    def __init__(
        self,
        lineage_repo: LineageRepository,
        file_repo: FileRepository,
        *,
        escalate_threshold: float = 0.7,
        auto_confirm_threshold: float = 0.95,
        limit: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._lineage_repo = lineage_repo
        self._file_repo = file_repo
        self._escalate = escalate_threshold
        self._auto_confirm = auto_confirm_threshold
        self._limit = limit if limit is not None else self.DEFAULT_LIMIT
        self._rows: list[LineageEdge] = []
        self._path_cache: dict = {}  # curator_id -> source_path
        self.refresh()

    # -- public API -----------------------------------------------------

    def refresh(self) -> None:
        self.beginResetModel()
        self._path_cache = {}
        try:
            # Clean call to the repo's public confidence-range query
            # (added in v0.39 alongside this model). Keeps the SQL
            # where it belongs.
            self._rows = self._lineage_repo.query_by_confidence(
                min_confidence=self._escalate,
                max_confidence=self._auto_confirm,
                limit=self._limit,
            )
        except Exception:
            self._rows = []
        self.endResetModel()

    def edge_at(self, row: int) -> LineageEdge | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- Qt protocol ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = index.row()
        if not (0 <= row < len(self._rows)):
            return None
        e = self._rows[row]
        col = index.column()
        if col == 0:
            return e.edge_kind.value if hasattr(e.edge_kind, "value") else str(e.edge_kind)
        if col == 1:
            return self._resolve_path(e.from_curator_id)
        if col == 2:
            return self._resolve_path(e.to_curator_id)
        if col == 3:
            return f"{e.confidence:.2f}"
        if col == 4:
            return e.detected_by
        return None

    # -- helpers --------------------------------------------------------

    def _resolve_path(self, curator_id) -> str:
        """Cached file_repo lookup; fall back to the UUID string."""
        if curator_id in self._path_cache:
            return self._path_cache[curator_id]
        try:
            f = self._file_repo.get(curator_id)
            label = f.source_path if f else f"({curator_id})"
        except Exception:
            label = f"({curator_id})"
        self._path_cache[curator_id] = label
        return label


# ---------------------------------------------------------------------------
# MigrationJobTableModel — Migrate tab (v1.1.0 Tracer Phase 2 Session C1)
# ---------------------------------------------------------------------------


def _format_duration(seconds: float | None) -> str:
    """Compact duration string: '4.2s' / '1m 23s' / '2h 5m'."""
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s:02d}s"
    h, rem = divmod(int(seconds), 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m"


class MigrationJobTableModel(QAbstractTableModel):
    """Table model over recent migration jobs.

    Wraps :meth:`MigrationJobRepository.list_jobs` (most-recent first).

    Columns: Status / Src → Dst / Files / Copied / Failed / Bytes /
    Started / Duration

    Used by the v1.1.0 Migrate tab. Read-only in this iteration
    (Tracer Phase 2 Session C1); job lifecycle actions (Abort, Resume)
    are wired in Session C2.
    """

    DEFAULT_LIMIT: int = 50

    COLUMNS: tuple[str, ...] = (
        "Status", "Src → Dst", "Files", "Copied", "Failed",
        "Bytes", "Started", "Duration",
    )

    def __init__(
        self,
        migration_job_repo: MigrationJobRepository,
        *,
        limit: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._repo = migration_job_repo
        self._limit = limit if limit is not None else self.DEFAULT_LIMIT
        self._rows: list[MigrationJob] = []
        self.refresh()

    # -- public API -----------------------------------------------------

    def refresh(self) -> None:
        self.beginResetModel()
        try:
            self._rows = self._repo.list_jobs(limit=self._limit)
        except Exception:
            self._rows = []
        self.endResetModel()

    def job_at(self, row: int) -> MigrationJob | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- Qt protocol ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self._rows)):
            return None
        j = self._rows[row]
        col = index.column()
        if role == Qt.ToolTipRole:
            # Tooltip on Src → Dst shows the full src_root + dst_root paths.
            if col == 1:
                return f"{j.src_source_id}: {j.src_root}\n→ {j.dst_source_id}: {j.dst_root}"
            # Tooltip on Status shows the error if the job failed.
            if col == 0 and j.error:
                return j.error
            return None
        if role != Qt.DisplayRole:
            return None
        if col == 0:
            return j.status
        if col == 1:
            # Compact: src_id → dst_id; full paths in tooltip
            return f"{j.src_source_id} → {j.dst_source_id}"
        if col == 2:
            return j.files_total
        if col == 3:
            return j.files_copied
        if col == 4:
            return j.files_failed
        if col == 5:
            return _format_size(j.bytes_copied)
        if col == 6:
            return _format_dt(j.started_at)
        if col == 7:
            return _format_duration(j.duration_seconds)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        reverse = order == Qt.SortOrder.DescendingOrder

        def key(j: MigrationJob) -> Any:
            if column == 0:
                return j.status or ""
            if column == 1:
                return f"{j.src_source_id} → {j.dst_source_id}"
            if column == 2:
                return j.files_total
            if column == 3:
                return j.files_copied
            if column == 4:
                return j.files_failed
            if column == 5:
                return j.bytes_copied
            if column == 6:
                return j.started_at or datetime.min
            if column == 7:
                return j.duration_seconds or 0.0
            return j.started_at or datetime.min

        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key, reverse=reverse)
        self.layoutChanged.emit()


# ---------------------------------------------------------------------------
# MigrationProgressTableModel — Migrate tab detail pane
# ---------------------------------------------------------------------------


class MigrationProgressTableModel(QAbstractTableModel):
    """Table model over per-file rows for a single migration job.

    Wraps :meth:`MigrationJobRepository.query_progress(job_id)`.
    Job ID is settable via :meth:`set_job_id`; on the initial state
    (no job selected) the model is empty. Used as the detail pane in
    the v1.1.0 Migrate tab's master/detail layout.

    Columns: Status / Outcome / Src Path / Size / Verified Hash
    """

    COLUMNS: tuple[str, ...] = (
        "Status", "Outcome", "Src Path", "Size", "Verified Hash",
    )

    def __init__(
        self,
        migration_job_repo: MigrationJobRepository,
        *,
        job_id: UUID | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._repo = migration_job_repo
        self._job_id: UUID | None = job_id
        self._rows: list[MigrationProgress] = []
        self.refresh()

    # -- public API -----------------------------------------------------

    def set_job_id(self, job_id: UUID | None) -> None:
        """Re-point at a different job (or clear via None) and refresh."""
        self._job_id = job_id
        self.refresh()

    @property
    def job_id(self) -> UUID | None:
        return self._job_id

    def refresh(self) -> None:
        self.beginResetModel()
        try:
            if self._job_id is None:
                self._rows = []
            else:
                self._rows = self._repo.query_progress(self._job_id)
        except Exception:
            self._rows = []
        self.endResetModel()

    def progress_at(self, row: int) -> MigrationProgress | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- Qt protocol ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self._rows)):
            return None
        p = self._rows[row]
        col = index.column()
        if role == Qt.ToolTipRole:
            if col == 2:
                # Full src_path AND dst_path on tooltip (cell shows just src)
                return f"src: {p.src_path}\ndst: {p.dst_path}"
            if col == 1 and p.error:
                return p.error
            return None
        if role != Qt.DisplayRole:
            return None
        if col == 0:
            return p.status
        if col == 1:
            return p.outcome or ""
        if col == 2:
            return p.src_path
        if col == 3:
            return _format_size(p.size)
        if col == 4:
            h = p.verified_xxhash
            return (h[:12] + "…") if h and len(h) > 12 else (h or "")
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        reverse = order == Qt.SortOrder.DescendingOrder

        def key(p: MigrationProgress) -> Any:
            if column == 0:
                return p.status or ""
            if column == 1:
                return p.outcome or ""
            if column == 2:
                return p.src_path or ""
            if column == 3:
                return p.size
            if column == 4:
                return p.verified_xxhash or ""
            return p.src_path or ""

        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key, reverse=reverse)
        self.layoutChanged.emit()


__all__ = [
    "FileTableModel",
    "BundleTableModel",
    "TrashTableModel",
    "AuditLogTableModel",
    "ConfigTableModel",
    "ScanJobTableModel",
    "PendingReviewTableModel",
    "MigrationJobTableModel",
    "MigrationProgressTableModel",
]
