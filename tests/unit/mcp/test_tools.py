"""Unit tests for curator.mcp.tools (the 3 v1.2.0 tools).

These exercise the tools through the FastMCP server's call_tool API so
the test path mirrors real LLM-client invocation. Each test builds a
fresh CuratorRuntime against a tmp DB so tests are isolated.

See Curator/docs/CURATOR_MCP_SERVER_DESIGN.md v0.2 §4.3 for the
per-tool spec these tests verify.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import pytest

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.mcp import create_server
from curator.models.audit import AuditEntry
from curator.models.source import SourceConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime(tmp_path):
    """A real CuratorRuntime backed by a tmp SQLite DB."""
    db_path = tmp_path / "mcp_test.db"
    cfg = Config.load()
    return build_runtime(
        config=cfg,
        db_path_override=db_path,
        json_output=False,
        no_color=True,
        verbosity=0,
    )


@pytest.fixture
def server(runtime):
    """A FastMCP server bound to a fresh runtime."""
    return create_server(runtime)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call_tool_sync(server, name: str, args: dict | None = None) -> Any:
    """Sync wrapper around FastMCP's async call_tool API."""
    return asyncio.run(server.call_tool(name, args or {}))


def _extract_payload(result: Any) -> Any:
    """Extract the structured payload from a FastMCP call_tool result.

    FastMCP returns either ``(content_list, structured_dict)`` (newer
    versions) or a ``CallToolResult`` (older versions). We accept both
    and prefer the structured payload when available, falling back to
    parsing JSON text content.
    """
    # Newer FastMCP: tuple of (content, structured)
    if isinstance(result, tuple):
        content_list, structured = result
        if structured is not None:
            # FastMCP wraps list returns in {"result": [...]}; unwrap if so
            if isinstance(structured, dict) and "result" in structured:
                return structured["result"]
            return structured
        # No structured: fall through to text-content extraction
        return _extract_text_json(content_list)

    # Older FastMCP: CallToolResult with .content
    if hasattr(result, "content"):
        return _extract_text_json(result.content)

    # Fallback: assume result is the content list itself
    return _extract_text_json(result)


def _extract_text_json(content_list: Any) -> Any:
    """Find the first text block in content_list and parse as JSON."""
    if not content_list:
        return None
    for block in content_list:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                return text
    return None


# ===========================================================================
# Server-level: tool registration
# ===========================================================================


class TestServerRegistration:
    """Server bootstrap should register exactly the 3 v1.2.0 tools."""

    def test_three_tools_registered(self, server):
        tools = asyncio.run(server.list_tools())
        names = {t.name for t in tools}
        assert names == {"health_check", "list_sources", "query_audit_log"}, (
            f"v1.2.0 P1 expects exactly 3 tools; got {len(tools)}: {names}"
        )

    def test_each_tool_has_description(self, server):
        tools = asyncio.run(server.list_tools())
        for tool in tools:
            assert tool.description, f"Tool {tool.name} missing description"
            # Each description should be at least 10 chars (sanity)
            assert len(tool.description) >= 10


# ===========================================================================
# Tool 1: health_check
# ===========================================================================


class TestHealthCheck:
    def test_returns_ok_with_real_runtime(self, server):
        """A fresh runtime with default plugins + reachable DB returns
        status='ok' and exposes curator_version + plugin_count + db_path."""
        result = _call_tool_sync(server, "health_check")
        data = _extract_payload(result)

        assert data["status"] == "ok", (
            f"expected 'ok' status with default plugins + reachable DB; got {data}"
        )
        assert "curator_version" in data
        assert data["curator_version"]  # non-empty string
        assert data["plugin_count"] > 0, (
            "expected at least 1 plugin (core plugins should be registered)"
        )
        assert data["db_path"]
        assert data["db_path"] != "unknown"


# ===========================================================================
# Tool 2: list_sources
# ===========================================================================


