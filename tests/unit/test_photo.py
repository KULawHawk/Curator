"""Unit tests for PhotoService + helpers (Phase Gamma F3).

Covers:
    * PHOTO_EXTENSIONS coverage
    * EXIF date parsing edge cases (corrupt years, missing time, "0000"
      timestamps, malformed strings)
    * PhotoMetadata properties (year_str, date_str fallbacks)
    * is_photo_file
    * read_metadata behavior:
        - non-photo extension returns None
        - nonexistent returns None
        - mtime fallback when no EXIF date
        - EXIF priority order (DateTimeOriginal > Digitized > DateTime)
    * propose_destination template with sanitization

Real EXIF reading is tested via Pillow with a programmatically-generated
JPEG that we tag in-memory. No bundled fixtures required.
"""

from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from curator.services.music import sanitize_path_component
from curator.services.photo import (
    PHOTO_EXTENSIONS,
    UNKNOWN_DATE,
    PhotoMetadata,
    PhotoService,
    _parse_exif_datetime,
)


# ===========================================================================
# _parse_exif_datetime
# ===========================================================================


class TestParseExifDatetime:
    def test_canonical_format(self):
        # Standard EXIF: "YYYY:MM:DD HH:MM:SS"
        d = _parse_exif_datetime("2024:03:15 14:30:00")
        assert d == datetime(2024, 3, 15, 14, 30, 0)

    def test_iso_dash_format(self):
        # Some software writes dashes instead of colons in the date.
        d = _parse_exif_datetime("2024-03-15 14:30:00")
        assert d == datetime(2024, 3, 15, 14, 30, 0)

    def test_date_only(self):
        # No time portion \u2014 should still parse with midnight.
        d = _parse_exif_datetime("2024:03:15")
        assert d == datetime(2024, 3, 15, 0, 0, 0)

    def test_iso_t_separator(self):
        d = _parse_exif_datetime("2024:03:15T14:30:00")
        assert d == datetime(2024, 3, 15, 14, 30, 0)

    def test_zero_timestamp_returns_none(self):
        # Common camera bug: "0000:00:00 00:00:00" for unset timestamps.
        # Don't put photos in /0000/.
        assert _parse_exif_datetime("0000:00:00 00:00:00") is None

    def test_year_below_1900_returns_none(self):
        # Corrupt EXIF or test patterns. Photography didn't exist there.
        assert _parse_exif_datetime("0042:01:01 00:00:00") is None
        assert _parse_exif_datetime("1899:12:31 00:00:00") is None

    def test_clamps_invalid_time_components(self):
        # Some cameras emit hour=24 or seconds=60. Clamp instead of None.
        d = _parse_exif_datetime("2024:03:15 24:60:60")
        assert d is not None
        assert d.year == 2024 and d.month == 3 and d.day == 15
        assert 0 <= d.hour <= 23
        assert 0 <= d.minute <= 59
        assert 0 <= d.second <= 59

    def test_none_returns_none(self):
        assert _parse_exif_datetime(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_exif_datetime("") is None
        assert _parse_exif_datetime("   ") is None

    def test_garbage_returns_none(self):
        assert _parse_exif_datetime("not a date") is None
        assert _parse_exif_datetime("2024-XX-15") is None
        assert _parse_exif_datetime("yesterday") is None


# ===========================================================================
# PhotoMetadata
# ===========================================================================


class TestPhotoMetadata:
    def test_empty_has_no_useful_date(self):
        m = PhotoMetadata()
        assert m.has_useful_date is False
        assert m.year_str == UNKNOWN_DATE
        assert m.date_str == UNKNOWN_DATE

    def test_with_date(self):
        m = PhotoMetadata(taken_at=datetime(2024, 3, 15, 14, 30))
        assert m.has_useful_date is True
        assert m.year_str == "2024"
        assert m.date_str == "2024-03-15"

    def test_year_str_zero_pads(self):
        # Defensive: even though we reject < 1900, the year string
        # should always be 4 chars wide.
        m = PhotoMetadata(taken_at=datetime(2007, 1, 1))
        assert m.year_str == "2007"


# ===========================================================================
# PhotoService.is_photo_file
# ===========================================================================


class TestIsPhotoFile:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("a.jpg", True),
            ("a.JPG", True),
            ("a.jpeg", True),
            ("a.png", True),
            ("a.heic", True),
            ("a.tiff", True),
            ("a.tif", True),
            ("a.webp", True),
            ("a.cr2", True),
            ("a.nef", True),
            ("a.arw", True),
            ("a.dng", True),
            ("a.txt", False),
            ("a.mp3", False),
            ("a.mp4", False),  # Video not in F3 v0.24 scope
            ("noext", False),
        ],
    )
    def test_extensions(self, path, expected):
        svc = PhotoService()
        assert svc.is_photo_file(path) is expected

    def test_extension_set_includes_common_formats(self):
        for ext in (".jpg", ".png", ".heic", ".cr2", ".dng"):
            assert ext in PHOTO_EXTENSIONS


