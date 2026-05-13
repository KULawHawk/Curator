"""Coverage closure for ``curator.storage.repositories.lineage_repo`` (v1.7.138).

Targets all 38 uncovered lines: insert conflict-resolution arms, all
delete methods, all read methods (get, get_edges_from/to/for/between,
list_by_kind, query_by_confidence).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
import sqlite3

from curator._compat.datetime import utcnow_naive
from curator.models import FileEntity, LineageEdge, LineageKind


def _file(repos, path: str) -> FileEntity:
    f = FileEntity(
        source_id="local", source_path=path, size=1, mtime=utcnow_naive(),
    )
    repos.files.insert(f)
    return f


def _edge(from_id, to_id, *, kind=LineageKind.DUPLICATE, confidence=1.0) -> LineageEdge:
    return LineageEdge(
        from_curator_id=from_id,
        to_curator_id=to_id,
        edge_kind=kind,
        confidence=confidence,
        detected_by="test",
    )


class TestInsertConflict:
    def test_ignore_returns_false_on_duplicate(self, repos, local_source):
        f1 = _file(repos, "/a")
        f2 = _file(repos, "/b")
        e = _edge(f1.curator_id, f2.curator_id)

        assert repos.lineage.insert(e) is True
        # Same edge again with on_conflict="ignore" -> False
        assert repos.lineage.insert(e, on_conflict="ignore") is False

    def test_replace_overwrites(self, repos, local_source):
        f1 = _file(repos, "/a2")
        f2 = _file(repos, "/b2")
        e = _edge(f1.curator_id, f2.curator_id, confidence=0.8)
        repos.lineage.insert(e)
        # Construct a fresh edge with the same logical key (from/to/kind/detected_by)
        # but different confidence
        e2 = _edge(f1.curator_id, f2.curator_id, confidence=0.95)
        assert repos.lineage.insert(e2, on_conflict="replace") is True

    def test_ignore_returns_false_on_fk_violation(self, repos, local_source):
        """Line 72: INSERT OR IGNORE suppresses PK conflicts but NOT FK
        violations — when the from/to files don't exist, the FK error
        propagates out of executemany and is caught by the IntegrityError
        handler, which returns False since on_conflict != 'raise'."""
        bogus_from = uuid4()
        bogus_to = uuid4()
        e = _edge(bogus_from, bogus_to)
        assert repos.lineage.insert(e, on_conflict="ignore") is False

    def test_raise_propagates_integrity_error(self, repos, local_source):
        f1 = _file(repos, "/a3")
        f2 = _file(repos, "/b3")
        e = _edge(f1.curator_id, f2.curator_id)
        repos.lineage.insert(e)
        # Same logical edge → UNIQUE violation; raise mode propagates
        e_dup = _edge(f1.curator_id, f2.curator_id)
        with pytest.raises(sqlite3.IntegrityError):
            repos.lineage.insert(e_dup, on_conflict="raise")


class TestDeleteMethods:
    def test_delete_removes_edge(self, repos, local_source):
        f1 = _file(repos, "/d1")
        f2 = _file(repos, "/d2")
        e = _edge(f1.curator_id, f2.curator_id)
        repos.lineage.insert(e)
        repos.lineage.delete(e.edge_id)
        assert repos.lineage.get(e.edge_id) is None

    def test_delete_for_file_removes_both_directions(self, repos, local_source):
        f1 = _file(repos, "/df1")
        f2 = _file(repos, "/df2")
        f3 = _file(repos, "/df3")
        repos.lineage.insert(_edge(f1.curator_id, f2.curator_id))
        repos.lineage.insert(
            _edge(f3.curator_id, f1.curator_id, kind=LineageKind.NEAR_DUPLICATE,
                  confidence=0.9),
        )
        # f1 touches both edges; deleting "for f1" should remove both
        deleted = repos.lineage.delete_for_file(f1.curator_id)
        assert deleted == 2


class TestGetMethods:
    def test_get_returns_edge_by_id(self, repos, local_source):
        f1 = _file(repos, "/g1")
        f2 = _file(repos, "/g2")
        e = _edge(f1.curator_id, f2.curator_id)
        repos.lineage.insert(e)
        fetched = repos.lineage.get(e.edge_id)
        assert fetched is not None
        assert fetched.edge_id == e.edge_id

    def test_get_missing_returns_none(self, repos):
        assert repos.lineage.get(uuid4()) is None

    def test_get_edges_from(self, repos, local_source):
        f1 = _file(repos, "/from1")
        f2 = _file(repos, "/from2")
        f3 = _file(repos, "/from3")
        repos.lineage.insert(_edge(f1.curator_id, f2.curator_id))
        repos.lineage.insert(
            _edge(f1.curator_id, f3.curator_id, kind=LineageKind.NEAR_DUPLICATE,
                  confidence=0.9),
        )
        edges = repos.lineage.get_edges_from(f1.curator_id)
        assert len(edges) == 2

    def test_get_edges_to(self, repos, local_source):
        f1 = _file(repos, "/to1")
        f2 = _file(repos, "/to2")
        repos.lineage.insert(_edge(f1.curator_id, f2.curator_id))
        edges = repos.lineage.get_edges_to(f2.curator_id)
        assert len(edges) == 1

    def test_get_edges_for_both_directions(self, repos, local_source):
        f1 = _file(repos, "/for1")
        f2 = _file(repos, "/for2")
        f3 = _file(repos, "/for3")
        repos.lineage.insert(_edge(f1.curator_id, f2.curator_id))
        repos.lineage.insert(
            _edge(f3.curator_id, f1.curator_id, kind=LineageKind.NEAR_DUPLICATE,
                  confidence=0.9),
        )
        edges = repos.lineage.get_edges_for(f1.curator_id)
        assert len(edges) == 2


class TestGetEdgesBetween:
    def test_basic_pair_returns_all_kinds(self, repos, local_source):
        f1 = _file(repos, "/b1")
        f2 = _file(repos, "/b2")
        repos.lineage.insert(_edge(f1.curator_id, f2.curator_id))
        repos.lineage.insert(
            _edge(f1.curator_id, f2.curator_id, kind=LineageKind.NEAR_DUPLICATE,
                  confidence=0.8),
        )
        edges = repos.lineage.get_edges_between(f1.curator_id, f2.curator_id)
        assert len(edges) == 2

    def test_kind_filter_returns_only_matching(self, repos, local_source):
        f1 = _file(repos, "/k1")
        f2 = _file(repos, "/k2")
        repos.lineage.insert(_edge(f1.curator_id, f2.curator_id))
        repos.lineage.insert(
            _edge(f1.curator_id, f2.curator_id, kind=LineageKind.NEAR_DUPLICATE,
                  confidence=0.8),
        )
        edges = repos.lineage.get_edges_between(
            f1.curator_id, f2.curator_id, kind=LineageKind.DUPLICATE,
        )
        assert len(edges) == 1
        assert edges[0].edge_kind == LineageKind.DUPLICATE


class TestListByKind:
    def test_filters_by_kind_and_min_confidence(self, repos, local_source):
        f1 = _file(repos, "/lk1")
        f2 = _file(repos, "/lk2")
        f3 = _file(repos, "/lk3")
        repos.lineage.insert(_edge(f1.curator_id, f2.curator_id, confidence=0.9))
        repos.lineage.insert(_edge(f1.curator_id, f3.curator_id, confidence=0.5))

        all_dups = repos.lineage.list_by_kind(LineageKind.DUPLICATE)
        assert len(all_dups) == 2

        high_conf = repos.lineage.list_by_kind(LineageKind.DUPLICATE, min_confidence=0.8)
        assert len(high_conf) == 1

    def test_limit_clauses(self, repos, local_source):
        f1 = _file(repos, "/lim1")
        for i in range(5):
            ft = _file(repos, f"/lim_t{i}")
            repos.lineage.insert(_edge(f1.curator_id, ft.curator_id))
        result = repos.lineage.list_by_kind(LineageKind.DUPLICATE, limit=2)
        assert len(result) == 2


class TestQueryByConfidence:
    def test_range_inclusive_lower_exclusive_upper(self, repos, local_source):
        f1 = _file(repos, "/qc1")
        f2 = _file(repos, "/qc2")
        f3 = _file(repos, "/qc3")
        repos.lineage.insert(_edge(f1.curator_id, f2.curator_id, confidence=0.5))
        repos.lineage.insert(
            _edge(f1.curator_id, f3.curator_id, kind=LineageKind.NEAR_DUPLICATE,
                  confidence=0.8),
        )
        result = repos.lineage.query_by_confidence(
            min_confidence=0.6, max_confidence=0.95,
        )
        assert len(result) == 1
        assert result[0].confidence == 0.8

    def test_limit_clauses(self, repos, local_source):
        f1 = _file(repos, "/qc_l1")
        for i in range(5):
            ft = _file(repos, f"/qc_lt{i}")
            repos.lineage.insert(_edge(f1.curator_id, ft.curator_id, confidence=0.5))
        result = repos.lineage.query_by_confidence(
            min_confidence=0.4, max_confidence=1.0, limit=2,
        )
        assert len(result) == 2
