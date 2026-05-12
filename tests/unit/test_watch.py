"""Tests for v1.7.58: watch.py coverage lift (Tier 3 — final).

Backstory: v1.7.51's coverage baseline showed watch.py at 41.34% --
the fourth and last Tier 3 weak-coverage target. v1.7.55 closed
pii_scanner (22% -> 98%), v1.7.56 closed metadata_stripper (25% -> 94%),
v1.7.57 closed forecast (29% -> 98%). This ship closes the last one.

watch.py is the hardest of the four because of its threading + external
dependency story: the WatchService.watch() generator wraps the
``watchfiles`` library's blocking iterator. Tests cover:

  * **Pure helpers** (TestDebouncer, TestMatchesAnyPattern) -- the
    coalescing window logic + glob-pattern matching
  * **Data shapes** (TestChangeKind, TestPathChange) -- enum values,
    frozen dataclass behavior, to_dict serialization
  * **Errors** (TestWatchErrors) -- WatchError hierarchy
  * **Service init** (TestWatchServiceInit) -- defaults vs overrides
  * **Source resolution** (TestResolveRoots) -- filtering by
    type/enabled/exists; the 6 filter branches in _resolve_roots
  * **watch() error paths** (TestWatchErrorPaths) -- missing watchfiles,
    no local sources
  * **watch() main loop** (TestWatchMainLoop) -- emits PathChange, skips
    ignored, debounces, deduplicates -- via fake watchfiles module

Strategy: install a fake ``watchfiles`` module in sys.modules at
import-site so WatchService.watch() picks it up instead of the real
library. Yields controlled batches; assertions then check what the
generator emits.
"""
from __future__ import annotations

import sys
import threading
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from curator.services.watch import (
    DEFAULT_DEBOUNCE_MS,
    DEFAULT_IGNORE_PATTERNS,
    DEFAULT_STEP_MS,
    ChangeKind,
    NoLocalSourcesError,
    PathChange,
    WatchError,
    WatchService,
    WatchUnavailableError,
    _Debouncer,
    _matches_any_pattern,
)
from curator.models.source import SourceConfig
from curator.storage.repositories.source_repo import SourceRepository


# ---------------------------------------------------------------------------
# ChangeKind enum
# ---------------------------------------------------------------------------


class TestChangeKind:
    def test_enum_values(self):
        assert ChangeKind.ADDED == "added"
        assert ChangeKind.MODIFIED == "modified"
        assert ChangeKind.DELETED == "deleted"

    def test_is_str_enum(self):
        # Allows JSON serialization without conversion
        assert isinstance(ChangeKind.ADDED, str)


# ---------------------------------------------------------------------------
# PathChange dataclass
# ---------------------------------------------------------------------------


