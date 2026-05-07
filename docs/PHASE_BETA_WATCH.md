# Phase Beta Plan: File watcher service (Tier 6)

**Status:** scoping → starting (2026-05-06)
**Owner:** Jake Leese
**Tracking:** Phase Beta gate #3 in `BUILD_TRACKER.md` (Tier 6 in `DESIGN.md` §2.1)
**Cross-references:** `DESIGN.md` §6 (reactive scanning), `Github/CURATOR_RESEARCH_NOTES.md` Round 3 (watchfiles decision)

---

## Problem statement

Phase Alpha and earlier-Phase Beta scanning is purely batch: the user runs
`curator scan local /some/dir` and Curator processes everything fresh.
That's fine for one-shot ingest but doesn't reflect how files actually
change. A user editing a manuscript sees three modifications a minute;
re-scanning the whole tree on each save would be wasteful.

Tier 6 introduces a **reactive scanning** layer: a long-running watcher
that observes filesystem events and reacts incrementally. The MVP just
emits typed events; later milestones wire those events into
`ScanService` for partial-scan execution.

`watchfiles` (Rust-backed, watchdog-compatible API) is the right
foundation: cross-platform (Windows ReadDirectoryChangesW, macOS FSEvents,
Linux inotify), already does coalescing, and has a sync iterator API
that fits a CLI command perfectly.

---

## Contract design

Single new module: `src/curator/services/watch.py`

```python
@dataclass(frozen=True)
class PathChange:
    """One filesystem event Curator cares about."""
    kind: ChangeKind            # added | modified | deleted
    path: Path                  # absolute path
    source_id: str              # the SourceConfig this came from
    detected_at: datetime       # UTC timestamp


class ChangeKind(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


class WatchService:
    """Observes local source roots and yields PathChange events.

    v0.16 (this turn): standalone iterator, no ScanService wiring.
    v0.17+:           consumers can pipe events into ScanService for
                      incremental hash + lineage updates.
    """

    def __init__(
        self,
        source_repo: SourceRepository,
        *,
        debounce_ms: int = 1000,
        step_ms: int = 50,
        ignore_patterns: list[str] | None = None,
    ) -> None: ...

    def watch(
        self,
        source_ids: list[str] | None = None,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[PathChange]: ...

    def __len__(self) -> int:
        """Number of source roots currently being watched."""
```

### What's watched

* All `SourceConfig` rows with `source_type = "local"` and `enabled = True`.
* Each source's `root` (resolved at iteration start, not re-checked
  while iterating).
* Network drives that disappear mid-watch get logged + skipped on the
  next event tick; we don't crash.

### What's filtered

* `ignore_patterns` defaults to `[".git/", "__pycache__/", ".pytest_cache/", "*.tmp", "*~"]`
  so editor temp files and VCS chatter don't dominate the stream.
* Patterns are simple glob fragments; matched against the relative path
  from the source root. (Future: respect `.gitignore` per-source.)

### Debouncing semantics

`watchfiles` emits multiple events per save in many editors (vim makes
4-5 syscalls per `:w`). We use the library's built-in `step` parameter
plus our own per-path debounce:

* Library `step_ms=50`: latency between OS event and our handler.
* Our `debounce_ms=1000`: a path that just emitted ADDED/MODIFIED is
  silently coalesced if the same kind fires again within 1s.

DELETED events are NOT debounced — they're rarer and we want to react
fast.

### Stop semantics

The iterator runs until either:
1. The provided `stop_event` is set (preferred, clean).
2. The caller breaks out of the loop (still clean — `watchfiles` handles
   teardown).
3. SIGINT (Ctrl+C) — propagates as `KeyboardInterrupt`; the CLI
   command catches and logs `watch ended (Ctrl+C)`.

---

## Lifecycle