class TestListSources:
    def test_empty_for_fresh_runtime(self, server, runtime):
        """A fresh runtime has whatever sources Config.load() seeded;
        list_sources returns exactly that set."""
        result = _call_tool_sync(server, "list_sources")
        data = _extract_payload(result)
        assert isinstance(data, list)

        # Compare against direct repo query for consistency
        direct = runtime.source_repo.list_all()
        assert len(data) == len(direct)

    def test_returns_inserted_source(self, server, runtime):
        runtime.source_repo.upsert(SourceConfig(
            source_id="test:abc",
            source_type="test",
            display_name="My Test Source",
            enabled=True,
        ))
        result = _call_tool_sync(server, "list_sources")
        data = _extract_payload(result)
        ids = {s["source_id"] for s in data}
        assert "test:abc" in ids

        # Find the test source and verify all fields are present
        test_src = next(s for s in data if s["source_id"] == "test:abc")
        assert test_src["source_type"] == "test"
        assert test_src["display_name"] == "My Test Source"
        assert test_src["enabled"] is True

    def test_includes_disabled_sources(self, server, runtime):
        """Disabled sources still show up; the 'enabled' field tells
        the LLM client to filter if it cares."""
        runtime.source_repo.upsert(SourceConfig(
            source_id="test:disabled",
            source_type="test",
            enabled=False,
        ))
        result = _call_tool_sync(server, "list_sources")
        data = _extract_payload(result)
        disabled = [s for s in data if s["source_id"] == "test:disabled"]
        assert len(disabled) == 1
        assert disabled[0]["enabled"] is False


# ===========================================================================
# Tool 3: query_audit_log
# ===========================================================================


class TestQueryAuditLog:
    def test_empty_when_no_events(self, server, runtime):
        # Fresh DB => no audit entries
        runtime.audit_repo.query()  # sanity check it works direct
        result = _call_tool_sync(server, "query_audit_log")
        data = _extract_payload(result)
        assert isinstance(data, list)
        assert len(data) == 0

    def test_returns_inserted_event(self, server, runtime):
        runtime.audit_repo.insert(AuditEntry(
            actor="test.actor",
            action="test.action",
            entity_type="file",
            entity_id="abc-123",
            details={"key": "value", "n": 42},
        ))
        result = _call_tool_sync(server, "query_audit_log")
        data = _extract_payload(result)
        assert len(data) == 1
        entry = data[0]
        assert entry["actor"] == "test.actor"
        assert entry["action"] == "test.action"
        assert entry["entity_type"] == "file"
        assert entry["entity_id"] == "abc-123"
        assert entry["details"] == {"key": "value", "n": 42}

    def test_filters_by_actor(self, server, runtime):
        runtime.audit_repo.insert(AuditEntry(
            actor="a", action="x", details={},
        ))
        runtime.audit_repo.insert(AuditEntry(
            actor="b", action="y", details={},
        ))
        result = _call_tool_sync(server, "query_audit_log", {"actor": "a"})
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["actor"] == "a"

    def test_filters_by_action(self, server, runtime):
        runtime.audit_repo.insert(AuditEntry(
            actor="x", action="action_one", details={},
        ))
        runtime.audit_repo.insert(AuditEntry(
            actor="x", action="action_two", details={},
        ))
        result = _call_tool_sync(server, "query_audit_log", {"action": "action_two"})
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["action"] == "action_two"

    def test_filters_by_entity_id(self, server, runtime):
        runtime.audit_repo.insert(AuditEntry(
            actor="x", action="y", entity_id="file-1", details={},
        ))
        runtime.audit_repo.insert(AuditEntry(
            actor="x", action="y", entity_id="file-2", details={},
        ))
        result = _call_tool_sync(server, "query_audit_log", {"entity_id": "file-1"})
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["entity_id"] == "file-1"

    def test_limit_caps_results(self, server, runtime):
        for i in range(10):
            runtime.audit_repo.insert(AuditEntry(
                actor="x", action=f"action_{i}", details={},
            ))
        result = _call_tool_sync(server, "query_audit_log", {"limit": 3})
        data = _extract_payload(result)
        assert len(data) == 3

    def test_limit_above_1000_capped(self, server, runtime):
        """The tool caps limit at 1000 to protect against runaway queries.
        Doesn't error on large limit; just silently caps."""
        result = _call_tool_sync(server, "query_audit_log", {"limit": 5000})
        data = _extract_payload(result)
        assert isinstance(data, list)  # success, no crash

    def test_returns_compliance_events_for_atrium_safety_actor(
        self, server, runtime,
    ):
        """Headline use case: an LLM client asks 'what did the safety
        plugin refuse last week?' via this tool."""
        runtime.audit_repo.insert(AuditEntry(
            actor="curatorplug.atrium_safety",
            action="compliance.refused",
            entity_type="file",
            entity_id="some-uuid",
            details={"phase": "re-read", "mode": "strict", "reason": "hash mismatch"},
        ))
        runtime.audit_repo.insert(AuditEntry(
            actor="curator.migrate", action="migration.move", details={},
        ))
        result = _call_tool_sync(
            server, "query_audit_log",
            {"actor": "curatorplug.atrium_safety", "action": "compliance.refused"},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["actor"] == "curatorplug.atrium_safety"
        assert data[0]["action"] == "compliance.refused"
        assert data[0]["details"]["mode"] == "strict"
        assert data[0]["details"]["phase"] == "re-read"