class TestPathChange:
    def test_basic_construction(self):
        change = PathChange(
            kind=ChangeKind.ADDED,
            path=Path("/test/a.txt"),
            source_id="src1",
        )
        assert change.kind == ChangeKind.ADDED
        assert change.path == Path("/test/a.txt")
        assert change.source_id == "src1"

    def test_detected_at_default_is_utc(self):
        change = PathChange(
            kind=ChangeKind.ADDED,
            path=Path("/a"),
            source_id="x",
        )
        assert change.detected_at.tzinfo == timezone.utc

    def test_frozen_dataclass(self):
        """v1.7.58: PathChange is frozen so it can go in sets/dicts."""
        change = PathChange(
            kind=ChangeKind.ADDED, path=Path("/a"), source_id="x",
        )
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            change.kind = ChangeKind.DELETED

    def test_to_dict_serialization(self):
        change = PathChange(
            kind=ChangeKind.MODIFIED,
            path=Path("/test/b.txt"),
            source_id="src2",
            detected_at=datetime(2026, 5, 12, 14, 30, 0, tzinfo=timezone.utc),
        )
        d = change.to_dict()
        assert d["kind"] == "modified"
        assert d["path"] == str(Path("/test/b.txt"))
        assert d["source_id"] == "src2"
        assert "2026-05-12" in d["detected_at"]

    def test_hashable_for_set_membership(self):
        """v1.7.58: frozen + hashable so the deduplication use case works."""
        a = PathChange(ChangeKind.ADDED, Path("/x"), "s1",
                       detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = PathChange(ChangeKind.ADDED, Path("/x"), "s1",
                       detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        # Same data -> same hash, equal
        assert hash(a) == hash(b)
        assert a == b
        s = {a, b}
        assert len(s) == 1


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class TestWatchErrors:
    def test_unavailable_inherits_from_watch_error(self):
        assert issubclass(WatchUnavailableError, WatchError)

    def test_no_local_inherits_from_watch_error(self):
        assert issubclass(NoLocalSourcesError, WatchError)


# ---------------------------------------------------------------------------
# _Debouncer
# ---------------------------------------------------------------------------


class TestDebouncer:
    def test_initial_emit_allowed(self):
        d = _Debouncer(window_ms=1000)
        assert d.should_emit("/p", ChangeKind.ADDED, 0.0) is True

    def test_within_window_suppressed(self):
        d = _Debouncer(window_ms=1000)
        d.should_emit("/p", ChangeKind.MODIFIED, 0.0)
        # 0.5 seconds later (within 1 sec window) -> suppressed
        assert d.should_emit("/p", ChangeKind.MODIFIED, 0.5) is False

    def test_after_window_allowed_again(self):
        d = _Debouncer(window_ms=1000)
        d.should_emit("/p", ChangeKind.MODIFIED, 0.0)
        # 1.5 seconds later (past 1 sec window) -> allowed
        assert d.should_emit("/p", ChangeKind.MODIFIED, 1.5) is True

    def test_different_paths_independent(self):
        d = _Debouncer(window_ms=1000)
        d.should_emit("/p1", ChangeKind.ADDED, 0.0)
        # Different path, even within window -> allowed
        assert d.should_emit("/p2", ChangeKind.ADDED, 0.1) is True

    def test_different_kinds_independent(self):
        d = _Debouncer(window_ms=1000)
        d.should_emit("/p", ChangeKind.ADDED, 0.0)
        # Same path, different kind -> allowed
        assert d.should_emit("/p", ChangeKind.MODIFIED, 0.1) is True

    def test_deleted_bypasses_debouncer(self):
        """v1.7.58: DELETED events always emit; debouncer doesn't apply."""
        d = _Debouncer(window_ms=10_000)  # 10s window -- very generous
        assert d.should_emit("/p", ChangeKind.DELETED, 0.0) is True
        # Immediately again -> still allowed
        assert d.should_emit("/p", ChangeKind.DELETED, 0.001) is True

    def test_len_reports_tracked_keys(self):
        d = _Debouncer(window_ms=1000)
        assert len(d) == 0
        d.should_emit("/a", ChangeKind.ADDED, 0.0)
        d.should_emit("/b", ChangeKind.MODIFIED, 0.0)
        # 2 distinct (path, kind) keys
        assert len(d) == 2
        # DELETED doesn't add to tracked keys
        d.should_emit("/c", ChangeKind.DELETED, 0.0)
        assert len(d) == 2


# ---------------------------------------------------------------------------
# _matches_any_pattern
# ---------------------------------------------------------------------------


class TestMatchesAnyPattern:
    def test_no_patterns_no_match(self):
        assert _matches_any_pattern("foo/bar.txt", ()) is False

    def test_exact_glob_match(self):
        assert _matches_any_pattern("foo.pyc", ("*.pyc",)) is True

    def test_no_match_when_pattern_differs(self):
        assert _matches_any_pattern("foo.txt", ("*.pyc",)) is False

    def test_normalizes_windows_backslashes(self):
        """v1.7.58: \\\\-separated rel paths get converted to /."""
        # Use raw string to avoid escape issues
        assert _matches_any_pattern(r"sub\foo.pyc", ("*.pyc",)) is True

    def test_dir_pattern_matches_nested_component(self):
        """v1.7.58: __pycache__ pattern catches any __pycache__ subdir."""
        assert _matches_any_pattern(
            "src/curator/__pycache__/x.pyc", ("__pycache__",),
        ) is True

    def test_dir_glob_pattern_with_slash_star(self):
        # `.git/*` matches HEAD when .git is the FIRST component
        # (fnmatch doesn't recurse into deeper levels for slash-patterns)
        assert _matches_any_pattern(
            ".git/HEAD", (".git/*",),
        ) is True

    def test_multiple_patterns_any_match(self):
        """v1.7.58: returns True on first match."""
        patterns = ("*.pyc", "*.swp", "Thumbs.db")
        assert _matches_any_pattern("foo.swp", patterns) is True

    def test_default_patterns_filter_common_noise(self):
        """v1.7.58: shipped DEFAULT_IGNORE_PATTERNS covers common files."""
        for filename in ("Thumbs.db", "foo.pyc", ".DS_Store", "x.tmp"):
            assert _matches_any_pattern(filename, DEFAULT_IGNORE_PATTERNS), (
                f"Expected {filename} to be filtered"
            )


# ---------------------------------------------------------------------------
# Legacy tests preserved from pre-v1.7.58 test_watch.py
# ---------------------------------------------------------------------------
# v1.7.58 caught lesson #65 (see CHANGELOG): the v1.7.58 ship initially
# overwrote a pre-existing 22-test test_watch.py without checking git status.
# This class restores the 9 specific test names from the pre-existing file
# that weren't directly re-covered by name in the v1.7.58 rewrite, so the
# original test history is preserved alongside the broader new coverage.


class TestLegacyPathChange:
    """Legacy PathChange test preserved from pre-v1.7.58."""

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


class TestLegacyIgnorePatterns:
    """Legacy specific-pattern tests preserved from pre-v1.7.58."""

    def test_pyc_files_filtered(self):
        assert _matches_any_pattern("foo/bar.pyc", DEFAULT_IGNORE_PATTERNS) is True

    def test_pycache_dir_filtered(self):
        # Nested __pycache__ caught by component matching
        assert _matches_any_pattern(
            "src/curator/__pycache__/x.pyc", DEFAULT_IGNORE_PATTERNS,
        ) is True

    def test_git_dir_filtered(self):
        assert _matches_any_pattern(".git/HEAD", DEFAULT_IGNORE_PATTERNS) is True
        assert _matches_any_pattern(
            "src/.git/objects/abc", DEFAULT_IGNORE_PATTERNS,
        ) is True

    def test_vim_swap_filtered(self):
        assert _matches_any_pattern(
            "notes/.notes.txt.swp", DEFAULT_IGNORE_PATTERNS,
        ) is True

    def test_emacs_lock_filtered(self):
        assert _matches_any_pattern(
            "docs/.#README.md", DEFAULT_IGNORE_PATTERNS,
        ) is True

    def test_regular_files_not_filtered(self):
        """v1.7.58: negative — common non-noise files should NOT be filtered."""
        assert _matches_any_pattern("README.md", DEFAULT_IGNORE_PATTERNS) is False
        assert _matches_any_pattern("src/main.py", DEFAULT_IGNORE_PATTERNS) is False
        assert _matches_any_pattern("docs/notes.md", DEFAULT_IGNORE_PATTERNS) is False

    def test_custom_pattern(self):
        assert _matches_any_pattern(
            "scratch/draft1.md", ("scratch/*",),
        ) is True
        assert _matches_any_pattern(
            "docs/draft1.md", ("scratch/*",),
        ) is False


class TestLegacyConstants:
    """Legacy constant-sanity test preserved from pre-v1.7.58."""

    def test_debounce_default_is_one_second(self):
        assert DEFAULT_DEBOUNCE_MS == 1000


# ---------------------------------------------------------------------------
# WatchService init
# ---------------------------------------------------------------------------


class TestWatchServiceInit:
    def test_defaults_applied(self, db):
        repo = SourceRepository(db)
        svc = WatchService(repo)
        assert svc._debounce_ms == DEFAULT_DEBOUNCE_MS
        assert svc._step_ms == DEFAULT_STEP_MS
        assert svc._ignore_patterns == DEFAULT_IGNORE_PATTERNS
        assert svc._active_roots == {}

    def test_custom_debounce_and_step(self, db):
        repo = SourceRepository(db)
        svc = WatchService(repo, debounce_ms=500, step_ms=20)
        assert svc._debounce_ms == 500
        assert svc._step_ms == 20

    def test_custom_ignore_patterns(self, db):
        repo = SourceRepository(db)
        svc = WatchService(repo, ignore_patterns=("*.log",))
        assert svc._ignore_patterns == ("*.log",)

    def test_len_is_zero_when_idle(self, db):
        repo = SourceRepository(db)
        svc = WatchService(repo)
        assert len(svc) == 0


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _mk_source(
    *, source_id, source_type="local", enabled=True, config=None,
):
    """Construct a SourceConfig with sensible defaults."""
    return SourceConfig(
        source_id=source_id,
        source_type=source_type,
        display_name=source_id,
        enabled=enabled,
        config=config or {},
    )


class TestResolveRoots:
    def test_skips_non_local_sources(self, db, tmp_path):
        repo = SourceRepository(db)
        # Non-local source -> skipped
        repo.insert(_mk_source(
            source_id="remote",
            source_type="gdrive",
            config={"root": str(tmp_path)},
        ))
        svc = WatchService(repo)
        roots = svc._resolve_roots(None)
        assert roots == {}

    def test_skips_disabled_sources(self, db, tmp_path):
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="off",
            source_type="local",
            enabled=False,
            config={"root": str(tmp_path)},
        ))
        svc = WatchService(repo)
        roots = svc._resolve_roots(None)
        assert roots == {}

    def test_skips_sources_missing_root_in_config(self, db):
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="no_root",
            source_type="local",
            config={},  # missing "root"
        ))
        svc = WatchService(repo)
        roots = svc._resolve_roots(None)
        assert roots == {}

    def test_skips_nonexistent_root(self, db):
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="ghost",
            source_type="local",
            config={"root": "/this/path/definitely/does/not/exist/xyz"},
        ))
        svc = WatchService(repo)
        roots = svc._resolve_roots(None)
        assert roots == {}

    def test_skips_root_that_is_a_file_not_directory(self, db, tmp_path):
        repo = SourceRepository(db)
        # Point root at a file, not a dir
        a_file = tmp_path / "notadir.txt"
        a_file.write_text("x")
        repo.insert(_mk_source(
            source_id="filerr",
            source_type="local",
            config={"root": str(a_file)},
        ))
        svc = WatchService(repo)
        roots = svc._resolve_roots(None)
        assert roots == {}

    def test_valid_source_appears_in_roots(self, db, tmp_path):
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="ok",
            source_type="local",
            config={"root": str(tmp_path)},
        ))
        svc = WatchService(repo)
        roots = svc._resolve_roots(None)
        assert len(roots) == 1
        assert tmp_path.resolve() in roots
        assert roots[tmp_path.resolve()] == "ok"

    def test_filter_by_source_ids(self, db, tmp_path):
        """v1.7.58: when source_ids passed, only those are considered."""
        repo = SourceRepository(db)
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        repo.insert(_mk_source(source_id="a", config={"root": str(a)}))
        repo.insert(_mk_source(source_id="b", config={"root": str(b)}))
        svc = WatchService(repo)
        # Request only "a"
        roots = svc._resolve_roots(["a"])
        assert len(roots) == 1
        assert "a" in roots.values()
        assert "b" not in roots.values()

    def test_filter_by_unknown_source_id_returns_empty(self, db, tmp_path):
        """v1.7.58: source_ids referencing non-existent sources are ignored."""
        repo = SourceRepository(db)
        repo.insert(_mk_source(source_id="real", config={"root": str(tmp_path)}))
        svc = WatchService(repo)
        roots = svc._resolve_roots(["nonexistent"])
        assert roots == {}

    def test_multiple_valid_sources(self, db, tmp_path):
        repo = SourceRepository(db)
        a = tmp_path / "x"; a.mkdir()
        b = tmp_path / "y"; b.mkdir()
        repo.insert(_mk_source(source_id="x", config={"root": str(a)}))
        repo.insert(_mk_source(source_id="y", config={"root": str(b)}))
        svc = WatchService(repo)
        roots = svc._resolve_roots(None)
        assert len(roots) == 2


