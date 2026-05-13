"""Focused coverage tests for services/metadata_stripper.py.

Sub-ship v1.7.101 of the Coverage Sweep arc.

Closes the seven uncovered lines + one partial branch:

* Lines 269-270: `strip_directory`'s `except ValueError` when
  `src.relative_to(src_root_p)` raises — defensive against symlinks/
  junctions that escape the tree. Exercised by monkeypatching
  Path.relative_to.
* Lines 311, 313, 315, 320: per-info-field detection arms in
  `_strip_image` — exercised by patching PIL.Image.open to return
  fakes carrying the relevant info-dict keys (icc_profile, xmp,
  photoshop, comment).
* Line 325: keep-ICC-profile branch (the save_kwargs[icc_profile]
  assignment) — fake image with `icc_profile` AND
  `keep_icc_profile=True`.
* Branch 415->421: `_strip_pdf`'s `if md:` False arm — PDF whose
  metadata dict is falsy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from curator.services.metadata_stripper import (
    MetadataStripper,
    StripOutcome,
)


class _FakeImage:
    """Minimal Pillow-Image stand-in supporting `with Image.open()` as a
    context manager. Tests configure `info` (and `text`/`_getexif`) to
    drive the per-field detection arms in `_strip_image`."""

    def __init__(
        self,
        *,
        info: dict[str, Any] | None = None,
        text: dict[str, str] | None = None,
        getexif_result: Any = None,
        format: str = "JPEG",
    ):
        self.info: dict[str, Any] = info or {}
        self.text: dict[str, str] = text or {}
        self._exif_result = getexif_result
        self.format = format
        self.mode = "RGB"

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def _getexif(self):
        return self._exif_result

    def copy(self):
        return self

    def save(self, dst, format=None, **kwargs):
        # Write a non-empty bytes payload so dst.stat().st_size > 0.
        Path(dst).write_bytes(b"fake stripped image")


def _make_stripper(**kwargs) -> MetadataStripper:
    return MetadataStripper(**kwargs)


# ---------------------------------------------------------------------------
# strip_directory ValueError on relative_to (lines 269-270)
# ---------------------------------------------------------------------------


def test_strip_directory_relative_to_value_error_uses_basename(
    tmp_path, monkeypatch,
):
    # Lines 268-270: when `src.relative_to(src_root_p)` raises, fall
    # back to `Path(src.name)` so the file is mirrored at the root
    # of dst_root with its basename. Force the ValueError via a
    # selective Path.relative_to monkeypatch.
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    src_root.mkdir()
    img = src_root / "a.png"
    img.write_bytes(b"fake png data")

    orig_relative_to = Path.relative_to

    def boom_relative_to(self, *args, **kwargs):
        # Raise for the specific file under test, pass through otherwise
        if str(self).endswith("a.png"):
            raise ValueError("simulated escape from tree")
        return orig_relative_to(self, *args, **kwargs)

    monkeypatch.setattr(Path, "relative_to", boom_relative_to)

    # Patch _strip_image (and dispatch) to no-op write so we just test
    # the relative_to fallback. Easiest: filter to a non-image extension
    # via ext_filter so the file is recorded as SKIPPED.
    svc = _make_stripper()
    report = svc.strip_directory(
        src_root, dst_root, extensions=[".xyz"], recursive=False,
    )
    # The file's relative path was rebuilt via Path(src.name), and
    # the file was SKIPPED because its extension isn't in extensions.
    assert len(report.results) == 1
    assert report.results[0].outcome == StripOutcome.SKIPPED


# ---------------------------------------------------------------------------
# _strip_image: per-info-field detection arms (lines 311, 313, 315, 320, 325)
# ---------------------------------------------------------------------------


def test_strip_image_detects_all_metadata_fields(tmp_path):
    # Lines 308-325: all six detection arms (exif, icc_profile, xmp,
    # photoshop, png_text_chunks, comment) PLUS the keep-ICC-profile
    # save_kwargs assignment at line 325. One image carrying every
    # field; keep_icc_profile=True so line 325 fires.
    svc = _make_stripper(keep_icc_profile=True)
    src = tmp_path / "src.jpg"
    src.write_bytes(b"original")
    dst = tmp_path / "dst.jpg"

    fake = _FakeImage(
        info={
            "icc_profile": b"fake profile bytes",
            "xmp": b"<x:xmpmeta/>",
            "photoshop": b"\x00ph",
            "comment": b"hidden comment",
        },
        text={"Author": "X"},
        getexif_result={0x010F: "Make"},
        format="JPEG",
    )

    with patch("PIL.Image.open", return_value=fake):
        result = svc._strip_image(src, dst)

    assert result.outcome == StripOutcome.STRIPPED
    # Detection arms fired for everything except icc_profile (which
    # is NOT reported as removed when keep_icc_profile=True per
    # the conditional at line 310-311).
    removed = set(result.metadata_fields_removed)
    assert "exif" in removed
    assert "xmp" in removed
    assert "photoshop_iptc" in removed
    assert "png_text_chunks" in removed
    assert "comment" in removed


def test_strip_image_detects_icc_when_not_keeping(tmp_path):
    # Line 311 specifically: icc_profile in info AND keep_icc_profile=False
    # → "icc_profile" appended to removed.
    svc = _make_stripper(keep_icc_profile=False)
    src = tmp_path / "src.jpg"
    src.write_bytes(b"original")
    dst = tmp_path / "dst.jpg"

    fake = _FakeImage(
        info={"icc_profile": b"profile"},
        format="JPEG",
    )

    with patch("PIL.Image.open", return_value=fake):
        result = svc._strip_image(src, dst)

    assert "icc_profile" in result.metadata_fields_removed


def test_strip_image_keeps_icc_when_configured(tmp_path):
    # Line 325: keep_icc_profile=True AND icc_profile in info →
    # save_kwargs["icc_profile"] assignment fires.
    svc = _make_stripper(keep_icc_profile=True)
    src = tmp_path / "src.jpg"
    src.write_bytes(b"original")
    dst = tmp_path / "dst.jpg"

    fake = _FakeImage(
        info={"icc_profile": b"profile_bytes"},
        format="JPEG",
    )

    save_calls = []
    original_save = fake.save

    def recording_save(dst_arg, format=None, **kwargs):
        save_calls.append(kwargs)
        original_save(dst_arg, format=format, **kwargs)
    fake.save = recording_save

    with patch("PIL.Image.open", return_value=fake):
        svc._strip_image(src, dst)

    # ICC profile was carried into save_kwargs.
    assert save_calls[0].get("icc_profile") == b"profile_bytes"


def test_strip_image_detects_xmp_photoshop_comment(tmp_path):
    # Lines 313, 315, 320: xmp / photoshop / comment detection arms.
    svc = _make_stripper(keep_icc_profile=False)
    src = tmp_path / "src.jpg"
    src.write_bytes(b"original")
    dst = tmp_path / "dst.jpg"

    fake = _FakeImage(
        info={
            "xmp": b"<x:xmpmeta/>",
            "photoshop": b"\x00photoshop",
            "comment": b"hidden",
        },
        format="JPEG",
    )

    with patch("PIL.Image.open", return_value=fake):
        result = svc._strip_image(src, dst)

    removed = set(result.metadata_fields_removed)
    assert "xmp" in removed
    assert "photoshop_iptc" in removed
    assert "comment" in removed


# ---------------------------------------------------------------------------
# _strip_pdf: empty metadata dict (branch 415->421)
# ---------------------------------------------------------------------------


def test_strip_pdf_with_no_metadata_dict(tmp_path, monkeypatch):
    # Branch 415->421 False arm: when `reader.metadata` is falsy
    # (None or empty), the for-loop body is skipped entirely and
    # execution jumps to `writer = PdfWriter()` at line 421.
    #
    # PdfWriter automatically adds /Producer, so a real PDF always
    # has truthy metadata. Subclass PdfReader to override the
    # metadata property to None, then patch the symbol inside the
    # pypdf module so the stripper's local import sees our subclass.
    from pypdf import PdfReader as _RealPdfReader, PdfWriter
    import pypdf

    src = tmp_path / "src.pdf"
    dst = tmp_path / "dst.pdf"

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(src, "wb") as f:
        writer.write(f)

    class _NoMetaReader(_RealPdfReader):
        @property
        def metadata(self):
            return None

    monkeypatch.setattr(pypdf, "PdfReader", _NoMetaReader)

    svc = _make_stripper()
    result = svc._strip_pdf(src, dst)

    assert result.metadata_fields_removed == []
    assert result.outcome == StripOutcome.STRIPPED
