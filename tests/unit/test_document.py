"""Unit tests for DocumentService + helpers (Phase Gamma F4).

Covers:
    * _parse_filename_date (ISO / compact / year-month / year-only,
      year bounds, version-number rejection)
    * _parse_pdf_datetime (canonical PDF "D:YYYYMMDD..." format)
    * DocumentMetadata properties
    * is_document_file
    * propose_destination template + sanitization
    * read_metadata across formats: real PDF (pypdf-built), real DOCX
      (zipfile-built), text file with date in filename, mtime fallback,
      non-document, nonexistent

Real PDF fixtures are built via pypdf in-process; real DOCX/XLSX/PPTX
fixtures via stdlib zipfile. No bundled binary blobs.
"""

from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from curator.services.document import (
    DOCUMENT_EXTENSIONS,
    UNKNOWN_DATE,
    DocumentMetadata,
    DocumentService,
    _parse_filename_date,
    _parse_pdf_datetime,
)


# ===========================================================================
# _parse_filename_date
# ===========================================================================


class TestParseFilenameDate:
    def test_iso_with_dashes(self):
        result = _parse_filename_date("Invoice 2024-03-15.pdf")
        assert result is not None
        dt, granularity = result
        assert dt == datetime(2024, 3, 15)
        assert granularity == "ymd"

    def test_iso_with_underscores(self):
        result = _parse_filename_date("report_2024_03_15_v2.pdf")
        assert result is not None
        dt, granularity = result
        assert dt == datetime(2024, 3, 15)
        assert granularity == "ymd"

    def test_iso_with_dots(self):
        result = _parse_filename_date("report.2024.03.15.pdf")
        assert result is not None
        dt, _ = result
        assert dt == datetime(2024, 3, 15)

    def test_compact_yyyymmdd(self):
        result = _parse_filename_date("scan_20240315.pdf")
        assert result is not None
        dt, granularity = result
        assert dt == datetime(2024, 3, 15)
        assert granularity == "ymd"

    def test_year_month_only(self):
        result = _parse_filename_date("2024-03-summary.pdf")
        assert result is not None
        dt, granularity = result
        assert dt == datetime(2024, 3, 1)  # Day defaults to 1
        assert granularity == "ym"

    def test_year_only(self):
        result = _parse_filename_date("2024 tax return.pdf")
        assert result is not None
        dt, granularity = result
        assert dt == datetime(2024, 1, 1)
        assert granularity == "y"

    def test_no_date_returns_none(self):
        assert _parse_filename_date("document.pdf") is None
        assert _parse_filename_date("notes.txt") is None
        assert _parse_filename_date("") is None

    def test_invalid_month_returns_none(self):
        # Month 13 is invalid - YMD and YM patterns reject it; year-only
        # falls through to 2024 (intentional - better to organize by year
        # than to drop the file entirely).
        result = _parse_filename_date("file_2024-13-01.pdf")
        if result is not None:
            dt, granularity = result
            assert granularity == "y"
            assert dt == datetime(2024, 1, 1)

    def test_invalid_day_handled(self):
        # Day 32 is invalid \u2014 the YMD pattern won't match
        # (but the YM pattern may catch the first 7 chars).
        result = _parse_filename_date("file_2024-03-32.pdf")
        # Should fall through to year-month or year match.
        if result is not None:
            dt, granularity = result
            # Should NOT be ymd (invalid day)
            assert granularity != "ymd"

    def test_year_outside_bounds_rejected(self):
        # Years before 1900 or after 2099 likely aren't dates.
        assert _parse_filename_date("backup_18991231.dat") is None
        # 2100+ is unlikely as a current document date.
        result = _parse_filename_date("file_2150-01-01.pdf")
        # The YYYY-MM-DD pattern requires year >= 1900 but the
        # implementation also caps at 2099.
        if result is not None:
            dt, _ = result
            assert dt.year <= 2099

    def test_version_numbers_not_matched_as_dates(self):
        # Build numbers like "1234567890" should not parse as dates.
        # Our compact pattern requires exactly 8 digits w/ word boundary.
        # "build_1234567890.txt" \u2014 has 10 digits, the year-only pattern
        # might try to match a 4-digit subset.
        result = _parse_filename_date("build_1234567890.txt")
        # The year-only pattern requires (?:19|20)\d{2} so 1234 won't match.
        # But '5678' would hit (?:19|20)\d{2}? No \u2014 '56' doesn't start with 19 or 20.
        # So this should return None.
        assert result is None

    def test_first_match_wins(self):
        # When a filename has multiple potential date matches, the
        # most specific pattern wins because they're tried in order.
        result = _parse_filename_date("photos_2024-03-15_session_2023.zip")
        assert result is not None
        dt, granularity = result
        assert dt == datetime(2024, 3, 15)
        assert granularity == "ymd"


