"""Focused coverage tests for services/document.py.

Sub-ship v1.7.106 (FINAL of the Coverage Sweep arc) — closes 22
uncovered lines + 4 partial branches.

Targets:
* Line 143-144: `_parse_filename_date` except (TypeError, ValueError)
* Line 177: `_parse_pdf_datetime` month/day out of range
* Lines 185-186: `_parse_pdf_datetime` int() failure path
* Lines 227-228: `_pypdf_available` ImportError
* Lines 298-299: read_metadata mtime stat OSError
* Line 321: `_read_pdf` info is None → return
* Lines 325-327: `_read_pdf` generic Exception
* Lines 355-356: `_read_pdf` raw dict construction failure
* Lines 369-371: `_read_ooxml` missing core.xml (KeyError)
* Lines 378-383: `_read_ooxml` generic Exception
* Branch 386->408: dcterms loop falls through
* Branch 397->399: text without trailing Z
* Branch 400->386: dt.year out of range → continue
* Lines 404-405: ISO parse ValueError → continue
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from curator.services.document import (
    DocumentMetadata,
    DocumentService,
    _parse_filename_date,
    _parse_pdf_datetime,
    _pypdf_available,
)


# ---------------------------------------------------------------------------
# _parse_filename_date defensive (143-144)
# ---------------------------------------------------------------------------


def test_parse_filename_date_invalid_components_continues(monkeypatch):
    # Lines 143-144: filename matches the regex but int() raises a
    # TypeError/ValueError → continue to next pattern.
    #
    # The production regex captures only digits, so int() never fails
    # naturally. Inject a custom pattern via monkeypatch that captures
    # non-digit text in (?P<y>) → int() raises ValueError.
    import re
    import curator.services.document as doc_mod

    custom_patterns = [
        (re.compile(r"^(?P<y>[A-Z]+)-(?P<mo>[A-Z]+)-(?P<d>[A-Z]+)$"), "ymd"),
        # No fallback - the original list comes after; we replace fully.
    ]
    monkeypatch.setattr(doc_mod, "_FILENAME_DATE_PATTERNS", custom_patterns)
    # "ZZZZ-AA-BB" matches the alphabetic pattern; int("ZZZZ") raises ValueError.
    result = _parse_filename_date("ZZZZ-AA-BB")
    assert result is None  # all patterns failed → None


# ---------------------------------------------------------------------------
# _parse_pdf_datetime defensives (177, 185-186)
# ---------------------------------------------------------------------------


def test_parse_pdf_datetime_invalid_month_returns_none():
    # Line 177: month or day out of valid range → return None.
    # PDF date with month=13: "D:20240013000000" parses year=2024, month=00,
    # day=13. month=0 fails `1 <= month <= 12` → return None.
    assert _parse_pdf_datetime("D:20240013000000") is None


def test_parse_pdf_datetime_invalid_calendar_date_returns_none():
    # Lines 185-186: datetime(year, month, day, ...) raises ValueError
    # for invalid calendar combinations (e.g. February 31) → caught
    # by except → return None.
    # The regex's day-of-31 check at line 176 allows day=31 regardless
    # of month, so Feb 31 makes it past the range checks and into the
    # datetime() constructor, which raises.
    assert _parse_pdf_datetime("D:20240231000000") is None  # Feb 31 invalid
    assert _parse_pdf_datetime("D:20240431000000") is None  # Apr 31 invalid


# ---------------------------------------------------------------------------
# _pypdf_available ImportError (227-228)
# ---------------------------------------------------------------------------


def test_pypdf_available_returns_false_when_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)
    assert _pypdf_available() is False


# ---------------------------------------------------------------------------
# read_metadata mtime stat OSError (298-299)
# ---------------------------------------------------------------------------


def test_read_metadata_mtime_stat_oserror_swallowed(tmp_path, monkeypatch):
    # Lines 298-299: stat() raises OSError → logger.debug; meta.created_at
    # stays None. Use a .pdf file (so format-specific parsing is
    # attempted but produces no date), then make stat raise.
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"not a real pdf - format parsing will skip")

    svc = DocumentService()
    # Patch pdf reading + filename parsing to no-op so read_metadata
    # falls through to the mtime fallback.
    monkeypatch.setattr(svc, "_read_pdf", lambda p, m: None)
    monkeypatch.setattr(
        "curator.services.document._parse_filename_date",
        lambda name: None,
    )

    # Path.exists() internally calls stat() in CPython 3.13, so a blanket
    # stat block would prevent read_metadata from passing the existence
    # check. Use a call counter: the FIRST stat (from .exists()) returns
    # normally; the SECOND stat (the mtime fallback at line 295) raises.
    orig_stat = Path.stat
    target_stat_calls = [0]

    def selective_boom_stat(self, *args, **kwargs):
        if self == target:
            target_stat_calls[0] += 1
            if target_stat_calls[0] >= 2:
                raise OSError("stat blocked")
        return orig_stat(self, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", selective_boom_stat)

    meta = svc.read_metadata(target)
    # mtime fallback failed → created_at stays None.
    assert meta is not None
    assert meta.created_at is None


# ---------------------------------------------------------------------------
# _read_pdf info-is-None (321) + unexpected Exception (325-327)
# ---------------------------------------------------------------------------


def test_read_pdf_info_is_none_returns_silently(tmp_path, monkeypatch):
    # Line 321: reader.metadata returns None → `return` (no fields
    # populated on meta).
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4 fake")

    fake_reader = MagicMock()
    fake_reader.metadata = None

    monkeypatch.setattr("pypdf.PdfReader", lambda path: fake_reader)

    svc = DocumentService()
    meta = DocumentMetadata()
    svc._read_pdf(target, meta)
    # No changes to meta.
    assert meta.created_at is None
    assert meta.title is None


def test_read_pdf_generic_exception_swallowed(tmp_path, monkeypatch):
    # Lines 325-327: PdfReader raises an unexpected exception (not in
    # the PdfReadError/OSError/ValueError set) → caught by the generic
    # except → return silently.
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4 fake")

    def boom_reader(path):
        raise RuntimeError("unexpected pypdf error")

    monkeypatch.setattr("pypdf.PdfReader", boom_reader)

    svc = DocumentService()
    meta = DocumentMetadata()
    svc._read_pdf(target, meta)  # must not raise
    assert meta.created_at is None


def test_read_pdf_raw_dict_construction_exception_swallowed(
    tmp_path, monkeypatch,
):
    # Lines 355-356: dict comprehension over info.items() raises →
    # caught with `pass`. Force by making info.items() raise.
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4 fake")

    bad_info = MagicMock()
    bad_info.get = lambda key: None  # all .get() calls return None
    bad_info.items.side_effect = RuntimeError("items failed")

    fake_reader = MagicMock()
    fake_reader.metadata = bad_info

    monkeypatch.setattr("pypdf.PdfReader", lambda path: fake_reader)

    svc = DocumentService()
    meta = DocumentMetadata()
    svc._read_pdf(target, meta)  # must not raise
    # Raw dict assignment was skipped; meta.raw stays empty.
    assert meta.raw == {}


# ---------------------------------------------------------------------------
# _read_ooxml missing core.xml (369-371) + unexpected exception (378-383)
# ---------------------------------------------------------------------------


def test_read_ooxml_missing_core_xml_returns(tmp_path):
    # Lines 369-371: archive has no docProps/core.xml → KeyError →
    # caught with `return`.
    target = tmp_path / "doc.docx"
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr("other.txt", "no core props here")

    svc = DocumentService()
    meta = DocumentMetadata()
    svc._read_ooxml(target, meta)
    assert meta.created_at is None
    assert meta.title is None


def test_read_ooxml_generic_exception_swallowed(tmp_path, monkeypatch):
    # Lines 378-383: ZipFile constructor raises an unexpected exception
    # (not BadZipFile/ParseError/OSError) → caught with logger.debug.
    target = tmp_path / "doc.docx"
    target.write_bytes(b"not a zip")

    def boom_zipfile(*args, **kwargs):
        raise RuntimeError("unexpected zip error")

    monkeypatch.setattr(zipfile, "ZipFile", boom_zipfile)

    svc = DocumentService()
    meta = DocumentMetadata()
    svc._read_ooxml(target, meta)  # must not raise


# ---------------------------------------------------------------------------
# OOXML dcterms loop branches (386->408, 397->399, 400->386, 404-405)
# ---------------------------------------------------------------------------


def _make_minimal_docx(path: Path, core_xml_bytes: bytes) -> None:
    """Write a minimal .docx with the given core.xml content."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("docProps/core.xml", core_xml_bytes)


