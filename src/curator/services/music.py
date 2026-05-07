"""Music metadata + destination-path service (Phase Gamma F2).

Phase Gamma Milestone Gamma-2 deliverable. This is the first
type-specific organization pipeline. Given an audio file, MusicService
extracts the relevant tags and proposes a destination path that
matches the canonical ``{Artist}/{Album}/{NN - Title}.{ext}`` template.

The service is intentionally split into three layers:

  1. **Tag reading** — :meth:`read_tags` returns a structured
     :class:`MusicMetadata` (or None for non-audio / unreadable files).
     Backed by ``mutagen`` (the reference Python audio-metadata library)
     for full read/write support; if mutagen isn't installed, this layer
     silently degrades and the rest of Curator still works.
  2. **Destination templating** — :meth:`propose_destination` applies
     the path template to a metadata record, with strict filename
     sanitization (Windows-safe + Unix-safe, length-capped per component).
  3. **Type detection** — :meth:`is_audio_file` returns True for known
     audio extensions. Used by ``OrganizeService`` to decide which
     pipeline to apply.

Future v0.22+ additions:

  * **DONE in v0.27** — Filename heuristic for files with empty mutagen
    tags (parses ``NN - Artist - Title.ext`` and other common patterns).
  * **DONE in v0.27** — MusicBrainz lookup via ``musicbrainzngs`` is
    available as an opt-in enrichment step (see
    :mod:`curator.services.musicbrainz`); not invoked automatically
    from ``read_tags`` to avoid network/rate-limit surprises.
  * AcoustID + Chromaprint audio fingerprinting for files where
    even the filename doesn't match anything sensible.
  * Compilation handling (Various Artists), disc numbers.
  * Conflict resolution (two files claim the same destination path).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


# ---------------------------------------------------------------------------
# Audio file extensions
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp3",
    ".flac",
    ".m4a",
    ".m4b",
    ".aac",
    ".ogg",
    ".oga",
    ".opus",
    ".wav",
    ".wma",
    ".ape",
    ".alac",
    ".aiff",
    ".aif",
    ".dsf",
    ".dff",
})
"""Lowercased file extensions (with dot) that MusicService will try to tag-read."""


# ---------------------------------------------------------------------------
# Defaults for missing tags
# ---------------------------------------------------------------------------

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------

# Characters that are illegal on Windows filesystems (and a few we'd
# rather not see anywhere): < > : " / \ | ? * + control chars.
_ILLEGAL_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Component length cap. Windows MAX_PATH is 260 by default; keeping
# each component under 200 chars means even deeply nested templates
# stay safe. (FAT32 is 255 per component; ext4 is 255 bytes.)
_MAX_COMPONENT_LEN = 200

# Windows reserved names. Names matching these (case-insensitive,
# regardless of extension) are illegal on Windows.
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})


def sanitize_path_component(s: str, *, fallback: str = "Unknown") -> str:
    """Return a string safe to use as one component in a filesystem path.

    Replaces illegal chars with ``_``, collapses whitespace, strips
    leading/trailing dots and spaces (Windows hates these), caps length,
    and avoids reserved names.

    If the input is empty or sanitizes to empty, returns ``fallback``.
    """
    if not s:
        return fallback
    cleaned = _ILLEGAL_FS_CHARS.sub("_", s)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip(". ")
    if not cleaned:
        return fallback
    if len(cleaned) > _MAX_COMPONENT_LEN:
        cleaned = cleaned[:_MAX_COMPONENT_LEN].rstrip(". ")
    if cleaned.upper().split(".")[0] in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


# ---------------------------------------------------------------------------
# MusicMetadata
# ---------------------------------------------------------------------------


@dataclass
class MusicMetadata:
    """Structured tag data extracted from an audio file.

    All string fields are stripped + normalized; numeric fields are
    None when the underlying tag was missing or unparseable. ``raw``
    preserves the underlying mutagen dict for callers that need fields
    we don't surface explicitly.
    """

    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    title: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    year: int | None = None
    genre: str | None = None
    duration_seconds: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_artist(self) -> str:
        """Album-artist if set (better for compilations); otherwise track artist."""
        return self.album_artist or self.artist or UNKNOWN_ARTIST

    @property
    def effective_album(self) -> str:
        return self.album or UNKNOWN_ALBUM

    @property
    def effective_title(self) -> str | None:
        return self.title

    @property
    def has_useful_tags(self) -> bool:
        """True if at least artist OR album OR title is set."""
        return any((self.artist, self.album_artist, self.album, self.title))


# ---------------------------------------------------------------------------
# Tag-reading backend availability
# ---------------------------------------------------------------------------


def _mutagen_available() -> bool:
    try:
        import mutagen  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Helpers for parsing fuzzy tag values
# ---------------------------------------------------------------------------


def _first(value: Any) -> Any:
    """Mutagen often returns lists. Pull the first non-empty entry."""
    if isinstance(value, (list, tuple)):
        for v in value:
            if v not in (None, ""):
                return v
        return None
    return value


def _to_str(value: Any) -> str | None:
    v = _first(value)
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _to_int(value: Any) -> int | None:
    """Parse fuzzy integer values like ``"3"``, ``"3/12"``, ``3``."""
    v = _first(value)
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # Common track-number format: "3/12" \u2192 just 3
    s = s.split("/", 1)[0].strip()
    # Year tags sometimes look like "2023-01-15" \u2192 2023
    s = s.split("-", 1)[0].strip()
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Filename heuristic (v0.27)
# ---------------------------------------------------------------------------

# Patterns matched against the filename STEM (no extension), applied in
# order so the most specific pattern wins. Each pattern uses ``-`` as
# the canonical separator after we normalize underscores to spaces.
#
# Designed for the realistic shapes most ripped/exported audio libraries
# use: track-numbered + artist + title, or just artist + title, or just
# track-numbered + title. Avoids matching unrelated 4-digit year-like
# prefixes by requiring the leading number to be 1-3 digits.
_FILENAME_MUSIC_PATTERNS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    # "NN - Artist - Title" or "NN. Artist - Title" / "NN) Artist - Title"
    (re.compile(r"^\s*(?P<track>\d{1,3})\s*[-.\)]\s*(?P<artist>.+?)\s+-\s+(?P<title>.+?)\s*$"),
     ("track", "artist", "title")),
    # "NN - Title" or "NN. Title" (no artist component)
    (re.compile(r"^\s*(?P<track>\d{1,3})\s*[-.\)]\s*(?P<title>.+?)\s*$"),
     ("track", "title")),
    # "Artist - Title" (must contain at least one ' - ' separator)
    (re.compile(r"^\s*(?P<artist>.+?)\s+-\s+(?P<title>.+?)\s*$"),
     ("artist", "title")),
)


def _normalize_filename_separators(stem: str) -> str:
    """Normalize underscore-separated filenames into space-separated.

    Many ripped libraries use ``Artist_-_Title.mp3`` rather than
    ``Artist - Title.mp3``. We collapse ``_-_`` into ``  -  `` so the
    patterns below match both styles. Standalone underscores within a
    title (uncommon but possible) are NOT touched — only the ``_-_``
    separator pattern.
    """
    # "_-_" or "_ - _" → " - "
    s = re.sub(r"\s*_+\s*-\s*_+\s*", " - ", stem)
    # Trailing/leading underscore-only segments around a dash
    s = re.sub(r"_+(\s*-\s*)", r" \1", s)
    s = re.sub(r"(\s*-\s*)_+", r"\1 ", s)
    # Collapse repeated whitespace from the substitutions.
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_music_filename(stem: str) -> dict[str, Any]:
    """Best-effort extraction of artist/title/track from a filename stem.

    Returns a dict with keys among ``artist``, ``title``, ``track_number``.
    Empty dict if no pattern matched. Always returns a dict so callers
    can ``meta.update(...)``-style consume it.

    Designed to be conservative: only fills fields that are missing
    from existing metadata; the caller decides which keys to honor.
    """
    if not stem:
        return {}
    normalized = _normalize_filename_separators(stem)
    for regex, fields in _FILENAME_MUSIC_PATTERNS:
        m = regex.match(normalized)
        if m is None:
            continue
        out: dict[str, Any] = {}
        for field_name in fields:
            value = m.group(field_name).strip()
            if not value:
                continue
            if field_name == "track":
                try:
                    out["track_number"] = int(value)
                except ValueError:
                    pass
            else:
                out[field_name] = value
        # A pattern matched but produced nothing useful — keep trying.
        if out:
            return out
    return {}


# ---------------------------------------------------------------------------
# MusicService
# ---------------------------------------------------------------------------


class MusicService:
    """Read music tags + propose canonical destination paths.

    Args:
        template: the destination-path template. The default produces
            ``Artist/Album/NN - Title.ext`` when track number is known,
            and ``Artist/Album/Title.ext`` otherwise.
            Placeholders: ``{artist}``, ``{album}``, ``{track:02d}``,
            ``{title}``, ``{ext}``.

            When ``track`` is None, the template segment containing it
            is dropped automatically (handled in :meth:`propose_destination`).
    """

    DEFAULT_TEMPLATE = "{artist}/{album}/{track:02d} - {title}{ext}"
    """Default path template. Falls back to omitting ``NN - `` if track is None."""

    def __init__(self, template: str = DEFAULT_TEMPLATE) -> None:
        self.template = template

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_audio_file(self, path: str | Path) -> bool:
        """True if ``path`` has a known audio extension."""
        return Path(path).suffix.lower() in AUDIO_EXTENSIONS

    def read_tags(self, path: str | Path) -> MusicMetadata | None:
        """Extract :class:`MusicMetadata` from an audio file.

        Returns None on:
          * mutagen not installed
          * non-audio extension
          * file doesn't exist
          * file exists but isn't a recognized audio container
          * any read error (logged at debug level)

        Returns a :class:`MusicMetadata` even when individual tags are
        missing — callers should check ``has_useful_tags`` to decide
        whether the result is worth using.
        """
        p = Path(path)
        if not self.is_audio_file(p):
            return None
        if not p.exists():
            return None
        if not _mutagen_available():
            logger.debug(
                "MusicService.read_tags: mutagen not installed; "
                "install curator[organize] to enable music tagging."
            )
            return None

        try:
            from mutagen import File as MutagenFile

            audio = MutagenFile(str(p), easy=True)
            if audio is None:
                return None
        except Exception as e:
            logger.debug("mutagen failed for {p}: {e}", p=p, e=e)
            return None

        # easy=True normalizes tag names across formats (artist, album,
        # title, tracknumber, date, genre, albumartist).
        raw = dict(audio.tags or {}) if audio.tags else {}
        meta = MusicMetadata(
            artist=_to_str(raw.get("artist")),
            album_artist=_to_str(raw.get("albumartist")),
            album=_to_str(raw.get("album")),
            title=_to_str(raw.get("title")),
            track_number=_to_int(raw.get("tracknumber")),
            disc_number=_to_int(raw.get("discnumber")),
            year=_to_int(raw.get("date") or raw.get("year") or raw.get("originaldate")),
            genre=_to_str(raw.get("genre")),
            duration_seconds=getattr(audio.info, "length", None),
            raw=raw,
        )

        # v0.27 filename heuristic: if mutagen produced no useful tags
        # (artist/album/title), parse the filename for clues. This rescues
        # the common case of a ripped collection whose ID3 tags were
        # stripped but whose filenames still encode "NN - Artist - Title".
        # We ONLY fill fields that mutagen left blank; we never overwrite.
        if not meta.has_useful_tags:
            from_name = _parse_music_filename(p.stem)
            if from_name:
                if meta.artist is None and from_name.get("artist"):
                    meta.artist = from_name["artist"]
                if meta.title is None and from_name.get("title"):
                    meta.title = from_name["title"]
                if meta.track_number is None and from_name.get("track_number"):
                    meta.track_number = from_name["track_number"]
                meta.raw["_filename_source"] = "true"

        return meta

    def propose_destination(
        self,
        metadata: MusicMetadata,
        *,
        original_path: str | Path,
        target_root: str | Path,
    ) -> Path:
        """Apply the template and return the proposed destination path.

        Args:
            metadata: tag data (normally from :meth:`read_tags`).
            original_path: the existing file path \u2014 used to derive
                ``{ext}`` and the title fallback.
            target_root: where the organized library should live.
                The returned path is rooted here.

        Returns:
            Absolute :class:`Path` under ``target_root``. Caller decides
            whether to actually move (Stage / Apply modes); this just
            says where it would go.

        The path components are sanitized for filesystem safety
        (illegal-char replacement, length cap, Windows reserved names).
        """
        original = Path(original_path)
        ext = original.suffix.lower()
        target = Path(target_root)

        artist = sanitize_path_component(
            metadata.effective_artist,
            fallback=UNKNOWN_ARTIST,
        )
        album = sanitize_path_component(
            metadata.effective_album,
            fallback=UNKNOWN_ALBUM,
        )
        title_str = (
            metadata.effective_title
            or original.stem  # fall back to the existing filename stem
        )
        title = sanitize_path_component(title_str, fallback=original.stem or "Unknown Title")

        # Build the leaf: "NN - Title.ext" or just "Title.ext".
        if metadata.track_number is not None:
            leaf = f"{metadata.track_number:02d} - {title}{ext}"
        else:
            leaf = f"{title}{ext}"

        # Re-sanitize the leaf because the joined string might exceed
        # the per-component cap or contain anything we missed.
        # (sanitize_path_component is idempotent.)
        leaf = sanitize_path_component(leaf, fallback=f"track{ext}")

        return target / artist / album / leaf

    # ------------------------------------------------------------------
    # MusicBrainz enrichment (v0.32, opt-in)
    # ------------------------------------------------------------------

    def enrich_via_musicbrainz(
        self,
        metadata: MusicMetadata,
        mb_client,
    ) -> MusicMetadata:
        """Fill in missing album / year / track_number from MusicBrainz.

        **Never overwrites** existing real data — only fills blank
        fields. Designed to be called AFTER ``read_tags`` so that:

          * Files with complete mutagen tags are returned unchanged
            (no point in a network call).
          * Files where the v0.27 filename heuristic produced just
            artist + title get album/year/track_number enriched from
            the MusicBrainz canonical data.
          * Files with NO useful tags at all (no artist or title) are
            skipped — there's nothing to look up.

        Args:
            metadata: the :class:`MusicMetadata` to enrich.
            mb_client: a :class:`MusicBrainzClient` instance. Caller is
                responsible for constructing it with proper contact
                info; this method just calls
                :meth:`MusicBrainzClient.lookup_recording`.

        Returns:
            The same :class:`MusicMetadata` instance, possibly mutated
            in place. Records ``raw["_mb_enriched"] = "true"`` and
            ``raw["_mb_recording_mbid"]`` when an enrichment fired.
            Records ``raw["_mb_no_match"] = "true"`` when MB returned
            nothing (so callers can distinguish "didn't try" from
            "tried and failed").

        Network errors / missing dependencies are absorbed by the MB
        client itself — it returns None rather than raising. This
        method also never raises: the worst case is the metadata is
        returned unchanged.
        """
        # Skip files that already have everything we'd populate. The
        # network call would just confirm what we already have.
        if (
            metadata.album is not None
            and metadata.year is not None
            and metadata.track_number is not None
        ):
            return metadata

        # Skip files with nothing to look up. Need both artist and
        # title to make a useful MB query.
        if not metadata.artist or not metadata.title:
            return metadata

        try:
            match = mb_client.lookup_recording(
                artist=metadata.artist,
                title=metadata.title,
            )
        except Exception as e:  # noqa: BLE001 — defensive at boundary
            logger.debug(
                "MB lookup failed for {a} - {t}: {e}",
                a=metadata.artist, t=metadata.title, e=e,
            )
            return metadata

        if match is None:
            metadata.raw["_mb_no_match"] = "true"
            return metadata

        # Fill ONLY blanks, never overwrite real data.
        if metadata.album is None and match.album:
            metadata.album = match.album
        if metadata.year is None and match.year is not None:
            metadata.year = match.year
        if metadata.track_number is None and match.track_number is not None:
            metadata.track_number = match.track_number

        metadata.raw["_mb_enriched"] = "true"
        if match.recording_mbid:
            metadata.raw["_mb_recording_mbid"] = match.recording_mbid
        if match.score is not None:
            metadata.raw["_mb_score"] = str(match.score)
        return metadata


__all__ = [
    "AUDIO_EXTENSIONS",
    "MusicMetadata",
    "MusicService",
    "UNKNOWN_ALBUM",
    "UNKNOWN_ARTIST",
    "sanitize_path_component",
    "_parse_music_filename",
]
