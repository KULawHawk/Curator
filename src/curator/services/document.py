"""Document metadata + destination-path service (Phase Gamma F4).

Phase Gamma Milestone Gamma-5 deliverable. Same shape as
:mod:`curator.services.music` and :mod:`curator.services.photo`:
extract metadata, propose a canonical destination, plug into
``OrganizeService.plan(organize_type=\"document\")``.

The default template is ``{year}/{year}-{month:02d}/{filename}`` —
year folder + year-month sub-folder + original filename. Mirrors the
photo template's date-based shape but coarser (year-month, not
year-month-day) because document workflows usually group by month
not by day.

Date resolution order:
    1. PDF embedded metadata (/CreationDate, then /ModDate)
    2. DOCX core.xml (dcterms:created, then dcterms:modified)
    3. Filename pattern matching (ISO ``2024-03-15``, compact
       ``20240315``, year-month ``2024-03``, year-only ``2024``)
    4. Filesystem mtime
    5. ``UNKNOWN_DATE`` placeholder

Step 3 (filename patterns) is important because document workflows
often encode dates in filenames (``Invoice 2024-03-15.pdf``,
``2024_Q1_report.docx``) even when no embedded metadata exists.
That pattern source typically beats mtime, which can be misleading
(a downloaded PDF's mtime is when you downloaded it, not when it
was authored).

The service is conservative about file types:
    * PDF: parsed with pypdf (lazy imported)
    * DOCX/XLSX/PPTX: parsed via stdlib zipfile + xml.etree
      (no python-docx dep needed for just core.xml)
    * Other extensions: filename + mtime only

Future v0.27+ additions:
    * EPUB / MOBI metadata
    * RTF / ODT date extraction
    * ``--type document`` with classification-aware sub-folders
      (Invoices/, Reports/, Letters/) using ClassificationService
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from loguru import logger

from curator.services.music import sanitize_path_component


# ---------------------------------------------------------------------------
# Document file extensions
# ---------------------------------------------------------------------------

DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({
    # Common documents
    ".pdf",
    ".doc",
    ".docx",
    ".odt",
    ".rtf",
    ".txt",
    ".md",
    ".rst",
    # E-books
    ".epub",
    ".mobi",
    ".azw",
    ".azw3",
    # Spreadsheets
    ".xls",
    ".xlsx",
    ".ods",
    ".csv",
    ".tsv",
    # Presentations
    ".ppt",
    ".pptx",
    ".odp",
    ".key",
    # Apple iWork
    ".pages",
    ".numbers",
})
"""Lowercased file extensions (with dot) that DocumentService will try to read."""


# Defaults for missing fields
UNKNOWN_DATE = "Unknown date"


# ---------------------------------------------------------------------------
# Filename date patterns
# ---------------------------------------------------------------------------

# Order matters — more specific patterns first so they match before the
# fallback (year-month and year-only at the end).
_FILENAME_DATE_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # ISO with separators: 2024-03-15, 2024_03_15, 2024.03.15
    (re.compile(r"(?<!\d)(?P<y>\d{4})[-_.](?P<mo>\d{1,2})[-_.](?P<d>\d{1,2})(?!\d)"), "ymd"),
    # Compact: 20240315 (must be exactly 8 digits, not embedded in a longer number)
    (re.compile(r"(?<!\d)(?P<y>\d{4})(?P<mo>\d{2})(?P<d>\d{2})(?!\d)"), "ymd"),
    # Year-month: 2024-03, 2024_03
    (re.compile(r"(?<!\d)(?P<y>\d{4})[-_.](?P<mo>\d{1,2})(?!\d)"), "ym"),
    # Year only: 2024 (with reasonable bounds 1900-2099)
    (re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})(?!\d)"), "y"),
)


def _parse_filename_date(name: str) -> tuple[datetime, str] | None:
    """Try to extract a date from a filename.

    Returns (datetime, granularity) where granularity is one of
    ``"ymd"``, ``"ym"``, ``"y"``. Day defaults to 1 for ym and y;
    month defaults to 1 for y. Returns None if no plausible date found.

    Year is bounded to 1900-2099 to avoid matching version numbers
    (e.g., "v1.2.0", "build_1234567890.txt").
    """
    if not name:
        return None
    for regex, granularity in _FILENAME_DATE_PATTERNS:
        m = regex.search(name)
        if m is None:
            continue
        try:
            year = int(m.group("y"))
            if not (1900 <= year <= 2099):
                continue
            month = int(m.group("mo")) if granularity in ("ymd", "ym") else 1
            day = int(m.group("d")) if granularity == "ymd" else 1
            if not (1 <= month <= 12):
                continue
            if not (1 <= day <= 31):
                continue
            return datetime(year, month, day), granularity
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# PDF date parsing
# ---------------------------------------------------------------------------

# PDF dates are formatted as "D:YYYYMMDDHHmmSSOHH'mm'" (PDF spec 7.9.4).
# The "D:" prefix and timezone are optional in practice.
_PDF_DATE_RE = re.compile(
    r"^D?:?(?P<y>\d{4})(?P<mo>\d{2})?(?P<d>\d{2})?"
    r"(?P<h>\d{2})?(?P<mi>\d{2})?(?P<s>\d{2})?"
)


def _parse_pdf_datetime(value: Any) -> datetime | None:
    """Parse a PDF /CreationDate or /ModDate string."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = _PDF_DATE_RE.match(s)
    if not m:
        return None
    try:
        year = int(m.group("y"))
        if not (1900 <= year <= 2099):
            return None
        month = int(m.group("mo") or 1)
        day = int(m.group("d") or 1)
        if not (1 <= month <= 12) or not (1 <= day <= 31):
            return None
        hour = int(m.group("h") or 0)
        minute = int(m.group("mi") or 0)
        second = int(m.group("s") or 0)
        hour = min(max(hour, 0), 23)
        minute = min(max(minute, 0), 59)
        second = min(max(second, 0), 59)
        return datetime(year, month, day, hour, minute, second)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# DocumentMetadata
