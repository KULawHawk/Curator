"""Lineage service — orchestrates lineage detection across files.

DESIGN.md §8.4.

This service:
    1. Finds candidate files that might be related to the input file
       (using indexed columns: xxhash, size, fuzzy_hash, parent dir).
    2. Runs every registered ``curator_compute_lineage`` plugin on each
       (input, candidate) pair.
    3. Filters proposed edges by per-kind confidence threshold.
    4. Persists qualifying edges via :class:`LineageRepository` (with
       ``on_conflict='ignore'`` so re-runs are idempotent).

Candidate selection avoids the O(n²) "compare every pair" trap by using
indexed columns to narrow the search. For Phase Alpha this is good
enough; Phase Beta+ adds LSH (datasketch) for cheaper fuzzy candidate
selection at scale.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pluggy
from loguru import logger

from curator.models.file import FileEntity
from curator.models.lineage import LineageEdge, LineageKind
from curator.storage.queries import FileQuery
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.lineage_repo import LineageRepository

if TYPE_CHECKING:  # pragma: no cover
    from curator.services.fuzzy_index import FuzzyIndex


# Confidence thresholds per edge kind (DESIGN §8.2).
# Edges below the kind's auto-confirm threshold are NOT persisted unless
# the plugin explicitly insists (no current way to insist; we may add
# `insist=True` to LineageEdge if needed).
_AUTO_CONFIRM_THRESHOLDS: dict[LineageKind, float] = {
    LineageKind.DUPLICATE: 1.0,
    LineageKind.NEAR_DUPLICATE: 0.95,
    LineageKind.DERIVED_FROM: 0.90,
    LineageKind.VERSION_OF: 0.85,
    LineageKind.REFERENCED_BY: 1.0,
    LineageKind.SAME_LOGICAL_FILE: 0.95,
}

# Below the auto-confirm threshold but above this -> "escalate" tier.
# Phase Alpha doesn't act on escalation differently from auto-confirm,
# but we expose the threshold so the CLI can surface them for review.
_ESCALATE_THRESHOLDS: dict[LineageKind, float] = {
    LineageKind.NEAR_DUPLICATE: 0.70,
    LineageKind.DERIVED_FROM: 0.60,
    LineageKind.VERSION_OF: 0.60,
    LineageKind.SAME_LOGICAL_FILE: 0.70,
}


class LineageService:
    """Compute, filter, and persist lineage edges for files.

    Phase Alpha behavior:
      * ``compute_for_file(file)`` finds candidates and emits edges.
      * Edges at or above the auto-confirm threshold are persisted.
      * Edges in the escalate tier are returned but NOT auto-persisted
        (CLI/UI surfaces them for user review). Phase Alpha doesn't yet
        have a UI for this — they're effectively dropped.
    """

    def __init__(
        self,
        plugin_manager: pluggy.PluginManager,
        file_repo: FileRepository,
        lineage_repo: LineageRepository,
        fuzzy_index: "FuzzyIndex | None" = None,
    ):
        self.pm = plugin_manager
        self.files = file_repo
        self.lineage = lineage_repo
        # Optional MinHash-LSH index for cheap fuzzy candidate selection
        # (Phase Beta v0.14). When None, falls back to O(n) DB scan.
        # When set, the service self-maintains the index: each file
        # processed by ``compute_for_file`` is added after detection
        # so subsequent files can find it via LSH.
        self.fuzzy_index = fuzzy_index

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_for_file(
        self,
        file: FileEntity,
        *,
        persist: bool = True,
    ) -> list[LineageEdge]:
        """Compute lineage edges for a single file against its candidates.

        Args:
            file: the file we just scanned/updated.
            persist: if True (default), insert qualifying edges into the
                     DB. Set False for dry-run / preview.

        Returns:
            List of edges that met the auto-confirm threshold. Edges in
            the escalate tier are NOT included (Phase Alpha drops them).
        """
        candidates = self._find_candidates(file)
        if not candidates:
            return []

        edges: list[LineageEdge] = []
        for candidate in candidates:
            edges.extend(self._run_detectors(file, candidate))

        # Filter by auto-confirm threshold.
        confirmed = [
            e for e in edges
            if e.confidence >= _AUTO_CONFIRM_THRESHOLDS.get(e.edge_kind, 1.0)
        ]

        if persist:
            for edge in confirmed:
                self.lineage.insert(edge, on_conflict="ignore")

        # Self-maintain the LSH index: now that we've processed this
        # file, add it so subsequent ``compute_for_file`` calls can
        # find it via the LSH path. Idempotent: if it's already there
        # (re-scan after content change), ``add`` replaces the entry.
        if self.fuzzy_index is not None and file.fuzzy_hash:
            try:
                self.fuzzy_index.add(file.curator_id, file.fuzzy_hash)
            except (ValueError, Exception) as e:  # pragma: no cover — defensive
                logger.warning(
                    "failed to add {cid} to fuzzy_index: {e}",
                    cid=file.curator_id, e=e,
                )

        return confirmed

    def compute_for_pair(
        self,
        file_a: FileEntity,
        file_b: FileEntity,
        *,
        persist: bool = True,
    ) -> list[LineageEdge]:
        """Compute lineage between an explicit pair of files.

        Useful for direct user-driven comparisons (CLI: ``curator lineage
        compare a b``).
        """
        edges = self._run_detectors(file_a, file_b)
        confirmed = [
            e for e in edges
            if e.confidence >= _AUTO_CONFIRM_THRESHOLDS.get(e.edge_kind, 1.0)
        ]
        if persist:
            for edge in confirmed:
                self.lineage.insert(edge, on_conflict="ignore")
        return confirmed

    def get_edges_for(self, curator_id: UUID) -> list[LineageEdge]:
        """All persisted edges touching this file (either direction)."""
        return self.lineage.get_edges_for(curator_id)

    def threshold(self, kind: LineageKind, *, escalate: bool = False) -> float:
        """Public accessor for the per-kind threshold tables.

        ``escalate=True`` returns the lower (review-tier) threshold.
        """
        if escalate and kind in _ESCALATE_THRESHOLDS:
            return _ESCALATE_THRESHOLDS[kind]
        return _AUTO_CONFIRM_THRESHOLDS.get(kind, 1.0)

    # ------------------------------------------------------------------
    # Internal: candidate selection
    # ------------------------------------------------------------------

    def _find_candidates(self, file: FileEntity) -> list[FileEntity]:
        """Find files that might be related to ``file``.

        Uses indexed columns to avoid O(n²). Sources of candidates:

          * Same xxhash3_128  -> DUPLICATE candidates
          * Same size         -> NEAR_DUPLICATE / DERIVED_FROM candidates
          * Has fuzzy_hash    -> NEAR_DUPLICATE candidates (we have one too)
          * Same parent dir   -> VERSION_OF candidates

        Returns deduplicated candidates (no curator_id appears twice).
        """
        candidates: dict[UUID, FileEntity] = {}

        # Same xxhash bucket
        if file.xxhash3_128:
            for f in self.files.find_by_hash(file.xxhash3_128):
                if f.curator_id != file.curator_id:
                    candidates[f.curator_id] = f

        # Same size
        for f in self.files.find_candidates_by_size(
            file.size, exclude_curator_id=file.curator_id
        ):
            candidates[f.curator_id] = f

        # Fuzzy hash bucket — only meaningful if we have one too.
        # Phase Beta v0.14: when a FuzzyIndex is available, route this
        # path through MinHash-LSH (O(1) average) instead of the O(n)
        # ``find_with_fuzzy_hash`` scan. Both paths produce the same
        # set of candidates (the downstream detector still gates the
        # actual edge emission via ``ppdeep.compare()``). The integration
        # test ``test_lsh_path_matches_baseline`` enforces this
        # equivalence on a controlled corpus.
        if file.fuzzy_hash:
            if self.fuzzy_index is not None and len(self.fuzzy_index) > 0:
                try:
                    candidate_ids = self.fuzzy_index.query(file.fuzzy_hash)
                except ValueError as e:
                    # Malformed fuzzy_hash on this file. Log + fall through
                    # to the O(n) path which is more permissive on input.
                    logger.warning(
                        "FuzzyIndex.query rejected hash {h!r}: {e}; falling back",
                        h=file.fuzzy_hash, e=e,
                    )
                    candidate_ids = None
                if candidate_ids is not None:
                    for cid in candidate_ids:
                        if cid == file.curator_id:
                            continue
                        if cid in candidates:
                            continue
                        f = self.files.get(cid)
                        if f is not None and f.deleted_at is None:
                            candidates[cid] = f
                else:
                    # Fallback path (O(n))
                    for f in self.files.find_with_fuzzy_hash():
                        if f.curator_id != file.curator_id:
                            candidates[f.curator_id] = f
            else:
                # No FuzzyIndex available, or it's empty — use the
                # original O(n) scan.
                for f in self.files.find_with_fuzzy_hash():
                    if f.curator_id != file.curator_id:
                        candidates[f.curator_id] = f

        # Same parent directory (for VERSION_OF detection)
        parent = str(Path(file.source_path).parent)
        if parent:
            query = FileQuery(
                source_ids=[file.source_id],
                source_path_starts_with=parent + os.sep,
                deleted=False,
                limit=500,  # safety cap; pathological deep dirs
            )
            try:
                for f in self.files.query(query):
                    if f.curator_id == file.curator_id:
                        continue
                    # Direct children only (not nested).
                    if Path(f.source_path).parent == Path(file.source_path).parent:
                        candidates[f.curator_id] = f
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("parent-dir candidate query failed: {e}", e=e)

        return list(candidates.values())

    # ------------------------------------------------------------------
    # Internal: detector invocation
    # ------------------------------------------------------------------

    def _run_detectors(
        self,
        file_a: FileEntity,
        file_b: FileEntity,
    ) -> list[LineageEdge]:
        """Run every ``curator_compute_lineage`` plugin on a pair."""
        results = self.pm.hook.curator_compute_lineage(
            file_a=file_a, file_b=file_b
        )
        return [r for r in results if r is not None]


    # ------------------------------------------------------------------
    # v1.7.1 (T-A01): Fuzzy-Match Version Stacking
    # ------------------------------------------------------------------

    def find_version_stacks(
        self,
        *,
        min_confidence: float = 0.7,
        kinds: list[LineageKind] | None = None,
    ) -> list[list[FileEntity]]:
        """Group files into 'version stacks' via connected components.

        A version stack is a maximal set of files connected (transitively)
        by lineage edges of the chosen kinds with confidence >=
        ``min_confidence``. Captures the "Draft_1 / Draft_Final /
        Draft_FINAL_v2" pattern that the existing
        :class:`~curator.plugins.core.lineage_fuzzy_dup.FuzzyDupPlugin`
        already detects pairwise — this method takes those pairwise edges
        and walks the graph to find whole families.

        Args:
            min_confidence: Drop edges below this confidence. Default
                0.7 matches the
                :data:`~curator.plugins.core.lineage_fuzzy_dup.SIMILARITY_THRESHOLD`
                of 70% (stored as 0.7 in the DB).
            kinds: Which lineage edge kinds to walk. Defaults to
                ``[NEAR_DUPLICATE, VERSION_OF]``. Pass ``[DUPLICATE]``
                to get exact-hash stacks instead (GroupDialog territory).

        Returns:
            List of stacks. Each stack is a list of :class:`FileEntity`
            with len >= 2, sorted by ``mtime`` descending (newest first).
            The list of stacks itself is sorted by stack size descending
            (biggest stacks first).

        Performance: O(E + V * alpha(V)) where E is edge count and V is
        file count touched by edges. Union-find with path compression.
        Deleted files are filtered out before grouping; a stack that
        ends up with <2 live files is dropped.
        """
        if kinds is None:
            kinds = [LineageKind.NEAR_DUPLICATE, LineageKind.VERSION_OF]

        # 1. Pull all qualifying edges across the requested kinds.
        edges: list[LineageEdge] = []
        for kind in kinds:
            edges.extend(
                self.lineage.list_by_kind(kind, min_confidence=min_confidence)
            )
        if not edges:
            return []

        # 2. Union-find with path compression.
        parent: dict[UUID, UUID] = {}

        def find(x: UUID) -> UUID:
            # iterative for safety against deep chains
            root = x
            while parent.get(root, root) != root:
                root = parent[root]
            # path compression
            cur = x
            while parent.get(cur, cur) != root:
                nxt = parent[cur]
                parent[cur] = root
                cur = nxt
            parent.setdefault(root, root)
            return root

        def union(a: UUID, b: UUID) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for edge in edges:
            union(edge.from_curator_id, edge.to_curator_id)

        # 3. Group curator_ids by their root.
        groups: dict[UUID, list[UUID]] = {}
        for cid in list(parent.keys()):
            root = find(cid)
            groups.setdefault(root, []).append(cid)

        # 4. Resolve to FileEntity; drop deleted; drop singletons.
        stacks: list[list[FileEntity]] = []
        for cids in groups.values():
            if len(cids) < 2:
                continue
            files: list[FileEntity] = []
            for cid in cids:
                f = self.files.get(cid)
                if f is not None and f.deleted_at is None:
                    files.append(f)
            if len(files) < 2:
                continue
            # newest first within each stack
            files.sort(
                key=lambda x: x.mtime if x.mtime is not None else 0.0,
                reverse=True,
            )
            stacks.append(files)

        # 5. Biggest stacks first.
        stacks.sort(key=len, reverse=True)
        return stacks
