"""Photo metadata + destination-path service (Phase Gamma F3).

Phase Gamma Milestone Gamma-3 deliverable. Same shape as
:mod:`curator.services.music`: extract metadata, propose a canonical
destination, plug into ``OrganizeService.plan(organize_type=...)``.

The default template is ``{YYYY}/{YYYY-MM-DD}/{original_filename}``,
which is the layout most photo-management workflows converge on
(Lightroom, digiKam, Apple Photos exports, etc.). Photographers who
want event-based folders can drop in a custom template later.

Date resolution order:
    1. EXIF DateTimeOriginal (when the shutter fired)
    2. EXIF DateTimeDigitized
    3. EXIF DateTime (last modified by camera/software)
    4. Filesystem mtime
    5. "Unknown date" placeholder

Step 4 (mtime fallback) is important because phones often strip EXIF
on iCloud sync, and many older scans/screenshots have no EXIF date at
all. Files that survive even that will still be organized somewhere
sensible.

The service is intentionally conservative about non-photo files
masquerading as photos (extension lies). It uses Pillow's verify path
which catches truncated / corrupted images and returns None metadata.

Future v0.25+ additions:
    * Video support via pymediainfo (MP4/MOV taken_at)
    * RAW formats requiring rawpy
    * GPS-based destinations (``Country/City/YYYY-MM-DD``)
    * Burst / event detection (group photos taken within N seconds)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from curator.services.music import sanitize_path_component


# ---------------------------------------------------------------------------
# Photo file extensions
# ---------------------------------------------------------------------------

PHOTO_EXTENSIONS: frozenset[str] = frozenset({
    # Common consumer formats
    ".jpg",
    ".jpeg",
    ".jpe",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".heic",
    ".heif",
    # Professional formats
    ".tiff",
    ".tif",
    # RAW formats (Pillow handles some natively; others get mtime fallback)
    ".dng",   # Adobe DNG
    ".cr2",   # Canon
    ".cr3",   # Canon (newer)
    ".nef",   # Nikon
    ".arw",   # Sony
    ".orf",   # Olympus
    ".raf",   # Fujifilm
    ".rw2",   # Panasonic
    ".pef",   # Pentax
    ".srw",   # Samsung
})
"""Lowercased file extensions (with dot) that PhotoService will try to read."""


# Defaults for missing fields
UNKNOWN_DATE = "Unknown date"


# ---------------------------------------------------------------------------
# EXIF date parsing
# ---------------------------------------------------------------------------

# EXIF dates are formatted "YYYY:MM:DD HH:MM:SS" per the spec. Some
# cameras emit subseconds or timezones; we accept whatever date prefix
# parses as a date.
_EXIF_DATE_RE = re.compile(
    r"^(?P<y>\d{4})[:\-](?P<mo>\d{1,2})[:\-](?P<d>\d{1,2})"
    r"(?:[ T](?P<h>\d{1,2}):(?P<mi>\d{1,2}):(?P<s>\d{1,2}))?"
)


def _parse_exif_datetime(value: Any) -> datetime | None:
    """Parse an EXIF date/time string into a datetime.

    Returns None for empty / unparseable / nonsense values. Times that
    parse but with year < 1900 (corrupt EXIF or "0000:00:00 00:00:00")
    are treated as None \u2014 we don't want photos in /1899/ folders.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = _EXIF_DATE_RE.match(s)
    if not m:
        return None
    try:
        year = int(m.group("y"))
        month = int(m.group("mo"))
        day = int(m.group("d"))
        if year < 1900 or month == 0 or day == 0:
            return None
        hour = int(m.group("h") or 0)
        minute = int(m.group("mi") or 0)
        second = int(m.group("s") or 0)
        # Some cameras emit invalid time components (e.g. seconds=60).
        # Normalize by clamping; a slightly-wrong time is still better
        # data than None.
        hour = min(max(hour, 0), 23)
        minute = min(max(minute, 0), 59)
        second = min(max(second, 0), 59)
        return datetime(year, month, day, hour, minute, second)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# PhotoMetadata
# ---------------------------------------------------------------------------


@dataclass
class PhotoMetadata:
    """Structured metadata extracted from a photo file."""

    taken_at: datetime | None = None
    taken_at_source: str | None = None  # "exif_original" | "exif_digitized" | "exif_modified" | "mtime"
    camera_make: str | None = None
    camera_model: str | None = None
    width: int | None = None
    height: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_useful_date(self) -> bool:
        """True if we have a date from any source."""
        return self.taken_at is not None

    @property
    def year_str(self) -> str:
        return f"{self.taken_at.year:04d}" if self.taken_at else UNKNOWN_DATE

    @property
    def date_str(self) -> str:
        if self.taken_at is None:
            return UNKNOWN_DATE
        return self.taken_at.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Pillow availability + EXIF tag mapping
# ---------------------------------------------------------------------------

# EXIF tag IDs we care about (from PIL.ExifTags.TAGS). Hard-coded to
# avoid needing PIL just to look up the constants \u2014 these IDs are
# stable per the EXIF spec.
_EXIF_TAG_DATETIME_ORIGINAL = 36867
_EXIF_TAG_DATETIME_DIGITIZED = 36868
_EXIF_TAG_DATETIME = 306
_EXIF_TAG_MAKE = 271
_EXIF_TAG_MODEL = 272


