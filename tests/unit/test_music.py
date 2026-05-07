"""Unit tests for MusicService + helpers (Phase Gamma F2 Milestone Gamma-2).

Most tests run pure-Python with no audio dependency: ``sanitize_path_component``,
the ``_to_int`` / ``_to_str`` value helpers, ``propose_destination`` template
logic, and ``MusicMetadata`` property semantics.

The ``read_tags`` path is exercised via a mocked ``mutagen.File`` so we
don't need a real audio fixture to verify the integration. The mocked
object behaves like the real mutagen file (dict-style tag access + an
``info`` namespace with ``length``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from curator.services.music import (
    AUDIO_EXTENSIONS,
    UNKNOWN_ALBUM,
    UNKNOWN_ARTIST,
    MusicMetadata,
    MusicService,
    _to_int,
    _to_str,
    sanitize_path_component,
)


# ===========================================================================
# sanitize_path_component
# ===========================================================================


class TestSanitizePathComponent:
    def test_passes_through_clean_string(self):
        assert sanitize_path_component("Pink Floyd") == "Pink Floyd"

    def test_replaces_illegal_chars_with_underscore(self):
        assert sanitize_path_component('AC/DC') == "AC_DC"
        assert sanitize_path_component('Foo<bar>') == "Foo_bar_"
        assert sanitize_path_component('a:b') == "a_b"
        assert sanitize_path_component('quote"') == "quote_"
        assert sanitize_path_component(r"back\slash") == "back_slash"
        assert sanitize_path_component("pipe|fish") == "pipe_fish"
        assert sanitize_path_component("ques?") == "ques_"
        assert sanitize_path_component("star*") == "star_"

    def test_strips_control_chars(self):
        assert sanitize_path_component("hello\x00world") == "hello_world"
        assert sanitize_path_component("a\x1fb") == "a_b"

    def test_collapses_whitespace(self):
        assert sanitize_path_component("foo   bar") == "foo bar"
        # Tab and newline are control chars (\x09, \x0a) caught by the
        # illegal-char regex BEFORE whitespace collapse runs — they're
        # replaced with underscore, which is more faithful than silently
        # swallowing them as spaces.
        assert sanitize_path_component("foo\tbar") == "foo_bar"
        assert sanitize_path_component("foo\nbar") == "foo_bar"

    def test_strips_leading_trailing_dots_and_spaces(self):
        # Windows refuses these.
        assert sanitize_path_component("...album...") == "album"
        assert sanitize_path_component("   spaced   ") == "spaced"
        assert sanitize_path_component(" .mixed. ") == "mixed"

    def test_empty_input_returns_fallback(self):
        assert sanitize_path_component("") == "Unknown"
        assert sanitize_path_component("", fallback="Whatever") == "Whatever"

    def test_only_illegal_chars_falls_back(self):
        # After sanitization this becomes empty after strip.
        assert sanitize_path_component("...") == "Unknown"

    def test_caps_long_components(self):
        long = "x" * 500
        result = sanitize_path_component(long)
        assert len(result) <= 200

    def test_avoids_windows_reserved_names(self):
        # CON, PRN, AUX, NUL plus COM1..9 / LPT1..9 are reserved on Windows.
        # We prefix with underscore to defuse them.
        assert sanitize_path_component("CON") == "_CON"
        assert sanitize_path_component("nul") == "_nul"
        assert sanitize_path_component("LPT1") == "_LPT1"
        # Even with extension after a dot, Windows still treats them as reserved.
        assert sanitize_path_component("CON.txt") == "_CON.txt"
        # Names that just happen to start with reserved letters are fine.
        assert sanitize_path_component("CONcert") == "CONcert"


# ===========================================================================
# _to_str / _to_int value helpers
# ===========================================================================


class TestToStr:
    def test_simple_string(self):
        assert _to_str("hello") == "hello"

    def test_strips_whitespace(self):
        assert _to_str("  hello  ") == "hello"

    def test_unwraps_list(self):
        assert _to_str(["first", "second"]) == "first"

    def test_skips_empty_in_list(self):
        assert _to_str(["", None, "actual"]) == "actual"

    def test_none_returns_none(self):
        assert _to_str(None) is None

    def test_empty_string_returns_none(self):
        assert _to_str("") is None
        assert _to_str("   ") is None


class TestToInt:
    def test_simple_int(self):
        assert _to_int(3) == 3

    def test_string_int(self):
        assert _to_int("12") == 12

    def test_track_number_with_total(self):
        # "3/12" \u2192 just 3 (mutagen often emits this format)
        assert _to_int("3/12") == 3

    def test_year_with_full_date(self):
        # "2023-01-15" \u2192 2023
        assert _to_int("2023-01-15") == 2023

    def test_unwraps_list(self):
        assert _to_int(["7"]) == 7

    def test_unparseable_returns_none(self):
        assert _to_int("not a number") is None
        assert _to_int("") is None

    def test_none_returns_none(self):
        assert _to_int(None) is None


# ===========================================================================
# MusicMetadata properties
# ===========================================================================


class TestMusicMetadata:
    def test_effective_artist_prefers_album_artist(self):
        m = MusicMetadata(artist="Track Artist", album_artist="Album Artist")
        assert m.effective_artist == "Album Artist"

    def test_effective_artist_falls_back_to_artist(self):
        m = MusicMetadata(artist="Track Artist", album_artist=None)
        assert m.effective_artist == "Track Artist"

    def test_effective_artist_falls_back_to_unknown(self):
        m = MusicMetadata()
        assert m.effective_artist == UNKNOWN_ARTIST

    def test_effective_album_falls_back_to_unknown(self):
        m = MusicMetadata()
        assert m.effective_album == UNKNOWN_ALBUM

    def test_has_useful_tags_true_with_any_field(self):
        assert MusicMetadata(title="X").has_useful_tags is True
        assert MusicMetadata(album="Y").has_useful_tags is True
        assert MusicMetadata(artist="Z").has_useful_tags is True
        assert MusicMetadata(album_artist="W").has_useful_tags is True

    def test_has_useful_tags_false_when_empty(self):
        assert MusicMetadata().has_useful_tags is False

    def test_has_useful_tags_false_when_only_track_number(self):
        # Track number alone isn't useful for organizing.
        m = MusicMetadata(track_number=3, year=2024)
        assert m.has_useful_tags is False


# ===========================================================================
# MusicService.is_audio_file
# ===========================================================================


class TestIsAudioFile:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("song.mp3", True),
            ("song.MP3", True),  # case-insensitive
            ("song.flac", True),
            ("song.m4a", True),
            ("song.opus", True),
            ("song.wav", True),
            ("song.txt", False),
            ("song.jpg", False),
            ("noext", False),
            ("foo/bar/baz.flac", True),
        ],
    )
    def test_recognizes_common_audio_extensions(self, path, expected):
        svc = MusicService()
        assert svc.is_audio_file(path) is expected

    def test_extension_set_matches_audio_extensions_constant(self):
        # Ensure all extensions we test against are in the canonical set.
        svc = MusicService()
        for ext in (".mp3", ".flac", ".m4a", ".opus", ".wav"):
            assert svc.is_audio_file(f"x{ext}") is True
            assert ext in AUDIO_EXTENSIONS


# ===========================================================================
# MusicService.propose_destination
# ===========================================================================


class TestProposeDestination:
    def test_full_metadata_produces_canonical_path(self, tmp_path):
        svc = MusicService()
        meta = MusicMetadata(
            artist="Pink Floyd",
            album="The Wall",
            title="Comfortably Numb",
            track_number=6,
        )
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "track.mp3",
            target_root=tmp_path / "library",
        )
        assert dest == tmp_path / "library" / "Pink Floyd" / "The Wall" / "06 - Comfortably Numb.mp3"

    def test_no_track_number_omits_nn_prefix(self, tmp_path):
        svc = MusicService()
        meta = MusicMetadata(
            artist="Some Band",
            album="Single",
            title="That Song",
            track_number=None,
        )
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "x.flac",
            target_root=tmp_path / "lib",
        )
        assert dest == tmp_path / "lib" / "Some Band" / "Single" / "That Song.flac"

    def test_missing_artist_uses_unknown(self, tmp_path):
        svc = MusicService()
        meta = MusicMetadata(album="X", title="Y", track_number=1)
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "x.mp3",
            target_root=tmp_path,
        )
        assert dest.parts[-3] == UNKNOWN_ARTIST

    def test_missing_album_uses_unknown(self, tmp_path):
        svc = MusicService()
        meta = MusicMetadata(artist="X", title="Y", track_number=1)
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "x.mp3",
            target_root=tmp_path,
        )
        assert dest.parts[-2] == UNKNOWN_ALBUM

    def test_missing_title_falls_back_to_filename_stem(self, tmp_path):
        svc = MusicService()
        meta = MusicMetadata(artist="A", album="B")
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "original_filename.mp3",
            target_root=tmp_path,
        )
        # Title fell back to "original_filename" (the stem).
        assert "original_filename" in dest.name

    def test_album_artist_used_for_path_when_set(self, tmp_path):
        # Compilations: album_artist should win for the path.
        svc = MusicService()
        meta = MusicMetadata(
            artist="Various",
            album_artist="Various Artists",
            album="Greatest Hits",
            title="Song",
            track_number=1,
        )
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "x.mp3",
            target_root=tmp_path,
        )
        assert dest.parts[-3] == "Various Artists"

    def test_illegal_chars_in_tags_are_sanitized(self, tmp_path):
        svc = MusicService()
        meta = MusicMetadata(
            artist="AC/DC",
            album='High Voltage: "Live"',
            title="T.N.T.",
            track_number=4,
        )
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "x.mp3",
            target_root=tmp_path,
        )
        # Slash is illegal as a path component (would split into two).
        # Sanitization replaces with underscore.
        assert dest.parts[-3] == "AC_DC"
        # Quote and colon are illegal on Windows.
        assert ":" not in dest.parts[-2]
        assert '"' not in dest.parts[-2]

    def test_extension_lowercased(self, tmp_path):
        svc = MusicService()
        meta = MusicMetadata(artist="A", album="B", title="C", track_number=1)
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "X.MP3",
            target_root=tmp_path,
        )
        assert dest.suffix == ".mp3"

    def test_destination_under_target_root(self, tmp_path):
        svc = MusicService()
        meta = MusicMetadata(artist="X", album="Y", title="Z", track_number=1)
        target = tmp_path / "library"
        dest = svc.propose_destination(
            meta,
            original_path=tmp_path / "x.mp3",
            target_root=target,
        )
        # The dest must be under target.
        assert str(dest).startswith(str(target))


# ===========================================================================
# MusicService.read_tags (mocked mutagen)
# ===========================================================================


class _FakeMutagenFile:
    """Quacks like mutagen.File output: dict-style tags + info.length."""

    def __init__(self, tags: dict, length: float | None = None):
        self.tags = tags
        self.info = SimpleNamespace(length=length)


class TestReadTags:
    def test_returns_none_for_non_audio(self, tmp_path):
        svc = MusicService()
        plain = tmp_path / "notes.txt"
        plain.write_text("hi")
        assert svc.read_tags(plain) is None

    def test_returns_none_for_nonexistent(self, tmp_path):
        svc = MusicService()
        ghost = tmp_path / "ghost.mp3"
        # Doesn't exist; even though extension is right, no file = None.
        assert svc.read_tags(ghost) is None

    def test_returns_none_when_mutagen_unavailable(self, tmp_path, monkeypatch):
        target = tmp_path / "x.mp3"
        target.write_bytes(b"")
        monkeypatch.setattr(
            "curator.services.music._mutagen_available",
            lambda: False,
        )
        svc = MusicService()
        assert svc.read_tags(target) is None

    def test_extracts_basic_tags_from_mutagen(self, tmp_path, monkeypatch):
        target = tmp_path / "song.mp3"
        target.write_bytes(b"")  # mutagen.File would read this; we mock instead

        fake = _FakeMutagenFile(
            tags={
                "artist": ["Pink Floyd"],
                "albumartist": ["Pink Floyd"],
                "album": ["The Wall"],
                "title": ["Comfortably Numb"],
                "tracknumber": ["6/26"],
                "discnumber": ["2/2"],
                "date": ["1979"],
                "genre": ["Progressive Rock"],
            },
            length=382.0,
        )
        monkeypatch.setattr(
            "mutagen.File",
            lambda path, easy=True: fake,
        )

        svc = MusicService()
        meta = svc.read_tags(target)
        assert meta is not None
        assert meta.artist == "Pink Floyd"
        assert meta.album_artist == "Pink Floyd"
        assert meta.album == "The Wall"
        assert meta.title == "Comfortably Numb"
        assert meta.track_number == 6
        assert meta.disc_number == 2
        assert meta.year == 1979
        assert meta.genre == "Progressive Rock"
        assert meta.duration_seconds == 382.0
        assert meta.has_useful_tags is True

    def test_returns_metadata_even_when_tags_empty(self, tmp_path, monkeypatch):
        target = tmp_path / "untagged.mp3"
        target.write_bytes(b"")

        fake = _FakeMutagenFile(tags={}, length=10.0)
        monkeypatch.setattr("mutagen.File", lambda path, easy=True: fake)

        svc = MusicService()
        meta = svc.read_tags(target)
        # Still returns a MusicMetadata, just one with has_useful_tags=False.
        assert meta is not None
        assert meta.has_useful_tags is False
        assert meta.duration_seconds == 10.0

    def test_returns_none_when_mutagen_returns_none(self, tmp_path, monkeypatch):
        # mutagen.File returns None for files it can't recognize.
        target = tmp_path / "bogus.mp3"
        target.write_bytes(b"not actually an mp3")

        monkeypatch.setattr("mutagen.File", lambda path, easy=True: None)

        svc = MusicService()
        assert svc.read_tags(target) is None

    def test_returns_none_on_mutagen_exception(self, tmp_path, monkeypatch):
        target = tmp_path / "broken.mp3"
        target.write_bytes(b"")

        def raise_it(path, easy=True):
            raise RuntimeError("mutagen exploded")

        monkeypatch.setattr("mutagen.File", raise_it)

        svc = MusicService()
        assert svc.read_tags(target) is None
