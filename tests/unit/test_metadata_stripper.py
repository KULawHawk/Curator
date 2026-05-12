"""Tests for v1.7.56: metadata_stripper.py coverage lift (Tier 3).

Backstory: v1.7.51's coverage baseline showed metadata_stripper.py at
24.66% -- second weakest after pii_scanner. pii_scanner closed in
v1.7.55 (22% -> 98%); this ship targets metadata_stripper next.

The module strips embedded metadata from images, DOCX, and PDF files
during privacy-preserving exports. Tests cover:

  * **Enum + dataclass shapes** (TestStripOutcome, TestStripResult,
    TestStripReport) -- value enumeration, count aggregation, duration
  * **Format-specific strippers** -- TestStripImage (Pillow re-save),
    TestStripDocx (OOXML zip), TestStripPdf (pypdf re-emit)
  * **Dispatch logic** (TestStripFileDispatch) -- extension routing,
    unknown-type passthrough, error paths
  * **Directory walk** (TestStripDirectory) -- mirror structure,
    recursive vs flat, extension whitelist, missing directories

Fixtures build real binary files via Pillow + pypdf + zipfile so the
test exercises the actual code paths the production code uses. No
mocking of stdlib internals; pytest's tmp_path fixture provides
clean per-test temp directories.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from curator.services.metadata_stripper import (
    MetadataStripper,
    StripOutcome,
    StripReport,
    StripResult,
    _EMPTY_APP_XML,
    _EMPTY_CORE_XML,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stripper():
    """Default-config MetadataStripper."""
    return MetadataStripper()


def _make_jpeg_with_exif(path: Path, *, size=(8, 8)) -> Path:
    """Write a JPEG with REAL EXIF metadata using Pillow.

    Uses Pillow's getexif() to construct a properly-structured EXIF
    block; Pillow's `_getexif()` recognizes this on read-back.
    """
    img = Image.new("RGB", size, color=(128, 64, 200))
    exif = img.getexif()
    # 0x010F = Make, 0x0110 = Model, 0x9003 = DateTimeOriginal
    exif[0x010F] = "TestCamera Co"
    exif[0x0110] = "TestModel X1"
    exif[0x9003] = "2026:05:12 12:00:00"
    img.save(path, format="JPEG", exif=exif)
    return path


def _make_png_with_text(path: Path, *, size=(8, 8)) -> Path:
    """Write a PNG with tEXt chunks via PngInfo."""
    from PIL.PngImagePlugin import PngInfo
    img = Image.new("RGB", size, color=(50, 100, 150))
    pnginfo = PngInfo()
    pnginfo.add_text("Author", "Test Author")
    pnginfo.add_text("Description", "Test Description")
    img.save(path, format="PNG", pnginfo=pnginfo)
    return path


def _make_minimal_docx(path: Path, *, with_custom: bool = False) -> Path:
    """Build a minimal OOXML zip with docProps/core.xml + app.xml.

    Doesn't need to be a Word-openable file; just needs the right
    archive structure for the stripper to find the metadata entries.
    """
    core_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:creator>Alice Test</dc:creator>
<cp:lastModifiedBy>Alice Test</cp:lastModifiedBy>
<cp:revision>5</cp:revision>
</cp:coreProperties>"""

    app_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
<Application>Microsoft Word</Application>
<Company>Test Co</Company>
<AppVersion>16.0000</AppVersion>
</Properties>"""

    custom_xml = b"""<?xml version="1.0"?><Properties></Properties>"""

    content_types = b"""<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="xml" ContentType="application/xml"/>
</Types>"""

    document_xml = b"""<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>Hello world</w:t></w:r></w:p></w:body>
</w:document>"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("docProps/app.xml", app_xml)
        if with_custom:
            zf.writestr("docProps/custom.xml", custom_xml)
        zf.writestr("word/document.xml", document_xml)
    return path


