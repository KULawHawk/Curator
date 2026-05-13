"""Coverage for ``curator.gui.lineage_view`` Part 2 (v1.7.187).

Round 4 Tier 2 sub-ship 3 of 6 — closes the Qt view layer
(``_make_lineage_graph_view`` factory + ``LineageGraphView`` class)
using pytest-qt's ``qtbot`` fixture. Combined with Part 1 (v1.7.186)
this closes ``gui/lineage_view.py`` at 100% line + branch.

The view is a ``QGraphicsView`` subclass that the factory constructs
via deferred imports. We exercise:

* The factory call (the deferred-import path)
* ``__init__`` (which invokes ``refresh`` at the tail)
* ``refresh`` both with and without ``max_detected_at`` arg
* ``clear_time_filter``
* ``_render_empty_state``
* ``_render`` with edges + nodes (including the
  "edge endpoint missing from node_positions" continue branch)

Per ``docs/GUI_TESTING_STRATEGY.md``, we drive the widget under the
shared qapp + qtbot fixtures. No real scene paint — pytest-qt + the
offscreen platform satisfy QGraphicsScene's add-item paths headlessly.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    import sys
    return QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# Helpers — reuse the GraphNode/GraphEdge/GraphLayout module objects
# ---------------------------------------------------------------------------


def _make_layout(*, with_edges=True, with_unresolved_edge=False):
    """Build a sample GraphLayout to feed the renderer."""
    from curator.gui.lineage_view import GraphEdge, GraphLayout, GraphNode
    a, b = uuid4(), uuid4()
    nodes = [
        GraphNode(curator_id=a, label="alpha", full_path="/p/alpha.txt", x=0.0, y=0.0),
        GraphNode(curator_id=b, label="beta", full_path="/p/beta.txt", x=1.0, y=1.0),
    ]
    edges: list = []
    if with_edges:
        edges.append(GraphEdge(
            from_id=a, to_id=b, edge_kind="duplicate",
            confidence=0.97, detected_by="byhash",
        ))
    if with_unresolved_edge:
        # Edge whose endpoints don't appear in `nodes` — triggers
        # the `if not src or not dst: continue` branch at line ~455.
        ghost = uuid4()
        edges.append(GraphEdge(
            from_id=ghost, to_id=uuid4(), edge_kind="version_of",
            confidence=0.8,
        ))
    return GraphLayout(nodes=nodes, edges=edges)


def _make_builder_returning(*, layout_factory, time_range=(None, None)):
    """Builder mock whose build_full_graph(...) returns the layout from
    the factory each call. The factory takes the max_detected_at arg."""
    builder = MagicMock()
    builder.build_full_graph.side_effect = lambda *, max_detected_at=None: layout_factory(
        max_detected_at,
    )
    builder.get_time_range.return_value = time_range
    return builder


# ---------------------------------------------------------------------------
# Factory + __init__
# ---------------------------------------------------------------------------


class TestFactoryAndInit:
    def test_factory_returns_view(self, qapp, qtbot):
        """The factory's deferred-import path runs and returns a widget."""
        from curator.gui.lineage_view import _make_lineage_graph_view
        builder = _make_builder_returning(
            layout_factory=lambda _max: _make_layout(),
        )
        view = _make_lineage_graph_view(builder)
        qtbot.addWidget(view)
        assert view is not None
        # Constants are class attributes
        assert view.SCENE_W == 800
        assert view.SCENE_H == 500
        assert view.NODE_RADIUS == 16
        # __init__ stashes the builder
        assert view._builder is builder
        # __init__ invokes refresh() at the tail, which calls the builder
        builder.build_full_graph.assert_called()

    def test_init_renders_empty_state_when_layout_empty(self, qapp, qtbot):
        from curator.gui.lineage_view import GraphLayout, _make_lineage_graph_view
        builder = _make_builder_returning(
            layout_factory=lambda _max: GraphLayout(),  # is_empty == True
        )
        view = _make_lineage_graph_view(builder)
        qtbot.addWidget(view)
        # Scene contains exactly the hint text item
        items = view._scene.items()
        # One item for the empty hint (a QGraphicsTextItem). May be 1 item.
        assert len(items) >= 1


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_refresh_without_arg_uses_persisted_filter(self, qapp, qtbot):
        """Calling refresh() with no arg preserves the persisted
        max_detected_at (line 406-407 branch)."""
        from curator.gui.lineage_view import _make_lineage_graph_view
        builder = _make_builder_returning(
            layout_factory=lambda _max: _make_layout(),
        )
        view = _make_lineage_graph_view(builder)
        qtbot.addWidget(view)
        builder.build_full_graph.reset_mock()
        # First, set a filter
        when = datetime(2026, 5, 1, 12, 0)
        view.refresh(max_detected_at=when)
        # Then call argument-less refresh — should reuse `when`
        view.refresh()
        # Last call's kwarg should still be `when`
        calls = builder.build_full_graph.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["max_detected_at"] == when
        assert calls[1].kwargs["max_detected_at"] == when

    def test_refresh_with_max_detected_at_persists(self, qapp, qtbot):
        from curator.gui.lineage_view import _make_lineage_graph_view
        builder = _make_builder_returning(
            layout_factory=lambda _max: _make_layout(),
        )
        view = _make_lineage_graph_view(builder)
        qtbot.addWidget(view)
        when = datetime(2026, 5, 1, 12, 0)
        view.refresh(max_detected_at=when)
        assert view._current_max_detected_at == when

    def test_refresh_populated_scene_has_items(self, qapp, qtbot):
        """After rendering a populated layout, the scene has many items
        (nodes + edges + labels)."""
        from curator.gui.lineage_view import _make_lineage_graph_view
        builder = _make_builder_returning(
            layout_factory=lambda _max: _make_layout(),
        )
        view = _make_lineage_graph_view(builder)
        qtbot.addWidget(view)
        # Per node: 1 ellipse + 1 label = 2 items.
        # Per edge: 1 line + 1 confidence label = 2 items.
        # 2 nodes + 1 edge = 4 + 2 = 6 items.
        items = view._scene.items()
        assert len(items) >= 6

    def test_refresh_with_unresolved_edge_skips_it(self, qapp, qtbot):
        """An edge whose endpoints aren't in node_positions triggers
        the `continue` branch at line ~455 (`if not src or not dst`)."""
        from curator.gui.lineage_view import _make_lineage_graph_view
        builder = _make_builder_returning(
            layout_factory=lambda _max: _make_layout(with_unresolved_edge=True),
        )
        view = _make_lineage_graph_view(builder)
        qtbot.addWidget(view)
        # The unresolved edge produces neither a line nor a confidence
        # label. Real edge produces 2 items (line + label). 2 nodes = 4
        # items (ellipse + label each). Expected: 4 + 2 = 6, NOT 4 + 4.
        items = view._scene.items()
        assert len(items) == 6