def test_read_ooxml_no_dcterms_dates_falls_through_to_title(tmp_path):
    # Branch 386->408: dcterms:created and dcterms:modified both
    # absent → loop completes without break → fall through to title
    # extraction at line 408.
    core_xml = b"""<?xml version="1.0"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>Test Document</dc:title>
  <dc:creator>Test Author</dc:creator>
</cp:coreProperties>"""
    target = tmp_path / "doc.docx"
    _make_minimal_docx(target, core_xml)

    svc = DocumentService()
    meta = DocumentMetadata()
    svc._read_ooxml(target, meta)
    assert meta.created_at is None
    assert meta.title == "Test Document"
    assert meta.author == "Test Author"


def test_read_ooxml_iso_date_without_z_suffix(tmp_path):
    # Branch 397->399: text doesn't end with "Z" → skip the [:-1]
    # strip → parse as-is.
    core_xml = b"""<?xml version="1.0"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dcterms="http://purl.org/dc/terms/">
  <dcterms:created>2024-03-15T14:30:00</dcterms:created>
</cp:coreProperties>"""
    target = tmp_path / "doc.docx"
    _make_minimal_docx(target, core_xml)

    svc = DocumentService()
    meta = DocumentMetadata()
    svc._read_ooxml(target, meta)
    assert meta.created_at is not None
    assert meta.created_at.year == 2024
    assert meta.created_at.month == 3
    assert meta.created_at_source == "docx_created"


