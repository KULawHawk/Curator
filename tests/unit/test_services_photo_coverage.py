"""Coverage closure for ``curator.services.photo`` (v1.7.141).

Targets the 11 uncovered lines:
- 130-131: ``_parse_exif_datetime`` TypeError/ValueError except
- 184-185: ``_pillow_available`` ImportError fallback
- 247-248: ``read_metadata`` OSError in mtime fallback
- 275-277: ``_read_exif`` unexpected-exception arm (catches non-Pillow errors)
- 309-310: ``_read_exif`` meta.raw build except arm
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

from curator.services.photo import (
    PhotoMetadata,
    PhotoService,
    _parse_exif_datetime,
    _pillow_available,
)


class TestParseExifDatetimeErrorPaths:
    def test_value_error_returns_none(self):
        """A regex-matching string with values that overflow int() returns None."""
        # Date-shaped but with insane values that ValueError on datetime() construction
        # (Feb 30 doesn't exist)
        result = _parse_exif_datetime("2026:02:30 12:00:00")
        # Try construction of datetime(2026, 2, 30, ...) raises ValueError -> caught
        assert result is None


class TestPillowAvailable:
    def test_returns_false_when_pil_import_fails(self, monkeypatch):
        """Force ImportError via sys.modules sentinel."""
        monkeypatch.setitem(sys.modules, "PIL", None)
        assert _pillow_available() is False


class TestReadMetadataStatFailure:
    def test_stat_failure_logs_and_returns_meta_with_no_taken_at(
        self, tmp_path, monkeypatch,
    ):
        """Lines 247-248: when p.stat() raises OSError in the mtime
        fallback, log and leave taken_at None.

        The first stat call comes from p.exists() — let it succeed. The
        second stat call (from the mtime fallback) raises OSError."""
        photo = tmp_path / "test.jpg"
        photo.write_bytes(b"\xff\xd8not-a-real-jpeg")

        # Force _pillow_available to False so we skip EXIF entirely
        monkeypatch.setattr(
            "curator.services.photo._pillow_available", lambda: False,
        )

        # Patch Path.stat with a call counter: first call (from exists())
        # passes through; second call (from mtime fallback) raises.
        original_stat = Path.stat
        calls = {"n": 0}

        def _flaky_stat(self, *args, **kwargs):
            if self == photo:
                calls["n"] += 1
                if calls["n"] >= 2:
                    raise OSError("stat denied")
            return original_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _flaky_stat)

        svc = PhotoService()
        meta = svc.read_metadata(photo)
        assert meta is not None
        assert meta.taken_at is None


class TestReadExifUnexpectedException:
    def test_unexpected_exception_swallowed(self, tmp_path, monkeypatch):
        """Lines 275-277: PIL.Image.open raising a non-(UnidentifiedImageError,
        OSError, ValueError) exception is caught by the broad except arm."""
        photo = tmp_path / "uncovered.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xe0fake")

        # Make sure pillow_available returns True so _read_exif runs
        monkeypatch.setattr(
            "curator.services.photo._pillow_available", lambda: True,
        )

        # Patch PIL.Image.open to raise an unexpected exception
        import PIL.Image

        def _boom(*args, **kwargs):
            raise RuntimeError("totally unexpected PIL error")

        monkeypatch.setattr(PIL.Image, "open", _boom)

        svc = PhotoService()
        meta = svc.read_metadata(photo)
        # The unexpected error was logged and swallowed; mtime fallback fills in
        assert meta is not None
        assert meta.taken_at is not None  # mtime fallback fired


class TestReadExifMetaRawBuildFailure:
    def test_raw_build_exception_is_swallowed(self, tmp_path, monkeypatch):
        """Lines 309-310: the dict-comprehension for meta.raw can throw if
        exif.items() returns something exotic; the except arm swallows it."""
        photo = tmp_path / "rb.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xe0fake")

        monkeypatch.setattr(
            "curator.services.photo._pillow_available", lambda: True,
        )

        # Build a fake exif object whose .items() throws AND that supports
        # __bool__ (so `if not exif` is False, we proceed into the for loop)
        class _FakeExif:
            def __bool__(self):
                return True

            def get(self, key, default=None):
                return None

            def items(self):
                raise RuntimeError("items boom")

        class _FakeImage:
            width = 100
            height = 100

            def getexif(self):
                return _FakeExif()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import PIL.Image

        def _open(*args, **kwargs):
            return _FakeImage()

        monkeypatch.setattr(PIL.Image, "open", _open)

        svc = PhotoService()
        meta = svc.read_metadata(photo)
        # Build threw but was caught; meta is otherwise populated
        assert meta is not None
        assert meta.width == 100
        assert meta.raw == {}  # never assigned because of exception
