"""Unit tests for :mod:`curator.services.watch`.

The pieces tested here (``_Debouncer``, ``_matches_any_pattern``,
``PathChange``) don't require ``watchfiles`` — they're pure-Python
support code. The actual filesystem-event integration test lives in
``tests/integration/test_watch_smoke.py`` and IS gated on watchfiles.

So this file runs on a vanilla install too, even before
``[beta]`` extras are installed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from curator.services.watch import (
    ChangeKind,
    DEFAULT_DEBOUNCE_MS,
    PathChange,
    _Debouncer,
    _matches_any_pattern,
    DEFAULT_IGNORE_PATTERNS,
)


# ---------------------------------------------------------------------------
# PathChange dataclass
# ---------------------------------------------------------------------------

class TestPathChange:
    def test_basic_construction(self):
        c = PathChange(
            kind=ChangeKind.ADDED,
            path=Path("/tmp/foo.txt"),
            source_id="local",
        )
        assert c.kind is ChangeKind.ADDED
        assert c.path == Path("/tmp/foo.txt")
        assert c.source_id == "local"
        assert c.detected_at.tzinfo is not None  # UTC-aware

    def test_frozen_dataclass(self):
        c = PathChange(
            kind=ChangeKind.MODIFIED,
            path=Path("/tmp/foo.txt"),
            source_id="local",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            c.kind = ChangeKind.DELETED  # type: ignore[misc]

    def test_to_dict_round_trip(self):
        ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        c = PathChange(
            kind=ChangeKind.DELETED,
            path=Path(r"C:\Users\jmlee\file.txt"),
            source_id="docs",
            detected_at=ts,
        )
        d = c.to_dict()
        assert d == {
            "kind": "deleted",
            "path": str(Path(r"C:\Users\jmlee\file.txt")),
            "source_id": "docs",
            "detected_at": "2026-01-15T12:00:00+00:00",
        }

    def test_hashable_for_set_dedup(self):
        # Frozen dataclass should be hashable so callers can dedup.
        ts = datetime(2026, 1, 15, tzinfo=timezone.utc)
        a = PathChange(ChangeKind.ADDED, Path("/x"), "s", ts)
        b = PathChange(ChangeKind.ADDED, Path("/x"), "s", ts)
        assert {a, b} == {a}  # same fields → same hash → set dedups


# ---------------------------------------------------------------------------
# Debouncer
# ---------------------------------------------------------------------------

class TestDebouncer:
    def test_first_emit_passes(self):
        d = _Debouncer(window_ms=1000)
        assert d.should_emit("/foo.txt", ChangeKind.MODIFIED, now_seconds=100.0) is True

    def test_duplicate_within_window_blocked(self):
        d = _Debouncer(window_ms=1000)
        d.should_emit("/foo.txt", ChangeKind.MODIFIED, 100.0)
        # 0.5s later → still within 1s window → blocked
        assert d.should_emit("/foo.txt", ChangeKind.MODIFIED, 100.5) is False

    def test_after_window_passes(self):
        d = _Debouncer(window_ms=1000)
        d.should_emit("/foo.txt", ChangeKind.MODIFIED, 100.0)
        # 1.5s later → outside 1s window → emits
        assert d.should_emit("/foo.txt", ChangeKind.MODIFIED, 101.5) is True

    def test_different_paths_independent(self):
        d = _Debouncer(window_ms=1000)
        d.should_emit("/foo.txt", ChangeKind.MODIFIED, 100.0)
        # Different path → not blocked even within window
        assert d.should_emit("/bar.txt", ChangeKind.MODIFIED, 100.1) is True

    def test_different_kinds_independent(self):
        d = _Debouncer(window_ms=1000)
        d.should_emit("/foo.txt", ChangeKind.MODIFIED, 100.0)
        # Same path, different kind → not blocked
        assert d.should_emit("/foo.txt", ChangeKind.ADDED, 100.1) is True

    def test_deleted_bypasses_debouncer(self):
        d = _Debouncer(window_ms=1000)
        # Even firing twice at the same instant, both DELETEs should emit.
        assert d.should_emit("/foo.txt", ChangeKind.DELETED, 100.0) is True
        assert d.should_emit("/foo.txt", ChangeKind.DELETED, 100.0) is True

    def test_len_tracks_state(self):
        d = _Debouncer(window_ms=1000)
        assert len(d) == 0
        d.should_emit("/a", ChangeKind.MODIFIED, 100.0)
        d.should_emit("/b", ChangeKind.MODIFIED, 100.0)
        assert len(d) == 2
        # DELETEs don't fill the table
        d.should_emit("/c", ChangeKind.DELETED, 100.0)
        assert len(d) == 2


# ---------------------------------------------------------------------------
# Ignore-pattern matching
# ---------------------------------------------------------------------------

class TestIgnorePatterns:
    def test_pyc_files_filtered(self):
        assert _matches_any_pattern("foo/bar.pyc", DEFAULT_IGNORE_PATTERNS) is True

    def test_pycache_dir_filtered(self):
        # Nested __pycache__ should be caught by component matching.
        assert _matches_any_pattern("src/curator/__pycache__/x.pyc", DEFAULT_IGNORE_PATTERNS) is True

    def test_git_dir_filtered(self):
        assert _matches_any_pattern(".git/HEAD", DEFAULT_IGNORE_PATTERNS) is True
        assert _matches_any_pattern("src/.git/objects/abc", DEFAULT_IGNORE_PATTERNS) is True

    def test_vim_swap_filtered(self):
        assert _matches_any_pattern("notes/.notes.txt.swp", DEFAULT_IGNORE_PATTERNS) is True

    def test_emacs_lock_filtered(self):
        assert _matches_any_pattern("docs/.#README.md", DEFAULT_IGNORE_PATTERNS) is True

    def test_regular_files_not_filtered(self):
        assert _matches_any_pattern("README.md", DEFAULT_IGNORE_PATTERNS) is False
        assert _matches_any_pattern("src/main.py", DEFAULT_IGNORE_PATTERNS) is False
        assert _matches_any_pattern("docs/notes.md", DEFAULT_IGNORE_PATTERNS) is False

    def test_windows_path_separators_normalized(self):
        # Backslashes get normalized to forward slashes before matching.
        assert _matches_any_pattern(r"src\curator\__pycache__\x.pyc", DEFAULT_IGNORE_PATTERNS) is True
        assert _matches_any_pattern(r"src\main.py", DEFAULT_IGNORE_PATTERNS) is False

    def test_empty_pattern_list_matches_nothing(self):
        assert _matches_any_pattern("anything.txt", ()) is False

    def test_custom_pattern(self):
        assert _matches_any_pattern("scratch/draft1.md", ("scratch/*",)) is True
        assert _matches_any_pattern("docs/draft1.md", ("scratch/*",)) is False


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

class TestConstants:
    def test_debounce_default_is_one_second(self):
        assert DEFAULT_DEBOUNCE_MS == 1000

    def test_ignore_patterns_includes_common_noise(self):
        # Spot-check that the defaults cover the editors we care about.
        assert ".git" in DEFAULT_IGNORE_PATTERNS
        assert "__pycache__" in DEFAULT_IGNORE_PATTERNS
        assert "*.swp" in DEFAULT_IGNORE_PATTERNS
        assert ".DS_Store" in DEFAULT_IGNORE_PATTERNS
        assert "Thumbs.db" in DEFAULT_IGNORE_PATTERNS