# ---------------------------------------------------------------------------
# Internal resolution helpers
# ---------------------------------------------------------------------------


class TestResolveSourceId:
    def test_path_under_root(self, db, tmp_path):
        repo = SourceRepository(db)
        svc = WatchService(repo)
        # Manually set active_roots
        svc._active_roots = {tmp_path.resolve(): "src1"}
        # A file inside the root
        f = tmp_path / "inner" / "file.txt"
        sid = svc._resolve_source_id(f.resolve())
        assert sid == "src1"

    def test_path_outside_returns_none(self, db, tmp_path):
        repo = SourceRepository(db)
        svc = WatchService(repo)
        svc._active_roots = {tmp_path.resolve(): "src1"}
        sid = svc._resolve_source_id(Path("/totally/unrelated/path"))
        assert sid is None

    def test_relative_to_source(self, db, tmp_path):
        repo = SourceRepository(db)
        svc = WatchService(repo)
        svc._active_roots = {tmp_path.resolve(): "src1"}
        f = tmp_path / "sub" / "x.txt"
        rel = svc._relative_to_source(f.resolve(), "src1")
        # Should be "sub/x.txt" or "sub\\x.txt" on Windows
        assert "x.txt" in rel
        assert "sub" in rel

    def test_relative_to_unknown_source_returns_none(self, db, tmp_path):
        repo = SourceRepository(db)
        svc = WatchService(repo)
        svc._active_roots = {tmp_path.resolve(): "src1"}
        rel = svc._relative_to_source(tmp_path / "x.txt", "src_unknown")
        assert rel is None