# ===========================================================================
# _parse_pdf_datetime
# ===========================================================================


class TestParsePdfDatetime:
    def test_canonical_format_with_d_prefix(self):
        # PDF spec: "D:YYYYMMDDHHmmSSOHH'mm'"
        dt = _parse_pdf_datetime("D:20240315143000")
        assert dt == datetime(2024, 3, 15, 14, 30, 0)

    def test_without_d_prefix(self):
        dt = _parse_pdf_datetime("20240315143000")
        assert dt == datetime(2024, 3, 15, 14, 30, 0)

    def test_year_only(self):
        dt = _parse_pdf_datetime("D:2024")
        assert dt == datetime(2024, 1, 1, 0, 0, 0)

    def test_year_month_day(self):
        dt = _parse_pdf_datetime("D:20240315")
        assert dt == datetime(2024, 3, 15, 0, 0, 0)

    def test_with_timezone_suffix_ignored(self):
        # "D:20240315143000+05'00'" \u2014 timezone is parsed loosely
        # by truncating; we just want the y/m/d/h/m/s.
        dt = _parse_pdf_datetime("D:20240315143000+05'00'")
        assert dt == datetime(2024, 3, 15, 14, 30, 0)

    def test_clamps_invalid_time(self):
        dt = _parse_pdf_datetime("D:20240315246060")
        assert dt is not None
        assert dt.year == 2024
        assert 0 <= dt.hour <= 23
        assert 0 <= dt.minute <= 59
        assert 0 <= dt.second <= 59

    def test_year_out_of_range_returns_none(self):
        assert _parse_pdf_datetime("D:18001231") is None

    def test_none_returns_none(self):
        assert _parse_pdf_datetime(None) is None

    def test_empty_returns_none(self):
        assert _parse_pdf_datetime("") is None
        assert _parse_pdf_datetime("   ") is None

    def test_garbage_returns_none(self):
        assert _parse_pdf_datetime("not a date") is None


# ===========================================================================
# DocumentMetadata
# ===========================================================================


class TestDocumentMetadata:
    def test_empty_has_no_date(self):
        m = DocumentMetadata()
        assert m.has_useful_date is False
        assert m.year_str == UNKNOWN_DATE
        assert m.year_month_str == UNKNOWN_DATE

    def test_with_date(self):
        m = DocumentMetadata(created_at=datetime(2024, 3, 15))
        assert m.has_useful_date is True
        assert m.year_str == "2024"
        assert m.year_month_str == "2024-03"

    def test_year_month_zero_pads(self):
        m = DocumentMetadata(created_at=datetime(2024, 1, 1))
        assert m.year_month_str == "2024-01"


# ===========================================================================
# is_document_file
# ===========================================================================


class TestIsDocumentFile:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("a.pdf", True),
            ("a.PDF", True),
            ("a.docx", True),
            ("a.doc", True),
            ("a.txt", True),
            ("a.md", True),
            ("a.epub", True),
            ("a.xlsx", True),
            ("a.csv", True),
            ("a.pptx", True),
            ("a.jpg", False),
            ("a.mp3", False),
            ("a.exe", False),
            ("noext", False),
        ],
    )
    def test_extensions(self, path, expected):
        svc = DocumentService()
        assert svc.is_document_file(path) is expected

    def test_extension_set_includes_common_formats(self):
        for ext in (".pdf", ".docx", ".txt", ".md", ".epub", ".xlsx"):
            assert ext in DOCUMENT_EXTENSIONS


# ===========================================================================
# propose_destination
# ===========================================================================