def _pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# PhotoService
# ---------------------------------------------------------------------------


class PhotoService:
    """Read photo metadata + propose date-based destination paths.

    Args:
        template: the destination-path template. The default produces
            ``YYYY/YYYY-MM-DD/original_filename``.
            Placeholders: ``{year}``, ``{date}``, ``{filename}``.
            ``{filename}`` includes the extension.
    """

    DEFAULT_TEMPLATE = "{year}/{date}/{filename}"

    def __init__(self, template: str = DEFAULT_TEMPLATE) -> None:
        self.template = template

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_photo_file(self, path: str | Path) -> bool:
        """True if ``path`` has a known photo extension."""
        return Path(path).suffix.lower() in PHOTO_EXTENSIONS

    def read_metadata(self, path: str | Path) -> PhotoMetadata | None:
        """Extract :class:`PhotoMetadata` from a photo file.

        Returns None on:
            * non-photo extension
            * file doesn't exist

        Returns a populated :class:`PhotoMetadata` even when EXIF is
        missing or unreadable \u2014 the mtime fallback ensures the file
        still organizes somewhere sensible. Callers can check
        ``has_useful_date`` to decide.
        """
        p = Path(path)
        if not self.is_photo_file(p):
            return None
        if not p.exists():
            return None

        meta = PhotoMetadata()

        # 1. Try EXIF via Pillow.
        if _pillow_available():
            self._read_exif(p, meta)

        # 2. mtime fallback if no EXIF date worked.
        if meta.taken_at is None:
            try:
                stat_mtime = p.stat().st_mtime
                meta.taken_at = datetime.fromtimestamp(stat_mtime)
                meta.taken_at_source = "mtime"
            except OSError as e:
                logger.debug(
                    "PhotoService: stat failed for {p}: {e}", p=p, e=e,
                )
                # Leave taken_at as None \u2014 propose_destination handles it.

        return meta

    def _read_exif(self, p: Path, meta: PhotoMetadata) -> None:
        """Populate ``meta`` from Pillow EXIF where possible.

        Mutates ``meta`` in place. Quietly returns without raising on
        any error (corrupt JPEG, unsupported format, missing tags).
        """
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError:  # pragma: no cover \u2014 _pillow_available guards
            return

        try:
            with Image.open(p) as img:
                # Cache size before any other access (lazy).
                meta.width = img.width
                meta.height = img.height
                exif = img.getexif() if hasattr(img, "getexif") else None
        except (UnidentifiedImageError, OSError, ValueError) as e:
            logger.debug("PhotoService: PIL.Image.open failed for {p}: {e}", p=p, e=e)
            return
        except Exception as e:  # noqa: BLE001 \u2014 PIL throws assorted exceptions
            logger.debug("PhotoService: unexpected PIL error for {p}: {e}", p=p, e=e)
            return

        if not exif:
            return

        # Try EXIF date tags in priority order.
        for tag_id, source_label in (
            (_EXIF_TAG_DATETIME_ORIGINAL, "exif_original"),
            (_EXIF_TAG_DATETIME_DIGITIZED, "exif_digitized"),
            (_EXIF_TAG_DATETIME, "exif_modified"),
        ):
            value = exif.get(tag_id)
            parsed = _parse_exif_datetime(value)
            if parsed is not None:
                meta.taken_at = parsed
                meta.taken_at_source = source_label
                break

        # Camera info (best-effort, often empty for screenshots).
        make = exif.get(_EXIF_TAG_MAKE)
        if make is not None:
            s = str(make).strip()
            meta.camera_make = s if s else None

        model = exif.get(_EXIF_TAG_MODEL)
        if model is not None:
            s = str(model).strip()
            meta.camera_model = s if s else None

        # Stash everything else for callers that want it.
        try:
            meta.raw = {str(k): str(v) for k, v in exif.items()}
        except Exception:  # noqa: BLE001
            pass

    def propose_destination(
        self,
        metadata: PhotoMetadata,
        *,
        original_path: str | Path,
        target_root: str | Path,
    ) -> Path:
        """Apply the template and return the proposed destination.

        Args:
            metadata: from :meth:`read_metadata`.
            original_path: the existing file path \u2014 the basename is
                preserved (photos rarely have meaningful tags to retitle
                from, and existing filenames often encode useful info
                like ``IMG_4823.jpg`` or ``DSC_0042.NEF``).
            target_root: where the organized library should live.

        Returns:
            Absolute :class:`Path` under ``target_root``. Path
            components are sanitized for filesystem safety.
        """
        original = Path(original_path)
        target = Path(target_root)

        year = sanitize_path_component(metadata.year_str, fallback=UNKNOWN_DATE)
        date = sanitize_path_component(metadata.date_str, fallback=UNKNOWN_DATE)
        filename = sanitize_path_component(
            original.name, fallback=f"file{original.suffix.lower()}",
        )

        return target / year / date / filename


__all__ = [
    "PHOTO_EXTENSIONS",
    "PhotoMetadata",
    "PhotoService",
    "UNKNOWN_DATE",
]