# ---------------------------------------------------------------------------
# watch() error paths
# ---------------------------------------------------------------------------


class TestWatchErrorPaths:
    def test_raises_watch_unavailable_when_no_watchfiles(self, db):
        """v1.7.58: clear error if watchfiles isn't installed."""
        repo = SourceRepository(db)
        svc = WatchService(repo)
        # Simulate no watchfiles installed: remove from sys.modules and
        # patch the import to fail
        original = sys.modules.get("watchfiles")
        try:
            # Force ImportError on next "from watchfiles import ..."
            sys.modules["watchfiles"] = None  # type: ignore[assignment]
            with pytest.raises(WatchUnavailableError, match="watchfiles"):
                # Need to actually iterate to trigger watch()
                list(svc.watch())
        finally:
            if original is not None:
                sys.modules["watchfiles"] = original
            else:
                sys.modules.pop("watchfiles", None)

    def test_raises_no_local_sources(self, db):
        """v1.7.58: clear error when zero enabled local sources."""
        repo = SourceRepository(db)  # empty repo
        svc = WatchService(repo)
        with pytest.raises(NoLocalSourcesError, match="No enabled local sources"):
            list(svc.watch())


# ---------------------------------------------------------------------------
# watch() main loop -- via fake watchfiles module
# ---------------------------------------------------------------------------


