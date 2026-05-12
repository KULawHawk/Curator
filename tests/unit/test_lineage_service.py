"""tests/unit/test_lineage_service.py   (v1.7.82)

Focused unit tests for LineageService (services/lineage.py).

Covers:
  * threshold() — pure data lookup across kinds + escalate flag
  * get_edges_for() — repo passthrough
  * compute_for_pair() — detector invocation, threshold filtering, persistence
  * compute_for_file() — candidate selection, detector aggregation, FuzzyIndex path
  * find_version_stacks() — union-find with chains, disjoint groups, filters,
    deleted-file dropping, single-file singleton dropping, sort orders

Stubs replace pluggy.PluginManager, FileRepository, LineageRepository, and
FuzzyIndex. Real FileEntity / LineageEdge / LineageKind models are used.

The intent is to cover the algorithmic logic of LineageService directly,
not to exercise the real plugin ecosystem (which is covered by integration
tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from uuid import UUID, uuid4

import pytest

from curator.models.file import FileEntity
from curator.models.lineage import LineageEdge, LineageKind
from curator.services.lineage import LineageService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 12, 12, 0, 0)


def make_file(
    *,
    curator_id: UUID | None = None,
    source_id: str = "local",
    source_path: str = "/data/file.bin",
    size: int = 1024,
    xxhash: str | None = None,
    fuzzy_hash: str | None = None,
    mtime: datetime | None = None,
    deleted_at: datetime | None = None,
) -> FileEntity:
    """Construct a real FileEntity with sensible defaults."""
    return FileEntity(
        curator_id=curator_id or uuid4(),
        source_id=source_id,
        source_path=source_path,
        size=size,
        mtime=mtime or NOW,
        xxhash3_128=xxhash,
        fuzzy_hash=fuzzy_hash,
        deleted_at=deleted_at,
    )


def make_edge(
    a: UUID,
    b: UUID,
    *,
    kind: LineageKind = LineageKind.NEAR_DUPLICATE,
    confidence: float = 0.95,
    detected_by: str = "test-detector",
) -> LineageEdge:
    return LineageEdge(
        from_curator_id=a,
        to_curator_id=b,
        edge_kind=kind,
        confidence=confidence,
        detected_by=detected_by,
    )


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubFileRepository:
    """Minimal FileRepository for LineageService tests.

    Per Lesson #70: stubs should match real-API behavior, not test convenience.
    The real FileRepository's `find_by_hash`, `find_candidates_by_size`, and
    `find_with_fuzzy_hash` filter out deleted files at the SQL level
    (`WHERE deleted_at IS NULL`). These stubs replicate that filter.
    """

    def __init__(self, files: list[FileEntity] | None = None):
        self._files: dict[UUID, FileEntity] = {f.curator_id: f for f in (files or [])}
        # Per-method overrides used to control what `query` returns:
        self._query_results: list[FileEntity] = []

    # Insertion helpers used by tests
    def add(self, f: FileEntity) -> None:
        self._files[f.curator_id] = f

    def set_query_results(self, files: list[FileEntity]) -> None:
        self._query_results = files

    # Methods called by LineageService._find_candidates.
    # All non-`get` finder methods filter deleted files to match the real
    # repo's SQL-level `deleted_at IS NULL` predicate.
    def find_by_hash(self, xxhash: str) -> list[FileEntity]:
        return [
            f for f in self._files.values()
            if f.xxhash3_128 == xxhash and f.deleted_at is None
        ]

    def find_candidates_by_size(
        self, size: int, *, exclude_curator_id: UUID | None = None
    ) -> list[FileEntity]:
        return [
            f for f in self._files.values()
            if f.size == size
            and f.curator_id != exclude_curator_id
            and f.deleted_at is None
        ]

    def find_with_fuzzy_hash(self) -> list[FileEntity]:
        return [
            f for f in self._files.values()
            if f.fuzzy_hash and f.deleted_at is None
        ]

    def query(self, q) -> list[FileEntity]:  # noqa: ARG002
        # Real repo applies the FileQuery's `deleted=False` filter; the stub
        # returns whatever was injected via set_query_results() (tests are
        # responsible for not passing deleted files when simulating a
        # deleted=False query).
        return self._query_results

    def get(self, curator_id: UUID) -> FileEntity | None:
        # `get` returns deleted files too — the service checks `deleted_at`
        # explicitly when needed.
        return self._files.get(curator_id)


class StubLineageRepository:
    """Minimal LineageRepository for LineageService tests."""

    def __init__(self):
        self.inserted: list[tuple[LineageEdge, str]] = []
        self._edges_for: dict[UUID, list[LineageEdge]] = {}
        self._by_kind: dict[tuple[LineageKind, float], list[LineageEdge]] = {}

    def insert(self, edge: LineageEdge, on_conflict: str = "raise") -> None:
        self.inserted.append((edge, on_conflict))

    def get_edges_for(self, curator_id: UUID) -> list[LineageEdge]:
        return self._edges_for.get(curator_id, [])

    def set_edges_for(self, curator_id: UUID, edges: list[LineageEdge]) -> None:
        self._edges_for[curator_id] = edges

    def list_by_kind(
        self, kind: LineageKind, *, min_confidence: float = 0.0
    ) -> list[LineageEdge]:
        return self._by_kind.get((kind, min_confidence), [])

    def set_list_by_kind(
        self, kind: LineageKind, min_confidence: float, edges: list[LineageEdge]
    ) -> None:
        self._by_kind[(kind, min_confidence)] = edges


@dataclass
class StubHookCaller:
    """Mimics pluggy's hook-caller: a callable returning a list of results."""

    impl: Callable[..., list[LineageEdge | None]] = field(
        default_factory=lambda: lambda **_: []
    )

    def __call__(self, **kwargs) -> list[LineageEdge | None]:
        return self.impl(**kwargs)


