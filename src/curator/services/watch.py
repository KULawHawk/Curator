"""WatchService — reactive filesystem watcher for local sources (Tier 6).

DESIGN.md §6 / Phase Beta gate #3 / scoping doc:
``docs/PHASE_BETA_WATCH.md``.

Phase Alpha and earlier-Phase Beta scanning is purely batch. Tier 6 adds
the reactive layer: a long-running iterator that observes filesystem
events on enabled local sources and yields :class:`PathChange` events.

v0.16 (this turn): standalone iterator. The CLI ``curator watch``
command consumes the stream and prints events. v0.17+ will pipe events
into :class:`ScanService` for incremental hash + lineage updates.

The implementation lazy-imports ``watchfiles`` so ``import curator``
still works when the optional ``[beta]`` extra isn't installed. If a
caller invokes :meth:`WatchService.watch` without ``watchfiles``, a
clear :class:`WatchUnavailableError` is raised with install
instructions.

Cross-platform: ``watchfiles`` uses ReadDirectoryChangesW on Windows,
FSEvents on macOS, inotify on Linux. We don't have to think about it.
"""

from __future__ import annotations

import fnmatch
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator

from loguru import logger

from curator.storage.repositories.source_repo import SourceRepository


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class WatchError(Exception):
    """Base for WatchService errors."""


class WatchUnavailableError(WatchError):
    """``watchfiles`` isn't installed."""


class NoLocalSourcesError(WatchError):
    """The caller asked to watch but no enabled local sources exist."""


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ChangeKind(str, Enum):
    """Kinds of filesystem events Curator cares about.

    Maps onto ``watchfiles.Change``:
        Change.added    -> ADDED
        Change.modified -> MODIFIED
        Change.deleted  -> DELETED
    """
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True)
class PathChange:
    """One filesystem event Curator surfaces from a source root.

    Frozen so consumers can store events in sets / dicts (e.g. for
    deduplication or coalescing).
    """
    kind: ChangeKind
    path: Path
    source_id: str
    detected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict[str, str]:
        """JSON-serializable view (for ``--json`` CLI output)."""
        return {
            "kind": self.kind.value,
            "path": str(self.path),
            "source_id": self.source_id,
            "detected_at": self.detected_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    ".git/*",
    ".git",
    "__pycache__/*",
    "__pycache__",
    ".pytest_cache/*",
    ".pytest_cache",
    "*.pyc",
    "*.tmp",
    "*~",            # vim/emacs backup
    ".#*",           # emacs lock file
    "*.swp",         # vim swap
    ".DS_Store",     # macOS metadata
    "Thumbs.db",     # Windows thumbnails
)

DEFAULT_DEBOUNCE_MS: int = 1000
"""How long to coalesce same-(path, kind) events before re-emitting."""

DEFAULT_STEP_MS: int = 50
"""Per-event poll latency in the underlying watchfiles loop."""


# ---------------------------------------------------------------------------
# Debouncer
# ---------------------------------------------------------------------------

class _Debouncer:
    """Per-(path, kind) coalescing window.

    Phase Beta v0.16: in-memory only, no LRU eviction. For long-running
    watches with millions of distinct paths, memory grows without bound
    — acceptable for this gate; Phase Gamma can add bounded LRU.
    """

    def __init__(self, window_ms: int) -> None:
        self._window_seconds = window_ms / 1000.0
        self._last_seen: dict[tuple[str, ChangeKind], float] = {}

    def should_emit(self, path: str, kind: ChangeKind, now_seconds: float) -> bool:
        """Return True if (path, kind) hasn't fired within the window.

        DELETED events bypass the debouncer — they're rare and we want
        to react fast.
        """
        if kind is ChangeKind.DELETED:
            return True
        key = (path, kind)
        last = self._last_seen.get(key)
        if last is not None and (now_seconds - last) < self._window_seconds:
            return False
        self._last_seen[key] = now_seconds
        return True

    def __len__(self) -> int:
        return len(self._last_seen)


# ---------------------------------------------------------------------------
# Ignore matching
# ---------------------------------------------------------------------------

def _matches_any_pattern(rel_path: str, patterns: tuple[str, ...]) -> bool:
    """True if ``rel_path`` matches any of the glob patterns.

    Patterns are matched against the full relative-from-source-root
    string AND against each path component, so ``__pycache__`` filters
    every nested ``foo/bar/__pycache__/baz.pyc`` correctly.
    """
    rel_norm = rel_path.replace("\\", "/")
    for pat in patterns:
        if fnmatch.fnmatchcase(rel_norm, pat):
            return True
        # Match against each component (handles dir-pattern shortcuts)
        for component in rel_norm.split("/"):
            if fnmatch.fnmatchcase(component, pat):
                return True
    return False


# ---------------------------------------------------------------------------
# WatchService
# ---------------------------------------------------------------------------