# ===========================================================================
# PhotoService.propose_destination
# ===========================================================================


class TestProposeDestination:
    def test_canonical_path(self, tmp_path):
        svc = PhotoService()
        meta = PhotoMetadata(
            taken_at=datetime(2024, 3, 15, 14, 30),
            taken_at_source="exif_original",
        )
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "IMG_1234.jpg",
            target_root=tmp_path / "photos",
        )
        assert dest == tmp_path / "photos" / "2024" / "2024-03-15" / "IMG_1234.jpg"

    def test_unknown_date_uses_placeholder(self, tmp_path):
        svc = PhotoService()
        meta = PhotoMetadata()  # no date at all
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "scan.tiff",
            target_root=tmp_path / "photos",
        )
        # Both year and date components fall back to UNKNOWN_DATE.
        # That's fine \u2014 the file still organizes deterministically.
        assert UNKNOWN_DATE in dest.parts[-3]
        assert UNKNOWN_DATE in dest.parts[-2]
        assert dest.name == "scan.tiff"

    def test_filename_preserved(self, tmp_path):
        # Photos preserve their original filenames \u2014 they often encode
        # info worth keeping (camera serial + sequence number).
        svc = PhotoService()
        meta = PhotoMetadata(taken_at=datetime(2024, 1, 1))
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "DSC_0042.NEF",
            target_root=tmp_path / "photos",
        )
        # Note: extension case is preserved here because we keep the
        # full original filename (in contrast to MusicService which
        # lowercases the suffix in its template).
        assert dest.name == "DSC_0042.NEF"

    def test_destination_under_target_root(self, tmp_path):
        svc = PhotoService()
        meta = PhotoMetadata(taken_at=datetime(2024, 6, 1))
        target = tmp_path / "library"
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "x.jpg",
            target_root=target,
        )
        assert str(dest).startswith(str(target))

    def test_illegal_chars_in_filename_sanitized(self, tmp_path):
        svc = PhotoService()
        meta = PhotoMetadata(taken_at=datetime(2024, 6, 1))
        # Pretend an upstream renamed a photo with illegal Windows chars.
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "weird:name?.jpg",
            target_root=tmp_path,
        )
        assert ":" not in dest.name
        assert "?" not in dest.name


# ===========================================================================
# PhotoService.read_metadata
# ===========================================================================


def _write_jpeg_with_exif(
    path: Path,
    *,
    datetime_original: str | None = None,
    datetime_digitized: str | None = None,
    datetime_modified: str | None = None,
    make: str | None = None,
    model: str | None = None,
) -> None:
    """Create a tiny real JPEG at ``path`` with the requested EXIF tags.

    Uses Pillow to build a 1x1 RGB image and inject EXIF. This is a
    real JPEG that PIL.Image.open + getexif() will read back correctly.
    """
    from PIL import Image
    img = Image.new("RGB", (1, 1), color=(255, 0, 0))

    exif = img.getexif()
    if datetime_original is not None:
        exif[36867] = datetime_original
    if datetime_digitized is not None:
        exif[36868] = datetime_digitized
    if datetime_modified is not None:
        exif[306] = datetime_modified
    if make is not None:
        exif[271] = make
    if model is not None:
        exif[272] = model

    img.save(path, format="JPEG", exif=exif)


