"""Coverage for ``curator.gui.lineage_view`` Part 1 (v1.7.186).

Round 4 Tier 2 sub-ship 2 of 6 — pure-Python builder + dataclasses +
helpers + edge-kind color mapping. The Qt view layer
(``_make_lineage_graph_view`` + ``LineageGraphView``) is exercised in
Part 2 (v1.7.187) via qtbot.

The builder is intentionally Qt-free (see module docstring) so Part 1
needs no qapp fixture. All collaborator dependencies (file_repo,
lineage_repo) are stubbed via ``MagicMock``.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

# Set offscreen before any potential Qt import inside the module
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------------------
# GraphNode / GraphEdge / GraphLayout dataclasses
# ---------------------------------------------------------------------------


class TestGraphDataclasses:
    def test_graph_node_construction_defaults(self):
        from curator.gui.lineage_view import GraphNode
        cid = uuid4()
        n = GraphNode(curator_id=cid, label="abc", full_path="/x/abc")
        assert n.curator_id == cid
        assert n.label == "abc"
        assert n.full_path == "/x/abc"
        assert n.x == 0.0
        assert n.y == 0.0

    def test_graph_node_explicit_coords(self):
        from curator.gui.lineage_view import GraphNode
        n = GraphNode(curator_id=uuid4(), label="L", full_path="/p", x=0.3, y=0.7)
        assert n.x == 0.3
        assert n.y == 0.7

    def test_graph_edge_construction(self):
        from curator.gui.lineage_view import GraphEdge
        a, b = uuid4(), uuid4()
        e = GraphEdge(from_id=a, to_id=b, edge_kind="duplicate", confidence=0.95)
        assert e.from_id == a
        assert e.to_id == b
        assert e.edge_kind == "duplicate"
        assert e.confidence == 0.95
        assert e.detected_by == ""
        assert e.detected_at is None

    def test_graph_edge_full_args(self):
        from curator.gui.lineage_view import GraphEdge
        when = datetime(2026, 1, 1, 12, 0)
        e = GraphEdge(
            from_id=uuid4(), to_id=uuid4(),
            edge_kind="version_of", confidence=0.88,
            detected_by="byhash", detected_at=when,
        )
        assert e.detected_by == "byhash"
        assert e.detected_at == when

    def test_graph_layout_empty_property(self):
        from curator.gui.lineage_view import GraphLayout, GraphNode, GraphEdge
        empty = GraphLayout()
        assert empty.is_empty is True

        with_nodes = GraphLayout(nodes=[
            GraphNode(curator_id=uuid4(), label="x", full_path="/x"),
        ])
        assert with_nodes.is_empty is False

        with_edges = GraphLayout(edges=[
            GraphEdge(from_id=uuid4(), to_id=uuid4(),
                      edge_kind="duplicate", confidence=1.0),
        ])
        assert with_edges.is_empty is False


# ---------------------------------------------------------------------------
# color_for_edge_kind + EDGE_KIND_COLORS
# ---------------------------------------------------------------------------


class TestColorMapping:
    @pytest.mark.parametrize("kind,expected_color", [
        ("duplicate", "#d33682"),
        ("near_duplicate", "#cb4b16"),
        ("version_of", "#268bd2"),
        ("derived_from", "#859900"),
        ("renamed_from", "#b58900"),
    ])
    def test_known_edge_kinds_get_their_color(self, kind, expected_color):
        from curator.gui.lineage_view import color_for_edge_kind
        assert color_for_edge_kind(kind) == expected_color

    def test_unknown_kind_returns_default(self):
        from curator.gui.lineage_view import (
            color_for_edge_kind, EDGE_KIND_DEFAULT_COLOR,
        )
        assert color_for_edge_kind("totally_bogus") == EDGE_KIND_DEFAULT_COLOR


# ---------------------------------------------------------------------------
# LineageGraphBuilder static / pure helpers
# ---------------------------------------------------------------------------


class TestBuilderStaticHelpers:
    def test_basename_forward_slash(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        assert LineageGraphBuilder._basename("/a/b/c.txt") == "c.txt"

    def test_basename_backslash(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        assert LineageGraphBuilder._basename(r"C:\Users\jake\file.pdf") == "file.pdf"

    def test_basename_no_separator(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        assert LineageGraphBuilder._basename("singletoken") == "singletoken"

    def test_basename_mixed_prefers_forward_slash_first(self):
        """Order is ('/', '\\'); '/' is checked first."""
        from curator.gui.lineage_view import LineageGraphBuilder
        # 'foo/bar\\baz' has '/' so it splits on '/' returning 'bar\\baz'
        assert LineageGraphBuilder._basename("foo/bar\\baz") == "bar\\baz"

    def test_ellipsize_under_max(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        assert LineageGraphBuilder._ellipsize("short") == "short"

    def test_ellipsize_exactly_max(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        s = "x" * LineageGraphBuilder.LABEL_MAX_LEN
        assert LineageGraphBuilder._ellipsize(s) == s

    def test_ellipsize_over_max(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        s = "x" * (LineageGraphBuilder.LABEL_MAX_LEN + 5)
        out = LineageGraphBuilder._ellipsize(s)
        assert len(out) == LineageGraphBuilder.LABEL_MAX_LEN
        assert out.endswith("…")

    def test_ellipsize_custom_max(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        out = LineageGraphBuilder._ellipsize("hello world", max_len=5)
        assert len(out) == 5
        assert out.endswith("…")

    def test_coerce_datetime_none(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        assert LineageGraphBuilder._coerce_datetime(None) is None

    def test_coerce_datetime_passthrough(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        when = datetime(2026, 5, 13, 12, 0)
        assert LineageGraphBuilder._coerce_datetime(when) is when

    def test_coerce_datetime_iso_string(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        result = LineageGraphBuilder._coerce_datetime("2026-05-13T12:00:00")
        assert isinstance(result, datetime)
        assert result.year == 2026 and result.month == 5

    def test_coerce_datetime_bad_string(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        assert LineageGraphBuilder._coerce_datetime("not-a-date") is None

    def test_coerce_datetime_unhandled_type(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        # int → fromisoformat raises TypeError → caught
        assert LineageGraphBuilder._coerce_datetime(42) is None


# ---------------------------------------------------------------------------
# LineageGraphBuilder __init__
# ---------------------------------------------------------------------------


def _make_builder(*, layout="spring", file_repo=None, lineage_repo=None):
    """Tiny factory: builder with MagicMock repos by default."""
    from curator.gui.lineage_view import LineageGraphBuilder
    return LineageGraphBuilder(
        file_repo=file_repo or MagicMock(),
        lineage_repo=lineage_repo or MagicMock(),
        layout=layout,
    )


class TestBuilderInit:
    def test_default_layout_is_spring(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        b = LineageGraphBuilder(MagicMock(), MagicMock())
        assert b._layout == "spring"
        assert b._seed == 42

    def test_layout_kwarg_accepted_if_known(self):
        b = _make_builder(layout="circular")
        assert b._layout == "circular"

    def test_unknown_layout_falls_back_to_default(self):
        b = _make_builder(layout="bogus_algorithm")
        assert b._layout == "spring"

    def test_seed_is_used(self):
        from curator.gui.lineage_view import LineageGraphBuilder
        b = LineageGraphBuilder(MagicMock(), MagicMock(), seed=99)
        assert b._seed == 99

    def test_cache_starts_empty(self):
        b = _make_builder()
        assert b._file_cache == {}


# ---------------------------------------------------------------------------
# _resolve_file
# ---------------------------------------------------------------------------


class TestResolveFile:
    def test_cache_miss_then_hit(self):
        fid = uuid4()
        file_repo = MagicMock()
        sentinel = object()
        file_repo.get.return_value = sentinel
        b = _make_builder(file_repo=file_repo)

        first = b._resolve_file(fid)
        second = b._resolve_file(fid)
        assert first is sentinel
        assert second is sentinel
        # Only called once due to cache
        file_repo.get.assert_called_once_with(fid)

    def test_exception_returns_none_and_caches(self):
        fid = uuid4()
        file_repo = MagicMock()
        file_repo.get.side_effect = RuntimeError("db gone")
        b = _make_builder(file_repo=file_repo)

        assert b._resolve_file(fid) is None
        # Cache stores None so a second call doesn't re-raise
        assert b._resolve_file(fid) is None
        assert file_repo.get.call_count == 1


# ---------------------------------------------------------------------------
# get_time_range
# ---------------------------------------------------------------------------


def _make_lineage_repo_with_time_range(min_v, max_v):
    """Build a lineage_repo MagicMock whose db.conn() context-manager
    returns a cursor that yields (min, max)."""
    repo = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = (min_v, max_v)
    conn_cm = MagicMock()
    conn_cm.__enter__ = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=cursor)))
    conn_cm.__exit__ = MagicMock(return_value=False)
    repo.db.conn.return_value = conn_cm
    return repo


class TestGetTimeRange:
    def test_populated_range(self):
        repo = _make_lineage_repo_with_time_range(
            "2026-01-01T00:00:00",
            "2026-05-13T12:00:00",
        )
        b = _make_builder(lineage_repo=repo)
        lo, hi = b.get_time_range()
        assert lo == datetime(2026, 1, 1, 0, 0)
        assert hi == datetime(2026, 5, 13, 12, 0)

    def test_both_null(self):
        repo = _make_lineage_repo_with_time_range(None, None)
        b = _make_builder(lineage_repo=repo)
        assert b.get_time_range() == (None, None)

    def test_row_is_none(self):
        """fetchone returns None (no row at all)."""
        repo = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn_cm = MagicMock()
        conn_cm.__enter__ = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=cursor)))
        conn_cm.__exit__ = MagicMock(return_value=False)
        repo.db.conn.return_value = conn_cm
        b = _make_builder(lineage_repo=repo)
        assert b.get_time_range() == (None, None)

    def test_exception_returns_none_tuple(self):
        repo = MagicMock()
        repo.db.conn.side_effect = RuntimeError("db gone")
        b = _make_builder(lineage_repo=repo)
        assert b.get_time_range() == (None, None)


# ---------------------------------------------------------------------------
# _fetch_all_edges
# ---------------------------------------------------------------------------


def _make_lineage_repo_for_fetch(rows, *, raise_on_conn=False):
    repo = MagicMock()
    if raise_on_conn:
        repo.db.conn.side_effect = RuntimeError("db gone")
        return repo
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock(execute=MagicMock(return_value=cursor))
    conn_cm = MagicMock()
    conn_cm.__enter__ = MagicMock(return_value=conn)
    conn_cm.__exit__ = MagicMock(return_value=False)
    repo.db.conn.return_value = conn_cm
    # _row_to_edge is also invoked from the builder; make it return identity.
    repo._row_to_edge = MagicMock(side_effect=lambda r: r)
    return repo


class TestFetchAllEdges:
    def test_no_filter_returns_rows_mapped(self):
        rows = ["row_a", "row_b"]
        repo = _make_lineage_repo_for_fetch(rows)
        b = _make_builder(lineage_repo=repo)
        assert b._fetch_all_edges() == rows

    def test_with_filter_passes_param(self):
        rows = ["row_a"]
        repo = _make_lineage_repo_for_fetch(rows)
        b = _make_builder(lineage_repo=repo)
        when = datetime(2026, 5, 1, 12, 0)
        result = b._fetch_all_edges(max_detected_at=when)
        assert result == rows
        # Confirm we hit the filtered SQL branch (call args contain the datetime).
        conn = repo.db.conn.return_value.__enter__.return_value
        sql, params = conn.execute.call_args[0]
        assert "detected_at IS NULL OR detected_at <=" in sql
        assert params == (when,)

    def test_exception_returns_empty(self):
        repo = _make_lineage_repo_for_fetch([], raise_on_conn=True)
        b = _make_builder(lineage_repo=repo)
        assert b._fetch_all_edges() == []


# ---------------------------------------------------------------------------
# build_full_graph
# ---------------------------------------------------------------------------


class _FakeEdge:
    """Minimal edge object compatible with _build_from_edges."""

    def __init__(self, a: UUID, b: UUID, *, kind="duplicate",
                 confidence=0.9, detected_by="by_hash",
                 detected_at: datetime | None = None):
        self.from_curator_id = a
        self.to_curator_id = b
        self.edge_kind = kind
        self.confidence = confidence
        self.detected_by = detected_by
        self.detected_at = detected_at


class _FakeFile:
    def __init__(self, source_path: str):
        self.source_path = source_path


class TestBuildFullGraph:
    def test_networkx_unavailable_returns_empty(self, monkeypatch):
        """First branch: NETWORKX_AVAILABLE False → empty GraphLayout."""
        import curator.gui.lineage_view as lv
        monkeypatch.setattr(lv, "NETWORKX_AVAILABLE", False)
        b = _make_builder()
        result = b.build_full_graph()
        assert result.is_empty

    def test_normal_flow(self):
        a, c = uuid4(), uuid4()
        edges = [_FakeEdge(a, c)]
        repo = _make_lineage_repo_for_fetch(edges)
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: _FakeFile(f"/p/{fid.hex[:6]}.txt")
        b = _make_builder(lineage_repo=repo, file_repo=file_repo)
        layout = b.build_full_graph()
        assert not layout.is_empty
        assert len(layout.nodes) == 2
        assert len(layout.edges) == 1

    def test_with_time_filter(self):
        a, c = uuid4(), uuid4()
        when = datetime(2026, 5, 1, 12, 0)
        edges = [_FakeEdge(a, c, detected_at=when)]
        repo = _make_lineage_repo_for_fetch(edges)
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: _FakeFile(f"/p/{fid.hex[:6]}.txt")
        b = _make_builder(lineage_repo=repo, file_repo=file_repo)
        layout = b.build_full_graph(max_detected_at=when)
        assert not layout.is_empty


# ---------------------------------------------------------------------------
# build_focus_graph (BFS)
# ---------------------------------------------------------------------------


class TestBuildFocusGraph:
    def test_networkx_unavailable_returns_empty(self, monkeypatch):
        import curator.gui.lineage_view as lv
        monkeypatch.setattr(lv, "NETWORKX_AVAILABLE", False)
        b = _make_builder()
        assert b.build_focus_graph(uuid4(), max_hops=2).is_empty

    def test_single_hop(self):
        focus = uuid4()
        neighbor = uuid4()
        edges_from_focus = [_FakeEdge(focus, neighbor)]
        repo = MagicMock()
        repo.get_edges_for.side_effect = lambda fid: (
            edges_from_focus if fid == focus else []
        )
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: _FakeFile(f"/{fid.hex[:6]}")
        b = _make_builder(lineage_repo=repo, file_repo=file_repo)
        layout = b.build_focus_graph(focus, max_hops=1)
        assert not layout.is_empty
        # focus + neighbor = 2 nodes
        assert len(layout.nodes) == 2

    def test_multi_hop_frontier_growth(self):
        f0, f1, f2 = uuid4(), uuid4(), uuid4()
        edges_map = {f0: [_FakeEdge(f0, f1)], f1: [_FakeEdge(f1, f2)]}
        repo = MagicMock()
        repo.get_edges_for.side_effect = lambda fid: edges_map.get(fid, [])
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: _FakeFile(f"/{fid.hex[:6]}")
        b = _make_builder(lineage_repo=repo, file_repo=file_repo)
        layout = b.build_focus_graph(f0, max_hops=2)
        assert len(layout.nodes) == 3  # f0 + f1 + f2

    def test_empty_frontier_breaks_early(self):
        """No edges → frontier becomes empty → loop breaks → empty layout."""
        repo = MagicMock()
        repo.get_edges_for.return_value = []
        b = _make_builder(lineage_repo=repo)
        layout = b.build_focus_graph(uuid4(), max_hops=5)
        assert layout.is_empty

    def test_get_edges_for_exception_is_swallowed(self):
        repo = MagicMock()
        repo.get_edges_for.side_effect = RuntimeError("db down")
        b = _make_builder(lineage_repo=repo)
        layout = b.build_focus_graph(uuid4(), max_hops=2)
        assert layout.is_empty

    def test_neighbor_already_visited_not_re_added(self):
        """Cycle: A↔B. After A→B, B shouldn't add A to next frontier."""
        a, b = uuid4(), uuid4()
        # Both edges visible from either node.
        all_edges = [_FakeEdge(a, b)]
        repo = MagicMock()
        repo.get_edges_for.return_value = all_edges
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: _FakeFile(f"/{fid.hex[:6]}")
        b_obj = _make_builder(lineage_repo=repo, file_repo=file_repo)
        layout = b_obj.build_focus_graph(a, max_hops=3)
        # A + B only; no infinite loop, no duplicate.
        assert len(layout.nodes) == 2


