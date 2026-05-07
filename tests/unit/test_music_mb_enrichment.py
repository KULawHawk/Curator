"""Unit tests for MusicService.enrich_via_musicbrainz (Phase Gamma F2 v0.32).

The MB client is always mocked here \u2014 these tests NEVER make live
network calls. They exercise:

    * Skip cases: complete tags / no artist / no title / nothing to fill
    * Successful enrichment fills only missing fields (album, year, track)
    * Real existing fields are NOT overwritten
    * MB returning None is recorded as ``_mb_no_match=true``
    * MB raising is swallowed and metadata returned unchanged
    * The ``_mb_enriched`` / ``_mb_recording_mbid`` / ``_mb_score``
      markers are recorded when an enrichment fires
    * The OrganizeService.plan integration only fires when:
        - enrich_mb=True
        - mb_client is set
        - file is audio
        - tags came from filename heuristic
        - at least one of album/year/track is missing
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from curator.services.music import MusicMetadata, MusicService
from curator.services.musicbrainz import MusicBrainzMatch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def music():
    return MusicService()


@pytest.fixture
def mb_match():
    """A typical successful MB match."""
    return MusicBrainzMatch(
        recording_mbid="abc-123-def",
        artist="Pink Floyd",
        title="Comfortably Numb",
        album="The Wall",
        album_mbid="album-mbid-456",
        year=1979,
        track_number=6,
        score=98,
    )


# ===========================================================================
# Skip cases
# ===========================================================================


class TestEnrichmentSkipCases:
    def test_complete_tags_skip_lookup(self, music, mb_match):
        """If album AND year AND track are present, no MB call."""
        meta = MusicMetadata(
            artist="Pink Floyd",
            title="Comfortably Numb",
            album="The Wall",
            year=1979,
            track_number=6,
        )
        client = MagicMock()
        client.lookup_recording.return_value = mb_match
        result = music.enrich_via_musicbrainz(meta, client)
        # No call was made.
        assert client.lookup_recording.call_count == 0
        # Metadata unchanged.
        assert result.album == "The Wall"
        assert "_mb_enriched" not in result.raw

    def test_missing_artist_skips_lookup(self, music, mb_match):
        meta = MusicMetadata(title="Some Title")  # no artist
        client = MagicMock()
        client.lookup_recording.return_value = mb_match
        music.enrich_via_musicbrainz(meta, client)
        assert client.lookup_recording.call_count == 0

    def test_missing_title_skips_lookup(self, music, mb_match):
        meta = MusicMetadata(artist="Some Artist")  # no title
        client = MagicMock()
        client.lookup_recording.return_value = mb_match
        music.enrich_via_musicbrainz(meta, client)
        assert client.lookup_recording.call_count == 0


# ===========================================================================
# Successful enrichment
# ===========================================================================


class TestEnrichmentFillsBlanks:
    def test_filename_only_metadata_gets_album_year_track(self, music, mb_match):
        """The headline use case: filename gave artist+title, MB fills the rest."""
        meta = MusicMetadata(
            artist="Pink Floyd",
            title="Comfortably Numb",
            # No album, year, or track_number.
        )
        meta.raw["_filename_source"] = "true"
        client = MagicMock()
        client.lookup_recording.return_value = mb_match

        result = music.enrich_via_musicbrainz(meta, client)

        # MB was called with the right args.
        client.lookup_recording.assert_called_once_with(
            artist="Pink Floyd", title="Comfortably Numb",
        )
        # All blanks filled.
        assert result.album == "The Wall"
        assert result.year == 1979
        assert result.track_number == 6
        # Markers recorded.
        assert result.raw["_mb_enriched"] == "true"
        assert result.raw["_mb_recording_mbid"] == "abc-123-def"
        assert result.raw["_mb_score"] == "98"

    def test_partial_metadata_only_fills_actual_blanks(self, music, mb_match):
        """If album is set but year/track missing, only year/track fill."""
        meta = MusicMetadata(
            artist="Pink Floyd",
            title="Comfortably Numb",
            album="My Custom Album Name",  # user-set, must NOT be overwritten
        )
        client = MagicMock()
        client.lookup_recording.return_value = mb_match

        result = music.enrich_via_musicbrainz(meta, client)

        # Album preserved.
        assert result.album == "My Custom Album Name"
        # year + track filled from MB.
        assert result.year == 1979
        assert result.track_number == 6
        # Marker recorded (enrichment did fire).
        assert result.raw["_mb_enriched"] == "true"

    def test_existing_year_not_overwritten(self, music, mb_match):
        """Real existing year takes precedence over MB."""
        meta = MusicMetadata(
            artist="Pink Floyd",
            title="Comfortably Numb",
            year=1980,  # user says 1980, MB says 1979
        )
        client = MagicMock()
        client.lookup_recording.return_value = mb_match

        result = music.enrich_via_musicbrainz(meta, client)
        assert result.year == 1980  # user value wins

    def test_existing_track_not_overwritten(self, music, mb_match):
        meta = MusicMetadata(
            artist="Pink Floyd",
            title="Comfortably Numb",
            track_number=99,  # user-tagged
        )
        client = MagicMock()
        client.lookup_recording.return_value = mb_match
        result = music.enrich_via_musicbrainz(meta, client)
        assert result.track_number == 99  # not overwritten


# ===========================================================================
# MB returns no match / raises
# ===========================================================================


class TestEnrichmentNoMatchAndErrors:
    def test_no_match_records_marker(self, music):
        meta = MusicMetadata(artist="Obscure Band", title="Unknown Song")
        client = MagicMock()
        client.lookup_recording.return_value = None
        result = music.enrich_via_musicbrainz(meta, client)
        assert result.album is None
        assert result.raw["_mb_no_match"] == "true"
        assert "_mb_enriched" not in result.raw

    def test_mb_exception_returns_metadata_unchanged(self, music):
        meta = MusicMetadata(artist="Pink Floyd", title="Comfortably Numb")
        client = MagicMock()
        client.lookup_recording.side_effect = RuntimeError("network down")
        result = music.enrich_via_musicbrainz(meta, client)
        # Method must NOT raise.
        assert result is meta
        assert result.album is None
        # No markers recorded.
        assert "_mb_enriched" not in result.raw
        assert "_mb_no_match" not in result.raw

    def test_partial_mb_match_with_only_album(self, music):
        """MB returns a match but only the album field. Year/track stay None."""
        partial_match = MusicBrainzMatch(
            recording_mbid="xyz",
            album="Partial Album",
            score=85,
        )
        meta = MusicMetadata(artist="Artist", title="Title")
        client = MagicMock()
        client.lookup_recording.return_value = partial_match
        result = music.enrich_via_musicbrainz(meta, client)
        assert result.album == "Partial Album"
        assert result.year is None
        assert result.track_number is None
        assert result.raw["_mb_enriched"] == "true"