class TestReadMetadata:
    def test_returns_none_for_non_photo(self, tmp_path):
        svc = PhotoService()
        plain = tmp_path / "notes.txt"
        plain.write_text("hi")
        assert svc.read_metadata(plain) is None

    def test_returns_none_for_nonexistent(self, tmp_path):
        svc = PhotoService()
        ghost = tmp_path / "ghost.jpg"
        assert svc.read_metadata(ghost) is None

    def test_mtime_fallback_when_no_exif(self, tmp_path):
        # A bare JPEG without EXIF should still produce a date from mtime.
        from PIL import Image
        bare = tmp_path / "bare.jpg"
        Image.new("RGB", (1, 1)).save(bare, format="JPEG")

        # Force a known mtime so the test is deterministic.
        ts = datetime(2020, 7, 4, 12, 0, 0).timestamp()
        os.utime(bare, (ts, ts))

        svc = PhotoService()
        meta = svc.read_metadata(bare)
        assert meta is not None
        assert meta.has_useful_date is True
        assert meta.taken_at_source == "mtime"
        assert meta.taken_at.year == 2020 and meta.taken_at.month == 7

    def test_extracts_exif_datetime_original(self, tmp_path):
        target = tmp_path / "with_exif.jpg"
        _write_jpeg_with_exif(
            target,
            datetime_original="2024:03:15 14:30:00",
            make="Canon",
            model="EOS R5",
        )
        svc = PhotoService()
        meta = svc.read_metadata(target)
        assert meta is not None
        assert meta.taken_at == datetime(2024, 3, 15, 14, 30, 0)
        assert meta.taken_at_source == "exif_original"
        assert meta.camera_make == "Canon"
        assert meta.camera_model == "EOS R5"

    def test_exif_priority_order(self, tmp_path):
        # When all three date tags are present, DateTimeOriginal wins.
        target = tmp_path / "priority.jpg"
        _write_jpeg_with_exif(
            target,
            datetime_original="2024:03:15 14:30:00",
            datetime_digitized="2023:01:01 00:00:00",
            datetime_modified="2022:01:01 00:00:00",
        )
        svc = PhotoService()
        meta = svc.read_metadata(target)
        assert meta.taken_at.year == 2024
        assert meta.taken_at_source == "exif_original"

    def test_falls_through_to_digitized_when_original_missing(self, tmp_path):
        target = tmp_path / "digitized.jpg"
        _write_jpeg_with_exif(
            target,
            datetime_original=None,
            datetime_digitized="2023:06:15 00:00:00",
        )
        svc = PhotoService()
        meta = svc.read_metadata(target)
        assert meta.taken_at.year == 2023
        assert meta.taken_at_source == "exif_digitized"

    def test_zero_exif_falls_back_to_mtime(self, tmp_path):
        # If EXIF date is "0000:00:00 00:00:00" it's treated as missing
        # \u2014 should fall through to mtime.
        target = tmp_path / "zero.jpg"
        _write_jpeg_with_exif(
            target, datetime_original="0000:00:00 00:00:00",
        )
        ts = datetime(2019, 5, 1, 0, 0, 0).timestamp()
        os.utime(target, (ts, ts))
        svc = PhotoService()
        meta = svc.read_metadata(target)
        assert meta.taken_at_source == "mtime"
        assert meta.taken_at.year == 2019

    def test_returns_metadata_when_pillow_unavailable(self, tmp_path, monkeypatch):
        # Even without Pillow, we should fall back to mtime gracefully.
        target = tmp_path / "x.jpg"
        target.write_bytes(b"not really a jpg")
        ts = datetime(2018, 1, 1, 0, 0, 0).timestamp()
        os.utime(target, (ts, ts))

        monkeypatch.setattr(
            "curator.services.photo._pillow_available",
            lambda: False,
        )
        svc = PhotoService()
        meta = svc.read_metadata(target)
        assert meta is not None
        assert meta.has_useful_date is True
        assert meta.taken_at_source == "mtime"
        assert meta.taken_at.year == 2018

    def test_handles_corrupt_jpeg_without_crashing(self, tmp_path):
        # Bytes that aren't a real JPEG. PIL throws \u2014 we should still
        # return a metadata via mtime fallback.
        target = tmp_path / "corrupt.jpg"
        target.write_bytes(b"definitely not a JPEG")
        ts = datetime(2017, 4, 1, 0, 0, 0).timestamp()
        os.utime(target, (ts, ts))
        svc = PhotoService()
        meta = svc.read_metadata(target)
        assert meta is not None
        assert meta.taken_at_source == "mtime"
