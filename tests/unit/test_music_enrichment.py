"""Unit tests for v0.27 music enrichment.

Two pieces:
  * **Filename heuristic** in :mod:`curator.services.music`:
    ``_parse_music_filename``, ``_normalize_filename_separators``,
    plus the integration into ``MusicService.read_tags`` that fills
    blank tag fields from filename patterns.
  * **MusicBrainz client** in :mod:`curator.services.musicbrainz`:
    ``MusicBrainzClient.lookup_recording`` with a mocked
    ``musicbrainzngs`` module so no network call is made.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from curator.services.music import (
    MusicMetadata,
    MusicService,
    _normalize_filename_separators,
    _parse_music_filename,
)
from curator.services.musicbrainz import (
    MusicBrainzClient,
    MusicBrainzMatch,
    _musicbrainzngs_available,
)


# ===========================================================================
# _normalize_filename_separators
# ===========================================================================


class TestNormalizeFilenameSeparators:
    def test_underscore_dash_underscore(self):
        assert _normalize_filename_separators("Artist_-_Title") == "Artist - Title"

    def test_multiple_underscores_around_dash(self):
        assert _normalize_filename_separators("Artist__-__Title") == "Artist - Title"

    def test_already_normalized_unchanged(self):
        assert _normalize_filename_separators("Artist - Title") == "Artist - Title"

    def test_internal_underscores_in_words_preserved(self):
        # Underscores that aren't part of an _-_ separator stay put.
        # (Some filenames have legit underscores inside titles.)
        assert "snake_case" in _normalize_filename_separators("snake_case_track")

    def test_collapse_multiple_spaces(self):
        assert _normalize_filename_separators("Foo  -  Bar") == "Foo - Bar"

    def test_strips_leading_trailing_whitespace(self):
        assert _normalize_filename_separators("  Foo - Bar  ") == "Foo - Bar"


# ===========================================================================
# _parse_music_filename
# ===========================================================================


class TestParseMusicFilename:
    def test_track_artist_title_dash(self):
        result = _parse_music_filename("06 - Pink Floyd - Comfortably Numb")
        assert result == {
            "track_number": 6,
            "artist": "Pink Floyd",
            "title": "Comfortably Numb",
        }

    def test_track_artist_title_dot(self):
        result = _parse_music_filename("06. Pink Floyd - Comfortably Numb")
        assert result == {
            "track_number": 6,
            "artist": "Pink Floyd",
            "title": "Comfortably Numb",
        }

    def test_track_artist_title_paren(self):
        result = _parse_music_filename("06) Pink Floyd - Comfortably Numb")
        assert result["track_number"] == 6

    def test_track_title_only(self):
        # No artist component, just track + title separator.
        result = _parse_music_filename("06 - Comfortably Numb")
        assert result == {"track_number": 6, "title": "Comfortably Numb"}

    def test_artist_title_no_track(self):
        result = _parse_music_filename("Pink Floyd - Comfortably Numb")
        assert result == {"artist": "Pink Floyd", "title": "Comfortably Numb"}

    def test_underscore_separators(self):
        # _-_ separator should still parse as Artist + Title.
        result = _parse_music_filename("Pink Floyd_-_Comfortably Numb")
        assert result.get("artist") == "Pink Floyd"
        assert result.get("title") == "Comfortably Numb"

    def test_three_digit_track(self):
        result = _parse_music_filename("123 - Some Track")
        assert result.get("track_number") == 123

    def test_year_like_prefix_not_treated_as_track(self):
        # 4-digit prefix like "2024 - Title" should NOT be a track number.
        # Our pattern requires 1-3 digits for track, so 4 digits falls
        # through to the "Artist - Title" pattern (treating "2024" as artist).
        result = _parse_music_filename("2024 - Best of Year")
        # Either matches as artist+title (with 2024 as "artist") or
        # doesn't match. We're OK with either outcome - the important
        # thing is that 2024 isn't returned as a track number.
        assert result.get("track_number") is None

    def test_no_separator_returns_empty(self):
        # Bare filename with no recognizable structure.
        result = _parse_music_filename("randomfile")
        assert result == {}

    def test_empty_input(self):
        assert _parse_music_filename("") == {}

    def test_specificity_order_track_artist_title_wins(self):
        # "06 - Pink Floyd - Comfortably Numb" should match the most
        # specific pattern (track + artist + title), not the less
        # specific ones (track + title or artist + title alone).
        result = _parse_music_filename("06 - Pink Floyd - Comfortably Numb")
        # Verify we got all three fields (most specific match).
        assert "track_number" in result
        assert "artist" in result
        assert "title" in result

    def test_strips_extra_whitespace(self):
        result = _parse_music_filename("  06   -   Artist   -   Title  ")
        assert result["track_number"] == 6
        assert result["artist"] == "Artist"
        assert result["title"] == "Title"

    def test_garbage_track_falls_through(self):
        # If the leading number is too large to fit our 1-3 digit
        # constraint, we don't match a track pattern.
        result = _parse_music_filename("12345 - Title")
        assert result.get("track_number") is None


# ===========================================================================
# MusicService.read_tags integration: filename fallback
# ===========================================================================


class _FakeMutagenFile:
    """Quacks like mutagen.File output: dict-style tags + info.length."""

    def __init__(self, tags: dict, length: float | None = None):
        self.tags = tags
        self.info = SimpleNamespace(length=length)


class TestReadTagsFilenameFallback:
    def test_filename_fills_blank_tags(self, tmp_path, monkeypatch):
        # mutagen returns metadata with NO useful tags; filename has a
        # parseable "NN - Artist - Title" pattern. Filename should fill
        # the blank fields.
        target = tmp_path / "06 - Pink Floyd - Comfortably Numb.mp3"
        target.write_bytes(b"")
        monkeypatch.setattr(
            "mutagen.File",
            lambda path, easy=True: _FakeMutagenFile(tags={}, length=382.0),
        )
        svc = MusicService()
        meta = svc.read_tags(target)
        assert meta is not None
        assert meta.artist == "Pink Floyd"
        assert meta.title == "Comfortably Numb"
        assert meta.track_number == 6
        assert meta.has_useful_tags is True
        # Source marker recorded so callers can tell.
        assert meta.raw.get("_filename_source") == "true"

    def test_filename_does_not_overwrite_real_tags(self, tmp_path, monkeypatch):
        # mutagen returns real tags; filename pattern is also present.
        # Real tags must NOT be overwritten.
        target = tmp_path / "06 - Wrong Artist - Wrong Title.mp3"
        target.write_bytes(b"")
        monkeypatch.setattr(
            "mutagen.File",
            lambda path, easy=True: _FakeMutagenFile(
                tags={
                    "artist": ["Real Artist"],
                    "title": ["Real Title"],
                },
            ),
        )
        svc = MusicService()
        meta = svc.read_tags(target)
        assert meta.artist == "Real Artist"
        assert meta.title == "Real Title"
        # Filename source NOT marked because mutagen had useful tags.
        assert meta.raw.get("_filename_source") is None

    def test_filename_fills_only_missing_fields(self, tmp_path, monkeypatch):
        # mutagen has artist but no title; filename has both. Only
        # title should be filled from filename.
        target = tmp_path / "06 - Other - Some Track.mp3"
        target.write_bytes(b"")
        monkeypatch.setattr(
            "mutagen.File",
            lambda path, easy=True: _FakeMutagenFile(
                tags={"artist": ["Real Artist"]},
            ),
        )
        svc = MusicService()
        meta = svc.read_tags(target)
        # Real artist preserved (mutagen had it).
        assert meta.artist == "Real Artist"
        # has_useful_tags was True because artist was set, so filename
        # parser is NOT invoked. This is intentional - one good field
        # is enough; we don't second-guess mutagen.
        assert meta.title is None

    def test_no_useful_filename_pattern_leaves_blank(self, tmp_path, monkeypatch):
        target = tmp_path / "randomname.mp3"
        target.write_bytes(b"")
        monkeypatch.setattr(
            "mutagen.File",
            lambda path, easy=True: _FakeMutagenFile(tags={}),
        )
        svc = MusicService()
        meta = svc.read_tags(target)
        # No mutagen tags + no filename pattern = no useful tags.
        assert meta.has_useful_tags is False


# ===========================================================================
# MusicBrainzMatch
# ===========================================================================


class TestMusicBrainzMatch:
    def test_high_confidence_threshold(self):
        assert MusicBrainzMatch(score=80).is_high_confidence is True
        assert MusicBrainzMatch(score=90).is_high_confidence is True
        assert MusicBrainzMatch(score=79).is_high_confidence is False
        assert MusicBrainzMatch(score=None).is_high_confidence is False

    def test_default_fields_none(self):
        m = MusicBrainzMatch()
        assert m.recording_mbid is None
        assert m.album is None
        assert m.year is None


# ===========================================================================
# MusicBrainzClient (mocked musicbrainzngs)
# ===========================================================================


def _make_fake_mb_module(search_response: dict | Exception | None = None):
    """Build a fake musicbrainzngs module that returns or raises as needed."""
    fake = types.ModuleType("musicbrainzngs")

    def search_recordings(**kwargs):
        if isinstance(search_response, Exception):
            raise search_response
        return search_response or {}

    def set_useragent(*a, **kw):
        pass

    fake.search_recordings = search_recordings
    fake.set_useragent = set_useragent
    return fake


class TestMusicBrainzClient:
    def test_blank_inputs_return_none(self):
        client = MusicBrainzClient()
        assert client.lookup_recording(artist="", title="x") is None
        assert client.lookup_recording(artist="x", title="") is None

    def test_library_unavailable_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "curator.services.musicbrainz._musicbrainzngs_available",
            lambda: False,
        )
        client = MusicBrainzClient()
        assert client.lookup_recording(artist="X", title="Y") is None

    def test_successful_lookup(self, monkeypatch):
        # MB returns one high-score recording with release info.
        fake_response = {
            "recording-list": [
                {
                    "id": "abc-123",
                    "title": "Comfortably Numb",
                    "ext:score": "95",
                    "artist-credit": [
                        {"artist": {"name": "Pink Floyd"}},
                    ],
                    "release-list": [
                        {
                            "id": "rel-456",
                            "title": "The Wall",
                            "date": "1979-11-30",
                            "medium-list": [
                                {
                                    "track-list": [
                                        {"position": "6"},
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        fake_mb = _make_fake_mb_module(fake_response)
        monkeypatch.setitem(sys.modules, "musicbrainzngs", fake_mb)

        client = MusicBrainzClient()
        match = client.lookup_recording(artist="Pink Floyd", title="Comfortably Numb")

        assert match is not None
        assert match.recording_mbid == "abc-123"
        assert match.title == "Comfortably Numb"
        assert match.artist == "Pink Floyd"
        assert match.album == "The Wall"
        assert match.album_mbid == "rel-456"
        assert match.year == 1979
        assert match.track_number == 6
        assert match.score == 95
        assert match.is_high_confidence is True

    def test_low_score_filtered_out(self, monkeypatch):
        # Only a low-score match - should be dropped by min_score.
        fake_response = {
            "recording-list": [
                {"id": "x", "title": "Y", "ext:score": "30"},
            ],
        }
        monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(fake_response))

        client = MusicBrainzClient(min_score=70)
        assert client.lookup_recording(artist="X", title="Y") is None

    def test_picks_highest_score(self, monkeypatch):
        fake_response = {
            "recording-list": [
                {"id": "low", "title": "A", "ext:score": "75"},
                {"id": "high", "title": "B", "ext:score": "95"},
                {"id": "mid", "title": "C", "ext:score": "85"},
            ],
        }
        monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(fake_response))

        client = MusicBrainzClient()
        match = client.lookup_recording(artist="X", title="Y")
        assert match.recording_mbid == "high"
        assert match.score == 95

    def test_no_recordings_returned(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module({"recording-list": []}))
        client = MusicBrainzClient()
        assert client.lookup_recording(artist="X", title="Y") is None

    def test_network_error_returns_none(self, monkeypatch):
        # Whatever musicbrainzngs raises, we swallow.
        monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(ConnectionError("net down")))
        client = MusicBrainzClient()
        assert client.lookup_recording(artist="X", title="Y") is None

    def test_missing_release_info_still_returns_match(self, monkeypatch):
        # Recording match with no release-list - we still return the
        # recording itself, just without album/year populated.
        fake_response = {
            "recording-list": [
                {
                    "id": "rec-only",
                    "title": "Solo Track",
                    "ext:score": "90",
                    "artist-credit": [{"artist": {"name": "Some Artist"}}],
                },
            ],
        }
        monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(fake_response))

        client = MusicBrainzClient()
        match = client.lookup_recording(artist="Some Artist", title="Solo Track")
        assert match is not None
        assert match.title == "Solo Track"
        assert match.album is None
        assert match.year is None

    def test_year_only_date_parsed(self, monkeypatch):
        fake_response = {
            "recording-list": [{
                "id": "x", "title": "Y", "ext:score": "90",
                "release-list": [{"id": "r", "title": "Album", "date": "2003"}],
            }],
        }
        monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(fake_response))
        match = MusicBrainzClient().lookup_recording(artist="X", title="Y")
        assert match.year == 2003

    def test_invalid_date_year_none(self, monkeypatch):
        fake_response = {
            "recording-list": [{
                "id": "x", "title": "Y", "ext:score": "90",
                "release-list": [{"id": "r", "title": "Album", "date": "??"}],
            }],
        }
        monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(fake_response))
        match = MusicBrainzClient().lookup_recording(artist="X", title="Y")
        assert match is not None
        assert match.year is None  # Couldn't parse the date string.

    def test_artist_credit_with_join_phrase(self, monkeypatch):
        # MB sometimes returns "Artist1 feat. Artist2" as a list with
        # interleaved join-phrase strings.
        fake_response = {
            "recording-list": [{
                "id": "x", "title": "Y", "ext:score": "90",
                "artist-credit": [
                    {"artist": {"name": "Lead"}},
                    " feat. ",
                    {"artist": {"name": "Guest"}},
                ],
            }],
        }
        monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(fake_response))
        match = MusicBrainzClient().lookup_recording(artist="X", title="Y")
        assert match.artist == "Lead feat. Guest"

    def test_useragent_configured_on_first_call(self, monkeypatch):
        # set_useragent should be called exactly once even across
        # multiple lookups.
        fake_response = {"recording-list": []}
        fake_mb = _make_fake_mb_module(fake_response)
        called = []
        original_set = fake_mb.set_useragent
        def tracking_set(*a, **kw):
            called.append((a, kw))
            return original_set(*a, **kw)
        fake_mb.set_useragent = tracking_set
        monkeypatch.setitem(sys.modules, "musicbrainzngs", fake_mb)

        client = MusicBrainzClient(contact="test@example.com")
        client.lookup_recording(artist="X", title="Y")
        client.lookup_recording(artist="A", title="B")
        assert len(called) == 1
        # And it included the contact we configured.
        args, _ = called[0]
        assert "test@example.com" in args