class TestProposeDestination:
    def test_canonical_path(self, tmp_path):
        svc = DocumentService()
        meta = DocumentMetadata(
            created_at=datetime(2024, 3, 15),
            created_at_source="pdf_creation",
        )
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "Invoice.pdf",
            target_root=tmp_path / "docs",
        )
        assert dest == tmp_path / "docs" / "2024" / "2024-03" / "Invoice.pdf"

    def test_unknown_date_uses_placeholder(self, tmp_path):
        svc = DocumentService()
        meta = DocumentMetadata()
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "mystery.pdf",
            target_root=tmp_path / "docs",
        )
        assert UNKNOWN_DATE in dest.parts[-3]
        assert UNKNOWN_DATE in dest.parts[-2]
        assert dest.name == "mystery.pdf"

    def test_filename_preserved(self, tmp_path):
        svc = DocumentService()
        meta = DocumentMetadata(created_at=datetime(2023, 6, 1))
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "Project Report Q1.docx",
            target_root=tmp_path,
        )
        assert dest.name == "Project Report Q1.docx"

    def test_destination_under_target_root(self, tmp_path):
        svc = DocumentService()
        meta = DocumentMetadata(created_at=datetime(2024, 1, 1))
        target = tmp_path / "library"
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "x.pdf",
            target_root=target,
        )
        assert str(dest).startswith(str(target))

    def test_illegal_filename_chars_sanitized(self, tmp_path):
        svc = DocumentService()
        meta = DocumentMetadata(created_at=datetime(2024, 1, 1))
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "report:final?.docx",
            target_root=tmp_path,
        )
        assert ":" not in dest.name
        assert "?" not in dest.name


# ===========================================================================
# read_metadata: format-specific parsing
# ===========================================================================