def _make_minimal_pdf(path: Path) -> Path:
    """Build a small PDF with author/title metadata via pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    # Add one blank page
    writer.add_blank_page(width=200, height=200)
    # Add metadata that the stripper should remove
    writer.add_metadata({
        "/Author": "Alice Test",
        "/Title": "Test Document",
        "/Producer": "Test Suite",
        "/Creator": "v1.7.56 fixture",
    })
    with open(path, "wb") as f:
        writer.write(f)
    return path


# ---------------------------------------------------------------------------
# StripOutcome / StripResult / StripReport shapes
# ---------------------------------------------------------------------------


class TestStripOutcome:
    def test_outcome_values(self):
        assert StripOutcome.STRIPPED == "stripped"
        assert StripOutcome.PASSTHROUGH == "passthrough"
        assert StripOutcome.SKIPPED == "skipped"
        assert StripOutcome.FAILED == "failed"


class TestStripResult:
    def test_basic_construction(self):
        r = StripResult(
            source="/in.jpg", destination="/out.jpg",
            outcome=StripOutcome.STRIPPED,
            bytes_in=100, bytes_out=80,
            metadata_fields_removed=["exif", "xmp"],
        )
        assert r.source == "/in.jpg"
        assert r.bytes_in == 100
        assert "exif" in r.metadata_fields_removed

    def test_failed_has_no_destination(self):
        r = StripResult(
            source="/in.jpg", destination=None,
            outcome=StripOutcome.FAILED,
            error="broken",
        )
        assert r.destination is None
        assert r.error == "broken"


class TestStripReport:
    """v1.7.56: aggregate-count properties."""

    def _mk_result(self, outcome: StripOutcome) -> StripResult:
        return StripResult(
            source="/x", destination="/y" if outcome != StripOutcome.FAILED else None,
            outcome=outcome,
        )

    def test_empty_report_zero_counts(self):
        r = StripReport(started_at=datetime(2026, 1, 1, 12, 0, 0))
        assert r.total_count == 0
        assert r.stripped_count == 0
        assert r.passthrough_count == 0
        assert r.skipped_count == 0
        assert r.failed_count == 0

    def test_duration_zero_when_not_completed(self):
        r = StripReport(started_at=datetime(2026, 1, 1, 12, 0, 0))
        assert r.duration_seconds == 0.0

    def test_duration_computed_when_completed(self):
        r = StripReport(
            started_at=datetime(2026, 1, 1, 12, 0, 0),
            completed_at=datetime(2026, 1, 1, 12, 0, 30),
        )
        assert r.duration_seconds == 30.0

    def test_mixed_outcome_counts(self):
        r = StripReport(started_at=datetime(2026, 1, 1, 12, 0, 0))
        r.results.append(self._mk_result(StripOutcome.STRIPPED))
        r.results.append(self._mk_result(StripOutcome.STRIPPED))
        r.results.append(self._mk_result(StripOutcome.PASSTHROUGH))
        r.results.append(self._mk_result(StripOutcome.SKIPPED))
        r.results.append(self._mk_result(StripOutcome.FAILED))
        assert r.total_count == 5
        assert r.stripped_count == 2
        assert r.passthrough_count == 1
        assert r.skipped_count == 1
        assert r.failed_count == 1


# ---------------------------------------------------------------------------
# Stub XML payloads
# ---------------------------------------------------------------------------


class TestStubXMLPayloads:
    """v1.7.56: the two empty XML stubs are valid OOXML."""

    def test_core_xml_is_valid_xml(self):
        # Should parse without raising
        import xml.etree.ElementTree as ET
        ET.fromstring(_EMPTY_CORE_XML)

    def test_app_xml_is_valid_xml(self):
        import xml.etree.ElementTree as ET
        ET.fromstring(_EMPTY_APP_XML)

    def test_core_xml_has_empty_creator(self):
        # The stripped XML has empty <dc:creator></dc:creator>
        assert b"<dc:creator></dc:creator>" in _EMPTY_CORE_XML

    def test_app_xml_advertises_curator(self):
        # Replacement Application name marks the file as touched
        assert b"<Application>Curator</Application>" in _EMPTY_APP_XML


# ---------------------------------------------------------------------------
# Image stripping (Pillow)
# ---------------------------------------------------------------------------


class TestStripImage:
    def test_strips_jpeg_exif(self, tmp_path, stripper):
        src = tmp_path / "in.jpg"
        dst = tmp_path / "out.jpg"
        _make_jpeg_with_exif(src)

        r = stripper.strip_file(src, dst)
        assert r.outcome == StripOutcome.STRIPPED
        assert r.destination == str(dst)
        assert dst.exists()
        # Verify the saved file is a valid JPEG
        with Image.open(dst) as im:
            assert im.format == "JPEG"
        # EXIF reported as removed (Pillow saw it on input)
        assert "exif" in r.metadata_fields_removed

    def test_strips_png_text_chunks(self, tmp_path, stripper):
        src = tmp_path / "in.png"
        dst = tmp_path / "out.png"
        _make_png_with_text(src)

        r = stripper.strip_file(src, dst)
        assert r.outcome == StripOutcome.STRIPPED
        # PNG text chunks reported as removed
        assert "png_text_chunks" in r.metadata_fields_removed

        # Verify the OUTPUT png has NO text chunks
        with Image.open(dst) as im:
            # Pillow exposes text via .text on PngImageFile
            assert not getattr(im, "text", {}), "PNG output still has text chunks"

    def test_bytes_in_and_out_populated(self, tmp_path, stripper):
        src = tmp_path / "in.jpg"
        dst = tmp_path / "out.jpg"
        _make_jpeg_with_exif(src)

        r = stripper.strip_file(src, dst)
        assert r.bytes_in == src.stat().st_size
        assert r.bytes_out == dst.stat().st_size
        assert r.bytes_in > 0
        assert r.bytes_out > 0

    def test_keep_icc_profile_default_true(self, tmp_path):
        """v1.7.56: default keeps ICC profile; not reported as removed."""
        src = tmp_path / "in.jpg"
        dst = tmp_path / "out.jpg"
        _make_jpeg_with_exif(src)
        s = MetadataStripper(keep_icc_profile=True)
        r = s.strip_file(src, dst)
        # Without an ICC profile in the source, removed list won't have it
        # either way; just verify the strip succeeded
        assert r.outcome == StripOutcome.STRIPPED

    def test_custom_jpeg_quality(self, tmp_path):
        """v1.7.56: jpeg_quality kwarg is honored."""
        src = tmp_path / "in.jpg"
        dst = tmp_path / "out.jpg"
        _make_jpeg_with_exif(src, size=(64, 64))
        s = MetadataStripper(jpeg_quality=50)
        r = s.strip_file(src, dst)
        assert r.outcome == StripOutcome.STRIPPED
        # Low quality should produce smaller bytes_out than high quality
        s_high = MetadataStripper(jpeg_quality=95)
        dst2 = tmp_path / "out2.jpg"
        r2 = s_high.strip_file(src, dst2)
        assert r2.bytes_out >= r.bytes_out


# ---------------------------------------------------------------------------
# DOCX stripping (zipfile)
# ---------------------------------------------------------------------------


class TestStripDocx:
    def test_strips_core_and_app_xml(self, tmp_path, stripper):
        src = tmp_path / "in.docx"
        dst = tmp_path / "out.docx"
        _make_minimal_docx(src)

        r = stripper.strip_file(src, dst)
        assert r.outcome == StripOutcome.STRIPPED
        assert "core_properties" in r.metadata_fields_removed
        assert "app_properties" in r.metadata_fields_removed

    def test_output_core_xml_is_empty_stub(self, tmp_path, stripper):
        src = tmp_path / "in.docx"
        dst = tmp_path / "out.docx"
        _make_minimal_docx(src)
        stripper.strip_file(src, dst)

        with zipfile.ZipFile(dst, "r") as zf:
            core = zf.read("docProps/core.xml")
            app = zf.read("docProps/app.xml")
            # Stubs are bit-identical to module constants
            assert core == _EMPTY_CORE_XML
            assert app == _EMPTY_APP_XML

    def test_drops_custom_xml(self, tmp_path, stripper):
        src = tmp_path / "in.docx"
        dst = tmp_path / "out.docx"
        _make_minimal_docx(src, with_custom=True)
        r = stripper.strip_file(src, dst)
        assert "custom_properties" in r.metadata_fields_removed
        # And it must NOT be in the output zip
        with zipfile.ZipFile(dst, "r") as zf:
            assert "docProps/custom.xml" not in zf.namelist()

    def test_document_content_preserved(self, tmp_path, stripper):
        src = tmp_path / "in.docx"
        dst = tmp_path / "out.docx"
        _make_minimal_docx(src)
        stripper.strip_file(src, dst)

        with zipfile.ZipFile(dst, "r") as zf:
            doc = zf.read("word/document.xml")
        assert b"Hello world" in doc

    def test_docm_dotx_dotm_also_supported(self, tmp_path, stripper):
        """v1.7.56: all four DOCX-family extensions dispatch to _strip_docx."""
        for ext in (".docm", ".dotx", ".dotm"):
            src = tmp_path / f"in{ext}"
            dst = tmp_path / f"out{ext}"
            _make_minimal_docx(src)
            r = stripper.strip_file(src, dst)
            assert r.outcome == StripOutcome.STRIPPED, f"failed for {ext}"


# ---------------------------------------------------------------------------
# PDF stripping (pypdf)
# ---------------------------------------------------------------------------


class TestStripPdf:
    def test_strips_pdf_metadata(self, tmp_path, stripper):
        src = tmp_path / "in.pdf"
        dst = tmp_path / "out.pdf"
        _make_minimal_pdf(src)

        r = stripper.strip_file(src, dst)
        assert r.outcome == StripOutcome.STRIPPED
        assert dst.exists()
        # Should have reported author/title/producer/creator as removed
        for field in ("author", "title", "producer", "creator"):
            assert field in r.metadata_fields_removed, (
                f"expected {field} to be reported; got {r.metadata_fields_removed}"
            )

    def test_output_has_no_metadata(self, tmp_path, stripper):
        src = tmp_path / "in.pdf"
        dst = tmp_path / "out.pdf"
        _make_minimal_pdf(src)
        stripper.strip_file(src, dst)

        from pypdf import PdfReader
        reader = PdfReader(str(dst))
        # /Author should not be present (or be empty)
        md = reader.metadata or {}
        assert not md.get("/Author"), f"/Author still present: {md}"
        assert not md.get("/Title")
        assert not md.get("/Creator")

    def test_pages_preserved(self, tmp_path, stripper):
        src = tmp_path / "in.pdf"
        dst = tmp_path / "out.pdf"
        _make_minimal_pdf(src)
        stripper.strip_file(src, dst)

        from pypdf import PdfReader
        in_pages = len(PdfReader(str(src)).pages)
        out_pages = len(PdfReader(str(dst)).pages)
        assert out_pages == in_pages


# ---------------------------------------------------------------------------
# Dispatch logic
# ---------------------------------------------------------------------------


class TestStripFileDispatch:
    def test_unknown_extension_is_passthrough(self, tmp_path, stripper):
        src = tmp_path / "data.bin"
        src.write_bytes(b"some binary data")
        dst = tmp_path / "out.bin"

        r = stripper.strip_file(src, dst)
        assert r.outcome == StripOutcome.PASSTHROUGH
        assert dst.read_bytes() == b"some binary data"

    def test_missing_source_returns_failed(self, tmp_path, stripper):
        r = stripper.strip_file(
            tmp_path / "nope.jpg",
            tmp_path / "out.jpg",
        )
        assert r.outcome == StripOutcome.FAILED
        assert r.destination is None
        assert r.error is not None
        assert "Not a file" in r.error

    def test_corrupted_image_returns_failed(self, tmp_path, stripper):
        """v1.7.56: corrupt files produce FAILED, no exception escapes."""
        src = tmp_path / "broken.jpg"
        src.write_bytes(b"not a real JPEG")
        dst = tmp_path / "out.jpg"
        r = stripper.strip_file(src, dst)
        assert r.outcome == StripOutcome.FAILED
        assert r.error is not None
        assert r.bytes_in > 0  # Error reports the size attempted

    def test_extension_matching_is_case_insensitive(self, tmp_path, stripper):
        """v1.7.56: .JPG and .Jpg dispatch the same as .jpg."""
        # Make a real JPEG, name it with uppercase extension
        src = tmp_path / "PHOTO.JPG"
        dst = tmp_path / "OUT.JPG"
        _make_jpeg_with_exif(src)
        r = stripper.strip_file(src, dst)
        # Should be STRIPPED (dispatched to _strip_image), not PASSTHROUGH
        assert r.outcome == StripOutcome.STRIPPED


# ---------------------------------------------------------------------------
# Directory walk
# ---------------------------------------------------------------------------


class TestStripDirectory:
    def test_recursive_walks_subdirs(self, tmp_path, stripper):
        src_root = tmp_path / "src"
        dst_root = tmp_path / "dst"
        src_root.mkdir()
        sub = src_root / "sub"
        sub.mkdir()

        _make_jpeg_with_exif(src_root / "a.jpg")
        _make_jpeg_with_exif(sub / "b.jpg")

        report = stripper.strip_directory(src_root, dst_root, recursive=True)
        # Both files should be processed
        assert report.total_count == 2
        # Both dst files should exist mirroring the structure
        assert (dst_root / "a.jpg").exists()
        assert (dst_root / "sub" / "b.jpg").exists()

    def test_non_recursive_skips_subdirs(self, tmp_path, stripper):
        src_root = tmp_path / "src"
        dst_root = tmp_path / "dst"
        src_root.mkdir()
        sub = src_root / "sub"
        sub.mkdir()
        _make_jpeg_with_exif(src_root / "top.jpg")
        _make_jpeg_with_exif(sub / "nested.jpg")

        report = stripper.strip_directory(src_root, dst_root, recursive=False)
        # Only top.jpg should be processed
        assert report.total_count == 1

    def test_extension_filter_skips_non_matching(self, tmp_path, stripper):
        src_root = tmp_path / "src"
        dst_root = tmp_path / "dst"
        src_root.mkdir()
        _make_jpeg_with_exif(src_root / "photo.jpg")
        (src_root / "data.bin").write_bytes(b"x")
        (src_root / "doc.txt").write_text("y")

        report = stripper.strip_directory(
            src_root, dst_root,
            recursive=False, extensions=[".jpg"],
        )
        # All 3 are visited; data.bin and doc.txt SKIPPED, photo.jpg STRIPPED
        assert report.total_count == 3
        assert report.stripped_count == 1
        assert report.skipped_count == 2
        # Only the matching file's destination should exist
        assert (dst_root / "photo.jpg").exists()
        assert not (dst_root / "data.bin").exists()
        assert not (dst_root / "doc.txt").exists()

    def test_invalid_directory_returns_error_report(self, tmp_path, stripper):
        # Pass a file instead of a directory
        not_a_dir = tmp_path / "x.txt"
        not_a_dir.write_text("ok")
        report = stripper.strip_directory(not_a_dir, tmp_path / "dst")
        assert report.total_count == 1
        assert report.failed_count == 1
        assert "Not a directory" in (report.results[0].error or "")

    def test_dst_root_is_created_if_missing(self, tmp_path, stripper):
        src_root = tmp_path / "src"
        src_root.mkdir()
        _make_jpeg_with_exif(src_root / "a.jpg")
        dst_root = tmp_path / "dst" / "nested" / "deep"  # doesn't exist
        report = stripper.strip_directory(src_root, dst_root, recursive=False)
        assert dst_root.is_dir()
        assert report.stripped_count == 1

    def test_report_has_started_and_completed_timestamps(
        self, tmp_path, stripper,
    ):
        src_root = tmp_path / "src"
        src_root.mkdir()
        report = stripper.strip_directory(src_root, tmp_path / "dst")
        assert report.started_at is not None
        assert report.completed_at is not None
        assert report.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Passthrough
# ---------------------------------------------------------------------------


class TestPassthrough:
    def test_byte_copy_preserves_content(self, tmp_path, stripper):
        src = tmp_path / "data.weird"
        src.write_bytes(b"hello \x00 binary \xff content")
        dst = tmp_path / "out.weird"

        r = stripper.strip_file(src, dst)
        assert r.outcome == StripOutcome.PASSTHROUGH
        assert dst.read_bytes() == src.read_bytes()

    def test_byte_counts_for_passthrough(self, tmp_path, stripper):
        src = tmp_path / "x.unknown"
        src.write_bytes(b"a" * 100)
        dst = tmp_path / "out.unknown"
        r = stripper.strip_file(src, dst)
        assert r.bytes_in == 100
        assert r.bytes_out == 100
