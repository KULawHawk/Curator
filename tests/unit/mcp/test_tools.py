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
import re
from datetime import datetime, timedelta, timezone
from curator._compat.datetime import utcnow_naive
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

    def test_nine_tools_registered(self, server):
        tools = asyncio.run(server.list_tools())
        names = {t.name for t in tools}
        assert names == {
            "health_check", "list_sources", "query_audit_log",
            "query_files", "inspect_file", "get_lineage",
            "find_duplicates", "list_trashed", "get_migration_status",
        }, (
            f"v1.2.0 expects exactly 9 tools; got {len(tools)}: {names}"
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


# ===========================================================================
# Helpers for P2 tool tests (build real FileEntity / LineageEdge / etc.)
# ===========================================================================


def _ensure_source(runtime, source_id: str = "local"):
    """Insert source if it doesn't already exist (FK requirement for files)."""
    if runtime.source_repo.get(source_id) is None:
        runtime.source_repo.upsert(SourceConfig(
            source_id=source_id,
            source_type=source_id.split(":")[0] if ":" in source_id else source_id,
            enabled=True,
        ))


def _make_file(
    runtime,
    *,
    source_id: str = "local",
    source_path: str = "/tmp/x.txt",
    size: int = 100,
    xxhash3_128: str | None = "deadbeef" * 4,
    extension: str | None = ".txt",
    file_type: str | None = "text/plain",
):
    """Insert a file into the repo and return the resulting FileEntity.

    Auto-creates the source if missing (FK requirement)."""
    from curator.models.file import FileEntity

    _ensure_source(runtime, source_id)
    f = FileEntity(
        source_id=source_id,
        source_path=source_path,
        size=size,
        mtime=utcnow_naive(),
        xxhash3_128=xxhash3_128,
        extension=extension,
        file_type=file_type,
    )
    runtime.file_repo.insert(f)
    return f


def _make_lineage(runtime, from_file, to_file, kind="duplicate", confidence=0.9):
    from curator.models.lineage import LineageEdge, LineageKind

    edge = LineageEdge(
        from_curator_id=from_file.curator_id,
        to_curator_id=to_file.curator_id,
        edge_kind=LineageKind(kind) if not isinstance(kind, LineageKind) else kind,
        confidence=confidence,
        detected_by="test",
    )
    runtime.lineage_repo.insert(edge)
    return edge


# ===========================================================================
# Tool 4: query_files
# ===========================================================================


class TestQueryFiles:
    def test_empty_for_empty_repo(self, server):
        result = _call_tool_sync(server, "query_files")
        data = _extract_payload(result)
        assert isinstance(data, list)
        assert len(data) == 0

    def test_returns_inserted_file(self, server, runtime):
        _make_file(runtime, source_path="/tmp/a.txt")
        result = _call_tool_sync(server, "query_files")
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["source_path"] == "/tmp/a.txt"
        assert data[0]["size"] == 100
        assert data[0]["file_id"]  # UUID string

    def test_filters_by_source_ids(self, server, runtime):
        _make_file(runtime, source_id="local", source_path="/a")
        _make_file(runtime, source_id="gdrive", source_path="/b")
        result = _call_tool_sync(
            server, "query_files", {"source_ids": ["local"]},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["source_id"] == "local"

    def test_filters_by_extension(self, server, runtime):
        _make_file(runtime, source_path="/a.pdf", extension=".pdf")
        _make_file(runtime, source_path="/b.txt", extension=".txt")
        result = _call_tool_sync(
            server, "query_files", {"extensions": [".pdf"]},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["extension"] == ".pdf"

    def test_filters_by_size_range(self, server, runtime):
        _make_file(runtime, source_path="/small", size=50)
        _make_file(runtime, source_path="/medium", size=500)
        _make_file(runtime, source_path="/large", size=5000)
        result = _call_tool_sync(
            server, "query_files", {"min_size": 100, "max_size": 1000},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["size"] == 500

    def test_limit_caps_results(self, server, runtime):
        for i in range(10):
            _make_file(runtime, source_path=f"/f{i}.txt")
        result = _call_tool_sync(server, "query_files", {"limit": 3})
        data = _extract_payload(result)
        assert len(data) == 3


# ===========================================================================
# Tool 5: inspect_file
# ===========================================================================


class TestInspectFile:
    def test_returns_none_for_invalid_file_id(self, server):
        result = _call_tool_sync(
            server, "inspect_file", {"file_id": "not-a-uuid"},
        )
        data = _extract_payload(result)
        assert data is None

    def test_returns_none_for_unknown_file_id(self, server):
        result = _call_tool_sync(
            server, "inspect_file",
            {"file_id": "00000000-0000-0000-0000-000000000000"},
        )
        data = _extract_payload(result)
        assert data is None

    def test_returns_file_detail(self, server, runtime):
        f = _make_file(runtime, source_path="/x.pdf", extension=".pdf")
        result = _call_tool_sync(
            server, "inspect_file", {"file_id": str(f.curator_id)},
        )
        data = _extract_payload(result)
        assert data["file"]["source_path"] == "/x.pdf"
        assert data["file"]["extension"] == ".pdf"
        assert data["lineage_edges"] == []
        assert data["bundles"] == []

    def test_includes_lineage_edges(self, server, runtime):
        f1 = _make_file(runtime, source_path="/a")
        f2 = _make_file(runtime, source_path="/b")
        _make_lineage(runtime, f1, f2, kind="duplicate", confidence=0.95)
        result = _call_tool_sync(
            server, "inspect_file", {"file_id": str(f1.curator_id)},
        )
        data = _extract_payload(result)
        assert len(data["lineage_edges"]) == 1
        edge = data["lineage_edges"][0]
        assert edge["edge_kind"] == "duplicate"
        assert edge["confidence"] == 0.95


# ===========================================================================
# Tool 6: get_lineage
# ===========================================================================


class TestGetLineage:
    def test_returns_none_for_invalid_file_id(self, server):
        result = _call_tool_sync(
            server, "get_lineage", {"file_id": "not-a-uuid"},
        )
        data = _extract_payload(result)
        assert data is None

    def test_single_node_when_no_edges(self, server, runtime):
        f = _make_file(runtime)
        result = _call_tool_sync(
            server, "get_lineage", {"file_id": str(f.curator_id)},
        )
        data = _extract_payload(result)
        assert data["root_file_id"] == str(f.curator_id)
        assert len(data["nodes"]) == 1
        assert data["edges"] == []

    def test_walks_to_depth_1(self, server, runtime):
        f1 = _make_file(runtime, source_path="/a")
        f2 = _make_file(runtime, source_path="/b")
        _make_lineage(runtime, f1, f2)
        result = _call_tool_sync(
            server, "get_lineage",
            {"file_id": str(f1.curator_id), "max_depth": 1},
        )
        data = _extract_payload(result)
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1

    def test_walks_to_depth_2(self, server, runtime):
        # f1 -> f2 -> f3
        f1 = _make_file(runtime, source_path="/a")
        f2 = _make_file(runtime, source_path="/b")
        f3 = _make_file(runtime, source_path="/c")
        _make_lineage(runtime, f1, f2)
        _make_lineage(runtime, f2, f3)
        result = _call_tool_sync(
            server, "get_lineage",
            {"file_id": str(f1.curator_id), "max_depth": 2},
        )
        data = _extract_payload(result)
        # Should reach all three
        assert len(data["nodes"]) == 3
        assert len(data["edges"]) == 2

    def test_max_depth_capped_at_5(self, server, runtime):
        f = _make_file(runtime)
        result = _call_tool_sync(
            server, "get_lineage",
            {"file_id": str(f.curator_id), "max_depth": 999},
        )
        data = _extract_payload(result)
        # Doesn't crash; returns OK
        assert data["root_file_id"] == str(f.curator_id)


# ===========================================================================
# Tool 7: find_duplicates
# ===========================================================================


class TestFindDuplicates:
    def test_empty_when_neither_input(self, server):
        result = _call_tool_sync(server, "find_duplicates")
        data = _extract_payload(result)
        assert data == []

    def test_finds_dups_by_hash(self, server, runtime):
        h = "a" * 32
        _make_file(runtime, source_path="/a", xxhash3_128=h)
        _make_file(runtime, source_path="/b", xxhash3_128=h)
        _make_file(runtime, source_path="/c", xxhash3_128="b" * 32)  # different
        result = _call_tool_sync(
            server, "find_duplicates", {"xxhash3_128": h},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["xxhash3_128"] == h
        assert len(data[0]["files"]) == 2

    def test_finds_dups_by_file_id(self, server, runtime):
        h = "c" * 32
        f1 = _make_file(runtime, source_path="/a", xxhash3_128=h)
        _make_file(runtime, source_path="/b", xxhash3_128=h)
        result = _call_tool_sync(
            server, "find_duplicates", {"file_id": str(f1.curator_id)},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert len(data[0]["files"]) == 2

    def test_returns_single_file_group_for_unique_file(self, server, runtime):
        """Querying a file with no dups returns a 1-file group.
        len(files) == 1 conveys 'no duplicates' to the LLM."""
        h = "d" * 32
        f = _make_file(runtime, xxhash3_128=h)
        result = _call_tool_sync(
            server, "find_duplicates", {"file_id": str(f.curator_id)},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert len(data[0]["files"]) == 1

    def test_empty_for_unknown_hash(self, server):
        result = _call_tool_sync(
            server, "find_duplicates", {"xxhash3_128": "nonexistent"},
        )
        data = _extract_payload(result)
        assert data == []

    def test_empty_for_invalid_file_id(self, server):
        result = _call_tool_sync(
            server, "find_duplicates", {"file_id": "not-a-uuid"},
        )
        data = _extract_payload(result)
        assert data == []


# ===========================================================================
# Tool 8: list_trashed
# ===========================================================================


class TestListTrashed:
    def _make_trash(self, runtime, **kwargs):
        """Insert a trash record. Auto-creates a backing file (FK requirement)
        unless caller provided an explicit curator_id."""
        from curator.models.trash import TrashRecord

        if "curator_id" not in kwargs:
            # Need a real file row for the FK to be satisfied
            backing_file = _make_file(
                runtime,
                source_id=kwargs.get("original_source_id", "local"),
                source_path=kwargs.get("original_path", "/tmp/x"),
            )
            kwargs["curator_id"] = backing_file.curator_id

        defaults = {
            "original_source_id": "local",
            "original_path": "/tmp/x",
            "trashed_by": "user.cli",
            "reason": "manual delete",
        }
        defaults.update(kwargs)
        record = TrashRecord(**defaults)
        runtime.trash_repo.insert(record)
        return record

    def test_empty_when_nothing_trashed(self, server):
        result = _call_tool_sync(server, "list_trashed")
        data = _extract_payload(result)
        assert data == []

    def test_returns_inserted_trash(self, server, runtime):
        self._make_trash(runtime, original_path="/old/file.txt")
        result = _call_tool_sync(server, "list_trashed")
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["original_path"] == "/old/file.txt"
        assert data[0]["trashed_by"] == "user.cli"

    def test_filters_by_trashed_by(self, server, runtime):
        self._make_trash(runtime, trashed_by="user.cli", original_path="/u")
        self._make_trash(
            runtime, trashed_by="curator.cleanup", original_path="/c",
        )
        result = _call_tool_sync(
            server, "list_trashed", {"trashed_by": "curator.cleanup"},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["trashed_by"] == "curator.cleanup"

    def test_filters_by_source_id_client_side(self, server, runtime):
        self._make_trash(runtime, original_source_id="local", original_path="/a")
        self._make_trash(runtime, original_source_id="gdrive", original_path="/b")
        result = _call_tool_sync(
            server, "list_trashed", {"source_id": "gdrive"},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["original_source_id"] == "gdrive"


# ===========================================================================
# Tool 9: get_migration_status
# ===========================================================================


class TestGetMigrationStatus:
    def _make_job(self, runtime, **kwargs):
        from curator.models.migration import MigrationJob

        defaults = {
            "src_source_id": "local",
            "src_root": "/src",
            "dst_source_id": "local:vault",
            "dst_root": "/dst",
            "status": "completed",
            "files_total": 10,
            "files_copied": 8,
            "files_skipped": 1,
            "files_failed": 1,
            "bytes_copied": 1024,
        }
        defaults.update(kwargs)
        job = MigrationJob(**defaults)
        runtime.migration_job_repo.insert_job(job)
        return job

    def test_empty_when_no_jobs(self, server):
        result = _call_tool_sync(server, "get_migration_status")
        data = _extract_payload(result)
        assert data == []

    def test_lists_recent_jobs(self, server, runtime):
        self._make_job(runtime, src_root="/job1")
        self._make_job(runtime, src_root="/job2")
        result = _call_tool_sync(server, "get_migration_status")
        data = _extract_payload(result)
        assert len(data) == 2
        # Each job has the expected fields
        for j in data:
            assert "job_id" in j
            assert "files_total" in j
            assert "files_copied" in j
            assert "files_failed" in j

    def test_get_specific_job_by_id(self, server, runtime):
        job = self._make_job(runtime, src_root="/specific")
        self._make_job(runtime, src_root="/other")
        result = _call_tool_sync(
            server, "get_migration_status", {"job_id": str(job.job_id)},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["src_root"] == "/specific"

    def test_filters_by_status(self, server, runtime):
        self._make_job(runtime, status="completed", src_root="/done")
        self._make_job(runtime, status="failed", src_root="/oops")
        result = _call_tool_sync(
            server, "get_migration_status", {"status": "failed"},
        )
        data = _extract_payload(result)
        assert len(data) == 1
        assert data[0]["status"] == "failed"

    def test_empty_for_invalid_job_id(self, server):
        result = _call_tool_sync(
            server, "get_migration_status", {"job_id": "not-a-uuid"},
        )
        data = _extract_payload(result)
        assert data == []

    def test_empty_for_unknown_job_id(self, server):
        result = _call_tool_sync(
            server, "get_migration_status",
            {"job_id": "00000000-0000-0000-0000-000000000000"},
        )
        data = _extract_payload(result)
        assert data == []


# ===========================================================================
# v2.0.0-rc2: UTCDatetime wire-format regression coverage (Lesson #107)
#
# Naive datetimes stored in SQLite were serialized as
# ``'2026-05-10 09:08:10.516556'`` (space separator, no timezone offset),
# which the MCP layer's JSON Schema ``format: date-time`` validator
# rejects. The fix routes every datetime model field through
# ``UTCDatetime``, an Annotated[datetime, AfterValidator(_as_utc),
# PlainSerializer(_to_iso_utc_offset, when_used="json")]. This block
# pins both the validator's two branches AND the wire shape so the
# regression can't quietly return on a future refactor.
# ===========================================================================


class TestUTCDatetimeFieldType:
    """Pin the v2.0.0-rc2 datetime serialization fix.

    Validator: naive -> aware-UTC (no wall-clock shift); aware-non-UTC ->
    aware-UTC (converted); aware-UTC -> passthrough.

    Serializer: JSON output uses the explicit ``+00:00`` offset (never
    ``Z`` and never a missing offset). Regex-matched against RFC 3339
    so any future drift (e.g. pydantic's ``Z`` default leaking back)
    fails loudly.
    """

    # Matches the RFC 3339 subset Curator emits: T separator, microseconds
    # optional (isoformat omits them when zero), explicit +00:00 offset.
    _WIRE_RE = (
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$"
    )

    def test_as_utc_labels_naive_as_utc(self):
        from curator.mcp.tools import _as_utc

        naive = datetime(2026, 5, 10, 9, 8, 10, 516556)
        out = _as_utc(naive)
        assert out.tzinfo is timezone.utc
        # Wall clock unchanged
        assert out.replace(tzinfo=None) == naive

    def test_as_utc_converts_aware_non_utc(self):
        """Aware datetime in a non-UTC zone gets converted (wall clock shifts)."""
        from curator.mcp.tools import _as_utc

        plus_five = timezone(timedelta(hours=5))
        aware = datetime(2026, 5, 10, 14, 0, 0, tzinfo=plus_five)
        out = _as_utc(aware)
        assert out.tzinfo == timezone.utc
        # 14:00 +05:00 == 09:00 UTC
        assert out.hour == 9

    def test_as_utc_passes_through_aware_utc(self):
        from curator.mcp.tools import _as_utc

        aware = datetime(2026, 5, 10, 9, 0, 0, tzinfo=timezone.utc)
        out = _as_utc(aware)
        assert out == aware
        assert out.tzinfo == timezone.utc

    def test_naive_datetime_field_emits_offset_on_wire(self):
        """SourceInfo.created_at, fed a naive datetime, must JSON-dump
        with an explicit ``+00:00`` offset."""
        from curator.mcp.tools import SourceInfo

        naive = datetime(2026, 5, 10, 9, 8, 10, 516556)
        si = SourceInfo(
            source_id="local",
            source_type="local",
            display_name=None,
            enabled=True,
            created_at=naive,
        )
        # Validator side: stored value is aware UTC.
        assert si.created_at.tzinfo == timezone.utc
        # Wire side: JSON dump has +00:00 not Z, not bare.
        dumped = json.loads(si.model_dump_json())
        assert re.match(self._WIRE_RE, dumped["created_at"]), dumped["created_at"]
        assert "+00:00" in dumped["created_at"]

    def test_audit_event_occurred_at_wire_format(self):
        from curator.mcp.tools import AuditEvent

        e = AuditEvent(
            audit_id=1,
            occurred_at=datetime(2026, 5, 10, 9, 0, 0),  # naive
            actor="x",
            action="y",
            entity_type=None,
            entity_id=None,
            details={},
        )
        dumped = json.loads(e.model_dump_json())
        assert re.match(self._WIRE_RE, dumped["occurred_at"]), dumped["occurred_at"]

    def test_file_summary_mtime_wire_format(self):
        from curator.mcp.tools import FileSummary

        fs = FileSummary(
            file_id="abc",
            source_id="local",
            source_path="/x",
            size=1,
            mtime=datetime(2026, 5, 10, 9, 0, 0),  # naive
            xxhash3_128=None,
            extension=None,
            file_type=None,
        )
        dumped = json.loads(fs.model_dump_json())
        assert re.match(self._WIRE_RE, dumped["mtime"]), dumped["mtime"]

    def test_trashed_file_trashed_at_wire_format(self):
        from curator.mcp.tools import TrashedFile

        tf = TrashedFile(
            file_id="abc",
            original_source_id="local",
            original_path="/x",
            file_hash=None,
            trashed_at=datetime(2026, 5, 10, 9, 0, 0),  # naive
            trashed_by="user.cli",
            reason="manual",
        )
        dumped = json.loads(tf.model_dump_json())
        assert re.match(self._WIRE_RE, dumped["trashed_at"]), dumped["trashed_at"]

    def test_migration_job_optional_datetimes_wire_format(self):
        """started_at / completed_at are Optional. None stays None on the
        wire; a naive datetime emits the +00:00 form."""
        from curator.mcp.tools import MigrationJobInfo

        mj = MigrationJobInfo(
            job_id="abc",
            src_source_id="local",
            src_root="/s",
            dst_source_id="local",
            dst_root="/d",
            status="completed",
            started_at=datetime(2026, 5, 10, 9, 0, 0),  # naive
            completed_at=None,
            files_total=1,
            files_copied=1,
            files_skipped=0,
            files_failed=0,
            bytes_copied=0,
            error=None,
        )
        dumped = json.loads(mj.model_dump_json())
        assert re.match(self._WIRE_RE, dumped["started_at"]), dumped["started_at"]
        assert dumped["completed_at"] is None

    def test_end_to_end_list_sources_wire_format(self, server, runtime):
        """Integration-style: the actual list_sources tool output goes
        through pydantic's JSON path with a real source row. Catches
        regressions where the projection helpers stop using the model."""
        runtime.source_repo.upsert(SourceConfig(
            source_id="test:tz",
            source_type="test",
            display_name="tz check",
            enabled=True,
        ))
        # Walk through the call_tool path that real clients use; ensure
        # the structured payload's datetime fields satisfy our wire regex.
        result = _call_tool_sync(server, "list_sources")
        data = _extract_payload(result)
        # The runtime may inject extra sources; pick ours.
        ours = next(s for s in data if s["source_id"] == "test:tz")
        # The structured payload here is a Python dict; if the value got
        # serialized as a datetime object (older FastMCP) we still want
        # to verify the model would JSON-dump correctly. Re-dump via the
        # model to make this independent of FastMCP version.
        from curator.mcp.tools import SourceInfo
        roundtripped = SourceInfo(**ours).model_dump_json()
        wire = json.loads(roundtripped)
        assert re.match(self._WIRE_RE, wire["created_at"]), wire["created_at"]
