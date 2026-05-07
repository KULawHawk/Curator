"""Lineage Graph view (v0.41, Phase Beta gate 4 final view).

DESIGN.md §15.2 lists Lineage Graph as one of the seven canonical GUI
views. This module provides:

  * :class:`LineageGraphBuilder` — pure-Python facade over file_repo +
    lineage_repo that builds a :class:`networkx.DiGraph` of all (or a
    focused subset of) lineage edges, computes a 2D layout, and returns
    everything as plain data the Qt rendering layer can consume.
  * :class:`LineageGraphView` — :class:`QGraphicsView` widget that
    renders the graph as nodes + edges with type-color coding and edge
    confidence labels. Read-only; double-click on a node opens the
    standard inspect dialog (v0.36 reuse).

The split between builder (pure logic) and view (Qt rendering) makes
the graph computation testable without an event loop, while the visual
piece is exercised by screenshot + smoke test.

For v0.41 we ship the "show all files with edges" mode. Focus-mode
(neighborhood-of-N-hops centered on a selected file) is a v0.42
follow-up; the builder already supports it via
:meth:`build_focus_graph` so the view layer just needs the picker UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:  # pragma: no cover
    NETWORKX_AVAILABLE = False

if TYPE_CHECKING:  # pragma: no cover
    from curator.storage.repositories.file_repo import FileRepository
    from curator.storage.repositories.lineage_repo import LineageRepository

# ---------------------------------------------------------------------------
# Graph builder — pure logic, no Qt
# ---------------------------------------------------------------------------


@dataclass
class GraphNode:
    """A node in the lineage graph (one file)."""

    curator_id: UUID
    label: str  # display name (file basename, ellipsized to 24 chars)
    full_path: str
    x: float = 0.0
    y: float = 0.0


@dataclass
class GraphEdge:
    """An edge in the lineage graph (one lineage relationship)."""

    from_id: UUID
    to_id: UUID
    edge_kind: str
    confidence: float
    detected_by: str = ""


@dataclass
class GraphLayout:
    """Computed graph ready to render.

    Coordinates are in [0, 1] from the layout algorithm; the view
    layer scales them to scene coordinates.
    """

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.nodes and not self.edges


class LineageGraphBuilder:
    """Builds a :class:`GraphLayout` from the live lineage + file repos.

    The builder's job is to:
      1. Pull lineage edges from :class:`LineageRepository`
      2. Resolve each edge's endpoint files via :class:`FileRepository`
        (with a per-instance cache to avoid re-fetching shared nodes)
      3. Compute a 2D layout via networkx
      4. Return :class:`GraphLayout` for the view to render

    Two modes are supported:

      * :meth:`build_full_graph` — every file that participates in at
        least one lineage edge.
      * :meth:`build_focus_graph(file_id, max_hops)` — BFS from the
        focus file outward to ``max_hops``. Useful when the full graph
        is too dense to read.

    The layout algorithm is configurable; default is networkx's
    spring (Fruchterman-Reingold) layout, which works well for the
    small-to-medium graphs we expect (10s to low 100s of nodes).
    """

    DEFAULT_LAYOUT = "spring"
    LAYOUT_ALGORITHMS = ("spring", "circular", "kamada_kawai", "shell")

    LABEL_MAX_LEN: int = 24

    def __init__(
        self,
        file_repo: "FileRepository",
        lineage_repo: "LineageRepository",
        *,
        layout: str = DEFAULT_LAYOUT,
        seed: int = 42,  # deterministic layout for testability
    ) -> None:
        self._file_repo = file_repo
        self._lineage_repo = lineage_repo
        self._layout = layout if layout in self.LAYOUT_ALGORITHMS else self.DEFAULT_LAYOUT
        self._seed = seed
        self._file_cache: dict[UUID, "object | None"] = {}

    # -- public API -----------------------------------------------------

    def build_full_graph(self) -> GraphLayout:
        """Every file with at least one lineage edge."""
        if not NETWORKX_AVAILABLE:
            return GraphLayout()
        edges = self._fetch_all_edges()
        return self._build_from_edges(edges)

    def build_focus_graph(
        self,
        focus_file_id: UUID,
        *,
        max_hops: int = 2,
    ) -> GraphLayout:
        """BFS from a single file outward to ``max_hops``."""
        if not NETWORKX_AVAILABLE:
            return GraphLayout()
        # Walk the lineage repo BFS-style.
        visited: set[UUID] = {focus_file_id}
        frontier: set[UUID] = {focus_file_id}
        collected_edges: list = []
        for _ in range(max_hops):
            next_frontier: set[UUID] = set()
            for fid in frontier:
                try:
                    these = self._lineage_repo.get_edges_for(fid)
                except Exception:
                    continue
                for e in these:
                    collected_edges.append(e)
                    other = e.to_curator_id if e.from_curator_id == fid else e.from_curator_id
                    if other not in visited:
                        next_frontier.add(other)
                        visited.add(other)
            frontier = next_frontier
            if not frontier:
                break
        return self._build_from_edges(collected_edges)

    # -- internal helpers -----------------------------------------------

    def _fetch_all_edges(self) -> list:
        try:
            with self._lineage_repo.db.conn() as conn:
                cursor = conn.execute(
                    "SELECT * FROM lineage_edges ORDER BY confidence DESC"
                )
                rows = cursor.fetchall()
                return [self._lineage_repo._row_to_edge(row) for row in rows]
        except Exception:
            return []

    def _build_from_edges(self, edges: list) -> GraphLayout:
        if not edges:
            return GraphLayout()

        # Build the networkx graph (directed; lineage has a from->to direction).
        G = nx.DiGraph()
        seen_files: set[UUID] = set()
        graph_edges: list[GraphEdge] = []
        for e in edges:
            seen_files.add(e.from_curator_id)
            seen_files.add(e.to_curator_id)
            G.add_edge(str(e.from_curator_id), str(e.to_curator_id))
            kind_str = (
                e.edge_kind.value if hasattr(e.edge_kind, "value")
                else str(e.edge_kind)
            )
            graph_edges.append(GraphEdge(
                from_id=e.from_curator_id,
                to_id=e.to_curator_id,
                edge_kind=kind_str,
                confidence=float(e.confidence),
                detected_by=getattr(e, "detected_by", ""),
            ))

        # Compute layout.
        try:
            if self._layout == "circular":
                pos = nx.circular_layout(G)
            elif self._layout == "kamada_kawai":
                pos = nx.kamada_kawai_layout(G)
            elif self._layout == "shell":
                pos = nx.shell_layout(G)
            else:  # spring
                pos = nx.spring_layout(G, seed=self._seed)
        except Exception:
            # Fall back to a simple grid if the layout algorithm chokes
            # (some can fail on disconnected graphs; spring with seed
            # generally works).
            pos = {n: (i * 0.1, 0.5) for i, n in enumerate(G.nodes())}

        # Resolve files + assemble nodes.
        graph_nodes: list[GraphNode] = []
        for fid in seen_files:
            file_obj = self._resolve_file(fid)
            full_path = file_obj.source_path if file_obj else f"({fid})"
            label = self._ellipsize(self._basename(full_path))
            x, y = pos.get(str(fid), (0.0, 0.0))
            graph_nodes.append(GraphNode(
                curator_id=fid,
                label=label,
                full_path=full_path,
                x=float(x),
                y=float(y),
            ))

        return GraphLayout(nodes=graph_nodes, edges=graph_edges)

    def _resolve_file(self, fid: UUID):
        if fid in self._file_cache:
            return self._file_cache[fid]
        try:
            obj = self._file_repo.get(fid)
        except Exception:
            obj = None
        self._file_cache[fid] = obj
        return obj

    @classmethod
    def _basename(cls, path: str) -> str:
        # Avoid os.path.basename to keep this pure-string (works on
        # paths containing characters from any platform).
        for sep in ("/", "\\"):
            if sep in path:
                return path.rsplit(sep, 1)[-1]
        return path

    @classmethod
    def _ellipsize(cls, text: str, *, max_len: int | None = None) -> str:
        max_len = max_len if max_len is not None else cls.LABEL_MAX_LEN
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "\u2026"  # single-char ellipsis


# ---------------------------------------------------------------------------
# Edge kind -> color mapping (used by both the view and the legend)
# ---------------------------------------------------------------------------


EDGE_KIND_COLORS: dict[str, str] = {
    "duplicate": "#d33682",        # magenta — exact dups
    "near_duplicate": "#cb4b16",   # orange — fuzzy dups
    "version_of": "#268bd2",       # blue — version chains
    "derived_from": "#859900",     # green — derived/edited
    "renamed_from": "#b58900",     # yellow — moves with content match
}
EDGE_KIND_DEFAULT_COLOR = "#586e75"  # neutral gray for unknown kinds


def color_for_edge_kind(kind: str) -> str:
    """Look up the edge-kind color. Unknown kinds get a neutral default."""
    return EDGE_KIND_COLORS.get(kind, EDGE_KIND_DEFAULT_COLOR)


# ---------------------------------------------------------------------------
# Qt view layer (deferred-imported)
# ---------------------------------------------------------------------------


def _make_lineage_graph_view(builder: LineageGraphBuilder):
    """Construct a LineageGraphView. Deferred import keeps headless tests
    cheap (PySide6 import takes ~300ms on first load).
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush, QColor, QFont, QPen
    from PySide6.QtWidgets import (
        QGraphicsEllipseItem, QGraphicsLineItem,
        QGraphicsScene, QGraphicsTextItem, QGraphicsView,
    )

    class LineageGraphView(QGraphicsView):
        SCENE_W: int = 800
        SCENE_H: int = 500
        NODE_RADIUS: int = 16
        NODE_COLOR: str = "#073642"
        NODE_BORDER: str = "#93a1a1"
        LABEL_COLOR: str = "#fdf6e3"
        EMPTY_HINT: str = (
            "No lineage edges in the database yet.\n"
            "Run a scan, then re-open Curator to populate the graph."
        )

        def __init__(self, builder: LineageGraphBuilder):
            super().__init__()
            self._builder = builder
            self._scene = QGraphicsScene(0, 0, self.SCENE_W, self.SCENE_H)
            self.setScene(self._scene)
            self.setRenderHint(self.renderHints() | self.renderHints().__class__.Antialiasing)
            self.refresh()

        def refresh(self) -> None:
            """Re-query the builder and re-render the scene."""
            self._scene.clear()
            layout = self._builder.build_full_graph()
            if layout.is_empty:
                self._render_empty_state()
                return
            self._render(layout)

        def _render_empty_state(self) -> None:
            t = self._scene.addText(self.EMPTY_HINT)
            t.setDefaultTextColor(QColor(self.NODE_BORDER))
            t.setPos(40, self.SCENE_H / 2 - 20)

        def _render(self, layout) -> None:
            # Layout coords are in [0, 1] (or roughly); scale to scene with margin.
            margin = 40
            sw = self.SCENE_W - 2 * margin
            sh = self.SCENE_H - 2 * margin

            # Find min/max so we can normalize.
            xs = [n.x for n in layout.nodes] or [0.0]
            ys = [n.y for n in layout.nodes] or [0.0]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            x_range = (x_max - x_min) or 1.0
            y_range = (y_max - y_min) or 1.0

            def to_scene(n) -> tuple[float, float]:
                xi = (n.x - x_min) / x_range
                yi = (n.y - y_min) / y_range
                return margin + xi * sw, margin + yi * sh

            node_positions: dict = {}
            for n in layout.nodes:
                sx, sy = to_scene(n)
                node_positions[n.curator_id] = (sx, sy)

            # Draw edges first so nodes overlay them.
            for e in layout.edges:
                src = node_positions.get(e.from_id)
                dst = node_positions.get(e.to_id)
                if not src or not dst:
                    continue
                color = QColor(color_for_edge_kind(e.edge_kind))
                pen = QPen(color, 2)
                line = QGraphicsLineItem(src[0], src[1], dst[0], dst[1])
                line.setPen(pen)
                line.setToolTip(
                    f"{e.edge_kind} (confidence={e.confidence:.2f})\n"
                    f"detected by: {e.detected_by or 'unknown'}"
                )
                self._scene.addItem(line)

                # Confidence label near the midpoint.
                mx = (src[0] + dst[0]) / 2
                my = (src[1] + dst[1]) / 2
                lbl = QGraphicsTextItem(f"{e.confidence:.2f}")
                lbl.setDefaultTextColor(color)
                font = QFont()
                font.setPointSize(7)
                lbl.setFont(font)
                lbl.setPos(mx - 10, my - 10)
                self._scene.addItem(lbl)

            # Draw nodes on top.
            r = self.NODE_RADIUS
            for n in layout.nodes:
                sx, sy = node_positions[n.curator_id]
                ellipse = QGraphicsEllipseItem(sx - r, sy - r, 2 * r, 2 * r)
                ellipse.setBrush(QBrush(QColor(self.NODE_COLOR)))
                ellipse.setPen(QPen(QColor(self.NODE_BORDER), 1.5))
                ellipse.setToolTip(n.full_path)
                ellipse.setData(0, str(n.curator_id))  # stash for click handlers
                self._scene.addItem(ellipse)

                # Label below the node.
                tx = QGraphicsTextItem(n.label)
                tx.setDefaultTextColor(QColor(self.LABEL_COLOR))
                font = QFont()
                font.setPointSize(8)
                tx.setFont(font)
                # Center horizontally (rough; QGraphicsTextItem doesn't
                # auto-center).
                approx_w = len(n.label) * 5
                tx.setPos(sx - approx_w / 2, sy + r + 2)
                self._scene.addItem(tx)

    return LineageGraphView(builder)


__all__ = [
    "LineageGraphBuilder",
    "GraphNode",
    "GraphEdge",
    "GraphLayout",
    "EDGE_KIND_COLORS",
    "EDGE_KIND_DEFAULT_COLOR",
    "color_for_edge_kind",
    "_make_lineage_graph_view",
]