@dataclass
class StubHooks:
    """The `.hook` namespace on pluggy.PluginManager."""

    curator_compute_lineage: StubHookCaller = field(default_factory=StubHookCaller)


@dataclass
class StubPluginManager:
    """Replacement for pluggy.PluginManager: only the .hook attribute is used."""

    hook: StubHooks = field(default_factory=StubHooks)

    def set_detector(self, fn: Callable[..., list[LineageEdge | None]]) -> None:
        self.hook.curator_compute_lineage = StubHookCaller(impl=fn)


class StubFuzzyIndex:
    """Minimal FuzzyIndex stub. The service uses len(), query(), and add()."""

    def __init__(self, members: dict[UUID, str] | None = None):
        # curator_id -> fuzzy_hash mapping
        self._members: dict[UUID, str] = dict(members or {})
        # Override what query() returns for any input hash:
        self._query_result: list[UUID] | None = None
        self._query_raises: Exception | None = None
        self.add_calls: list[tuple[UUID, str]] = []

    def __len__(self) -> int:
        return len(self._members)

    def query(self, fuzzy_hash: str) -> list[UUID]:
        if self._query_raises is not None:
            raise self._query_raises
        if self._query_result is not None:
            return self._query_result
        # Default: return everything (mimics "all in same bucket")
        return list(self._members.keys())

    def add(self, curator_id: UUID, fuzzy_hash: str) -> None:
        self.add_calls.append((curator_id, fuzzy_hash))
        self._members[curator_id] = fuzzy_hash

    def set_query_result(self, ids: list[UUID]) -> None:
        self._query_result = ids

    def set_query_raises(self, exc: Exception) -> None:
        self._query_raises = exc


# ===========================================================================
# threshold()
# ===========================================================================


class TestThreshold:
    def setup_method(self):
        self.svc = LineageService(
            plugin_manager=StubPluginManager(),
            file_repo=StubFileRepository(),
            lineage_repo=StubLineageRepository(),
        )

    def test_returns_auto_confirm_for_duplicate(self):
        assert self.svc.threshold(LineageKind.DUPLICATE) == 1.0

    def test_returns_auto_confirm_for_near_duplicate(self):
        assert self.svc.threshold(LineageKind.NEAR_DUPLICATE) == 0.95

    def test_returns_auto_confirm_for_version_of(self):
        assert self.svc.threshold(LineageKind.VERSION_OF) == 0.85

    def test_escalate_returns_lower_threshold_when_available(self):
        assert self.svc.threshold(LineageKind.NEAR_DUPLICATE, escalate=True) == 0.70
        assert self.svc.threshold(LineageKind.VERSION_OF, escalate=True) == 0.60

    def test_escalate_falls_back_to_auto_confirm_when_no_escalate_tier(self):
        # DUPLICATE has no escalate threshold; should fall through to 1.0
        assert self.svc.threshold(LineageKind.DUPLICATE, escalate=True) == 1.0
        assert self.svc.threshold(LineageKind.REFERENCED_BY, escalate=True) == 1.0


