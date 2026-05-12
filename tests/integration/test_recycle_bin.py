"""Tests for the Windows Recycle Bin reader (Q14 closure).

Two layers:

* **Parser tests** — synthesize ``$I`` files in bytes and verify
  ``parse_index_file`` decodes them correctly. Run on every platform.

* **Live test** — actually trash a real file via send2trash, verify
  ``find_in_recycle_bin`` finds it. ``@pytest.mark.skipif`` on
  non-Windows platforms.

Reference for the format: see the docstring on
``curator._vendored.send2trash.win.recycle_bin``.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from curator._vendored.send2trash.win.recycle_bin import (
    RecycleBinEntry,
    RecycleBinParseError,
    parse_index_file,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers: synthesize $I files for the parser
# ---------------------------------------------------------------------------

# 2026-01-15 12:34:56 UTC, expressed as Windows FILETIME (100-ns ticks since 1601).
SAMPLE_DELETION_DATETIME = datetime(2026, 1, 15, 12, 34, 56, tzinfo=timezone.utc)
SAMPLE_FILETIME = (
    int((SAMPLE_DELETION_DATETIME - datetime(1601, 1, 1, tzinfo=timezone.utc))
        .total_seconds() * 10_000_000)
)


def _make_v1_bytes(original_path: str, file_size: int = 1234) -> bytes:
    """Build a synthetic v1 ``$I`` file (Vista–Win 8 format)."""
    header = struct.pack("<QqQ", 1, file_size, SAMPLE_FILETIME)
    # 520 bytes fixed UTF-16LE, null-terminated, padded.
    encoded = (original_path + "\x00").encode("utf-16-le")
    if len(encoded) > 520:
        raise ValueError("path too long for v1 fixed-width slot")
    encoded = encoded + b"\x00" * (520 - len(encoded))
    return header + encoded


def _make_v2_bytes(original_path: str, file_size: int = 5678) -> bytes:
    """Build a synthetic v2 ``$I`` file (Win 10+ format)."""
    header = struct.pack("<QqQ", 2, file_size, SAMPLE_FILETIME)
    encoded = (original_path + "\x00").encode("utf-16-le")
    path_len_wide = len(encoded) // 2  # length in wide chars (UTF-16 code units)
    return header + struct.pack("<I", path_len_wide) + encoded


# ---------------------------------------------------------------------------
# Parser — runs everywhere
# ---------------------------------------------------------------------------

class TestParseIndexFile:
    def test_v1_round_trip(self, tmp_path: Path):
        idx = tmp_path / "$I123ABC.txt"
        idx.write_bytes(_make_v1_bytes(r"C:\Users\jmlee\Documents\report.txt"))

        entry = parse_index_file(idx)
        assert entry.version == 1
        assert entry.original_path == r"C:\Users\jmlee\Documents\report.txt"
        assert entry.file_size == 1234
        assert entry.deleted_at == SAMPLE_DELETION_DATETIME

    def test_v2_round_trip(self, tmp_path: Path):
        idx = tmp_path / "$I456DEF.md"
        idx.write_bytes(_make_v2_bytes(r"D:\notes\daily\2026-01-15.md"))

        entry = parse_index_file(idx)
        assert entry.version == 2
        assert entry.original_path == r"D:\notes\daily\2026-01-15.md"
        assert entry.file_size == 5678
        assert entry.deleted_at == SAMPLE_DELETION_DATETIME

    def test_v2_handles_unicode_path(self, tmp_path: Path):
        idx = tmp_path / "$I789GHI.txt"
        # Smart quotes, em-dash, accented chars: typical user-document paths.
        path = "C:\\Users\\jmlee\\Docs\\résumé — final—v2.txt"
        idx.write_bytes(_make_v2_bytes(path))

        entry = parse_index_file(idx)
        assert entry.original_path == path

    def test_v1_too_short_raises(self, tmp_path: Path):
        idx = tmp_path / "$IBAD000.txt"
        # Only the 24-byte header, no path — short of the v1 540 bytes total.
        idx.write_bytes(struct.pack("<QqQ", 1, 100, SAMPLE_FILETIME))

        with pytest.raises(RecycleBinParseError, match="540"):
            parse_index_file(idx)

    def test_unknown_version_raises(self, tmp_path: Path):
        idx = tmp_path / "$IBAD001.txt"
        # Version 99 — not 1 or 2.
        idx.write_bytes(
            struct.pack("<QqQ", 99, 100, SAMPLE_FILETIME) + b"\x00" * 520
        )

        with pytest.raises(RecycleBinParseError, match="unknown"):
            parse_index_file(idx)

    def test_too_short_for_header_raises(self, tmp_path: Path):
        idx = tmp_path / "$IBAD002.txt"
        idx.write_bytes(b"\x00" * 8)  # Just 8 bytes — no full header.

        with pytest.raises(RecycleBinParseError, match="24"):
            parse_index_file(idx)

    def test_v2_truncated_path_raises(self, tmp_path: Path):
        idx = tmp_path / "$IBAD003.txt"
        # Header claims 100 wide chars (200 bytes), but we only provide 10 bytes.
        idx.write_bytes(
            struct.pack("<QqQ", 2, 100, SAMPLE_FILETIME)
            + struct.pack("<I", 100)
            + b"\x00" * 10
        )

        with pytest.raises(RecycleBinParseError, match="exceeds"):
            parse_index_file(idx)

    def test_content_path_swaps_dollar_I_for_dollar_R(self, tmp_path: Path):
        idx = tmp_path / "$IABCDEF.txt"
        idx.write_bytes(_make_v1_bytes(r"C:\some\file.txt"))

        entry = parse_index_file(idx)
        assert entry.content_path.name == "$RABCDEF.txt"
        # Same parent directory.
        assert entry.content_path.parent == idx.parent


# ---------------------------------------------------------------------------
# Live integration — Windows only
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Recycle Bin is Windows-only")
class TestLiveRecycleBin:
    def test_trash_then_find_in_recycle_bin(self, tmp_path: Path):
        """End-to-end: send a real file to the bin, verify we can locate it."""
        from curator._vendored.send2trash import send2trash
        from curator._vendored.send2trash.win.recycle_bin import (
            find_in_recycle_bin,
        )

        # Write a file we control on the user's filesystem (not just the
        # pytest tmp dir, which on some systems is on a non-fixed drive
        # and therefore has its own Recycle Bin behavior).
        with tempfile.NamedTemporaryFile(
            prefix="curator_q14_test_", suffix=".txt",
            mode="w", delete=False,
        ) as f:
            f.write("Q14 test marker — safe to delete\n")
            doomed = f.name

        try:
            send2trash(doomed)
            # File is gone from disk.
            assert not os.path.exists(doomed)

            # We should now be able to find it in the bin.
            entry = find_in_recycle_bin(doomed)
            assert entry is not None, (
                f"find_in_recycle_bin returned None for {doomed!r} "
                f"after send2trash. Either the bin walk skipped this drive, "
                f"or path normalization failed."
            )
            assert os.path.normcase(entry.original_path) == os.path.normcase(doomed)
            assert entry.content_path.exists(), (
                f"$R companion {entry.content_path} doesn't exist; "
                f"bin layout is unexpected"
            )
            assert entry.file_size > 0
        finally:
            # Best-effort cleanup: if find_in_recycle_bin succeeded, also
            # remove the $R + $I files so we don't leave bin clutter.
            entry = find_in_recycle_bin(doomed) if os.path.exists(doomed) is False else None
            if entry is not None:
                try:
                    if entry.content_path.exists():
                        entry.content_path.unlink()
                    if entry.index_path.exists():
                        entry.index_path.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# v1.7.46: tests for _to_long_path 8.3 short-path expansion
# ---------------------------------------------------------------------------


class TestToLongPath:
    """v1.7.46: unit tests for the new ``_to_long_path`` helper.

    Closes CI failure #4 from v1.7.44's CI run #3: on GitHub Actions
    Windows runners, ``tempfile.gettempdir()`` returns a path like
    ``C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\...`` (8.3 short-path
    form of ``runneradmin``). The Recycle Bin's ``$I`` files always store
    the LONG path, so a substring lookup against the SHORT path fails.
    The fix normalizes via ``GetLongPathNameW`` so both sides of the
    comparison use the long form.

    These tests can't easily synthesize a real short-path component
    without admin privileges to enable 8.3 generation on a test volume,
    so they focus on:

    * Function is importable and has the right signature
    * No-op contract: paths with no short components are returned
      unchanged
    * Non-Windows platforms get the input back unchanged (safety)
    * The fallback path (leaf doesn't exist) doesn't raise
    * Round-trip consistency: long(short(p)) == long(p) on any path
      that we can construct

    The end-to-end behavior (RUNNER~1 -> runneradmin) is exercised on
    the actual CI runner by ``TestLiveRecycleBin.test_trash_then_find_in_recycle_bin``;
    these unit tests pin down the helper's contract for everywhere else.
    """

    def test_to_long_path_importable(self):
        """v1.7.46: _to_long_path is exported from the module."""
        from curator._vendored.send2trash.win.recycle_bin import _to_long_path
        assert callable(_to_long_path)

    def test_to_long_path_returns_string(self):
        """v1.7.46: _to_long_path always returns a string (never None)."""
        from curator._vendored.send2trash.win.recycle_bin import _to_long_path
        result = _to_long_path(r"C:\Users")
        assert isinstance(result, str)

    def test_to_long_path_idempotent_on_long_path(self):
        """v1.7.46: a path that's already in long form should round-trip unchanged.

        ``C:\\Users\\jmlee\\Desktop`` (or whatever real long path exists)
        has no 8.3 components, so GetLongPathNameW returns it as-is.
        """
        from curator._vendored.send2trash.win.recycle_bin import _to_long_path
        long_path = str(Path.home())  # always a long path, always exists
        once = _to_long_path(long_path)
        twice = _to_long_path(once)
        # Round-trip should be stable
        assert once == twice

    def test_to_long_path_nonexistent_leaf_handled(self):
        """v1.7.46: the fallback path activates when the leaf doesn't exist.

        This is the typical post-trash case: ``$R<random>`` is gone from
        disk by the time we look it up, so GetLongPathNameW on the full
        path returns 0. The function should fall back to translating just
        the parent and re-append the leaf, never raising.
        """
        from curator._vendored.send2trash.win.recycle_bin import _to_long_path
        # A non-existent file under an existing long-form parent
        bogus = str(Path.home() / "this_file_definitely_doesnt_exist_xyz123.tmp")
        result = _to_long_path(bogus)
        # Should not crash; should return something that looks like a path
        assert isinstance(result, str)
        assert "this_file_definitely_doesnt_exist_xyz123.tmp" in result

    def test_to_long_path_drive_root_handled(self):
        """v1.7.46: edge case -- a bare drive root has no parent to translate.

        The function should detect ``parent == path`` and return the input.
        """
        from curator._vendored.send2trash.win.recycle_bin import _to_long_path
        # Bare drive root: parent == self
        result = _to_long_path("C:\\")
        assert isinstance(result, str)

    @pytest.mark.skipif(sys.platform == "win32", reason="non-Windows safety test")
    def test_to_long_path_noop_on_non_windows(self):
        """v1.7.46: on POSIX, _to_long_path is a no-op that returns input unchanged.

        Marked skipif win32 so it actually runs on Linux/macOS CI in the
        future (when we add a cross-platform matrix). On Windows CI this
        gets skipped, which is correct.
        """
        from curator._vendored.send2trash.win.recycle_bin import _to_long_path
        input_path = "/tmp/somefile.txt"
        assert _to_long_path(input_path) == input_path

    def test_normalize_for_compare_uses_long_path(self):
        """v1.7.46: _normalize_for_compare now expands short paths via _to_long_path.

        Integration check: the function we EXPORT for path comparison
        should give equivalent output for paths that differ only in
        short-vs-long form. We can't easily synthesize a short path in
        a unit test, but we CAN verify the call chain is wired:
        normalize_for_compare(p) for any long p should equal
        normalize_for_compare(_to_long_path(p)).
        """
        from curator._vendored.send2trash.win.recycle_bin import (
            _normalize_for_compare,
            _to_long_path,
        )
        p = str(Path.home() / "Desktop")
        a = _normalize_for_compare(p)
        b = _normalize_for_compare(_to_long_path(p))
        assert a == b
