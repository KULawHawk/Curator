"""Focused coverage tests for curator/mcp/tools.py.

Sub-ship v1.7.121 of Round 2 Tier 2.

Covers the limit-clamping defensives, get_health failure paths,
get_lineage edge cases, and other small defensives missed by the
existing test_tools.py.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.mcp import create_server


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/unit/mcp/test_tools.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime(tmp_path):
    db_path = tmp_path / "mcp_cov.db"
    cfg = Config.load()
    return build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )


@pytest.fixture
def server(runtime):
    return create_server(runtime)


def _call_tool_sync(server, name: str, args: dict | None = None) -> Any:
    return asyncio.run(server.call_tool(name, args or {}))


def _extract_payload(result):
    if isinstance(result, tuple):
        content_list, structured = result
        if structured is not None:
            if isinstance(structured, dict) and "result" in structured:
                return structured["result"]
            return structured
    return result


# ---------------------------------------------------------------------------
# get_health failure paths (414-415, 420-424, 429-430, 433)
# ---------------------------------------------------------------------------


def test_get_health_handles_plugin_listing_exception(runtime, server, monkeypatch):
    # Lines 414-415: pm.list_name_plugin raises → caught, plugin_count
    # stays 0 → status becomes "degraded" (line 433).
    monkeypatch.setattr(
        runtime.pm, "list_name_plugin",
        lambda: (_ for _ in ()).throw(RuntimeError("pm boom")),
    )
    result = _call_tool_sync(server, "health_check")
    payload = _extract_payload(result)
    assert payload["status"] == "degraded"
    assert payload["plugin_count"] == 0


def test_get_health_uses_db_path_attribute_when_available(
    runtime, server, monkeypatch,
):
    # Line 420: when runtime.db has a `path` attribute, use it directly.
    # CuratorDB normally exposes `db_path` not `path`, so existing tests
    # don't hit line 420. Inject a `path` attribute on the db instance.
    runtime.db.path = "/synthetic/path/from/db.sqlite"
    result = _call_tool_sync(server, "health_check")
    payload = _extract_payload(result)
    assert payload["db_path"] == "/synthetic/path/from/db.sqlite"


def test_get_health_db_path_unknown_when_neither_db_nor_config_has_path(
    runtime, server, monkeypatch,
):
    # Branch 421->426: runtime.db lacks `path` AND runtime.config lacks
    # `db_path` → fall through with db_path="unknown".
    # CuratorDB doesn't expose `path` by default → first hasattr is False.
    # Strip `db_path` from the Config instance (it's a property; replace
    # it via direct __class__.__dict__ patching).
    cfg_class = type(runtime.config)
    # Hide db_path by overriding the descriptor with one that errors on
    # access — but hasattr swallows AttributeError, so this works:
    monkeypatch.setattr(
        cfg_class, "db_path",
        property(lambda self: (_ for _ in ()).throw(AttributeError("hidden"))),
    )
    result = _call_tool_sync(server, "health_check")
    payload = _extract_payload(result)
    assert payload["db_path"] == "unknown"


def test_get_health_db_path_resolution_swallows_exception(
    runtime, server, monkeypatch,
):
    # Lines 423-424: hasattr-or-str raises → except → db_path stays
    # "unknown". Force by giving runtime.db a `path` that raises on
    # str() conversion.
    class _ExplodingPath:
        def __str__(self):
            raise RuntimeError("str(path) blew up")
    runtime.db.path = _ExplodingPath()
    result = _call_tool_sync(server, "health_check")
    payload = _extract_payload(result)
    assert payload["db_path"] == "unknown"


def test_get_health_handles_audit_repo_exception(runtime, server, monkeypatch):
    # Lines 427-430: runtime.audit_repo.count() raises → except sets
    # status="degraded".
    monkeypatch.setattr(
        runtime.audit_repo, "count",
        lambda: (_ for _ in ()).throw(RuntimeError("audit DB unavailable")),
    )
    result = _call_tool_sync(server, "health_check")
    payload = _extract_payload(result)
    assert payload["status"] == "degraded"


# ---------------------------------------------------------------------------
# Limit clamping in multiple tools (497, 561, 563, 782, 784, 845, 847)
# ---------------------------------------------------------------------------


def test_query_audit_log_clamps_negative_limit(runtime, server):
    # Line 497: limit < 1 → limit = 1.
    result = _call_tool_sync(server, "query_audit_log", {"limit": -5})
    payload = _extract_payload(result)
    assert isinstance(payload, list)


def test_query_files_clamps_both_limits(runtime, server):
    # Lines 560-563: limit > 1000 → 1000; limit < 1 → 1.
    r1 = _call_tool_sync(server, "query_files", {"limit": 5000})
    assert isinstance(_extract_payload(r1), list)
    r2 = _call_tool_sync(server, "query_files", {"limit": -1})
    assert isinstance(_extract_payload(r2), list)


def test_list_trashed_clamps_both_limits(runtime, server):
    # Lines 781-784: limit > 1000 → 1000; limit < 1 → 1.
    r1 = _call_tool_sync(server, "list_trashed", {"limit": 5000})
    assert isinstance(_extract_payload(r1), list)
    r2 = _call_tool_sync(server, "list_trashed", {"limit": -1})
    assert isinstance(_extract_payload(r2), list)


def test_get_migration_status_clamps_both_limits(runtime, server):
    # Lines 844-847: limit > 200 → 200; limit < 1 → 1.
    r1 = _call_tool_sync(server, "get_migration_status", {"limit": 5000})
    assert isinstance(_extract_payload(r1), list)
    r2 = _call_tool_sync(server, "get_migration_status", {"limit": -1})
    assert isinstance(_extract_payload(r2), list)


# ---------------------------------------------------------------------------
# get_lineage defensive arms (647, 657, branches 681->670, 683->670)
# ---------------------------------------------------------------------------


def test_get_lineage_clamps_max_depth_below_one(runtime, server):
    # Line 647: max_depth < 1 → max_depth = 1.
    result = _call_tool_sync(server, "get_lineage", {
        "file_id": str(uuid4()),  # nonexistent
        "max_depth": -1,
    })
    payload = _extract_payload(result)
    # Nonexistent file_id → None payload (root_file is None at line 657).
    assert payload is None


def test_get_lineage_bfs_skips_already_visited_and_missing_other(
    runtime, server, monkeypatch,
):
    # Branches 681->670 and 683->670: BFS edge loop where
    # - other_cid is already in visited_files (skip the add)
    # - other_file is None (skip the add)
    # Mock lineage_repo + file_repo to exercise both.
    from datetime import datetime
    from uuid import UUID
    from curator.models.file import FileEntity
    from curator.models.lineage import LineageEdge, LineageKind

    NOW = datetime(2026, 5, 13, 12, 0, 0)
    root_id = UUID("11111111-1111-1111-1111-111111111111")
    other_id = UUID("22222222-2222-2222-2222-222222222222")
    ghost_id = UUID("33333333-3333-3333-3333-333333333333")

    root_file = FileEntity(
        curator_id=root_id, source_id="local",
        source_path="/a.txt", size=10, mtime=NOW,
    )
    other_file = FileEntity(
        curator_id=other_id, source_id="local",
        source_path="/b.txt", size=10, mtime=NOW,
    )

    def fake_get(cid):
        if cid == root_id:
            return root_file
        if cid == other_id:
            return other_file
        return None  # ghost_id → triggers branch 683->670

    monkeypatch.setattr(runtime.file_repo, "get", fake_get)

    # Edges:
    # 1. root→other (normal, populates visited_files with other)
    # 2. root→ghost (other_cid not visited, but file_repo.get returns
    #    None → branch 683->670)
    # 3. From other: other→root (other_cid root IS in visited →
    #    branch 681->670)
    edge1 = LineageEdge(
        from_curator_id=root_id, to_curator_id=other_id,
        edge_kind=LineageKind.NEAR_DUPLICATE, confidence=0.8,
        detected_by="test",
    )
    edge2 = LineageEdge(
        from_curator_id=root_id, to_curator_id=ghost_id,
        edge_kind=LineageKind.NEAR_DUPLICATE, confidence=0.7,
        detected_by="test",
    )
    edge3 = LineageEdge(
        from_curator_id=other_id, to_curator_id=root_id,
        edge_kind=LineageKind.VERSION_OF, confidence=0.9,
        detected_by="test",
    )

    def fake_get_edges_for(cid):
        if cid == root_id:
            return [edge1, edge2]
        if cid == other_id:
            return [edge3]
        return []

    monkeypatch.setattr(
        runtime.lineage_repo, "get_edges_for", fake_get_edges_for,
    )

    result = _call_tool_sync(server, "get_lineage", {
        "file_id": str(root_id), "max_depth": 2,
    })
    payload = _extract_payload(result)
    assert payload is not None
    # Nodes: root + other (ghost skipped). All 3 edges still recorded
    # because edge_ids are distinct.
    assert len(payload["nodes"]) == 2


def test_get_lineage_returns_none_for_missing_root_file(runtime, server):
    # Line 657: root_file is None → return None.
    result = _call_tool_sync(server, "get_lineage", {
        "file_id": str(uuid4()),
        "max_depth": 2,
    })
    payload = _extract_payload(result)
    assert payload is None


# ---------------------------------------------------------------------------
# find_duplicates: file with no hash (741)
# ---------------------------------------------------------------------------


def test_find_duplicates_returns_empty_when_file_has_no_hash(
    runtime, server, monkeypatch,
):
    # Line 740-741: file exists but has xxhash3_128=None → return [].
    fake_file = MagicMock()
    fake_file.xxhash3_128 = None
    monkeypatch.setattr(
        runtime.file_repo, "get",
        lambda cid: fake_file,
    )
    result = _call_tool_sync(server, "find_duplicates", {
        "file_id": str(uuid4()),
    })
    payload = _extract_payload(result)
    assert payload == []