# ===========================================================================
# get_edges_for()
# ===========================================================================


class TestGetEdgesFor:
    def test_passthrough_to_repo(self):
        repo = StubLineageRepository()
        cid = uuid4()
        edges = [make_edge(cid, uuid4())]
        repo.set_edges_for(cid, edges)

        svc = LineageService(
            plugin_manager=StubPluginManager(),
            file_repo=StubFileRepository(),
            lineage_repo=repo,
        )
        assert svc.get_edges_for(cid) == edges

    def test_returns_empty_for_unknown_id(self):
        svc = LineageService(
            plugin_manager=StubPluginManager(),
            file_repo=StubFileRepository(),
            lineage_repo=StubLineageRepository(),
        )
        assert svc.get_edges_for(uuid4()) == []


# ===========================================================================
# compute_for_pair()
# ===========================================================================


class TestComputeForPair:
    def _make_svc(self, detector_result):
        pm = StubPluginManager()
        pm.set_detector(lambda **_: detector_result)
        repo = StubLineageRepository()
        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository(),
            lineage_repo=repo,
        )
        return svc, repo

    def test_no_edges_from_detector_returns_empty(self):
        svc, repo = self._make_svc([])
        result = svc.compute_for_pair(make_file(), make_file())
        assert result == []
        assert repo.inserted == []

    def test_filters_out_none_results(self):
        # Detectors that don't fire return None; service must skip them.
        svc, repo = self._make_svc([None, None])
        result = svc.compute_for_pair(make_file(), make_file())
        assert result == []
        assert repo.inserted == []

    def test_persists_edges_above_threshold(self):
        a, b = make_file(), make_file()
        edge = make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.NEAR_DUPLICATE,
            confidence=0.96,  # above 0.95 threshold
        )
        svc, repo = self._make_svc([edge])
        result = svc.compute_for_pair(a, b)
        assert result == [edge]
        assert len(repo.inserted) == 1
        assert repo.inserted[0][0] == edge
        assert repo.inserted[0][1] == "ignore"

    def test_drops_edges_below_threshold(self):
        a, b = make_file(), make_file()
        edge = make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.NEAR_DUPLICATE,
            confidence=0.80,  # below 0.95 threshold
        )
        svc, repo = self._make_svc([edge])
        result = svc.compute_for_pair(a, b)
        assert result == []
        assert repo.inserted == []

    def test_persist_false_skips_inserts(self):
        a, b = make_file(), make_file()
        edge = make_edge(a.curator_id, b.curator_id, confidence=1.0)
        svc, repo = self._make_svc([edge])
        result = svc.compute_for_pair(a, b, persist=False)
        assert result == [edge]  # still returned
        assert repo.inserted == []  # but not persisted

    def test_mixed_confidence_edges_split_correctly(self):
        a, b = make_file(), make_file()
        e_high = make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.DUPLICATE, confidence=1.0,
        )
        e_low = make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.NEAR_DUPLICATE, confidence=0.80,
        )
        svc, repo = self._make_svc([e_high, e_low])
        result = svc.compute_for_pair(a, b)
        assert result == [e_high]
        assert len(repo.inserted) == 1


# ===========================================================================
# compute_for_file()
# ===========================================================================


