"""Windows Recycle Bin reader — closes Q14.

DESIGN.md §10. Phase Alpha originally couldn't track ``os_trash_location``
for trashed files because the ctypes-based ``send2trash`` Windows path
just calls ``SHFileOperationW(FO_DELETE | FOF_ALLOWUNDO)`` and doesn't
return where the file ended up. This module solves the lookup *after*
the fact by parsing the metadata files Windows writes into the Recycle
Bin alongside each trashed file.

Recycle Bin layout (Windows Vista+):

    <drive>:\\$Recycle.Bin\\<user-SID>\\
        $IXXXXXX.ext    ← metadata (the "index" file)
        $RXXXXXX.ext    ← actual content (renamed)

The ``$I`` and ``$R`` file pair share the same 6-character random suffix
and the same extension as the original file. Trashed-from-D: files go
into ``D:\\$Recycle.Bin\\<SID>\\``, etc. — each drive has its own bin.

``$I`` file binary format:

    v1 (Vista through Windows 8):
        offset 0x00  uint64  header version (= 1, little-endian)
        offset 0x08  int64   original file size in bytes
        offset 0x10  FILETIME deletion timestamp (100ns since 1601-01-01 UTC)
        offset 0x18  bytes   original path, fixed 520 bytes UTF-16LE,
                             null-terminated, 260 wide chars max.

    v2 (Windows 10+):
        offset 0x00  uint64  header version (= 2)
        offset 0x08  int64   original file size
        offset 0x10  FILETIME deletion timestamp
        offset 0x18  int32   path length IN WIDE CHARACTERS
                             (i.e. byte length / 2; includes trailing NUL)
        offset 0x1C  bytes   UTF-16LE path, length matches the int32 above.

This module:
  * exports :class:`RecycleBinEntry` — one parsed ``$I`` record
  * exports :func:`enumerate_recycle_bin` — walk all bins (all drives,
    all SIDs the current user can read)
  * exports :func:`find_in_recycle_bin` — find the most recently deleted
    entry whose original path matches a given absolute path

Non-Windows imports raise :class:`RuntimeError` immediately so callers
get a clear error rather than mysterious ``ctypes`` failures.
"""

from __future__ import annotations

