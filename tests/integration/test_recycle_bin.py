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