class TestComputeForFile:
    def test_no_candidates_returns_empty(self):
        # File with no hash, no fuzzy_hash, no same-size siblings, no parent
        f = make_file(source_path="lonely.bin")
        pm = StubPluginManager()
        # The detector should never even be called since no candidates exist.
        called = [False]
        def detector(**_):
            called[0] = True
            return []
        pm.set_detector(detector)

        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([f]),  # only the file itself
            lineage_repo=StubLineageRepository(),
        )
        result = svc.compute_for_file(f)
        assert result == []
        assert called[0] is False

    def test_finds_candidates_via_xxhash(self):
        a = make_file(xxhash="abc123", source_path="/data/a.bin")
        b = make_file(xxhash="abc123", source_path="/data/b.bin")

        pm = StubPluginManager()
        # Detector emits a high-confidence DUPLICATE
        def detector(file_a, file_b):
            return [make_edge(
                file_a.curator_id, file_b.curator_id,
                kind=LineageKind.DUPLICATE, confidence=1.0,
            )]
        pm.set_detector(detector)

        repo = StubLineageRepository()
        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a, b]),
            lineage_repo=repo,
        )
        result = svc.compute_for_file(a)
        assert len(result) == 1
        assert len(repo.inserted) == 1

    def test_finds_candidates_via_size(self):
        a = make_file(size=2048, source_path="/data/a.bin")
        b = make_file(size=2048, source_path="/data/b.bin")
        pm = StubPluginManager()
        pm.set_detector(lambda **_: [make_edge(
            a.curator_id, b.curator_id, confidence=0.96,
        )])
        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a, b]),
            lineage_repo=StubLineageRepository(),
        )
        result = svc.compute_for_file(a)
        # Detector returns the same edge twice (once per duplicate candidate slot),
        # but we just care that at least one came through:
        assert len(result) >= 1

    def test_persist_false_skips_inserts(self):
        a = make_file(xxhash="hash1", source_path="/x.bin")
        b = make_file(xxhash="hash1", source_path="/y.bin")
        pm = StubPluginManager()
        pm.set_detector(lambda **_: [make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.DUPLICATE, confidence=1.0,
        )])
        repo = StubLineageRepository()
        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a, b]),
            lineage_repo=repo,
        )
        svc.compute_for_file(a, persist=False)
        assert repo.inserted == []

    def test_finds_candidates_via_parent_directory(self):
        # Two files in the same directory; no xxhash, no fuzzy_hash, different sizes.
        # Only the parent-dir path should surface b as a candidate.
        a = make_file(
            source_path="/data/proj/draft_v1.docx",
            size=1000,
        )
        b = make_file(
            source_path="/data/proj/draft_v2.docx",
            size=2000,
        )
        pm = StubPluginManager()
        # Detector emits VERSION_OF when invoked
        pm.set_detector(lambda **_: [make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.VERSION_OF, confidence=0.90,
        )])

        file_repo = StubFileRepository([a, b])
        # The parent-dir query() call returns both files
        file_repo.set_query_results([a, b])

        svc = LineageService(
            plugin_manager=pm,
            file_repo=file_repo,
            lineage_repo=StubLineageRepository(),
        )
        result = svc.compute_for_file(a)
        assert len(result) == 1
        assert result[0].edge_kind == LineageKind.VERSION_OF

    def test_fuzzy_index_path_used_when_populated(self):
        # When fuzzy_index has members, it should drive candidate selection
        a = make_file(fuzzy_hash="3:abcde:fgh", source_path="/a.txt")
        b = make_file(fuzzy_hash="3:abcde:fgi", source_path="/b.txt")

        fuzzy = StubFuzzyIndex({b.curator_id: b.fuzzy_hash})
        pm = StubPluginManager()
        pm.set_detector(lambda **_: [make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.NEAR_DUPLICATE, confidence=0.96,
        )])

        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a, b]),
            lineage_repo=StubLineageRepository(),
            fuzzy_index=fuzzy,
        )
        result = svc.compute_for_file(a)
        assert len(result) == 1
        # Self-maintenance: a should have been added to the fuzzy index after detect.
        assert any(cid == a.curator_id for cid, _ in fuzzy.add_calls)

    def test_fuzzy_index_query_raises_falls_back_to_scan(self):
        # If FuzzyIndex.query() raises ValueError, service falls back to O(n).
        a = make_file(fuzzy_hash="3:abcde:fgh", source_path="/a.txt")
        b = make_file(fuzzy_hash="3:abcde:fgi", source_path="/b.txt")

        fuzzy = StubFuzzyIndex({b.curator_id: b.fuzzy_hash})
        fuzzy.set_query_raises(ValueError("malformed hash"))

        pm = StubPluginManager()
        pm.set_detector(lambda **_: [make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.NEAR_DUPLICATE, confidence=0.96,
        )])

        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a, b]),
            lineage_repo=StubLineageRepository(),
            fuzzy_index=fuzzy,
        )
        # Should not raise; fallback to find_with_fuzzy_hash() path emits the edge.
        result = svc.compute_for_file(a)
        assert len(result) == 1

    def test_fuzzy_index_empty_uses_legacy_scan(self):
        # When fuzzy_index is set but empty (len==0), the legacy O(n) scan runs.
        a = make_file(fuzzy_hash="3:abcde:fgh", source_path="/a.txt")
        b = make_file(fuzzy_hash="3:abcde:fgi", source_path="/b.txt")

        fuzzy = StubFuzzyIndex({})  # empty

        pm = StubPluginManager()
        pm.set_detector(lambda **_: [make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.NEAR_DUPLICATE, confidence=0.96,
        )])

        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a, b]),
            lineage_repo=StubLineageRepository(),
            fuzzy_index=fuzzy,
        )
        result = svc.compute_for_file(a)
        assert len(result) >= 1
        # Self-maintenance still happens even when scan was used
        assert any(cid == a.curator_id for cid, _ in fuzzy.add_calls)

    def test_fuzzy_index_skips_already_seen_candidates(self):
        # FuzzyIndex returns a candidate that's already in the candidates dict
        # via another path (xxhash). The service should de-dup, not double-count.
        a = make_file(xxhash="h1", fuzzy_hash="3:abc:def", source_path="/a.txt")
        b = make_file(xxhash="h1", fuzzy_hash="3:abc:dxx", source_path="/b.txt")

        fuzzy = StubFuzzyIndex({b.curator_id: b.fuzzy_hash})
        # FuzzyIndex.query() returns b (which is also a hash-bucket match)

        # Detector counts how many times it's been called per pair
        call_count = {"n": 0}
        def detector(file_a, file_b):
            call_count["n"] += 1
            return [make_edge(
                file_a.curator_id, file_b.curator_id,
                kind=LineageKind.DUPLICATE, confidence=1.0,
            )]
        pm = StubPluginManager()
        pm.set_detector(detector)

        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a, b]),
            lineage_repo=StubLineageRepository(),
            fuzzy_index=fuzzy,
        )
        svc.compute_for_file(a)
        # b should only have been compared once, not twice
        assert call_count["n"] == 1

    def test_fuzzy_index_skips_own_curator_id(self):
        # If FuzzyIndex returns the input file's own id, the service must
        # filter it out (otherwise we'd compare a file against itself).
        a = make_file(fuzzy_hash="3:abcde:fgh", source_path="/a.txt")

        # FuzzyIndex contains `a` itself and returns it on query
        fuzzy = StubFuzzyIndex({a.curator_id: a.fuzzy_hash})
        fuzzy.set_query_result([a.curator_id])

        call_count = {"n": 0}
        def detector(file_a, file_b):
            call_count["n"] += 1
            return []
        pm = StubPluginManager()
        pm.set_detector(detector)

        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a]),
            lineage_repo=StubLineageRepository(),
            fuzzy_index=fuzzy,
        )
        result = svc.compute_for_file(a)
        assert result == []
        # Detector must not have been called against self
        assert call_count["n"] == 0

    def test_fuzzy_index_is_sole_discovery_path(self):
        # Engineer a scenario where FuzzyIndex is the ONLY way `b` is found:
        # different xxhash, different size, different parent directory.
        # This exercises the fresh-fetch arm (`f = self.files.get(cid)`
        # followed by `candidates[cid] = f`).
        a = make_file(
            xxhash="hash_a",
            fuzzy_hash="3:abcde:fgh",
            size=100,
            source_path="/dirA/a.txt",
        )
        b = make_file(
            xxhash="hash_b",       # different hash
            fuzzy_hash="3:abcde:fgi",
            size=999,              # different size
            source_path="/dirB/b.txt",  # different parent dir
        )

        # FuzzyIndex returns b
        fuzzy = StubFuzzyIndex({b.curator_id: b.fuzzy_hash})
        fuzzy.set_query_result([b.curator_id])

        pm = StubPluginManager()
        pm.set_detector(lambda **_: [make_edge(
            a.curator_id, b.curator_id,
            kind=LineageKind.NEAR_DUPLICATE, confidence=0.96,
        )])

        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a, b]),
            lineage_repo=StubLineageRepository(),
            fuzzy_index=fuzzy,
        )
        result = svc.compute_for_file(a)
        # b was found via FuzzyIndex only and the edge was emitted
        assert len(result) == 1
        assert result[0].to_curator_id == b.curator_id

    def test_parent_dir_query_skips_nested_files(self):
        # The parent-dir query may return files in nested subdirectories;
        # the service must filter to direct children only (same parent).
        # Make sizes unique so the size path doesn't surface b or c
        # (we want the parent-dir path to be the only discovery channel).
        a = make_file(source_path="/data/proj/a.txt", size=100)
        # b is a direct sibling (same parent /data/proj) -> kept
        b = make_file(source_path="/data/proj/b.txt", size=200)
        # c is nested (parent /data/proj/sub) -> dropped
        c = make_file(source_path="/data/proj/sub/c.txt", size=300)

        pm = StubPluginManager()
        call_pairs = []
        def detector(file_a, file_b):
            call_pairs.append((file_a.curator_id, file_b.curator_id))
            return []
        pm.set_detector(detector)

        file_repo = StubFileRepository([a, b, c])
        # query() returns BOTH b and c (the prefix matches both); the
        # parent-equality check inside the service filters c out.
        file_repo.set_query_results([a, b, c])

        svc = LineageService(
            plugin_manager=pm,
            file_repo=file_repo,
            lineage_repo=StubLineageRepository(),
        )
        svc.compute_for_file(a)
        # Only b should have been considered a candidate; c filtered out.
        candidate_ids = {pair[1] for pair in call_pairs}
        assert b.curator_id in candidate_ids
        assert c.curator_id not in candidate_ids

    def test_fuzzy_index_returns_missing_file_skipped(self):
        # If FuzzyIndex returns a cid that the file repo can't resolve
        # (stale entry: file was deleted from index but still in LSH),
        # the service must skip it gracefully, not crash.
        a = make_file(fuzzy_hash="3:abc:def", source_path="/a.txt")
        stale_cid = uuid4()  # not in file_repo

        fuzzy = StubFuzzyIndex({stale_cid: "3:abc:dgh"})
        fuzzy.set_query_result([stale_cid])

        pm = StubPluginManager()
        call_count = {"n": 0}
        def detector(**_):
            call_count["n"] += 1
            return []
        pm.set_detector(detector)

        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a]),  # stale_cid not present
            lineage_repo=StubLineageRepository(),
            fuzzy_index=fuzzy,
        )
        result = svc.compute_for_file(a)
        # Stale cid was skipped; no detector invocation, no result
        assert result == []
        assert call_count["n"] == 0

    def test_fuzzy_index_returns_deleted_file_skipped(self):
        # If FuzzyIndex returns a cid that points to a deleted file
        # (deleted_at is not None), the service must skip it.
        a = make_file(fuzzy_hash="3:abc:def", source_path="/a.txt")
        b_dead = make_file(
            fuzzy_hash="3:abc:dgh",
            source_path="/b.txt",
            deleted_at=NOW,
        )

        fuzzy = StubFuzzyIndex({b_dead.curator_id: b_dead.fuzzy_hash})
        fuzzy.set_query_result([b_dead.curator_id])

        pm = StubPluginManager()
        call_count = {"n": 0}
        def detector(**_):
            call_count["n"] += 1
            return []
        pm.set_detector(detector)

        svc = LineageService(
            plugin_manager=pm,
            file_repo=StubFileRepository([a, b_dead]),
            lineage_repo=StubLineageRepository(),
            fuzzy_index=fuzzy,
        )
        result = svc.compute_for_file(a)
        # Deleted file skipped
        assert result == []
        assert call_count["n"] == 0


