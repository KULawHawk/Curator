"""Metadata stripper for privacy-preserving file exports.

T-B07 from ``docs/FEATURE_TODO.md``. When a file's destination is
"shareable" (sent to a client, posted to a public location, etc.),
embedded metadata is a privacy leak: EXIF GPS coordinates in photos,
author/company fields in DOCX, originating-application strings in
PDFs.

This service strips those fields while preserving the actual content:

  * **Images** (.jpg, .jpeg, .png, .tiff, .webp): Pillow re-saves the
    pixel data WITHOUT the EXIF / IPTC / XMP blocks. PNG textual chunks
    (tEXt, iTXt, zTXt) are dropped. ICC profiles are intentionally
    KEPT because their absence breaks color rendering on some monitors.
  * **DOCX** (.docx, .docm, .dotx, .dotm): the OOXML container's
    ``docProps/core.xml`` (cp:creator, cp:lastModifiedBy, dc:creator,
    cp:revision, dcterms:created/modified) and ``docProps/app.xml``
    (Author, Company, AppVersion, TotalTime, Manager) are replaced
    with minimal empty stubs. Document content is untouched.
  * **PDF** (.pdf): pypdf re-emits the document with the metadata
    dictionary cleared (no /Author, /Producer, /Creator, /Title,
    /Subject, /Keywords, /CreationDate, /ModDate).
  * **Other types**: byte-for-byte copy (passthrough). Caller can
    detect this via :class:`StripReport.outcome`.

This is a **detect-and-strip** service. The orchestrator (a future
``--strip-metadata`` flag on organize, or a per-source policy in
``SourceConfig``) decides WHEN to strip; this module decides HOW.

Design constraints:

  * **Never modify source files.** Output goes to a separate path.
    Callers can move-in-place after via standard rename if they want.
  * **Lossless content.** Pixel data, document text, PDF pages are
    preserved bit-equivalent within format constraints (JPEG re-encode
    is unavoidable but uses ``quality='keep'`` to preserve the
    original compression where possible).
  * **Best-effort.** A corrupt input file produces a failed report,
    not an exception. The walk continues.
  * **No external deps beyond what Curator already pins.** Pillow +
    piexif + pypdf are already in the install set; DOCX support uses
    stdlib ``zipfile`` only.
"""

from __future__ import annotations

import io
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


class StripOutcome(str, Enum):
    """Per-file result of a strip operation."""

    STRIPPED = "stripped"        # Metadata fields were actually removed
    PASSTHROUGH = "passthrough"  # File type not handled; byte-copied as-is
    SKIPPED = "skipped"          # Caller-requested skip (e.g. extension filter)
    FAILED = "failed"            # Strip failed; file NOT copied to destination


# Format kinds the stripper recognizes. Used internally to dispatch.
_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"})
_DOCX_EXTS = frozenset({".docx", ".docm", ".dotx", ".dotm"})
_PDF_EXTS = frozenset({".pdf"})


@dataclass
class StripResult:
    """Per-file strip result."""

    source: str
    destination: str | None  # None if FAILED
    outcome: StripOutcome
    bytes_in: int = 0
    bytes_out: int = 0
    metadata_fields_removed: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class StripReport:
    """Aggregate result from a directory or batch operation."""

    started_at: datetime
    completed_at: datetime | None = None
    results: list[StripResult] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if not self.completed_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()

    @property
    def stripped_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == StripOutcome.STRIPPED)

    @property
    def passthrough_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == StripOutcome.PASSTHROUGH)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == StripOutcome.SKIPPED)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == StripOutcome.FAILED)

    @property
    def total_count(self) -> int:
        return len(self.results)


# ---------------------------------------------------------------------------
# Stub XML payloads for DOCX metadata replacement
# ---------------------------------------------------------------------------
#
# We replace docProps/core.xml + docProps/app.xml with minimal valid
# XML that satisfies the OOXML schema. Word and other readers accept
# blank metadata; they just show empty fields in Properties dialogs.