# ---------------------------------------------------------------------------
# clear_time_filter
# ---------------------------------------------------------------------------


class TestClearTimeFilter:
    def test_clears_persisted_filter_and_refreshes(self, qapp, qtbot):
        from curator.gui.lineage_view import _make_lineage_graph_view
        builder = _make_builder_returning(
            layout_factory=lambda _max: _make_layout(),
        )
        view = _make_lineage_graph_view(builder)
        qtbot.addWidget(view)
        view.refresh(max_detected_at=datetime(2026, 5, 1, 12, 0))
        assert view._current_max_detected_at is not None

        builder.build_full_graph.reset_mock()
        view.clear_time_filter()
        # Filter cleared and refresh invoked
        assert view._current_max_detected_at is None
        builder.build_full_graph.assert_called_once()
        # The cleared call passes None as the filter
        assert builder.build_full_graph.call_args.kwargs["max_detected_at"] is None


# ---------------------------------------------------------------------------
# Module export sanity (covered already in Part 1's color tests, but
# verify the Qt names made it into __all__)
# ---------------------------------------------------------------------------


class TestModuleExports:
    def test_all_names_present(self):
        from curator.gui import lineage_view
        for name in (
            "LineageGraphBuilder", "GraphNode", "GraphEdge", "GraphLayout",
            "EDGE_KIND_COLORS", "EDGE_KIND_DEFAULT_COLOR",
            "color_for_edge_kind", "_make_lineage_graph_view",
        ):
            assert name in lineage_view.__all__