# ===========================================================================
# find_version_stacks() — union-find logic
# ===========================================================================


class TestFindVersionStacks:
    def _make_svc(self, files, edges_by_kind):
        repo = StubLineageRepository()
        file_repo = StubFileRepository(files)
        for kind, conf_threshold, edges in edges_by_kind:
            repo.set_list_by_kind(kind, conf_threshold, edges)
        return LineageService(
            plugin_manager=StubPluginManager(),
            file_repo=file_repo,
            lineage_repo=repo,
        )

    def test_no_edges_returns_empty(self):
        svc = self._make_svc([], [])
        assert svc.find_version_stacks() == []

    def test_single_edge_forms_stack_of_two(self):
        a = make_file(source_path="/a.txt")
        b = make_file(source_path="/b.txt")
        edges = [make_edge(a.curator_id, b.curator_id, confidence=0.9)]
        svc = self._make_svc(
            [a, b],
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, edges),
                (LineageKind.VERSION_OF, 0.7, []),
            ],
        )
        stacks = svc.find_version_stacks()
        assert len(stacks) == 1
        assert len(stacks[0]) == 2
        assert {f.curator_id for f in stacks[0]} == {a.curator_id, b.curator_id}

    def test_transitive_chain_forms_single_stack(self):
        # A--B, B--C  =>  {A, B, C}
        a = make_file(source_path="/a.txt")
        b = make_file(source_path="/b.txt")
        c = make_file(source_path="/c.txt")
        edges = [
            make_edge(a.curator_id, b.curator_id, confidence=0.9),
            make_edge(b.curator_id, c.curator_id, confidence=0.9),
        ]
        svc = self._make_svc(
            [a, b, c],
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, edges),
                (LineageKind.VERSION_OF, 0.7, []),
            ],
        )
        stacks = svc.find_version_stacks()
        assert len(stacks) == 1
        assert {f.curator_id for f in stacks[0]} == {
            a.curator_id, b.curator_id, c.curator_id,
        }

    def test_disjoint_stacks(self):
        # Two unrelated pairs: {A,B} and {C,D}
        a = make_file(source_path="/a.txt")
        b = make_file(source_path="/b.txt")
        c = make_file(source_path="/c.txt")
        d = make_file(source_path="/d.txt")
        edges = [
            make_edge(a.curator_id, b.curator_id, confidence=0.9),
            make_edge(c.curator_id, d.curator_id, confidence=0.9),
        ]
        svc = self._make_svc(
            [a, b, c, d],
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, edges),
                (LineageKind.VERSION_OF, 0.7, []),
            ],
        )
        stacks = svc.find_version_stacks()
        assert len(stacks) == 2

    def test_biggest_stack_first(self):
        # Stack 1: {a,b,c}; Stack 2: {d,e}
        files = [make_file(source_path=f"/f{i}.txt") for i in range(5)]
        a, b, c, d, e = files
        edges = [
            make_edge(a.curator_id, b.curator_id, confidence=0.9),
            make_edge(b.curator_id, c.curator_id, confidence=0.9),
            make_edge(d.curator_id, e.curator_id, confidence=0.9),
        ]
        svc = self._make_svc(
            files,
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, edges),
                (LineageKind.VERSION_OF, 0.7, []),
            ],
        )
        stacks = svc.find_version_stacks()
        assert len(stacks) == 2
        assert len(stacks[0]) == 3  # biggest first
        assert len(stacks[1]) == 2

    def test_drops_deleted_files(self):
        a = make_file(source_path="/a.txt")
        b = make_file(source_path="/b.txt", deleted_at=NOW)  # tombstoned
        edges = [make_edge(a.curator_id, b.curator_id, confidence=0.9)]
        svc = self._make_svc(
            [a, b],
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, edges),
                (LineageKind.VERSION_OF, 0.7, []),
            ],
        )
        # After dropping b, only a remains -> singleton -> stack dropped entirely
        assert svc.find_version_stacks() == []

    def test_drops_stacks_that_collapse_to_singleton(self):
        # 3 connected, 2 deleted -> 1 live -> drop the whole stack
        a = make_file(source_path="/a.txt")
        b = make_file(source_path="/b.txt", deleted_at=NOW)
        c = make_file(source_path="/c.txt", deleted_at=NOW)
        edges = [
            make_edge(a.curator_id, b.curator_id, confidence=0.9),
            make_edge(b.curator_id, c.curator_id, confidence=0.9),
        ]
        svc = self._make_svc(
            [a, b, c],
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, edges),
                (LineageKind.VERSION_OF, 0.7, []),
            ],
        )
        assert svc.find_version_stacks() == []

    def test_sorts_within_stack_by_mtime_desc(self):
        a = make_file(source_path="/a.txt", mtime=NOW - timedelta(days=10))
        b = make_file(source_path="/b.txt", mtime=NOW - timedelta(days=2))  # newest
        c = make_file(source_path="/c.txt", mtime=NOW - timedelta(days=5))
        edges = [
            make_edge(a.curator_id, b.curator_id, confidence=0.9),
            make_edge(b.curator_id, c.curator_id, confidence=0.9),
        ]
        svc = self._make_svc(
            [a, b, c],
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, edges),
                (LineageKind.VERSION_OF, 0.7, []),
            ],
        )
        stacks = svc.find_version_stacks()
        assert len(stacks) == 1
        paths = [f.source_path for f in stacks[0]]
        # Newest first within the stack
        assert paths == ["/b.txt", "/c.txt", "/a.txt"]

    def test_min_confidence_filter_passes_through_to_repo(self):
        # If we ask for min_confidence=0.95, repo must be queried with that.
        # Confirm by registering edges only at the 0.95 key.
        a = make_file(source_path="/a.txt")
        b = make_file(source_path="/b.txt")
        edges = [make_edge(a.curator_id, b.curator_id, confidence=0.97)]
        svc = self._make_svc(
            [a, b],
            [
                (LineageKind.NEAR_DUPLICATE, 0.95, edges),
                (LineageKind.VERSION_OF, 0.95, []),
            ],
        )
        stacks = svc.find_version_stacks(min_confidence=0.95)
        assert len(stacks) == 1

    def test_kinds_parameter_restricts_walk(self):
        # Only walk DUPLICATE edges; ignore NEAR_DUPLICATE
        a = make_file(source_path="/a.txt")
        b = make_file(source_path="/b.txt")
        c = make_file(source_path="/c.txt")
        near_dup_edges = [
            make_edge(a.curator_id, b.curator_id,
                      kind=LineageKind.NEAR_DUPLICATE, confidence=0.9),
        ]
        dup_edges = [
            make_edge(b.curator_id, c.curator_id,
                      kind=LineageKind.DUPLICATE, confidence=1.0),
        ]
        svc = self._make_svc(
            [a, b, c],
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, near_dup_edges),
                (LineageKind.VERSION_OF, 0.7, []),
                (LineageKind.DUPLICATE, 0.7, dup_edges),
            ],
        )
        # Ask only for DUPLICATE walks; NEAR_DUPLICATE edge between a-b ignored.
        stacks = svc.find_version_stacks(kinds=[LineageKind.DUPLICATE])
        assert len(stacks) == 1
        assert {f.curator_id for f in stacks[0]} == {b.curator_id, c.curator_id}
        # a is not part of the dup walk, so it's excluded.

    def test_triangle_edges_exercise_already_same_root_branch(self):
        # Triangle edges A-B, B-C, A-C. The third union finds A and C
        # already connected (same root via path compression). Exercises
        # the `if ra != rb:` False branch in the inner `union` function.
        a = make_file(source_path="/a.txt")
        b = make_file(source_path="/b.txt")
        c = make_file(source_path="/c.txt")
        edges = [
            make_edge(a.curator_id, b.curator_id, confidence=0.9),
            make_edge(b.curator_id, c.curator_id, confidence=0.9),
            make_edge(a.curator_id, c.curator_id, confidence=0.9),
        ]
        svc = self._make_svc(
            [a, b, c],
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, edges),
                (LineageKind.VERSION_OF, 0.7, []),
            ],
        )
        stacks = svc.find_version_stacks()
        assert len(stacks) == 1
        assert len(stacks[0]) == 3

    def test_self_loop_edge_produces_singleton_dropped_group(self):
        # An edge where from_curator_id == to_curator_id is a self-loop;
        # it puts the node in `parent` but no other connections form.
        # The resulting group has len(cids) == 1, exercises the
        # `if len(cids) < 2: continue` branch in find_version_stacks.
        a = make_file(source_path="/a.txt")
        # A self-loop edge: from and to are the same id
        edges = [
            make_edge(a.curator_id, a.curator_id, confidence=0.9),
        ]
        svc = self._make_svc(
            [a],
            [
                (LineageKind.NEAR_DUPLICATE, 0.7, edges),
                (LineageKind.VERSION_OF, 0.7, []),
            ],
        )
        stacks = svc.find_version_stacks()
        # Self-loop produces a singleton group, which the service drops.
        assert stacks == []
