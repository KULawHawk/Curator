"""Focused coverage tests for services/watch.py.

Sub-ship v1.7.97 of the Coverage Sweep arc.

Closes the three uncovered lines + one partial branch:

* Lines 361-362: `_relative_to_source`'s `except ValueError: return None`
  path — fires when a duplicate-sid entry in `_active_roots` matches the
  sid first but the path isn't under that specific root.
* Line 285 + partial branch at 284 (`if rel is None: continue`) in the
  streaming loop — the path inside the loop where `_relative_to_source`
  returns None. Reached via a monkeypatched `_relative_to_source` that
  returns None during a fake-watchfiles streaming run.

Reuses the existing `fake_watchfiles_factory` fixture pattern.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from curator.models.source import SourceConfig
from curator.services.watch import WatchService
from curator.storage.repositories.source_repo import SourceRepository


def _mk_source(source_id="s", source_type="local", config=None, enabled=True):
    return SourceConfig(
        source_id=source_id,
        source_type=source_type,
        display_name=source_id,
        enabled=enabled,
        config=config or {},
    )


# ---------------------------------------------------------------------------
# _relative_to_source ValueError → None (lines 359-362)
# ---------------------------------------------------------------------------


def test_relative_to_source_value_error_returns_none(db, tmp_path):
    # Lines 359-362: when `_active_roots` has a duplicate-sid entry whose
    # root is iterated FIRST but the path is under a DIFFERENT root with
    # the same sid, `abs_path.relative_to(first_root)` raises ValueError
    # and the function returns None immediately (without trying other
    # roots for the same sid).
    repo = SourceRepository(db)
    svc = WatchService(repo)
    other_root = tmp_path / "other_root"
    real_root = tmp_path / "real_root"
    other_root.mkdir()
    real_root.mkdir()

    # Dict order: `other_root` first. Both map to "sid1". Calling with
    # a path under `real_root` finds the sid match at `other_root` first,
    # which raises ValueError on relative_to.
    svc._active_roots = {
        other_root.resolve(): "sid1",
        real_root.resolve(): "sid1",
    }
    abs_path = (real_root / "x.txt").resolve()

    rel = svc._relative_to_source(abs_path, "sid1")
    assert rel is None


# ---------------------------------------------------------------------------
# Streaming loop: if rel is None: continue (line 284-285)
# ---------------------------------------------------------------------------


def _install_fake_watchfiles(batches):
    """Same shape as the helper in tests/unit/test_watch.py."""
    original = sys.modules.get("watchfiles")

    class _FakeChange:
        added = "ADDED"
        modified = "MODIFIED"
        deleted = "DELETED"

    def _fake_watch(*roots, step=None, stop_event=None, yield_on_timeout=None):
        for batch in batches:
            translated = [
                (
                    {"added": _FakeChange.added,
                     "modified": _FakeChange.modified,
                     "deleted": _FakeChange.deleted}.get(c, c),
                    p,
                )
                for c, p in batch
            ]
            yield translated

    fake_mod = types.ModuleType("watchfiles")
    fake_mod.Change = _FakeChange  # type: ignore[attr-defined]
    fake_mod.watch = _fake_watch  # type: ignore[attr-defined]
    sys.modules["watchfiles"] = fake_mod

    def cleanup():
        if original is not None:
            sys.modules["watchfiles"] = original
        else:
            sys.modules.pop("watchfiles", None)

    return cleanup


def test_streaming_loop_skips_event_when_relative_to_source_returns_none(
    db, tmp_path, monkeypatch,
):
    # Line 285 + branch 284->next-iter: when `_relative_to_source`
    # returns None inside the streaming loop, the event is skipped via
    # `continue`. Easiest to drive by monkeypatching the bound method
    # to return None for one specific incoming path.
    repo = SourceRepository(db)
    repo.insert(_mk_source(
        source_id="s",
        source_type="local",
        config={"root": str(tmp_path)},
    ))
    target = tmp_path / "skip_me.txt"
    target.write_text("x")

    cleanup = _install_fake_watchfiles([
        [("added", str(target))],
    ])
    try:
        svc = WatchService(repo)

        # Force the inner `_relative_to_source` to return None so the
        # streaming loop hits its `if rel is None: continue` arm.
        monkeypatch.setattr(svc, "_relative_to_source", lambda p, sid: None)

        events = list(svc.watch())
        # Event was skipped entirely (no emission).
        assert events == []
    finally:
        cleanup()
