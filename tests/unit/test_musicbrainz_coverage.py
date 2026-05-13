"""Focused coverage tests for services/musicbrainz.py.

Sub-ship v1.7.102 of the Coverage Sweep arc.

Closes the 12 uncovered lines + 7 partial branches:

* Lines 91-92: `_musicbrainzngs_available` ImportError branch.
* Lines 186-187: `except (TypeError, ValueError): score = 0`
  defensive when "ext:score" is non-numeric.
* Lines 221-222: `except (TypeError, ValueError): pass` defensive
  when track position is non-numeric.
* Lines 242-243: `except Exception` defensive in
  `_configure_user_agent` (mb.set_useragent raises).
* Line 273: `_parse_year` returns None when input is falsy.
* Lines 281-283: `_parse_year` `except ValueError: pass; return None`
  defensive when s[:4] is non-numeric.
* Branches 216->224, 218->216, 224->214: track_list iteration
  branches when track has no position or scan exhausts.
* Branches 258->254, 260->254: `_extract_artist` credit-entry
  iteration branches.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from curator.services.musicbrainz import (
    MusicBrainzClient,
    _musicbrainzngs_available,
)


def _make_fake_mb_module(search_response):
    """Build a fake musicbrainzngs module returning a canned response."""
    fake = types.ModuleType("musicbrainzngs")

    def search_recordings(**kwargs):
        return search_response

    def set_useragent(*a, **kw):
        pass

    fake.search_recordings = search_recordings
    fake.set_useragent = set_useragent
    return fake


# ---------------------------------------------------------------------------
# _musicbrainzngs_available ImportError branch (91-92)
# ---------------------------------------------------------------------------


def test_musicbrainzngs_available_returns_false_when_missing(monkeypatch):
    # Lines 91-92: sys.modules[name] = None pattern.
    monkeypatch.setitem(sys.modules, "musicbrainzngs", None)
    assert _musicbrainzngs_available() is False


# ---------------------------------------------------------------------------
# _configure_user_agent defensive boundary (242-243)
# ---------------------------------------------------------------------------


def test_configure_user_agent_swallows_set_useragent_exception():
    # Lines 236-244: mb.set_useragent raises → caught with
    # logger.debug, doesn't propagate; _configured still set True.
    client = MusicBrainzClient()
    mock_mb = MagicMock()
    mock_mb.set_useragent.side_effect = RuntimeError("ua failed")

    client._configure_user_agent(mock_mb)
    assert client._configured is True
    mock_mb.set_useragent.assert_called_once()


# ---------------------------------------------------------------------------
# lookup_recording ext:score non-numeric defensive (186-187)
# ---------------------------------------------------------------------------


def test_lookup_recording_score_non_numeric_treated_as_zero(monkeypatch):
    # Lines 184-187: when r.get("ext:score") raises TypeError/ValueError
    # on int(), score defaults to 0. With min_score=0, a 0-score
    # recording is still included (min check: `score >= 0` is True).
    client = MusicBrainzClient(min_score=0)
    # Stub out the actual MB API call via internal _search_recording.
    response = {
        "recording-list": [
            {
                "id": "rec-1",
                "title": "T",
                "artist-credit": [{"name": "A"}],
                "ext:score": "not-a-number",  # triggers ValueError
            }
        ]
    }

    # Patch the network entry point. The class calls a method like
    # _search_recording or directly hits the mb module. Easiest: patch
    # the module-level musicbrainzngs.search_recordings.
    monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(response))

    match = client.lookup_recording(artist="A", title="T")
    # With score=0 (from defensive fallback), the recording was still
    # picked because min_score=0.
    assert match is not None
    assert match.recording_mbid == "rec-1"
    assert match.score == 0


# ---------------------------------------------------------------------------
# lookup_recording track iteration branches (216->224, 218->216, 224->214,
# 221-222)
# ---------------------------------------------------------------------------


def test_lookup_recording_track_without_position_falls_through(monkeypatch):
    # Branch 218->216: track without "position" or "number" → skip
    # (continue inner loop without `if pos:` body).
    # Branch 216->224: track_list exhausted without finding match.
    # Branch 224->214: continue to next medium when track_number still
    # None.
    client = MusicBrainzClient(min_score=0)
    response = {
        "recording-list": [
            {
                "id": "rec-1",
                "title": "T",
                "ext:score": "100",
                "artist-credit": [{"name": "A"}],
                "release-list": [
                    {
                        "id": "rel-1",
                        "title": "Album",
                        "date": "2020-01-01",
                        "medium-list": [
                            # Medium 1: no useful tracks
                            {"track-list": [{}, {"foo": "bar"}]},
                            # Medium 2: no useful tracks either
                            {"track-list": [{}]},
                        ],
                    }
                ],
            }
        ]
    }
    monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(response))

    match = client.lookup_recording(artist="A", title="T")
    assert match is not None
    # No track number found across both mediums.
    assert match.track_number is None
    assert match.album == "Album"
    assert match.year == 2020


def test_lookup_recording_track_position_non_numeric_defensive(monkeypatch):
    # Lines 219-222: int(pos) raises → except (TypeError, ValueError)
    # → pass → break out of inner loop (224->214 next-medium check or
    # 224->exit). Net: track_number stays None.
    client = MusicBrainzClient(min_score=0)
    response = {
        "recording-list": [
            {
                "id": "rec-1",
                "title": "T",
                "ext:score": "100",
                "artist-credit": [{"name": "A"}],
                "release-list": [
                    {
                        "id": "rel-1",
                        "title": "Album",
                        "date": "2021",
                        "medium-list": [
                            {"track-list": [{"position": "garbage"}]},
                        ],
                    }
                ],
            }
        ]
    }
    monkeypatch.setitem(sys.modules, "musicbrainzngs", _make_fake_mb_module(response))

    match = client.lookup_recording(artist="A", title="T")
    assert match is not None
    # Non-numeric position swallowed → track_number stays None.
    assert match.track_number is None


# ---------------------------------------------------------------------------
# _extract_artist credit-entry branches (258->254, 260->254)
# ---------------------------------------------------------------------------


def test_extract_artist_skips_dict_entry_without_name():
    # Branch 258->254: dict entry whose artist.name AND entry.name
    # are both missing → no append, continue to next entry.
    client = MusicBrainzClient()
    recording = {
        "artist-credit": [
            {"artist": {"id": "abc"}},  # no "name" key anywhere
            {"name": "RealArtist"},
        ]
    }
    result = client._extract_artist(recording)
    assert result == "RealArtist"


def test_extract_artist_skips_non_dict_non_str_entry():
    # Branch 260->254: entry is neither dict nor str (e.g. int, None,
    # list) → no append, continue.
    client = MusicBrainzClient()
    recording = {
        "artist-credit": [
            None,
            42,
            {"name": "RealArtist"},
        ]
    }
    result = client._extract_artist(recording)
    assert result == "RealArtist"


# ---------------------------------------------------------------------------
# _parse_year defensive arms (273, 281-283)
# ---------------------------------------------------------------------------


def test_parse_year_falsy_input_returns_none():
    # Line 273: not date_str → return None.
    assert MusicBrainzClient._parse_year(None) is None
    assert MusicBrainzClient._parse_year("") is None
    assert MusicBrainzClient._parse_year(0) is None  # falsy int


def test_parse_year_non_numeric_prefix_returns_none():
    # Lines 281-283: int(s[:4]) raises ValueError → except → pass →
    # return None.
    assert MusicBrainzClient._parse_year("abcd-04-15") is None
    assert MusicBrainzClient._parse_year("not-a-date") is None


def test_parse_year_out_of_range_returns_none():
    # Branch 279->283: numeric year that's NOT in [1900, 2099] →
    # `if 1900 <= year <= 2099` False → fall through to `return None`.
    assert MusicBrainzClient._parse_year("1800-01-01") is None
    assert MusicBrainzClient._parse_year("3000-01-01") is None