def test_read_ooxml_iso_year_out_of_range_continues(tmp_path):
    # Branch 400->386: dt parsed but year not in [1900, 2099] →
    # `if 1900 <= dt.year <= 2099` is False → fall through (no
    # assignment) → continue to next iteration of the dcterms loop.
    # Provide a created with year 1800 and a modified with year 2024;
    # the modified should win because created is skipped.
    core_xml = b"""<?xml version="1.0"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dcterms="http://purl.org/dc/terms/">
  <dcterms:created>1800-01-01T00:00:00Z</dcterms:created>
  <dcterms:modified>2024-06-15T10:00:00Z</dcterms:modified>
</cp:coreProperties>"""
    target = tmp_path / "doc.docx"
    _make_minimal_docx(target, core_xml)

    svc = DocumentService()
    meta = DocumentMetadata()
    svc._read_ooxml(target, meta)
    assert meta.created_at is not None
    assert meta.created_at.year == 2024
    assert meta.created_at_source == "docx_modified"


def test_read_ooxml_iso_unparseable_date_continues(tmp_path):
    # Lines 404-405: datetime.fromisoformat raises ValueError → except
    # → continue to next iteration. Use unparseable created, valid
    # modified.
    core_xml = b"""<?xml version="1.0"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dcterms="http://purl.org/dc/terms/">
  <dcterms:created>this-is-not-a-date</dcterms:created>
  <dcterms:modified>2024-06-15T10:00:00Z</dcterms:modified>
</cp:coreProperties>"""
    target = tmp_path / "doc.docx"
    _make_minimal_docx(target, core_xml)

    svc = DocumentService()
    meta = DocumentMetadata()
    svc._read_ooxml(target, meta)
    assert meta.created_at is not None
    assert meta.created_at_source == "docx_modified"
