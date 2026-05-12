"""Cleanup service — empty dirs, broken symlinks, junk files, duplicates (Phase Gamma F6 + F7).

Phase Gamma Milestones Gamma-4 (F6) and Gamma-6 (F7) deliverables.
Four independent sub-pipelines that share a common report shape and
apply-mode plumbing:

  * **find_empty_dirs(root)** — walks the tree bottom-up, flags
    directories that have zero entries (or only system-junk files
    like Thumbs.db / .DS_Store, when ``ignore_system_junk=True``).

  * **find_broken_symlinks(root)** — flags symlinks whose target
    no longer exists. On Windows includes both true symlinks and
    junctions (Path.is_symlink covers both).

  * **find_junk_files(root, patterns=None)** — flags files matching
    a curated list of platform-junk filenames + glob patterns.
    Default patterns cover Windows (Thumbs.db, ehthumbs.db,
    desktop.ini), macOS (.DS_Store, ._*), Office lock files (~$*),
    temp files (*.tmp), and freedesktop trash (.Trash-*).

  * **find_duplicates(...)** — (v0.28) groups indexed files by
    ``xxhash3_128`` content hash, picks a canonical keeper per group
    via configurable strategy (shortest_path / longest_path / oldest /
    newest / keep_under), and flags the rest as removable. The dedup
    layer leverages Curator's existing hash index from Phase Alpha.

All find_* methods are plan-mode by default — they produce a
:class:`CleanupReport` of findings with sizes + reasons, no
filesystem changes. :meth:`apply` performs the actual deletions:

  * Empty dirs → ``Path.rmdir`` (refuses non-empty by definition).
  * Broken symlinks → ``Path.unlink`` (removes the link, not the
    nonexistent target).
  * Junk files / duplicate files → vendored send2trash (recoverable
    from Recycle Bin / Trash), with a fallback to direct
    ``Path.unlink`` if trash is unavailable.

SafetyService is consulted on every apply target — REFUSE-tier paths
(under OS_MANAGED registries) are skipped with a clear reason. This
prevents a misconfigured cleanup pattern from ever touching
``%SystemRoot%`` or similar.

Audit: every successful deletion writes an audit entry via the
optional :class:`AuditRepository` so cleanup operations are
introspectable forever.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from datetime import datetime
from curator._compat.datetime import utcnow_naive
from enum import Enum
from pathlib import Path
from typing import Iterable

from loguru import logger

from curator.models.file import FileEntity
from curator.services.fuzzy_index import (
    FuzzyIndex,
    FuzzyIndexUnavailableError,
)
from curator.services.safety import SafetyLevel, SafetyService
from curator.storage.queries import FileQuery
from curator.storage.repositories.audit_repo import AuditRepository
from curator.storage.repositories.file_repo import FileRepository


# ---------------------------------------------------------------------------
# Curated junk patterns
# ---------------------------------------------------------------------------

DEFAULT_JUNK_PATTERNS: tuple[str, ...] = (
    # Windows
    "Thumbs.db",
    "ehthumbs.db",
    "ehthumbs_vista.db",
    "desktop.ini",
    # macOS
    ".DS_Store",
    "._*",          # AppleDouble metadata sidecars
    ".AppleDouble",
    ".LSOverride",
    ".Spotlight-V100",
    ".Trashes",
    # Office lock files (any platform)
    "~$*",          # Office uses these to mark open documents
    # Temp files
    "*.tmp",
    "*.temp",
    "*.bak",        # Editor backup files
    "*~",           # vi/emacs backup
    # Linux desktop trash
    ".Trash-*",
    ".nfs*",        # NFS silly-rename
)
"""Glob patterns matched against file *basenames* (not full paths)."""


# Files that should NOT count toward "this directory has content" when
# detecting empty dirs. A folder that contains only a Thumbs.db is
# effectively empty.
SYSTEM_JUNK_NAMES: frozenset[str] = frozenset({
    "Thumbs.db", "ehthumbs.db", "ehthumbs_vista.db", "desktop.ini",
    ".DS_Store", ".Spotlight-V100",
})


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class CleanupKind(str, Enum):
    EMPTY_DIR = "empty_dir"
    BROKEN_SYMLINK = "broken_symlink"
    JUNK_FILE = "junk_file"
    DUPLICATE_FILE = "duplicate_file"


# Keep-strategy values for find_duplicates.
KEEP_STRATEGIES: tuple[str, ...] = (
    "shortest_path",
    "longest_path",
    "oldest",
    "newest",
)

# Match-kind values for find_duplicates (v0.30).
MATCH_KINDS: tuple[str, ...] = (
    "exact",  # bit-identical via xxhash3_128 (v0.28)
    "fuzzy",  # near-duplicates via MinHash-LSH on fuzzy_hash (v0.30)
)

# Default jaccard threshold for fuzzy dedup. Stricter than the lineage
# default (0.5) because cleanup is destructive — false positives mean
# the user accidentally deletes a file they wanted to keep.
DEFAULT_FUZZY_SIMILARITY_THRESHOLD: float = 0.85


@dataclass
class CleanupFinding:
    """A single thing that could be cleaned up."""

    path: str
    kind: CleanupKind
    size: int = 0
    details: dict = field(default_factory=dict)


@dataclass
class CleanupReport:
    """Result of a find_* call. Plan-mode output."""

    kind: CleanupKind
    root: str
    findings: list[CleanupFinding] = field(default_factory=list)
    started_at: datetime = field(default_factory=utcnow_naive)
    completed_at: datetime | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.findings)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.findings)

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


class ApplyOutcome(str, Enum):
    DELETED = "deleted"
    SKIPPED_REFUSE = "skipped_refuse"   # safety REFUSE-tier
    SKIPPED_MISSING = "skipped_missing"  # path no longer exists
    FAILED = "failed"


@dataclass
class ApplyResult:
    """Per-finding outcome of an apply pass."""

    finding: CleanupFinding
    outcome: ApplyOutcome
    error: str | None = None


@dataclass
class ApplyReport:
    """Result of :meth:`CleanupService.apply`."""

    kind: CleanupKind
    started_at: datetime = field(default_factory=utcnow_naive)
    completed_at: datetime | None = None
    results: list[ApplyResult] = field(default_factory=list)

    @property
    def deleted_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == ApplyOutcome.DELETED)

    @property
    def skipped_count(self) -> int:
        return sum(
            1 for r in self.results
            if r.outcome in (ApplyOutcome.SKIPPED_REFUSE, ApplyOutcome.SKIPPED_MISSING)
        )

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == ApplyOutcome.FAILED)

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


# ---------------------------------------------------------------------------
# CleanupService
# ---------------------------------------------------------------------------


class CleanupService:
    """Find + remove empty dirs, broken symlinks, junk files, duplicates.

    Args:
        safety: configured :class:`SafetyService`. Apply-mode will
            consult this on every target and skip REFUSE-tier paths.
        audit: optional :class:`AuditRepository`. When provided,
            apply writes one audit entry per successful deletion.
        file_repo: optional :class:`FileRepository`. Required for
            :meth:`find_duplicates`; the other find_* methods don't
            need it.
    """

    def __init__(
        self,
        safety: SafetyService,
        audit: AuditRepository | None = None,
        file_repo: FileRepository | None = None,
    ) -> None:
        self.safety = safety
        self.audit = audit
        self.file_repo = file_repo

    # ------------------------------------------------------------------
    # Empty-dir finder
    # ------------------------------------------------------------------

    def find_empty_dirs(
        self,
        root: str | Path,
        *,
        ignore_system_junk: bool = True,
    ) -> CleanupReport:
        """Walk ``root`` bottom-up flagging empty directories.

        With ``ignore_system_junk=True`` (default), a directory that
        contains only files matching :data:`SYSTEM_JUNK_NAMES`
        (Thumbs.db, .DS_Store, etc.) is considered empty. With it
        False, a strict zero-entry rule is used.

        The walk is bottom-up so that when a parent's only contents
        are subdirs that turned out to be empty, the parent is also
        flagged. (This mirrors how a user thinks about cleanup:
        "delete the leaves, then check if any branches became empty.")
        """
        root_p = Path(root)
        report = CleanupReport(
            kind=CleanupKind.EMPTY_DIR,
            root=str(root_p),
        )

        if not root_p.exists():
            report.errors.append(f"root does not exist: {root_p}")
            report.completed_at = utcnow_naive()
            return report
        if not root_p.is_dir():
            report.errors.append(f"root is not a directory: {root_p}")
            report.completed_at = utcnow_naive()
            return report

        # Track which dirs we've already classified as empty so a
        # parent containing only such dirs is also empty.
        empty_dirs: set[str] = set()

        try:
            for dirpath, dirnames, filenames in os.walk(root_p, topdown=False):
                p = Path(dirpath)
                # Don't flag the root itself; cleanup never deletes the
                # tree the user pointed us at.
                if p.resolve() == root_p.resolve():
                    continue

                meaningful_files = [
                    f for f in filenames
                    if not (ignore_system_junk and f in SYSTEM_JUNK_NAMES)
                ]
                meaningful_subdirs = [
                    d for d in dirnames
                    if str((p / d).resolve()) not in empty_dirs
                ]

                if not meaningful_files and not meaningful_subdirs:
                    empty_dirs.add(str(p.resolve()))
                    junk_present = [
                        f for f in filenames if f in SYSTEM_JUNK_NAMES
                    ]
                    report.findings.append(CleanupFinding(
                        path=str(p),
                        kind=CleanupKind.EMPTY_DIR,
                        size=0,
                        details={
                            "system_junk_present": junk_present,
                        },
                    ))
        except OSError as e:
            report.errors.append(f"os.walk failed: {e}")

        report.completed_at = utcnow_naive()
        logger.debug(
            "find_empty_dirs: {n} empty dirs under {r}",
            n=report.count, r=root_p,
        )
        return report

    # ------------------------------------------------------------------
    # Broken-symlink finder
    # ------------------------------------------------------------------

    def find_broken_symlinks(self, root: str | Path) -> CleanupReport:
        """Walk ``root`` flagging symlinks whose target doesn't exist.

        Uses ``Path.is_symlink`` + ``Path.exists()`` — the second
        returns False for a symlink whose target was deleted.
        On Windows this catches both true symlinks and junctions
        (both have is_symlink True).
        """
        root_p = Path(root)
        report = CleanupReport(
            kind=CleanupKind.BROKEN_SYMLINK,
            root=str(root_p),
        )

        if not root_p.exists():
            report.errors.append(f"root does not exist: {root_p}")
            report.completed_at = utcnow_naive()
            return report

        try:
            for dirpath, dirnames, filenames in os.walk(
                root_p, followlinks=False
            ):
                p = Path(dirpath)
                # Both files AND subdirs can be broken symlinks
                # (a dir-symlink whose target was deleted).
                for name in (*filenames, *dirnames):
                    candidate = p / name
                    try:
                        if candidate.is_symlink() and not candidate.exists():
                            target = None
                            try:
                                target = os.readlink(candidate)
                            except OSError:
                                pass
                            report.findings.append(CleanupFinding(
                                path=str(candidate),
                                kind=CleanupKind.BROKEN_SYMLINK,
                                size=0,
                                details={"target": target},
                            ))
                    except OSError as e:
                        # Permission or other read error on the link
                        # itself — record + move on.
                        report.errors.append(f"{candidate}: {e}")
        except OSError as e:
            report.errors.append(f"os.walk failed: {e}")

        report.completed_at = utcnow_naive()
        logger.debug(
            "find_broken_symlinks: {n} broken symlinks under {r}",
            n=report.count, r=root_p,
        )
        return report

    # ------------------------------------------------------------------
    # Junk-file finder
    # ------------------------------------------------------------------

    def find_junk_files(
        self,
        root: str | Path,
        *,
        patterns: Iterable[str] | None = None,
    ) -> CleanupReport:
        """Walk ``root`` flagging files matching ``patterns`` (basename glob).

        Patterns are fnmatch-style globs applied to the basename only,
        not the full path. Default patterns come from
        :data:`DEFAULT_JUNK_PATTERNS` and cover the well-known platform
        junk files (Thumbs.db, .DS_Store, desktop.ini, ~$*, *.tmp, etc.).
        """
        root_p = Path(root)
        used_patterns = tuple(patterns) if patterns is not None else DEFAULT_JUNK_PATTERNS
        report = CleanupReport(
            kind=CleanupKind.JUNK_FILE,
            root=str(root_p),
        )

        if not root_p.exists():
            report.errors.append(f"root does not exist: {root_p}")
            report.completed_at = utcnow_naive()
            return report

        try:
            for dirpath, _dirnames, filenames in os.walk(root_p):
                p = Path(dirpath)
                for name in filenames:
                    matched = next(
                        (pat for pat in used_patterns if fnmatch.fnmatch(name, pat)),
                        None,
                    )
                    if matched is None:
                        continue
                    candidate = p / name
                    try:
                        size = candidate.stat().st_size
                    except OSError:
                        size = 0
                    report.findings.append(CleanupFinding(
                        path=str(candidate),
                        kind=CleanupKind.JUNK_FILE,
                        size=size,
                        details={"matched_pattern": matched},
                    ))
        except OSError as e:
            report.errors.append(f"os.walk failed: {e}")

        report.completed_at = utcnow_naive()
        logger.debug(
            "find_junk_files: {n} junk files ({s} bytes) under {r}",
            n=report.count, s=report.total_size, r=root_p,
        )
        return report

    # ------------------------------------------------------------------
    # Duplicate finder (v0.28, F7)
    # ------------------------------------------------------------------

    def find_duplicates(
        self,
        *,
        source_id: str | None = None,
        root_prefix: str | None = None,
        keep_strategy: str = "shortest_path",
        keep_under: str | None = None,
        match_kind: str = "exact",
        similarity_threshold: float = DEFAULT_FUZZY_SIMILARITY_THRESHOLD,
    ) -> CleanupReport:
        """Find duplicate files in the index; flag non-keepers for removal.

        Two match modes:

          * ``match_kind="exact"`` (default, v0.28): groups files by
            ``xxhash3_128``. Bit-identical content only. Fast, accurate,
            zero false positives.

          * ``match_kind="fuzzy"`` (v0.30): groups files by MinHash-LSH
            similarity over ``fuzzy_hash`` at the configured threshold.
            Catches re-encoded JPEGs, re-compressed MP3s, re-OCR'd PDFs
            — anything that's the same content but different bytes.
            Connected components are walked transitively: if A~B and B~C
            but A and C aren't direct LSH matches, all three still land
            in the same group. **Higher false-positive risk** — always
            review the plan output before --apply, and the trash safety
            net (default ON) is highly recommended.

        Args:
            source_id: only consider files from this source.
            root_prefix: only consider files whose ``source_path`` starts
                with this prefix.
            keep_strategy: one of :data:`KEEP_STRATEGIES`.
            keep_under: optional path prefix taking precedence over
                ``keep_strategy``.
            match_kind: one of :data:`MATCH_KINDS`.
            similarity_threshold: only used for ``match_kind="fuzzy"``.
                Jaccard similarity on MinHash signatures, range (0, 1].
                Default :data:`DEFAULT_FUZZY_SIMILARITY_THRESHOLD` (0.85).
                Higher = stricter (fewer false positives, more misses);
                lower = looser (more recall, more false positives).

        Raises:
            RuntimeError: if no ``file_repo`` was provided to the ctor.
            ValueError: if ``keep_strategy`` or ``match_kind`` is invalid.
            FuzzyIndexUnavailableError: if ``match_kind="fuzzy"`` and
                ``datasketch`` is not installed.
        """
        if self.file_repo is None:
            raise RuntimeError(
                "find_duplicates requires a FileRepository; pass file_repo "
                "into CleanupService(...). The CLI runtime wires this "
                "automatically; tests must inject one explicitly."
            )
        if keep_strategy not in KEEP_STRATEGIES:
            raise ValueError(
                f"unknown keep_strategy {keep_strategy!r}. "
                f"Valid: {', '.join(KEEP_STRATEGIES)}"
            )
        if match_kind not in MATCH_KINDS:
            raise ValueError(
                f"unknown match_kind {match_kind!r}. "
                f"Valid: {', '.join(MATCH_KINDS)}"
            )

        if match_kind == "fuzzy":
            return self._find_fuzzy_duplicates(
                source_id=source_id,
                root_prefix=root_prefix,
                keep_strategy=keep_strategy,
                keep_under=keep_under,
                similarity_threshold=similarity_threshold,
            )

        # Default exact path (v0.28).
        report = CleanupReport(
            kind=CleanupKind.DUPLICATE_FILE,
            root=root_prefix or "<entire index>",
        )

        # Pull all hashed, non-deleted files matching the source/prefix.
        try:
            query = FileQuery(
                has_xxhash=True,
                source_ids=[source_id] if source_id else None,
                source_path_starts_with=root_prefix,
                deleted=False,
                # No limit — we need every hashed file to find groups.
                order_by="source_path ASC",
            )
            candidates = self.file_repo.query(query)
        except Exception as e:  # noqa: BLE001 — defensive at boundary
            report.errors.append(f"file_repo.query failed: {e}")
            report.completed_at = utcnow_naive()
            return report

        # Group by xxhash3_128.
        by_hash: dict[str, list[FileEntity]] = {}
        for f in candidates:
            if f.xxhash3_128 is None:  # defensive; FileQuery filters this out
                continue
            by_hash.setdefault(f.xxhash3_128, []).append(f)

        # For each group with >1 file, pick keeper + emit findings for the rest.
        for hash_key, group in by_hash.items():
            if len(group) < 2:
                continue
            keeper, kept_reason = self._pick_keeper(
                group, keep_strategy=keep_strategy, keep_under=keep_under,
            )
            for f in group:
                if f is keeper:
                    continue
                report.findings.append(CleanupFinding(
                    path=f.source_path,
                    kind=CleanupKind.DUPLICATE_FILE,
                    size=f.size,
                    details={
                        "kept_path": keeper.source_path,
                        "kept_reason": kept_reason,
                        "dupset_id": hash_key,
                        "hash": hash_key,
                        "mtime": f.mtime.isoformat() if f.mtime else None,
                        "source_id": f.source_id,
                        "match_kind": "exact",
                    },
                ))

        report.completed_at = utcnow_naive()
        logger.debug(
            "find_duplicates: {n} duplicates ({s} bytes) across {g} groups",
            n=report.count, s=report.total_size,
            g=sum(1 for v in by_hash.values() if len(v) > 1),
        )
        return report

    # ------------------------------------------------------------------
    # Fuzzy dedup (v0.30, F9)
    # ------------------------------------------------------------------

    def _find_fuzzy_duplicates(
        self,
        *,
        source_id: str | None,
        root_prefix: str | None,
        keep_strategy: str,
        keep_under: str | None,
        similarity_threshold: float,
    ) -> CleanupReport:
        """Group near-duplicate files via MinHash-LSH on ``fuzzy_hash``.

        Algorithm:
          1. Pull files with ``has_fuzzy_hash=True`` from the index.
          2. Build a :class:`FuzzyIndex` at the requested threshold.
          3. For each file, query for similar files → adjacency list.
          4. Walk connected components via union-find (transitive grouping).
          5. For each component with >1 file, pick keeper, emit findings.
        """
        report = CleanupReport(
            kind=CleanupKind.DUPLICATE_FILE,
            root=root_prefix or "<entire index>",
        )

        # Pull all fuzzy-hashed, non-deleted files matching the filters.
        try:
            query = FileQuery(
                has_fuzzy_hash=True,
                source_ids=[source_id] if source_id else None,
                source_path_starts_with=root_prefix,
                deleted=False,
                order_by="source_path ASC",
            )
            candidates = self.file_repo.query(query)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"file_repo.query failed: {e}")
            report.completed_at = utcnow_naive()
            return report

        if not candidates:
            report.completed_at = utcnow_naive()
            return report

        # Build LSH index. FuzzyIndexUnavailableError propagates by design —
        # the caller asked for fuzzy mode and we can't deliver, so they
        # need to know rather than getting a silent empty result.
        try:
            index = FuzzyIndex(threshold=similarity_threshold)
        except FuzzyIndexUnavailableError:
            raise
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"FuzzyIndex init failed: {e}")
            report.completed_at = utcnow_naive()
            return report

        # Map curator_id <-> FileEntity for lookup after queries.
        by_id: dict = {f.curator_id: f for f in candidates if f.fuzzy_hash}
        # Build the index. Skip files with malformed fuzzy_hashes silently —
        # they were probably hashed with a different tool / version.
        for entity in by_id.values():
            try:
                index.add(entity.curator_id, entity.fuzzy_hash)
            except (ValueError, FuzzyIndexUnavailableError) as e:
                report.errors.append(
                    f"FuzzyIndex.add failed for {entity.source_path}: {e}"
                )

        # Build adjacency: for each file, find its LSH neighbors.
        # The index returns the queried file too (it's in the index),
        # so we filter self out below.
        adjacency: dict = {cid: set() for cid in by_id}
        for cid, entity in by_id.items():
            try:
                neighbors = index.query(entity.fuzzy_hash)
            except (ValueError, FuzzyIndexUnavailableError):
                continue
            for n in neighbors:
                if n == cid:
                    continue  # self
                if n not in by_id:
                    continue  # defensive — shouldn't happen
                adjacency[cid].add(n)
                adjacency[n].add(cid)

        # Walk connected components via iterative BFS.
        components: list[list[FileEntity]] = []
        seen: set = set()
        for cid in by_id:
            if cid in seen:
                continue
            if not adjacency[cid]:
                # Isolated node — not a duplicate of anything.
                seen.add(cid)
                continue
            # BFS from cid.
            component_ids: set = set()
            queue = [cid]
            while queue:
                current = queue.pop()
                if current in component_ids:
                    continue
                component_ids.add(current)
                for neighbor in adjacency[current]:
                    if neighbor not in component_ids:
                        queue.append(neighbor)
            seen.update(component_ids)
            if len(component_ids) > 1:
                components.append([by_id[c] for c in component_ids])

        # Pick keeper per component + emit findings for non-keepers.
        for idx, group in enumerate(components):
            keeper, kept_reason = self._pick_keeper(
                group, keep_strategy=keep_strategy, keep_under=keep_under,
            )
            dupset_id = f"fuzzy:{idx}"
            for f in group:
                if f is keeper:
                    continue
                report.findings.append(CleanupFinding(
                    path=f.source_path,
                    kind=CleanupKind.DUPLICATE_FILE,
                    size=f.size,
                    details={
                        "kept_path": keeper.source_path,
                        "kept_reason": kept_reason,
                        "dupset_id": dupset_id,
                        "fuzzy_hash": f.fuzzy_hash,
                        "kept_fuzzy_hash": keeper.fuzzy_hash,
                        "mtime": f.mtime.isoformat() if f.mtime else None,
                        "source_id": f.source_id,
                        "match_kind": "fuzzy",
                        "similarity_threshold": similarity_threshold,
                    },
                ))

        report.completed_at = utcnow_naive()
        logger.debug(
            "find_fuzzy_duplicates: {n} fuzzy duplicates ({s} bytes) "
            "across {g} components at threshold {t}",
            n=report.count, s=report.total_size,
            g=len(components), t=similarity_threshold,
        )
        return report

    @staticmethod
    def _pick_keeper(
        group: list[FileEntity],
        *,
        keep_strategy: str,
        keep_under: str | None,
    ) -> tuple[FileEntity, str]:
        """Pick the keeper from a duplicate group. Returns (keeper, reason_str).

        ``keep_under`` is composable with ``keep_strategy``: when set,
        files matching the prefix are preferred, then the strategy
        breaks ties among them. When no file matches, the strategy
        applies to the whole group.
        """
        candidates = group
        keep_under_used = False
        if keep_under:
            normalized = keep_under.rstrip("/\\")
            matched = [
                f for f in group
                if f.source_path.startswith(normalized)
            ]
            if matched:
                candidates = matched
                keep_under_used = True

        if keep_strategy == "shortest_path":
            keeper = min(candidates, key=lambda f: (len(f.source_path), f.source_path))
        elif keep_strategy == "longest_path":
            keeper = max(candidates, key=lambda f: (len(f.source_path), f.source_path))
        elif keep_strategy == "oldest":
            keeper = min(candidates, key=lambda f: (f.mtime, f.source_path))
        elif keep_strategy == "newest":
            keeper = max(candidates, key=lambda f: (f.mtime, f.source_path))
        else:  # pragma: no cover — caller validates
            raise ValueError(f"unknown keep_strategy: {keep_strategy}")

        reason = keep_strategy
        if keep_under_used:
            reason = f"{keep_strategy} + keep_under"
        return keeper, reason

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(
        self,
        report: CleanupReport,
        *,
        use_trash: bool = True,
    ) -> ApplyReport:
        """Perform the deletions described in ``report``.

        Args:
            report: a :class:`CleanupReport` from one of the find_*
                methods. The report's ``kind`` determines deletion
                strategy:
                  * EMPTY_DIR → ``Path.rmdir`` (system junk files
                    are unlinked first if present so rmdir succeeds)
                  * BROKEN_SYMLINK → ``Path.unlink``
                  * JUNK_FILE → vendored send2trash if available and
                    ``use_trash=True``, else ``Path.unlink``
            use_trash: only affects junk files. When True (default),
                files are sent to the OS Recycle Bin / Trash so the
                user can recover them. When False (or trash unavailable),
                files are permadeleted.

        Returns an :class:`ApplyReport` with per-finding outcomes.
        SafetyService is consulted on every target — REFUSE-tier paths
        are skipped without modification. The on-disk world should
        never see a destructive action against an OS_MANAGED path.
        """
        out = ApplyReport(
            kind=report.kind,
            started_at=utcnow_naive(),
        )

        for finding in report.findings:
            target = Path(finding.path)
            if not target.exists() and not target.is_symlink():
                # is_symlink check handles broken symlinks (exists()
                # is False but the link itself is still there to unlink).
                out.results.append(ApplyResult(
                    finding=finding,
                    outcome=ApplyOutcome.SKIPPED_MISSING,
                ))
                continue

            # Safety check.
            try:
                verdict = self.safety.check_path(finding.path)
            except Exception as e:
                logger.warning(
                    "cleanup apply: safety check failed for {p}: {e}",
                    p=finding.path, e=e,
                )
                out.results.append(ApplyResult(
                    finding=finding,
                    outcome=ApplyOutcome.SKIPPED_REFUSE,
                    error=f"safety check failed: {e}",
                ))
                continue

            if verdict.level == SafetyLevel.REFUSE:
                concern_summary = ", ".join(
                    f"{c.value}: {d}" for c, d in verdict.concerns
                ) or "REFUSE"
                out.results.append(ApplyResult(
                    finding=finding,
                    outcome=ApplyOutcome.SKIPPED_REFUSE,
                    error=f"REFUSE: {concern_summary}",
                ))
                continue

            try:
                self._delete_one(finding, target, use_trash=use_trash)
            except Exception as e:
                logger.error(
                    "cleanup apply: delete failed for {p}: {e}",
                    p=finding.path, e=e,
                )
                out.results.append(ApplyResult(
                    finding=finding,
                    outcome=ApplyOutcome.FAILED,
                    error=str(e),
                ))
                continue

            out.results.append(ApplyResult(
                finding=finding,
                outcome=ApplyOutcome.DELETED,
            ))

            # v0.29: keep the Curator index consistent with the filesystem.
            # If this file was indexed, mark its FileEntity deleted so it
            # doesn't return as a phantom in subsequent queries (most
            # importantly, in subsequent find_duplicates runs).
            self._mark_index_deleted(finding)

            if self.audit is not None:
                try:
                    self.audit.log(
                        actor="curator.cleanup",
                        action=f"cleanup.{report.kind.value}.delete",
                        entity_type="path",
                        entity_id=finding.path,
                        details={
                            "kind": report.kind.value,
                            "size": finding.size,
                            **finding.details,
                            "use_trash": use_trash,
                        },
                    )
                except Exception as e:  # pragma: no cover — defensive
                    logger.warning("cleanup audit log failed: {e}", e=e)

        out.completed_at = utcnow_naive()
        logger.info(
            "cleanup apply ({k}): deleted={d} skipped={s} failed={f} in {dur:.2f}s",
            k=report.kind.value,
            d=out.deleted_count,
            s=out.skipped_count,
            f=out.failed_count,
            dur=out.duration_seconds or 0.0,
        )
        return out

    def _delete_one(
        self,
        finding: CleanupFinding,
        target: Path,
        *,
        use_trash: bool,
    ) -> None:
        """Strategy-pick deletion based on finding kind. Raises on failure."""
        if finding.kind == CleanupKind.EMPTY_DIR:
            # Remove any system-junk files first so rmdir succeeds.
            junk_present = finding.details.get("system_junk_present", []) or []
            for junk_name in junk_present:
                junk_path = target / junk_name
                try:
                    junk_path.unlink()
                except OSError:
                    pass  # Best effort; rmdir below may still succeed
            target.rmdir()

        elif finding.kind == CleanupKind.BROKEN_SYMLINK:
            target.unlink()

        elif finding.kind in (CleanupKind.JUNK_FILE, CleanupKind.DUPLICATE_FILE):
            # Both junk files and duplicate files use the same
            # send2trash-with-permadelete-fallback path. Duplicates
            # especially benefit from trash mode — if the dedup
            # heuristic picked the wrong keeper, the user can recover
            # from Recycle Bin / Trash.
            if use_trash:
                try:
                    from curator._vendored.send2trash import send2trash
                    send2trash(str(target))
                    return
                except Exception as e:
                    logger.debug(
                        "send2trash failed for {p} ({e}); falling back to unlink",
                        p=target, e=e,
                    )
            target.unlink()

        else:
            raise ValueError(f"unknown CleanupKind: {finding.kind}")

    # ------------------------------------------------------------------
    # Index sync (v0.29)
    # ------------------------------------------------------------------

    def _mark_index_deleted(self, finding: CleanupFinding) -> None:
        """Best-effort: mark the file deleted in the index after on-disk delete.

        Closes the phantom-file gap from v0.25 / v0.28. When a cleanup
        operation removes a file from disk, the corresponding FileEntity
        (if it exists in the index) is soft-deleted so subsequent
        queries don't return it.

        This is best-effort and NEVER raises:
          * ``self.file_repo`` may be None (CleanupService was constructed
            without it — e.g. in older tests). Skip silently.
          * Junk files / empty dirs / broken symlinks frequently AREN'T in
            the index (Thumbs.db etc. get filtered by classification).
            That's expected; we just skip.
          * For DUPLICATE_FILE findings, the source_id is in
            ``finding.details["source_id"]`` (set by find_duplicates).
            For other kinds, we don't know the source so we try the
            most common one ("local") as a best-effort fallback.
          * Empty-dir findings refer to a directory, not a file, so
            they're skipped entirely — directories aren't in the index.
        """
        if self.file_repo is None:
            return
        if finding.kind == CleanupKind.EMPTY_DIR:
            # Directories aren't tracked as FileEntity rows.
            return

        source_id = finding.details.get("source_id") or "local"
        try:
            entity = self.file_repo.find_by_path(source_id, finding.path)
        except Exception as e:  # noqa: BLE001 — defensive at boundary
            logger.debug(
                "index sync: find_by_path failed for {p}: {e}",
                p=finding.path, e=e,
            )
            return

        if entity is None:
            # File wasn't indexed under this source. For duplicates this
            # would be unexpected (find_duplicates pulled it FROM the
            # index); for junk/symlink it's normal.
            if finding.kind == CleanupKind.DUPLICATE_FILE:
                logger.debug(
                    "index sync: duplicate {p} not found under source_id={s}; "
                    "index may be stale",
                    p=finding.path, s=source_id,
                )
            return

        try:
            self.file_repo.mark_deleted(entity.curator_id)
            logger.debug(
                "index sync: marked {p} deleted in index (curator_id={cid})",
                p=finding.path, cid=entity.curator_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "index sync: mark_deleted failed for {p}: {e}",
                p=finding.path, e=e,
            )


__all__ = [
    "DEFAULT_FUZZY_SIMILARITY_THRESHOLD",
    "DEFAULT_JUNK_PATTERNS",
    "KEEP_STRATEGIES",
    "MATCH_KINDS",
    "SYSTEM_JUNK_NAMES",
    "ApplyOutcome",
    "ApplyReport",
    "ApplyResult",
    "CleanupFinding",
    "CleanupKind",
    "CleanupReport",
    "CleanupService",
]