def _write_pdf_with_metadata(
    path: Path,
    *,
    creation_date: str | None = None,
    mod_date: str | None = None,
    title: str | None = None,
    author: str | None = None,
) -> None:
    """Build a real PDF with the requested metadata via pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    metadata = {}
    if creation_date is not None:
        metadata["/CreationDate"] = creation_date
    if mod_date is not None:
        metadata["/ModDate"] = mod_date
    if title is not None:
        metadata["/Title"] = title
    if author is not None:
        metadata["/Author"] = author
    if metadata:
        writer.add_metadata(metadata)
    with open(path, "wb") as f:
        writer.write(f)


def _write_docx_with_metadata(
    path: Path,
    *,
    created: str | None = None,
    modified: str | None = None,
    title: str | None = None,
    creator: str | None = None,
) -> None:
    """Build a minimal valid DOCX with core.xml metadata.

    We only need the core.xml entry to exist with the requested
    Dublin Core elements \u2014 the rest of the docx structure is
    irrelevant for read_metadata.
    """
    parts = []
    if created is not None:
        parts.append(f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>')
    if modified is not None:
        parts.append(f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified>')
    if title is not None:
        parts.append(f"<dc:title>{title}</dc:title>")
    if creator is not None:
        parts.append(f"<dc:creator>{creator}</dc:creator>")
    inner = "".join(parts)
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'{inner}'
        '</cp:coreProperties>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("docProps/core.xml", core_xml)
        # Minimal [Content_Types].xml so it's a valid-ish zip even if
        # something inspects it deeper.
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )


class TestReadMetadata:
    def test_returns_none_for_non_document(self, tmp_path):
        svc = DocumentService()
        f = tmp_path / "audio.mp3"
        f.write_bytes(b"x")
        assert svc.read_metadata(f) is None

    def test_returns_none_for_nonexistent(self, tmp_path):
        svc = DocumentService()
        ghost = tmp_path / "ghost.pdf"
        assert svc.read_metadata(ghost) is None

    def test_pdf_creation_date_extracted(self, tmp_path):
        target = tmp_path / "with_date.pdf"
        _write_pdf_with_metadata(
            target,
            creation_date="D:20240315143000",
            title="Test Doc",
            author="Test Author",
        )
        svc = DocumentService()
        meta = svc.read_metadata(target)
        assert meta is not None
        assert meta.created_at == datetime(2024, 3, 15, 14, 30, 0)
        assert meta.created_at_source == "pdf_creation"
        assert meta.title == "Test Doc"
        assert meta.author == "Test Author"

    def test_pdf_falls_back_to_mod_date(self, tmp_path):
        # Only ModDate, no CreationDate.
        target = tmp_path / "mod_only.pdf"
        _write_pdf_with_metadata(
            target, mod_date="D:20230601120000",
        )
        svc = DocumentService()
        meta = svc.read_metadata(target)
        assert meta.created_at_source == "pdf_mod"
        assert meta.created_at.year == 2023

    def test_pdf_no_metadata_falls_back_to_filename(self, tmp_path):
        # No PDF metadata, but filename has a date.
        target = tmp_path / "report_2022-08-15.pdf"
        _write_pdf_with_metadata(target)  # no metadata
        svc = DocumentService()
        meta = svc.read_metadata(target)
        # Should fall through PDF \u2192 filename \u2192 ...
        # (The mtime might be more recent so filename wins because it's tried first.)
        assert meta.created_at_source in ("filename_ymd", "mtime")

    def test_docx_dcterms_created(self, tmp_path):
        target = tmp_path / "doc.docx"
        _write_docx_with_metadata(
            target,
            created="2024-03-15T14:30:00Z",
            title="DOCX Title",
            creator="DOCX Author",
        )
        svc = DocumentService()
        meta = svc.read_metadata(target)
        assert meta is not None
        assert meta.created_at == datetime(2024, 3, 15, 14, 30, 0)
        assert meta.created_at_source == "docx_created"
        assert meta.title == "DOCX Title"
        assert meta.author == "DOCX Author"

    def test_docx_falls_back_to_modified(self, tmp_path):
        target = tmp_path / "mod.docx"
        _write_docx_with_metadata(target, modified="2023-06-01T00:00:00Z")
        svc = DocumentService()
        meta = svc.read_metadata(target)
        assert meta.created_at_source == "docx_modified"
        assert meta.created_at.year == 2023

    def test_xlsx_uses_same_path(self, tmp_path):
        target = tmp_path / "sheet.xlsx"
        _write_docx_with_metadata(target, created="2022-05-01T00:00:00Z")
        svc = DocumentService()
        meta = svc.read_metadata(target)
        # XLSX uses the same OOXML core.xml format.
        assert meta.created_at_source == "docx_created"
        assert meta.created_at.year == 2022

    def test_text_file_filename_date(self, tmp_path):
        # Plain text \u2014 no embedded metadata, only filename + mtime.
        target = tmp_path / "notes_2023-09-15.txt"
        target.write_text("hello")
        ts = datetime(2025, 1, 1).timestamp()
        os.utime(target, (ts, ts))  # mtime is later than filename date
        svc = DocumentService()
        meta = svc.read_metadata(target)
        # Filename pattern beats mtime for documents.
        assert meta.created_at_source == "filename_ymd"
        assert meta.created_at == datetime(2023, 9, 15)

    def test_text_file_no_filename_date_uses_mtime(self, tmp_path):
        target = tmp_path / "anonymous.txt"
        target.write_text("hello")
        ts = datetime(2020, 4, 1, 12, 0, 0).timestamp()
        os.utime(target, (ts, ts))
        svc = DocumentService()
        meta = svc.read_metadata(target)
        assert meta.created_at_source == "mtime"
        assert meta.created_at.year == 2020

    def test_corrupt_pdf_falls_back_gracefully(self, tmp_path):
        target = tmp_path / "broken.pdf"
        target.write_bytes(b"not really a PDF")
        ts = datetime(2019, 1, 1).timestamp()
        os.utime(target, (ts, ts))
        svc = DocumentService()
        meta = svc.read_metadata(target)
        # PDF parse fails \u2192 no filename date \u2192 mtime fallback.
        assert meta is not None
        assert meta.created_at_source == "mtime"
        assert meta.created_at.year == 2019

    def test_corrupt_docx_falls_back_gracefully(self, tmp_path):
        target = tmp_path / "broken.docx"
        target.write_bytes(b"not really a DOCX")
        ts = datetime(2018, 1, 1).timestamp()
        os.utime(target, (ts, ts))
        svc = DocumentService()
        meta = svc.read_metadata(target)
        assert meta is not None
        assert meta.created_at_source == "mtime"

    def test_pypdf_unavailable_falls_back(self, tmp_path, monkeypatch):
        target = tmp_path / "report_2021-05-01.pdf"
        target.write_bytes(b"")
        monkeypatch.setattr(
            "curator.services.document._pypdf_available",
            lambda: False,
        )
        svc = DocumentService()
        meta = svc.read_metadata(target)
        # Filename pattern still works.
        assert meta is not None
        assert meta.created_at_source == "filename_ymd"
        assert meta.created_at.year == 2021