# ---------------------------------------------------------------------------
# _build_from_edges (layout branches)
# ---------------------------------------------------------------------------


class TestBuildFromEdges:
    def test_empty_edges_returns_empty_layout(self):
        b = _make_builder()
        assert b._build_from_edges([]).is_empty

    @pytest.mark.parametrize("layout_name", [
        "spring", "circular", "kamada_kawai", "shell",
    ])
    def test_each_layout_algorithm(self, layout_name):
        a, c = uuid4(), uuid4()
        # kamada_kawai chokes on disconnected single-edge graphs sometimes
        # but with one edge it works; add a second edge for robustness.
        d = uuid4()
        edges = [_FakeEdge(a, c), _FakeEdge(c, d)]
        repo = MagicMock()
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: _FakeFile(f"/{fid.hex[:6]}")
        from curator.gui.lineage_view import LineageGraphBuilder
        builder = LineageGraphBuilder(file_repo, repo, layout=layout_name)
        layout = builder._build_from_edges(edges)
        assert len(layout.nodes) == 3
        assert len(layout.edges) == 2

    def test_layout_algorithm_exception_falls_back_to_grid(self, monkeypatch):
        """If a layout algorithm raises, the fallback positions assign
        (i*0.1, 0.5) per node."""
        import curator.gui.lineage_view as lv
        # Patch nx.spring_layout to raise
        original = lv.nx.spring_layout
        monkeypatch.setattr(
            lv.nx, "spring_layout",
            MagicMock(side_effect=RuntimeError("layout failed")),
        )
        a, c = uuid4(), uuid4()
        edges = [_FakeEdge(a, c)]
        repo = MagicMock()
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: _FakeFile(f"/{fid.hex[:6]}")
        b = _make_builder(lineage_repo=repo, file_repo=file_repo)
        layout = b._build_from_edges(edges)
        # Fallback positions are along y=0.5; assert at least one node has y=0.5
        ys = {n.y for n in layout.nodes}
        assert 0.5 in ys
        # Restore (not strictly necessary with monkeypatch but defensive)
        lv.nx.spring_layout = original

    def test_edge_kind_with_value_attr_uses_value(self):
        """If edge.edge_kind has .value (like Enum), it's used as the
        string. Otherwise str() is called."""
        a, c = uuid4(), uuid4()

        class _Kind:
            value = "version_of"

        e = _FakeEdge(a, c, kind=_Kind())
        repo = MagicMock()
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: _FakeFile(f"/{fid.hex[:6]}")
        b = _make_builder(lineage_repo=repo, file_repo=file_repo)
        layout = b._build_from_edges([e])
        assert layout.edges[0].edge_kind == "version_of"

    def test_edge_kind_without_value_attr_uses_str(self):
        a, c = uuid4(), uuid4()
        e = _FakeEdge(a, c, kind="raw_string_kind")
        repo = MagicMock()
        file_repo = MagicMock()
        file_repo.get.side_effect = lambda fid: _FakeFile(f"/{fid.hex[:6]}")
        b = _make_builder(lineage_repo=repo, file_repo=file_repo)
        layout = b._build_from_edges([e])
        assert layout.edges[0].edge_kind == "raw_string_kind"

    def test_file_unresolvable_uses_placeholder_path(self):
        """If file_repo.get returns None for a node, label uses ``(fid)``."""
        a, c = uuid4(), uuid4()
        edges = [_FakeEdge(a, c)]
        repo = MagicMock()
        file_repo = MagicMock()
        file_repo.get.return_value = None  # Both files unresolvable
        b = _make_builder(lineage_repo=repo, file_repo=file_repo)
        layout = b._build_from_edges(edges)
        # full_path falls back to f"({fid})"
        assert all(n.full_path.startswith("(") and n.full_path.endswith(")")
                   for n in layout.nodes)