# ---------------------------------------------------------------------------


@dataclass
class DocumentMetadata:
    """Structured metadata extracted from a document file."""

    created_at: datetime | None = None
    created_at_source: str | None = None  # "pdf_creation" | "pdf_mod" | "docx_created" | "docx_modified" | "filename_ymd" | "filename_ym" | "filename_y" | "mtime"
    title: str | None = None
    author: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_useful_date(self) -> bool:
        return self.created_at is not None

    @property
    def year_str(self) -> str:
        return f"{self.created_at.year:04d}" if self.created_at else UNKNOWN_DATE

    @property
    def year_month_str(self) -> str:
        if self.created_at is None:
            return UNKNOWN_DATE
        return self.created_at.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Backend availability
# ---------------------------------------------------------------------------


def _pypdf_available() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


# Office Open XML core properties namespace constants.
_OOXML_DCTERMS_NS = "{http://purl.org/dc/terms/}"
_OOXML_DC_NS = "{http://purl.org/dc/elements/1.1/}"


# ---------------------------------------------------------------------------
# DocumentService
# ---------------------------------------------------------------------------


class DocumentService:
    """Read document metadata + propose date-based destination paths."""

    DEFAULT_TEMPLATE = "{year}/{year_month}/{filename}"

    def __init__(self, template: str = DEFAULT_TEMPLATE) -> None:
        self.template = template

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_document_file(self, path: str | Path) -> bool:
        """True if ``path`` has a known document extension."""
        return Path(path).suffix.lower() in DOCUMENT_EXTENSIONS

    def read_metadata(self, path: str | Path) -> DocumentMetadata | None:
        """Extract :class:`DocumentMetadata` from a document file.

        Returns None on:
            * non-document extension
            * file doesn't exist

        Returns populated metadata even when format-specific parsing
        fails — the filename + mtime fallbacks ensure we always have
        something to organize by.
        """
        p = Path(path)
        if not self.is_document_file(p):
            return None
        if not p.exists():
            return None

        meta = DocumentMetadata()
        ext = p.suffix.lower()

        # 1. Format-specific parsing.
        if ext == ".pdf":
            self._read_pdf(p, meta)
        elif ext in (".docx", ".xlsx", ".pptx"):
            self._read_ooxml(p, meta)

        # 2. Filename-pattern fallback if no embedded date.
        if meta.created_at is None:
            parsed = _parse_filename_date(p.name)
            if parsed is not None:
                dt, granularity = parsed
                meta.created_at = dt
                meta.created_at_source = f"filename_{granularity}"

        # 3. mtime fallback as last resort.
        if meta.created_at is None:
            try:
                stat_mtime = p.stat().st_mtime
                meta.created_at = datetime.fromtimestamp(stat_mtime)
                meta.created_at_source = "mtime"
            except OSError as e:
                logger.debug(
                    "DocumentService: stat failed for {p}: {e}", p=p, e=e,
                )

        return meta

    def _read_pdf(self, p: Path, meta: DocumentMetadata) -> None:
        """Populate ``meta`` from PDF metadata where possible."""
        if not _pypdf_available():
            logger.debug(
                "DocumentService.read_pdf: pypdf not installed; "
                "install curator[organize] to enable document parsing."
            )
            return

        try:
            from pypdf import PdfReader
            from pypdf.errors import PdfReadError

            reader = PdfReader(str(p))
            info = reader.metadata
            if info is None:
                return
        except (PdfReadError, OSError, ValueError) as e:
            logger.debug("DocumentService: pypdf failed for {p}: {e}", p=p, e=e)
            return
        except Exception as e:  # noqa: BLE001 — pypdf throws assorted exceptions
            logger.debug("DocumentService: unexpected pypdf error for {p}: {e}", p=p, e=e)
            return

        # Try /CreationDate then /ModDate.
        for key, source_label in (
            ("/CreationDate", "pdf_creation"),
            ("/ModDate", "pdf_mod"),
        ):
            value = info.get(key)
            parsed = _parse_pdf_datetime(value)
            if parsed is not None:
                meta.created_at = parsed
                meta.created_at_source = source_label
                break

        # Title + author (best-effort).
        title = info.get("/Title")
        if title is not None:
            s = str(title).strip()
            meta.title = s if s else None

        author = info.get("/Author")
        if author is not None:
            s = str(author).strip()
            meta.author = s if s else None

        # Stash everything else for callers that want it.
        try:
            meta.raw = {str(k): str(v) for k, v in info.items()}
        except Exception:  # noqa: BLE001
            pass

    def _read_ooxml(self, p: Path, meta: DocumentMetadata) -> None:
        """Populate ``meta`` from Office Open XML core properties.

        DOCX/XLSX/PPTX are zip archives containing ``docProps/core.xml``
        with Dublin Core metadata. We only need stdlib zipfile +
        xml.etree, so no python-docx / openpyxl dependency.
        """
        try:
            with zipfile.ZipFile(p, "r") as zf:
                try:
                    core_xml = zf.read("docProps/core.xml")
                except KeyError:
                    # No core props in this archive.
                    return
                root = ET.fromstring(core_xml)
        except (zipfile.BadZipFile, ET.ParseError, OSError) as e:
            logger.debug(
                "DocumentService: OOXML read failed for {p}: {e}", p=p, e=e,
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "DocumentService: unexpected OOXML error for {p}: {e}",
                p=p, e=e,
            )
            return

        # Try dcterms:created then dcterms:modified.
        for tag, source_label in (
            (f"{_OOXML_DCTERMS_NS}created", "docx_created"),
            (f"{_OOXML_DCTERMS_NS}modified", "docx_modified"),
        ):
            elem = root.find(tag)
            if elem is None or not elem.text:
                continue
            # Office stores ISO 8601: "2024-03-15T14:30:00Z"
            text = elem.text.strip()
            try:
                # Strip the Z and any timezone suffix; treat as naive UTC.
                if text.endswith("Z"):
                    text = text[:-1]
                dt = datetime.fromisoformat(text)
                if 1900 <= dt.year <= 2099:
                    meta.created_at = dt
                    meta.created_at_source = source_label
                    break
            except ValueError:
                continue

        # Title + author from Dublin Core elements.
        title = root.find(f"{_OOXML_DC_NS}title")
        if title is not None and title.text:
            meta.title = title.text.strip() or None

        creator = root.find(f"{_OOXML_DC_NS}creator")
        if creator is not None and creator.text:
            meta.author = creator.text.strip() or None

    def propose_destination(
        self,
        metadata: DocumentMetadata,
        *,
        original_path: str | Path,
        target_root: str | Path,
    ) -> Path:
        """Apply the template and return the proposed destination."""
        original = Path(original_path)
        target = Path(target_root)

        year = sanitize_path_component(metadata.year_str, fallback=UNKNOWN_DATE)
        year_month = sanitize_path_component(
            metadata.year_month_str, fallback=UNKNOWN_DATE,
        )
        filename = sanitize_path_component(
            original.name,
            fallback=f"file{original.suffix.lower()}",
        )

        return target / year / year_month / filename


__all__ = [
    "DOCUMENT_EXTENSIONS",
    "DocumentMetadata",
    "DocumentService",
    "UNKNOWN_DATE",
]