def _install_fake_watchfiles(batches):
    """Install a fake ``watchfiles`` module that yields the given batches.

    batches: iterable of [(wf_change, path_str), ...] tuples.

    Returns a callable that uninstalls the fake (cleanup).
    """
    original = sys.modules.get("watchfiles")

    class _FakeChange:
        added = "ADDED"
        modified = "MODIFIED"
        deleted = "DELETED"

    def _fake_watch(*roots, step=None, stop_event=None, yield_on_timeout=None):
        for batch in batches:
            # Translate string change kinds to the fake's attributes
            translated = []
            for change_str, path in batch:
                if change_str == "added":
                    translated.append((_FakeChange.added, path))
                elif change_str == "modified":
                    translated.append((_FakeChange.modified, path))
                elif change_str == "deleted":
                    translated.append((_FakeChange.deleted, path))
                else:
                    translated.append((change_str, path))
            yield translated

    fake_mod = types.ModuleType("watchfiles")
    fake_mod.Change = _FakeChange  # type: ignore[attr-defined]
    fake_mod.watch = _fake_watch  # type: ignore[attr-defined]
    sys.modules["watchfiles"] = fake_mod

    def _cleanup():
        if original is not None:
            sys.modules["watchfiles"] = original
        else:
            sys.modules.pop("watchfiles", None)

    return _cleanup


@pytest.fixture
def fake_watchfiles_factory():
    """Fixture that lets a test install a fake watchfiles module.

    Yields a function ``installer(batches)`` that returns nothing; the
    cleanup happens automatically at test teardown.
    """
    cleanups = []
    def installer(batches):
        cleanups.append(_install_fake_watchfiles(batches))
    yield installer
    for cleanup in cleanups:
        cleanup()


