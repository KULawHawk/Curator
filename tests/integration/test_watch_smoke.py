"""Integration smoke test for WatchService — actually exercises ``watchfiles``.

Phase Beta v0.16 / Tier 6. See ``docs/PHASE_BETA_WATCH.md``.

Marked ``@pytest.mark.slow`` because it touches the real filesystem and
event-arrival latency is timing-sensitive. Run via:

    pytest tests/integration/test_watch_smoke.py -m slow -v

Skipped when ``watchfiles`` isn't installed (Phase Beta optional dep).

The test creates a real local source pointing at ``tmp_path``, then in
a background thread mutates files in that directory while the main
thread iterates ``WatchService.watch()``. The service yields events;
the test asserts an ADDED event arrived for the file we just created,
then signals the stop event so the test terminates cleanly.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

# Skip the entire module if the optional dep is missing.
pytest.importorskip("watchfiles")

from curator.models import SourceConfig  # noqa: E402
from curator.services.watch import (  # noqa: E402
    ChangeKind,
    NoLocalSourcesError,
    PathChange,
    WatchService,
)


pytestmark = [pytest.mark.slow, pytest.mark.integration]


# A short but not-too-short wait window. watchfiles needs ~50ms to set
# up the OS watcher; if we mutate before that, the event is missed.
SETUP_DELAY_S = 0.5

# Maximum time we'll wait for an event before declaring the test failed.
EVENT_TIMEOUT_S = 5.0


def _add_local_source(repos, root: Path, source_id: str = "watch_test") -> None:
    """Insert a 'local' SourceConfig pointing at ``root``."""
    src = SourceConfig(
        source_id=source_id,
        source_type="local",
        display_name="Local FS (watch test)",
        enabled=True,
        config={"root": str(root)},
    )
    repos.sources.insert(src)


def test_watch_emits_added_on_new_file(repos, tmp_path: Path):
    """Real filesystem: create a file, watcher should yield an ADDED event."""
    _add_local_source(repos, tmp_path)

    service = WatchService(repos.sources, debounce_ms=100, step_ms=20)
    stop = threading.Event()
    received: list[PathChange] = []

    def producer():
        # Give the watcher time to set up the OS-level subscription.
        time.sleep(SETUP_DELAY_S)
        (tmp_path / "new_file.txt").write_text("hello watcher")

    threading.Thread(target=producer, daemon=True).start()

    deadline = time.time() + EVENT_TIMEOUT_S
    try:
        for change in service.watch(stop_event=stop):
            received.append(change)
            if change.kind is ChangeKind.ADDED and change.path.name == "new_file.txt":
                stop.set()
                break
            if time.time() > deadline:
                stop.set()
                break
    except StopIteration:
        pass

    matched = [
        c for c in received
        if c.kind is ChangeKind.ADDED and c.path.name == "new_file.txt"
    ]
    assert matched, (
        f"Did not receive ADDED event for new_file.txt within {EVENT_TIMEOUT_S}s. "
        f"Got events: {[(c.kind.value, c.path.name) for c in received]}"
    )
    # Source attribution survived the round-trip.
    assert matched[0].source_id == "watch_test"


def test_watch_emits_modified_on_existing_file(repos, tmp_path: Path):
    """Modify a pre-existing file; watcher should yield MODIFIED."""
    _add_local_source(repos, tmp_path)

    target = tmp_path / "existing.txt"
    target.write_text("v1")

    service = WatchService(repos.sources, debounce_ms=100, step_ms=20)
    stop = threading.Event()
    received: list[PathChange] = []

    def producer():
        time.sleep(SETUP_DELAY_S)
        # Append + flush forces a clear MODIFIED event on every platform.
        with target.open("a") as f:
            f.write(" v2\n")
            f.flush()

    threading.Thread(target=producer, daemon=True).start()

    deadline = time.time() + EVENT_TIMEOUT_S
    try:
        for change in service.watch(stop_event=stop):
            received.append(change)
            if change.kind is ChangeKind.MODIFIED and change.path.name == "existing.txt":
                stop.set()
                break
            if time.time() > deadline:
                stop.set()
                break
    except StopIteration:
        pass

    assert any(
        c.kind is ChangeKind.MODIFIED and c.path.name == "existing.txt"
        for c in received
    ), (
        f"Did not receive MODIFIED event for existing.txt within {EVENT_TIMEOUT_S}s. "
        f"Got events: {[(c.kind.value, c.path.name) for c in received]}"
    )


def test_watch_filters_ignored_patterns(repos, tmp_path: Path):
    """Files matching default ignore patterns should NOT yield events.

    We create both an ignored file (``.git/HEAD``) and a real file
    (``real.txt``); only the real file's event should surface.
    """
    _add_local_source(repos, tmp_path)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    service = WatchService(repos.sources, debounce_ms=100, step_ms=20)
    stop = threading.Event()
    received: list[PathChange] = []

    def producer():
        time.sleep(SETUP_DELAY_S)
        # Create the ignored file FIRST; it shouldn't surface.
        (git_dir / "HEAD").write_text("ref: refs/heads/main")
        time.sleep(0.1)
        # Then the real one — that's the one we expect to see.
        (tmp_path / "real.txt").write_text("real content")

    threading.Thread(target=producer, daemon=True).start()

    deadline = time.time() + EVENT_TIMEOUT_S
    try:
        for change in service.watch(stop_event=stop):
            received.append(change)
            if change.path.name == "real.txt":
                stop.set()
                break
            if time.time() > deadline:
                stop.set()
                break
    except StopIteration:
        pass

    # The real file should be in the events; the .git/HEAD file should NOT.
    paths = {c.path.name for c in received}
    assert "real.txt" in paths, (
        f"real.txt event missing within {EVENT_TIMEOUT_S}s. Got: {paths}"
    )
    assert "HEAD" not in paths, (
        f".git/HEAD slipped past the ignore filter. Got: {paths}"
    )


def test_watch_raises_when_no_local_sources(repos, tmp_path: Path):
    """No enabled local sources -> NoLocalSourcesError."""
    service = WatchService(repos.sources)
    with pytest.raises(NoLocalSourcesError):
        # Need to actually iterate — watch() is a generator.
        next(iter(service.watch()))


def test_watch_skips_disabled_sources(repos, tmp_path: Path):
    """A disabled source should be skipped during root resolution."""
    src = SourceConfig(
        source_id="disabled_one",
        source_type="local",
        display_name="Disabled",
        enabled=False,
        config={"root": str(tmp_path)},
    )
    repos.sources.insert(src)

    service = WatchService(repos.sources)
    with pytest.raises(NoLocalSourcesError):
        next(iter(service.watch()))


def test_len_is_zero_when_idle():
    # Doesn't even need a real source repo; len is a state read.
    class FakeRepo:
        def list_all(self):
            return []
    service = WatchService(FakeRepo())  # type: ignore[arg-type]
    assert len(service) == 0
