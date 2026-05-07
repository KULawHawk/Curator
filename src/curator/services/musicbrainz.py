"""MusicBrainz lookup client (Phase Gamma F2 enrichment, v0.27).

A thin, opt-in wrapper around ``musicbrainzngs`` for callers who want
canonical metadata (album, year, MBIDs) for music files whose local
tags are missing or unreliable. NOT invoked automatically from
:meth:`curator.services.music.MusicService.read_tags` because:

  1. **Network calls are slow.** A naive 5000-track lookup would take
     85 minutes at MB's 1-req/sec rate limit.
  2. **The TOS requires a custom User-Agent** identifying the
     application + contact info. We default to one that identifies
     Curator but callers SHOULD override it for their own deployments.
  3. **Empty / wrong matches are common** for obscure releases. The
     caller should be in control of when to apply enrichment vs. just
     use what local tags + filename gave them.

Typical usage::

    from curator.services.musicbrainz import MusicBrainzClient

    mb = MusicBrainzClient(contact="me@example.com")
    match = mb.lookup_recording(artist="Pink Floyd", title="Comfortably Numb")
    if match is not None:
        meta.album = match.album
        meta.year = match.year

The client respects MB's 1 req/sec rate limit via ``musicbrainzngs``'
own throttling. It catches and logs every error type the library can
raise, returning ``None`` rather than propagating - the calling
pipeline never crashes because MB is down.

This module is **lazy**: importing :mod:`curator.services.musicbrainz`
does NOT import ``musicbrainzngs`` at module load. The actual library
is imported only when :meth:`MusicBrainzClient.lookup_recording` is
called.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger


# Default User-Agent. Per MB's TOS, this MUST identify the application
# + contact. Callers SHOULD override the contact via the constructor.
_DEFAULT_USER_AGENT_APP = "Curator"
_DEFAULT_USER_AGENT_VERSION = "0.33"
_DEFAULT_CONTACT = "https://github.com/anthropics/curator"


# ---------------------------------------------------------------------------
# Match result
# ---------------------------------------------------------------------------


@dataclass
class MusicBrainzMatch:
    """A canonical recording match from MusicBrainz.

    Fields that MB couldn't provide remain None. ``score`` is MB's
    relevance score (0-100) for the search hit - callers may want to
    drop matches below ~80 to avoid bad enrichment.
    """

    recording_mbid: str | None = None
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    album_mbid: str | None = None
    year: int | None = None
    track_number: int | None = None
    score: int | None = None
    raw: dict[str, Any] | None = None

    @property
    def is_high_confidence(self) -> bool:
        """True if MB's relevance score >= 80."""
        return self.score is not None and self.score >= 80


# ---------------------------------------------------------------------------
# Backend availability
# ---------------------------------------------------------------------------


def _musicbrainzngs_available() -> bool:
    try:
        import musicbrainzngs  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# MusicBrainzClient
# ---------------------------------------------------------------------------


class MusicBrainzClient:
    """Look up canonical music metadata via the MusicBrainz public API.

    Args:
        contact: a URL or email identifying who to contact about the
            request. Required by MB's TOS; defaults to Curator's repo
            URL but production callers should override it.
        user_agent_app: application name reported in the User-Agent.
        user_agent_version: application version.
        min_score: matches below this MB relevance score are dropped
            from :meth:`lookup_recording`. Default 70 - conservative.
            Set to 0 to accept everything (useful for testing).
    """

    def __init__(
        self,
        *,
        contact: str = _DEFAULT_CONTACT,
        user_agent_app: str = _DEFAULT_USER_AGENT_APP,
        user_agent_version: str = _DEFAULT_USER_AGENT_VERSION,
        min_score: int = 70,
    ) -> None:
        self.contact = contact
        self.user_agent_app = user_agent_app
        self.user_agent_version = user_agent_version
        self.min_score = min_score
        self._configured = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup_recording(
        self,
        *,
        artist: str,
        title: str,
        limit: int = 5,
    ) -> MusicBrainzMatch | None:
        """Search MusicBrainz for a recording matching artist + title.

        Returns the best match above ``self.min_score`` (default 70),
        or None if MB is unavailable / no matches / all matches below
        the threshold / network error / library not installed.

        The lookup queries MB's recording index, then for the best
        hit pulls back its release info to populate album + year.
        Total: at most 2 HTTP requests per call. MB's library
        auto-throttles to 1 req/sec.
        """
        if not artist or not title:
            return None
        if not _musicbrainzngs_available():
            logger.debug(
                "MusicBrainzClient: musicbrainzngs not installed; "
                "install curator[organize] to enable enrichment."
            )
            return None

        try:
            import musicbrainzngs as mb
        except ImportError:  # pragma: no cover
            return None

        self._configure_user_agent(mb)

        try:
            response = mb.search_recordings(
                artist=artist,
                recording=title,
                limit=limit,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "MusicBrainz search_recordings failed for {a} - {t}: {e}",
                a=artist, t=title, e=e,
            )
            return None

        recordings = response.get("recording-list") or []
        # Filter by min_score, then take the best.
        scored = []
        for r in recordings:
            try:
                score = int(r.get("ext:score", 0))
            except (TypeError, ValueError):
                score = 0
            if score >= self.min_score:
                scored.append((score, r))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]

        match = MusicBrainzMatch(
            recording_mbid=best.get("id"),
            artist=self._extract_artist(best),
            title=best.get("title"),
            score=best_score,
            raw=best,
        )

        # Try to enrich with release info (album, year, track_number).
        release_list = best.get("release-list") or []
        if release_list:
            release = release_list[0]
            match.album = release.get("title")
            match.album_mbid = release.get("id")
            year = self._parse_year(release.get("date"))
            if year is not None:
                match.year = year
            # Track number on this release, if present.
            medium_list = release.get("medium-list") or []
            for medium in medium_list:
                track_list = medium.get("track-list") or []
                for track in track_list:
                    pos = track.get("position") or track.get("number")
                    if pos:
                        try:
                            match.track_number = int(pos)
                        except (TypeError, ValueError):
                            pass
                        break
                if match.track_number is not None:
                    break

        return match

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _configure_user_agent(self, mb) -> None:
        if self._configured:
            return
        try:
            mb.set_useragent(
                self.user_agent_app,
                self.user_agent_version,
                self.contact,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("set_useragent failed: {e}", e=e)
        self._configured = True

    @staticmethod
    def _extract_artist(recording: dict) -> str | None:
        """Pull the credit-name string from a recording's artist-credit."""
        credit = recording.get("artist-credit")
        if not credit:
            return recording.get("artist") or None
        # artist-credit is a list alternating dicts and join phrases.
        names = []
        for entry in credit:
            if isinstance(entry, dict):
                artist = entry.get("artist") or {}
                name = artist.get("name") or entry.get("name")
                if name:
                    names.append(name)
            elif isinstance(entry, str):
                # Join phrase like " feat. "
                names.append(entry)
        joined = "".join(names).strip()
        return joined or None

    @staticmethod
    def _parse_year(date_str: Any) -> int | None:
        """Pull a 4-digit year out of an MB date string.

        MB dates can be ``2023``, ``2023-04``, or ``2023-04-15``.
        """
        if not date_str:
            return None
        s = str(date_str).strip()
        if len(s) < 4:
            return None
        try:
            year = int(s[:4])
            if 1900 <= year <= 2099:
                return year
        except ValueError:
            pass
        return None


__all__ = [
    "MusicBrainzClient",
    "MusicBrainzMatch",
]
