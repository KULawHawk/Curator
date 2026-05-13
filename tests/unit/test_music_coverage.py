"""Focused coverage tests for services/music.py.

Sub-ship v1.7.100 (🎉 100-ship milestone) of the Coverage Sweep arc.

Closes the remaining uncovered lines + partial branches:

* Lines 177-178: `_mutagen_available` ImportError branch (optional dep).
* Line 193: `_first` returns None when list/tuple has only empty entries.
* Lines 288, 292-293: `_parse_music_filename` defensive arms inside
  the per-field loop — exercised via a monkeypatched
  `_FILENAME_MUSIC_PATTERNS` that produces empty-string captures and
  non-numeric "track" values.
* Branch 297->280: `if out: return out` False arm — same
  monkeypatch fixture: a pattern whose match yields empty captures
  for every field, forcing the for-loop to continue to the next
  pattern.
* Branches 395->397, 397->399, 399->401: `read_tags`'s filename-
  rescue fill-only-blanks logic when `from_name` lacks specific
  keys.
* Branches 541->543, 543->545: `enrich_from_match` writing
  `_mb_recording_mbid` and `_mb_score` only when the match has those
  fields populated — covered with a match that lacks both.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

import curator.services.music as music_mod
from curator.services.music import (
    MusicMetadata,
    MusicService,
    _first,
    _mutagen_available,
    _parse_music_filename,
)


# ---------------------------------------------------------------------------
# _mutagen_available ImportError branch (177-178)
# ---------------------------------------------------------------------------


def test_mutagen_available_returns_false_when_module_missing(monkeypatch):
    # Lines 177-178: simulate mutagen not installed.
    monkeypatch.setitem(sys.modules, "mutagen", None)
    assert _mutagen_available() is False


# ---------------------------------------------------------------------------
# _first all-empty entries (193)
# ---------------------------------------------------------------------------


def test_first_returns_none_when_list_has_only_empty_values():
    # Line 193: list/tuple with only None/"" entries → return None.
    assert _first([None, "", None]) is None
    assert _first(["", "", ""]) is None
    assert _first(()) is None


# ---------------------------------------------------------------------------
# _parse_music_filename defensive arms via monkeypatched pattern tuple
# (lines 288, 292-293; branch 297->280)
# ---------------------------------------------------------------------------


def test_parse_filename_defensive_arms_via_custom_patterns(monkeypatch):
    # Cover the defensive arms inside the per-field loop AND the
    # "loop continues when out is empty" branch (297->280). Build a
    # patched _FILENAME_MUSIC_PATTERNS with two synthetic regexes:
    #
    # 1. First pattern's captures are all empty (after .strip()), so
    #    `if not value: continue` (line 287-288) fires for every
    #    field, leaving `out` empty. Then `if out: return out`
    #    (line 297) is False → the loop continues to pattern 2
    #    (branch 297->280).
    # 2. Second pattern has a non-numeric "track" capture, exercising
    #    the `int(value)` failure path (lines 290-293).
    custom_patterns = (
        # First: every capture is whitespace-only (trims to empty)
        (re.compile(r"^(?P<artist>\s+)\s+(?P<title>\s+)$"),
         ("artist", "title")),
        # Second: track is alphabetic (int(value) raises ValueError)
        (re.compile(r"^(?P<track>[A-Z]+)\.(?P<title>.+)$"),
         ("track", "title")),
    )
    monkeypatch.setattr(music_mod, "_FILENAME_MUSIC_PATTERNS", custom_patterns)

    # An input matching only the second pattern. The first pattern
    # would need both groups to be whitespace-only between the regex's
    # explicit `\s+` separator — impossible in practice — so the first
    # pattern returns no match and we move on. To force pattern 1 to
    # match-but-yield-empty, we'd need different regex shapes. The
    # cleaner path: just test pattern 2's int-failure arm.
    result = _parse_music_filename("ZZ.cool song")
    # Track was "ZZ" → int() fails → `pass`. Title was "cool song".
    assert result == {"title": "cool song"}


def test_parse_filename_empty_capture_falls_through(monkeypatch):
    # Line 288 + branch 297->280: when a regex captures an empty
    # string (via .*?), `if not value: continue` fires for that
    # field. With every field-capture empty, the for-loop completes
    # with `out == {}`; `if out:` is False, so the OUTER pattern-
    # loop continues to the next pattern.
    #
    # Construct a pattern with literal anchors around an empty-
    # capable capture, then a follow-up pattern that doesn't match.
    custom_patterns = (
        # "XY" with empty capture in between (.* can match empty)
        (re.compile(r"^X(?P<title>.*?)Y$"),
         ("title",)),
        # Doesn't match "XY"
        (re.compile(r"^XX_NO_MATCH$"),
         ("title",)),
    )
    monkeypatch.setattr(music_mod, "_FILENAME_MUSIC_PATTERNS", custom_patterns)

    # `_normalize_filename_separators("XY")` returns "XY" unchanged
    # (no underscores, dashes, or extra whitespace). Pattern 1
    # matches with title="" → strip → "" → `if not value: continue`
    # → `out` empty → `if out:` False → loop continues → Pattern 2
    # doesn't match → returns {}.
    result = _parse_music_filename("XY")
    assert result == {}


# ---------------------------------------------------------------------------
# read_tags filename-rescue branches 395->397, 397->399, 399->401
# ---------------------------------------------------------------------------


def test_read_tags_filename_rescue_skips_when_meta_already_has_artist(tmp_path, monkeypatch):
    # Branches 395->397, 397->399, 399->401: when meta.X is NOT None
    # (or from_name lacks key X), the inner fill skips. The simplest
    # way to exercise the False arms of all three: monkeypatch
    # `_parse_music_filename` to return a dict that's missing keys
    # AND give meta values that are already populated. But meta is
    # built from mutagen — we need the OUTER `if not has_useful_tags`
    # to be True yet meta to have one of the three fields set.
    # Achievable by monkeypatching _parse_music_filename to a custom
    # dict that contains only "title" (no artist, no track_number) —
    # then 395->397 covers the False arm (from_name.get("artist") is
    # None), 397->399 covers the True branch via the title fill, and
    # 399->401 covers the False arm (no "track_number" key).
    audio_file = tmp_path / "nocoolartist - Cool Title.mp3"
    audio_file.write_bytes(b"fake mp3 bytes")

    # Custom from_name dict — only "title" present.
    custom_from_name = {"title": "Rescued Title"}
    monkeypatch.setattr(
        music_mod, "_parse_music_filename",
        lambda stem: custom_from_name,
    )

    # Force mutagen to produce a metadata with no useful tags so the
    # rescue path runs. Easiest: monkeypatch mutagen.File to return
    # an object whose tags dict is empty.
    class _FakeAudio:
        tags = {}
        info = type("info", (), {"length": None})()

    monkeypatch.setattr(
        "mutagen.File",
        lambda path, easy=True: _FakeAudio(),
    )

    svc = MusicService()
    meta = svc.read_tags(audio_file)
    # Filename-rescue filled in the title and only the title.
    assert meta.title == "Rescued Title"
    assert meta.artist is None
    assert meta.track_number is None
    assert meta.raw.get("_filename_source") == "true"


def test_read_tags_filename_rescue_skips_when_from_name_lacks_title(
    tmp_path, monkeypatch,
):
    # Branch 397->399 False arm: when from_name has "artist" but NOT
    # "title", the `if meta.title is None and from_name.get("title")`
    # check fires the False arm (from_name.get returns None / falsy).
    audio_file = tmp_path / "rescue2.mp3"
    audio_file.write_bytes(b"fake")

    custom_from_name = {"artist": "FilenameArtist"}  # NO title
    monkeypatch.setattr(
        music_mod, "_parse_music_filename",
        lambda stem: custom_from_name,
    )

    class _FakeAudio:
        tags = {}
        info = type("info", (), {"length": None})()

    monkeypatch.setattr(
        "mutagen.File",
        lambda path, easy=True: _FakeAudio(),
    )

    svc = MusicService()
    meta = svc.read_tags(audio_file)
    # Artist filled; title and track stayed None.
    assert meta.artist == "FilenameArtist"
    assert meta.title is None
    assert meta.track_number is None


# ---------------------------------------------------------------------------
# enrich_from_match: match without mbid/score (branches 541->543, 543->545)
# ---------------------------------------------------------------------------


@dataclass
class _StubMatch:
    """Mimics the MBMatch shape used by enrich_from_match. Only the
    fields the function reads need to be present."""

    album: str | None = None
    year: int | None = None
    track_number: int | None = None
    recording_mbid: str | None = None
    score: float | None = None
    artist: str | None = None
    title: str | None = None


def test_enrich_via_musicbrainz_without_mbid_or_score():
    # Branches 541->543, 543->545: when match.recording_mbid is None
    # AND match.score is None, the two `if match.X:` checks both go
    # to their False arm. Use a stub mb_client whose lookup_recording
    # returns a match lacking those fields.
    svc = MusicService()
    metadata = MusicMetadata(
        artist="A", title="T", raw={},
        album=None, year=None, track_number=None,
    )
    match = _StubMatch(album="Best Album", year=2020, track_number=5)

    class _StubMBClient:
        def lookup_recording(self, *, artist, title):
            return match

    result = svc.enrich_via_musicbrainz(metadata, _StubMBClient())
    assert result.album == "Best Album"
    assert "_mb_recording_mbid" not in result.raw
    assert "_mb_score" not in result.raw
    assert result.raw.get("_mb_enriched") == "true"