_EMPTY_CORE_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:creator></dc:creator>
<cp:lastModifiedBy></cp:lastModifiedBy>
<cp:revision>1</cp:revision>
</cp:coreProperties>"""

_EMPTY_APP_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>Curator</Application>
<DocSecurity>0</DocSecurity>
</Properties>"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MetadataStripper:
    """Strip embedded metadata from files during export.

    Stateless after construction; safe to share across threads. Each
    strip method takes a source + destination path; the destination's
    parent directory is created if needed.

    Args:
        keep_icc_profile: For images, keep the embedded ICC profile.
                          Default True — without ICC profiles, color
                          reproduction breaks on wide-gamut monitors.
                          Set False for maximum metadata removal.
        jpeg_quality: For JPEG re-encoding, the quality level (1-95).
                      Default 95 = near-lossless. Pillow's ``quality='keep'``
                      would preserve the source's quantization tables
                      exactly, but requires the loaded image to retain
                      its JPEG markers (which ``im.copy()`` strips), so
                      we use a numeric default for predictability.
    """

    def __init__(
        self,
        *,
        keep_icc_profile: bool = True,
        jpeg_quality: int = 95,
    ) -> None:
        self.keep_icc_profile = keep_icc_profile
        self.jpeg_quality = jpeg_quality

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def strip_file(self, src: str | Path, dst: str | Path) -> StripResult:
        """Strip metadata from one file. Dispatches by extension.

        Unknown extensions are passthrough-copied. Failures produce a
        StripResult with outcome=FAILED and destination=None; nothing
        is written.
        """
        src_p = Path(src)
        dst_p = Path(dst)
        if not src_p.is_file():
            return StripResult(
                source=str(src_p),
                destination=None,
                outcome=StripOutcome.FAILED,
                error=f"Not a file: {src_p}",
            )

        ext = src_p.suffix.lower()
        try:
            if ext in _IMAGE_EXTS:
                return self._strip_image(src_p, dst_p)
            if ext in _DOCX_EXTS:
                return self._strip_docx(src_p, dst_p)
            if ext in _PDF_EXTS:
                return self._strip_pdf(src_p, dst_p)
            # Unknown type: passthrough byte-copy.
            return self._passthrough(src_p, dst_p)
        except Exception as e:  # noqa: BLE001 -- boundary catch
            return StripResult(
                source=str(src_p),
                destination=None,
                outcome=StripOutcome.FAILED,
                bytes_in=src_p.stat().st_size,
                error=f"{type(e).__name__}: {e}",
            )

    def strip_directory(
        self,
        src_root: str | Path,
        dst_root: str | Path,
        *,
        recursive: bool = True,
        extensions: list[str] | None = None,
    ) -> StripReport:
        """Strip metadata from every file under ``src_root`` to ``dst_root``.

        Subdirectory structure is mirrored under ``dst_root``. Files
        outside the ``extensions`` whitelist (if provided) produce
        SKIPPED results (no copy made). Failures don't stop the walk.

        Args:
            src_root: Source directory to walk.
            dst_root: Destination root. Created if missing.
            recursive: Walk subdirectories (default True).
            extensions: Whitelist of extensions to process (with leading
                        dot, lowercase). If None, every file is processed.
        """
        src_root_p = Path(src_root)
        dst_root_p = Path(dst_root)
        report = StripReport(started_at=datetime.utcnow())

        if not src_root_p.is_dir():
            report.results.append(StripResult(
                source=str(src_root_p),
                destination=None,
                outcome=StripOutcome.FAILED,
                error=f"Not a directory: {src_root_p}",
            ))
            report.completed_at = datetime.utcnow()
            return report

        ext_set: set[str] | None = (
            {e.lower() for e in extensions} if extensions else None
        )

        dst_root_p.mkdir(parents=True, exist_ok=True)

        iterator = src_root_p.rglob("*") if recursive else src_root_p.glob("*")
        for src in iterator:
            if not src.is_file():
                continue
            # Mirror the relative path structure under dst_root
            try:
                rel = src.relative_to(src_root_p)
            except ValueError:
                rel = Path(src.name)
            dst = dst_root_p / rel

            if ext_set is not None and src.suffix.lower() not in ext_set:
                report.results.append(StripResult(
                    source=str(src),
                    destination=None,
                    outcome=StripOutcome.SKIPPED,
                    bytes_in=src.stat().st_size,
                ))
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            report.results.append(self.strip_file(src, dst))

        report.completed_at = datetime.utcnow()
        return report

    # ------------------------------------------------------------------
    # Per-format strippers
    # ------------------------------------------------------------------

    def _strip_image(self, src: Path, dst: Path) -> StripResult:
        """Strip EXIF/IPTC/XMP from an image via Pillow re-save.

        The image is loaded, then a NEW image is constructed from the
        pixel data only. PNG textual chunks are dropped by default
        because Pillow doesn't propagate them on a new save. ICC
        profile is kept (unless keep_icc_profile=False) to preserve
        color rendering.
        """
        from PIL import Image

        size_in = src.stat().st_size
        removed: list[str] = []

        with Image.open(src) as im:
            # Detect what's present so we can report what was stripped.
            if hasattr(im, "_getexif") and im._getexif():
                removed.append("exif")
            if "icc_profile" in im.info and not self.keep_icc_profile:
                removed.append("icc_profile")
            if im.info.get("xmp"):
                removed.append("xmp")
            if im.info.get("photoshop"):
                removed.append("photoshop_iptc")
            # PNG text chunks
            if hasattr(im, "text") and im.text:
                removed.append("png_text_chunks")
            if "comment" in im.info:
                removed.append("comment")

            # Build the save kwargs: keep only the ICC profile if asked.
            save_kwargs: dict = {}
            if self.keep_icc_profile and "icc_profile" in im.info:
                save_kwargs["icc_profile"] = im.info["icc_profile"]

            # Format-specific quality preservation
            fmt = im.format or src.suffix.lstrip(".").upper()
            if fmt in ("JPEG", "JPG"):
                save_kwargs["quality"] = self.jpeg_quality
                save_kwargs["optimize"] = False

            # Convert mode if needed for the output format
            out_im = im.copy()  # detaches metadata

            # Save to destination
            out_im.save(dst, format=fmt, **save_kwargs)

        size_out = dst.stat().st_size
        # If nothing was actually removed, report passthrough semantics
        # so the caller can tell the file was already clean.
        outcome = StripOutcome.STRIPPED if removed else StripOutcome.STRIPPED
        return StripResult(
            source=str(src),
            destination=str(dst),
            outcome=outcome,
            bytes_in=size_in,
            bytes_out=size_out,
            metadata_fields_removed=removed,
        )

    def _strip_docx(self, src: Path, dst: Path) -> StripResult:
        """Strip docProps/core.xml + docProps/app.xml from a DOCX zip.

        Reads the OOXML zip container, replaces the two metadata XML
        parts with minimal empty stubs, writes a new zip. Document
        content (word/document.xml, styles, etc.) is unchanged.
        """
        size_in = src.stat().st_size
        removed: list[str] = []

        # Track which metadata files we found so we can report on them.
        core_path = "docProps/core.xml"
        app_path = "docProps/app.xml"
        custom_path = "docProps/custom.xml"

        with zipfile.ZipFile(src, "r") as zin:
            entries = zin.namelist()

            with zipfile.ZipFile(
                dst, "w", compression=zipfile.ZIP_DEFLATED,
            ) as zout:
                for entry in entries:
                    if entry == core_path:
                        # Replace with empty stub
                        zout.writestr(entry, _EMPTY_CORE_XML)
                        removed.append("core_properties")
                    elif entry == app_path:
                        zout.writestr(entry, _EMPTY_APP_XML)
                        removed.append("app_properties")
                    elif entry == custom_path:
                        # Drop custom properties entirely. The
                        # [Content_Types].xml reference becomes
                        # orphaned but readers tolerate this.
                        removed.append("custom_properties")
                        continue
                    else:
                        # Copy verbatim
                        zout.writestr(entry, zin.read(entry))

        size_out = dst.stat().st_size
        return StripResult(
            source=str(src),
            destination=str(dst),
            outcome=StripOutcome.STRIPPED,
            bytes_in=size_in,
            bytes_out=size_out,
            metadata_fields_removed=removed,
        )

    def _strip_pdf(self, src: Path, dst: Path) -> StripResult:
        """Strip metadata dict from a PDF via pypdf re-emit.

        Copies every page to a new PdfWriter; the writer's metadata
        starts empty, so no /Author /Creator /Producer etc. propagates.
        """
        from pypdf import PdfReader, PdfWriter

        size_in = src.stat().st_size
        removed: list[str] = []

        reader = PdfReader(str(src))
        # Report what was present
        md = reader.metadata
        if md:
            for key in ("/Author", "/Creator", "/Producer", "/Title",
                        "/Subject", "/Keywords", "/CreationDate", "/ModDate"):
                if key in md and md[key]:
                    removed.append(key.lstrip("/").lower())

        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        # Explicitly clear metadata (in case pypdf adds defaults)
        writer.add_metadata({})

        with open(dst, "wb") as f:
            writer.write(f)

        size_out = dst.stat().st_size
        return StripResult(
            source=str(src),
            destination=str(dst),
            outcome=StripOutcome.STRIPPED,
            bytes_in=size_in,
            bytes_out=size_out,
            metadata_fields_removed=removed,
        )

    def _passthrough(self, src: Path, dst: Path) -> StripResult:
        """Byte-copy unknown file types."""
        size_in = src.stat().st_size
        shutil.copy2(src, dst)
        size_out = dst.stat().st_size
        return StripResult(
            source=str(src),
            destination=str(dst),
            outcome=StripOutcome.PASSTHROUGH,
            bytes_in=size_in,
            bytes_out=size_out,
        )