import os
import string
import struct
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Windows FILETIME epoch (Jan 1, 1601 UTC) and its delta to the Unix epoch.
_WINDOWS_TICK_PER_MICROSECOND = 10  # FILETIME is in 100-ns intervals
_WINDOWS_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class RecycleBinEntry:
    """One row from the Recycle Bin, parsed from a ``$I`` file."""

    original_path: str
    """Absolute pre-trash path, exactly as Windows recorded it
    (including drive letter, e.g. ``C:\\Users\\me\\file.txt``)."""

    file_size: int
    """File size in bytes at trash time."""

    deleted_at: datetime
    """When the file was trashed (UTC)."""

    index_path: Path
    """Path to the ``$IXXXXXX.ext`` metadata file."""

    content_path: Path
    """Path to the matching ``$RXXXXXX.ext`` content file
    (same suffix and extension as ``index_path``, with the leading
    ``$I`` swapped to ``$R``)."""

    version: int
    """``$I`` format version: 1 or 2."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class RecycleBinParseError(ValueError):
    """Malformed ``$I`` file."""


def _filetime_to_datetime(ft: int) -> datetime:
    """Convert a Windows FILETIME (100-ns ticks since 1601-01-01) to UTC datetime."""
    if ft <= 0:
        return _WINDOWS_EPOCH
    return _WINDOWS_EPOCH + timedelta(microseconds=ft / _WINDOWS_TICK_PER_MICROSECOND)


def parse_index_file(index_path: Path) -> RecycleBinEntry:
    """Parse a ``$IXXXXXX.ext`` metadata file.

    Raises :class:`RecycleBinParseError` if the file is too short or has
    an unrecognized version header.
    """
    data = index_path.read_bytes()
    if len(data) < 0x18:
        raise RecycleBinParseError(
            f"{index_path}: only {len(data)} bytes, need at least 24 for the header"
        )

    version, file_size, filetime = struct.unpack_from("<QqQ", data, 0)

    if version == 1:
        # v1: 520 bytes fixed UTF-16LE path starting at 0x18
        if len(data) < 0x18 + 520:
            raise RecycleBinParseError(
                f"{index_path}: v1 file is only {len(data)} bytes, need 540"
            )
        raw = data[0x18 : 0x18 + 520]
        original_path = raw.decode("utf-16-le", errors="replace").rstrip("\x00")
    elif version == 2:
        # v2: 4-byte length-in-wide-chars at 0x18, then variable UTF-16LE path
        if len(data) < 0x1C:
            raise RecycleBinParseError(
                f"{index_path}: v2 header truncated ({len(data)} bytes)"
            )
        (path_len_wide,) = struct.unpack_from("<I", data, 0x18)
        path_byte_len = path_len_wide * 2
        if len(data) < 0x1C + path_byte_len:
            raise RecycleBinParseError(
                f"{index_path}: declared path length {path_byte_len}B exceeds "
                f"file size {len(data)}B"
            )
        raw = data[0x1C : 0x1C + path_byte_len]
        original_path = raw.decode("utf-16-le", errors="replace").rstrip("\x00")
    else:
        raise RecycleBinParseError(
            f"{index_path}: unknown $I version {version} (expected 1 or 2)"
        )

    # The companion $R file lives in the same dir, with $I → $R.
    content_path = index_path.with_name(index_path.name.replace("$I", "$R", 1))

    return RecycleBinEntry(
        original_path=original_path,
        file_size=file_size,
        deleted_at=_filetime_to_datetime(filetime),
        index_path=index_path,
        content_path=content_path,
        version=version,
    )


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

def _candidate_drives() -> list[Path]:
    """Return the existing top-level ``$Recycle.Bin`` paths across drive letters.

    Windows fixed/removable drives have ``$Recycle.Bin`` at their root.
    Network drives don't, so we silently skip those that aren't present.
    """
    if sys.platform != "win32":
        return []
    drives: list[Path] = []
    for letter in string.ascii_uppercase:
        bin_root = Path(f"{letter}:\\$Recycle.Bin")
        # ``Path.exists`` will swallow PermissionError on locked drives.
        try:
            if bin_root.exists():
                drives.append(bin_root)
        except OSError:
            continue
    return drives


def _enumerate_index_files(bin_root: Path):
    """Yield every ``$IXXXXXX.*`` file under ``bin_root``, across all SIDs.

    Each per-user subfolder is named after a SID (S-1-5-21-...). We don't
    parse the SID itself — we just iterate. Folders we can't read are
    silently skipped (e.g. another user's bin we don't have access to).
    """
    try:
        sid_dirs = list(bin_root.iterdir())
    except OSError:
        return
    for sid_dir in sid_dirs:
        if not sid_dir.is_dir():
            continue
        try:
            for entry in sid_dir.iterdir():
                if entry.is_file() and entry.name.startswith("$I"):
                    yield entry
        except OSError:
            # Permission denied on another user's bin, skipped silently.
            continue


def enumerate_recycle_bin(only_drive: str | None = None):
    """Walk every parseable ``$I`` file across (or under) one drive.

    Args:
        only_drive: optional single-letter drive (``"C"``) to restrict to.
            Useful when the original path's drive is known — saves walking
            other drives.

    Yields:
        :class:`RecycleBinEntry` for each parseable index file. Files
        that fail to parse are skipped silently (a corrupt ``$I`` file
        shouldn't break the whole search).
    """
    if sys.platform != "win32":
        return

    if only_drive is not None:
        roots = [Path(f"{only_drive.upper()}:\\$Recycle.Bin")]
        roots = [r for r in roots if r.exists()]
    else:
        roots = _candidate_drives()

    for root in roots:
        for index_file in _enumerate_index_files(root):
            try:
                yield parse_index_file(index_file)
            except (RecycleBinParseError, OSError):
                continue


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def _drive_letter(path: str) -> str | None:
    """Return the drive letter of an absolute path (without ``:``), or None."""
    if len(path) >= 2 and path[1] == ":":
        return path[0].upper()
    return None


def _normalize_for_compare(path: str) -> str:
    """Normalize a Windows path for case-insensitive equality comparison.

    Windows paths are case-insensitive. We also unify forward-slashes
    and trailing separators so equivalent paths compare equal.
    """
    return os.path.normcase(os.path.normpath(path))


def find_in_recycle_bin(original_path: str) -> RecycleBinEntry | None:
    """Find the most-recently-trashed entry whose original path matches.

    Searches only the drive that ``original_path`` was on (Windows trashes
    files into the Recycle Bin of the same drive). If multiple entries
    match (e.g. the same path was trashed twice in the past), returns the
    one with the latest ``deleted_at``.

    Returns None if no match (or running on a non-Windows platform).
    """
    if sys.platform != "win32":
        return None

    drive = _drive_letter(original_path)
    target = _normalize_for_compare(original_path)

    best: RecycleBinEntry | None = None
    for entry in enumerate_recycle_bin(only_drive=drive):
        if _normalize_for_compare(entry.original_path) != target:
            continue
        if best is None or entry.deleted_at > best.deleted_at:
            best = entry
    return best


__all__ = [
    "RecycleBinEntry",
    "RecycleBinParseError",
    "enumerate_recycle_bin",
    "find_in_recycle_bin",
    "parse_index_file",
]