class WatchService:
    """Observe local source roots; yield :class:`PathChange` events.

    See module docstring + ``docs/PHASE_BETA_WATCH.md`` for rationale.
    """

    def __init__(
        self,
        source_repo: SourceRepository,
        *,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        step_ms: int = DEFAULT_STEP_MS,
        ignore_patterns: tuple[str, ...] | None = None,
    ) -> None:
        self._sources = source_repo
        self._debounce_ms = debounce_ms
        self._step_ms = step_ms
        self._ignore_patterns = (
            ignore_patterns
            if ignore_patterns is not None
            else DEFAULT_IGNORE_PATTERNS
        )
        # Lazy-set when watch() starts.
        self._active_roots: dict[Path, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def watch(
        self,
        source_ids: list[str] | None = None,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[PathChange]:
        """Yield :class:`PathChange` events until ``stop_event`` is set
        or the caller breaks out of the loop.

        Args:
            source_ids: optional list of source ids to watch. If None,
                        watches every enabled local source.
            stop_event: optional threading.Event to signal early
                        termination. The watcher checks it between
                        events; the underlying watchfiles loop also
                        respects it.

        Raises:
            WatchUnavailableError: ``watchfiles`` not installed.
            NoLocalSourcesError: no enabled local sources resolved.
        """
        try:
            from watchfiles import Change, watch as wf_watch
        except ImportError as e:
            raise WatchUnavailableError(
                "watchfiles is not installed. Install it with: "
                "pip install 'curator[beta]'  (or just: pip install watchfiles)"
            ) from e

        roots = self._resolve_roots(source_ids)
        if not roots:
            raise NoLocalSourcesError(
                "No enabled local sources to watch. Add one with "
                "`curator sources add <id> --type local --root <path>`."
            )
        self._active_roots = roots

        debouncer = _Debouncer(self._debounce_ms)

        # Map watchfiles Change -> our ChangeKind
        change_map = {
            Change.added: ChangeKind.ADDED,
            Change.modified: ChangeKind.MODIFIED,
            Change.deleted: ChangeKind.DELETED,
        }

        logger.info(
            "WatchService starting on {n} root(s): {paths}",
            n=len(roots),
            paths=[str(p) for p in roots],
        )

        try:
            for batch in wf_watch(
                *roots.keys(),
                step=self._step_ms,
                stop_event=stop_event,
                yield_on_timeout=False,
            ):
                now = datetime.now(timezone.utc).timestamp()
                for wf_change, raw_path in batch:
                    kind = change_map.get(wf_change)
                    if kind is None:
                        continue
                    abs_path = Path(raw_path).resolve()
                    source_id = self._resolve_source_id(abs_path)
                    if source_id is None:
                        # File outside any watched root — shouldn't
                        # happen but be defensive.
                        continue
                    rel = self._relative_to_source(abs_path, source_id)
                    if rel is None:
                        continue
                    if _matches_any_pattern(rel, self._ignore_patterns):
                        continue
                    if not debouncer.should_emit(str(abs_path), kind, now):
                        continue
                    yield PathChange(
                        kind=kind,
                        path=abs_path,
                        source_id=source_id,
                    )
        finally:
            self._active_roots = {}
            logger.info("WatchService stopped")

    def __len__(self) -> int:
        """Number of source roots currently being watched (0 when idle)."""
        return len(self._active_roots)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_roots(
        self, source_ids: list[str] | None
    ) -> dict[Path, str]:
        """Build {abs_root_path: source_id} for the requested sources.

        Skips sources that:
          * aren't local
          * are disabled
          * have a non-existent root
          * resolve to a non-directory
        """
        if source_ids is None:
            sources = self._sources.list_all()
        else:
            sources = [
                s for s in [self._sources.get(sid) for sid in source_ids]
                if s is not None
            ]

        roots: dict[Path, str] = {}
        for s in sources:
            if s.source_type != "local":
                continue
            if not s.enabled:
                continue
            root_str = s.config.get("root") if isinstance(s.config, dict) else None
            if not root_str:
                logger.debug("source {sid} has no 'root' in config; skipping", sid=s.source_id)
                continue
            root = Path(root_str).resolve()
            if not root.exists() or not root.is_dir():
                logger.warning(
                    "source {sid} root {r} doesn't exist or isn't a directory; skipping",
                    sid=s.source_id, r=root,
                )
                continue
            roots[root] = s.source_id
        return roots

    def _resolve_source_id(self, abs_path: Path) -> str | None:
        """Find which active root a path falls under."""
        for root, sid in self._active_roots.items():
            try:
                abs_path.relative_to(root)
                return sid
            except ValueError:
                continue
        return None

    def _relative_to_source(self, abs_path: Path, source_id: str) -> str | None:
        for root, sid in self._active_roots.items():
            if sid == source_id:
                try:
                    return str(abs_path.relative_to(root))
                except ValueError:
                    return None
        return None


__all__ = [
    "ChangeKind",
    "PathChange",
    "WatchError",
    "WatchService",
    "WatchUnavailableError",
    "NoLocalSourcesError",
    "DEFAULT_DEBOUNCE_MS",
    "DEFAULT_IGNORE_PATTERNS",
    "DEFAULT_STEP_MS",
]