class TestWatchMainLoop:
    def test_emits_added_event(self, db, tmp_path, fake_watchfiles_factory):
        """v1.7.58: a single ADDED event from watchfiles becomes a PathChange."""
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="s", source_type="local",
            config={"root": str(tmp_path)},
        ))
        target = tmp_path / "new.txt"
        target.write_text("x")
        fake_watchfiles_factory([
            [("added", str(target))],
        ])

        svc = WatchService(repo)
        events = list(svc.watch())
        assert len(events) == 1
        assert events[0].kind == ChangeKind.ADDED
        assert events[0].source_id == "s"
        assert events[0].path.resolve() == target.resolve()

    def test_emits_modified_event(self, db, tmp_path, fake_watchfiles_factory):
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="s", source_type="local",
            config={"root": str(tmp_path)},
        ))
        target = tmp_path / "mod.txt"
        target.write_text("x")
        fake_watchfiles_factory([
            [("modified", str(target))],
        ])
        svc = WatchService(repo)
        events = list(svc.watch())
        assert len(events) == 1
        assert events[0].kind == ChangeKind.MODIFIED

    def test_emits_deleted_event(self, db, tmp_path, fake_watchfiles_factory):
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="s", source_type="local",
            config={"root": str(tmp_path)},
        ))
        target = tmp_path / "deleted.txt"
        target.write_text("x")
        target_path_str = str(target)
        target.unlink()  # actually delete it
        fake_watchfiles_factory([
            [("deleted", target_path_str)],
        ])
        svc = WatchService(repo)
        events = list(svc.watch())
        assert len(events) == 1
        assert events[0].kind == ChangeKind.DELETED

    def test_skips_ignored_file_patterns(self, db, tmp_path, fake_watchfiles_factory):
        """v1.7.58: DEFAULT_IGNORE_PATTERNS filters out .pyc / __pycache__."""
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="s", source_type="local",
            config={"root": str(tmp_path)},
        ))
        # Create a real file to find AND a pyc file
        ok = tmp_path / "keep.txt"; ok.write_text("y")
        pyc = tmp_path / "skip.pyc"; pyc.write_text("z")
        fake_watchfiles_factory([
            [
                ("added", str(ok)),
                ("added", str(pyc)),
            ],
        ])
        svc = WatchService(repo)
        events = list(svc.watch())
        # Only the non-pyc file should emit
        assert len(events) == 1
        assert events[0].path.name == "keep.txt"

    def test_debounces_rapid_repeats(self, db, tmp_path, fake_watchfiles_factory):
        """v1.7.58: identical (path, kind) in rapid succession coalesces."""
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="s", source_type="local",
            config={"root": str(tmp_path)},
        ))
        f = tmp_path / "flap.txt"; f.write_text("a")
        # Same file modified 3x in two consecutive batches; debouncer
        # should let the FIRST one through and suppress later ones
        # because the batches come from the same `now` timestamp loop tick.
        fake_watchfiles_factory([
            [("modified", str(f))],
            [("modified", str(f))],
            [("modified", str(f))],
        ])
        # Use a long debounce so all 3 are within the same window
        svc = WatchService(repo, debounce_ms=10_000)
        events = list(svc.watch())
        # First emit allowed; subsequent suppressed
        assert len(events) == 1

    def test_skips_unknown_change_kind(self, db, tmp_path, fake_watchfiles_factory):
        """v1.7.58: change kinds not in the mapping are ignored."""
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="s", source_type="local",
            config={"root": str(tmp_path)},
        ))
        f = tmp_path / "x.txt"; f.write_text("ok")
        # An unknown change kind that isn't in the mapping
        fake_watchfiles_factory([
            [("totally_unknown_kind", str(f))],
        ])
        svc = WatchService(repo)
        events = list(svc.watch())
        assert events == []

    def test_skips_paths_outside_watched_roots(
        self, db, tmp_path, fake_watchfiles_factory,
    ):
        """v1.7.58: defensive — paths that don't fall under any active
        root return None from _resolve_source_id and are skipped."""
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="s", source_type="local",
            config={"root": str(tmp_path)},
        ))
        # Build a path OUTSIDE tmp_path (use a known-unrelated absolute path)
        outside = Path("C:/some/unrelated/path/x.txt") if sys.platform == "win32" else Path("/unrelated/x.txt")
        fake_watchfiles_factory([
            [("added", str(outside))],
        ])
        svc = WatchService(repo)
        events = list(svc.watch())
        # Path outside any watched root -> filtered out
        assert events == []

    def test_active_roots_cleared_after_watch_ends(
        self, db, tmp_path, fake_watchfiles_factory,
    ):
        """v1.7.58: the finally block clears _active_roots."""
        repo = SourceRepository(db)
        repo.insert(_mk_source(
            source_id="s", source_type="local",
            config={"root": str(tmp_path)},
        ))
        fake_watchfiles_factory([[]])  # one empty batch then end
        svc = WatchService(repo)
        # During iteration _active_roots is set; after generator exhausts
        # it should clear.
        list(svc.watch())
        assert svc._active_roots == {}
        assert len(svc) == 0