```
                 ┌─────────────────────────────────────────────┐
 CLI: ──────────▶│ curator watch [SOURCE]                       │
                 │   1. Build WatchService from runtime         │
                 │   2. Resolve roots from SourceRepository     │
                 │   3. for change in service.watch(): ...      │
                 │   4. KeyboardInterrupt → clean stop          │
                 └─────────────────────────────────────────────┘
                                       │
                                       ▼
                 ┌─────────────────────────────────────────────┐
                 │ WatchService.watch()                         │
                 │   • Composes watchfiles.watch(*roots)        │
                 │   • For each event:                          │
                 │       - filter by ignore_patterns            │
                 │       - debounce per (path, kind)            │
                 │       - resolve back to (source_id, abs_path)│
                 │       - yield PathChange                     │
                 └─────────────────────────────────────────────┘
                                       │
                                       ▼
                 ┌─────────────────────────────────────────────┐
                 │ Caller (CLI for v0.16; ScanService for v0.17)│
                 └─────────────────────────────────────────────┘
```

---

## Cut-off plan

### v0.16 — `WatchService` + `curator watch` CLI (THIS GATE)

In:
* `src/curator/services/watch.py` with the contract above.
* `watchfiles` added to `[beta]` extras in `pyproject.toml`.
* Lazy-imported (so `import curator` still works without it).
* `curator watch [SOURCE]` CLI command:
  * No SOURCE arg → watch all enabled local sources.
  * SOURCE arg → watch only that source (errors if not local/not enabled).
  * `--json` flag → emit JSON-lines per event instead of human-readable.
  * Blocks until Ctrl+C.
* Unit tests at `tests/unit/test_watch.py`:
  * PathChange dataclass round-trips.
  * Debouncer coalesces duplicate ADDED+ADDED within debounce window.
  * Debouncer does NOT coalesce ADDED followed by DELETED.
  * Ignore patterns: `.git/objects/abc` filtered, `regular.txt` not.
* Integration test at `tests/integration/test_watch_smoke.py`:
  * Skipped without `watchfiles` (`pytest.importorskip`).
  * Real filesystem: create file, observe ADDED event; modify, observe
    MODIFIED; delete, observe DELETED.
  * Uses a `threading.Event` to stop after first event arrives.
  * Marked `@pytest.mark.slow` — actual FS events are timing-sensitive.

Out (deferred to v0.17+):
* Wiring events into `ScanService` for incremental scanning.
* Persistent watch state across CLI invocations.
* GUI integration.
* Multi-process safety (locking when two `curator watch` processes
  observe overlapping roots).

### v0.17 — Incremental scan integration

In:
* New `ScanService.scan_paths(source_id, paths)` that processes a
  specific list rather than walking from a root.
* `curator watch --apply` flag: every event triggers a scan_paths call.
* Backpressure handling: if events arrive faster than scans complete,
  coalesce into a queue.

### v0.18 — Polish + docs

In:
* `examples/watch_demo.py` — a script that watches a folder and
  prints events.
* README section on reactive scanning.

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| `watchfiles` install fails on Jake's Python (3.13) | Low | watchfiles has Python 3.13 wheels on Windows; verified by checking PyPI page |
| Editor temp files dominate event stream | High | Default `ignore_patterns` covers vim/emacs/jetbrains conventions; users can override |
| Network drive disappears mid-watch | Medium | Catch `OSError` per-event; log + continue; iterator terminates if all roots gone |
| Multiple `curator watch` processes step on each other | Low (Phase Beta) | Doc as Phase Gamma concern; v0.16 doesn't lock |
| Watching root with millions of files (e.g. `C:\`) is slow | Medium | watchfiles is Rust-backed and uses native FS APIs; scales to ~100k+ files. Doc the practical ceiling |

---

## Out of scope

* GUI (separate gate).
* Cloud source watching (Drive/OneDrive/Dropbox each have their own
  webhook/polling mechanisms — separate per-source gates).
* Cross-platform send2trash (separate gate).

---

## Revision log

* **2026-05-06** — Doc created. v0.16 starts immediately.
