"""v1.2.0 read-only tool implementations for the Curator MCP server.

Tools are factory-registered via :func:`register_tools(mcp, runtime)`.
The runtime is captured by closure; tools are pure functions of
``(input args) -> Pydantic model``, querying the runtime's repos /
services without mutating state.

See ``Curator/docs/CURATOR_MCP_SERVER_DESIGN.md`` v0.2 for the per-tool
specification.

v1.2.0 implements 3 of 9 designed tools (P1 of the 3-session plan):

* :func:`health_check`     (DESIGN.md §4.3 #1)
* :func:`list_sources`     (DESIGN.md §4.3 #2)
* :func:`query_audit_log`  (DESIGN.md §4.3 #3)

The remaining 6 tools (``query_files``, ``inspect_file``, ``get_lineage``,
``find_duplicates``, ``list_trashed``, ``get_migration_status``) are
documented as P2 stubs at the bottom of this file. They will be
implemented in the next session per DESIGN.md §5 P2.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from curator.cli.runtime import CuratorRuntime


# ===========================================================================
# Return-shape Pydantic models
# ===========================================================================
#
# Each tool returns a Pydantic model (or list of models) so FastMCP can
# generate the JSON schema automatically. Field descriptions are written
# for an LLM reader -- terse, concrete, and self-explanatory.


class HealthStatus(BaseModel):
    """Curator MCP server health information."""

    status: str = Field(
        ...,
        description=(
            "'ok' if the DB is reachable AND at least one plugin is "
            "registered; 'degraded' otherwise."
        ),
    )
    curator_version: str = Field(
        ..., description="The Curator package version running this server.",
    )
    plugin_count: int = Field(
        ..., description="Number of registered Curator plugins.",
    )
    db_path: str = Field(
        ...,
        description=(
            "Filesystem path to the SQLite DB this server reads. "
            "May be 'unknown' if the runtime didn't expose a path."
        ),
    )


class SourceInfo(BaseModel):
    """One row in the response from list_sources."""

    source_id: str = Field(
        ...,
        description=(
            "The source's stable identifier. Examples: 'local' (single "
            "local FS source), 'gdrive:jake@example.com' (per-account "
            "Google Drive)."
        ),
    )
    source_type: str = Field(
        ...,
        description=(
            "The plugin source type. Examples: 'local', 'gdrive', "
            "'onedrive', 'dropbox'."
        ),
    )
    display_name: str | None = Field(
        None,
        description=(
            "Human-readable name. May be None if the source was "
            "registered without one."
        ),
    )
    enabled: bool = Field(
        ...,
        description=(
            "Whether this source is currently enabled. Disabled sources "
            "are excluded from new scans but their existing files remain "
            "in the index."
        ),
    )
    created_at: datetime = Field(
        ..., description="When this source was first registered.",
    )


class AuditEvent(BaseModel):
    """One row in the response from query_audit_log."""

    audit_id: int = Field(
        ..., description="Unique identifier for this audit event.",
    )
    occurred_at: datetime = Field(
        ..., description="When this event was logged (UTC).",
    )
    actor: str = Field(
        ...,
        description=(
            "The component that emitted this event. Convention: dotted "
            "names. Examples: 'curator.migrate' (core), "
            "'curatorplug.atrium_safety' (plugin)."
        ),
    )
    action: str = Field(
        ...,
        description=(
            "What happened. Convention: dotted verb-phrase. Examples: "
            "'migration.move', 'compliance.refused', "
            "'compliance.approved', 'trash.send'."
        ),
    )
    entity_type: str | None = Field(
        None,
        description=(
            "The type of entity this event is about. Examples: "
            "'file', 'migration_job'. May be None for events not "
            "tied to a specific entity."
        ),
    )
    entity_id: str | None = Field(
        None,
        description=(
            "The entity's identifier (typically a UUID for files, "
            "a string ID for migrations). May be None alongside entity_type."
        ),
    )
    details: dict[str, Any] = Field(
        ...,
        description=(
            "Structured event-specific data. Schema varies by action. "
            "Examples: for compliance.refused, includes 'phase' "
            "('decide' or 're-read'), 'mode' ('strict' or 'lax'), "
            "'reason'. For migration.move, includes src/dst paths, "
            "hashes, sizes."
        ),
    )


# ===========================================================================
# Tool registration factory
# ===========================================================================


def register_tools(mcp: "FastMCP", runtime: "CuratorRuntime") -> None:
    """Register all v1.2.0 read-only tools on the given FastMCP server.

    Tools close over ``runtime``; multiple servers with different
    runtimes can coexist (each call to register_tools binds a separate
    set of closures to a separate FastMCP instance). This is the
    pattern used by tests to bind a fresh runtime per test case.

    The 3 tools registered here (v1.2.0 P1) are:

    * ``health_check`` — server / DB / plugin sanity check
    * ``list_sources`` — list configured Curator sources
    * ``query_audit_log`` — query the audit log with filters

    Args:
        mcp: The FastMCP instance to register tools on.
        runtime: The CuratorRuntime providing repo/service access.
    """

    # ---------------------------------------------------------------------
    # Tool 1: health_check
    # ---------------------------------------------------------------------
    @mcp.tool()
    def health_check() -> HealthStatus:
        """Confirm the Curator MCP server is alive and able to read its DB.

        Use this to sanity-check connectivity and verify which Curator
        instance and DB this server is wired to. Safe to call freely;
        no state is mutated.

        Returns 'ok' status when the DB is reachable AND at least one
        plugin is registered; 'degraded' when either fails.
        """
        from curator import __version__

        # Plugin count: defensive against pluggy edge cases
        plugin_count = 0
        try:
            plugin_count = len(list(runtime.pm.list_name_plugin()))
        except Exception:
            pass

        # DB path: try several attribute names to be robust across
        # Curator versions / config shapes
        db_path = "unknown"
        try:
            if hasattr(runtime.db, "path"):
                db_path = str(runtime.db.path)
            elif hasattr(runtime.config, "db_path"):
                db_path = str(runtime.config.db_path)
        except Exception:
            pass

        # DB liveness: a trivial read against the audit_repo
        status = "ok"
        try:
            runtime.audit_repo.count()
        except Exception:
            status = "degraded"

        if plugin_count == 0:
            status = "degraded"

        return HealthStatus(
            status=status,
            curator_version=__version__,
            plugin_count=plugin_count,
            db_path=db_path,
        )

    # ---------------------------------------------------------------------
    # Tool 2: list_sources
    # ---------------------------------------------------------------------
    @mcp.tool()
    def list_sources() -> list[SourceInfo]:
        """List every source configured in this Curator instance.

        A "source" is a place files come from: local filesystem,
        Google Drive account, OneDrive account, Dropbox, etc. Each
        source has a stable source_id you can use to filter other
        tools (e.g. `query_files`, `query_audit_log`).

        Returns all sources, both enabled and disabled. Use the
        'enabled' field to filter client-side if needed. Empty list if
        no sources are configured.
        """
        sources = runtime.source_repo.list_all()
        return [
            SourceInfo(
                source_id=s.source_id,
                source_type=s.source_type,
                display_name=s.display_name,
                enabled=s.enabled,
                created_at=s.created_at,
            )
            for s in sources
        ]

    # ---------------------------------------------------------------------
    # Tool 3: query_audit_log
    # ---------------------------------------------------------------------
    @mcp.tool()
    def query_audit_log(
        actor: str | None = None,
        action: str | None = None,
        entity_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[AuditEvent]:
        """Query Curator's audit log for events matching the given filters.

        The audit log records every significant Curator operation:
        migrations, trashing, restoring, plugin enforcement decisions
        from atrium-safety (compliance.approved / .refused / .warned),
        and more. Filters are AND-combined; passing none returns the
        most recent events across all actors and actions.

        Common queries:

        - All atrium-safety enforcement decisions:
          ``actor='curatorplug.atrium_safety'``
        - Specifically compliance refusals:
          ``actor='curatorplug.atrium_safety', action='compliance.refused'``
        - All events for a specific file:
          ``entity_id='<file_id>'``
        - Recent migration activity:
          ``action='migration.move', limit=20``
        - Events since a given time:
          ``since=datetime(2026, 5, 1)`` (UTC)

        Args:
            actor: Filter by emitting component (exact match). Common
                values: 'curator.migrate', 'curatorplug.atrium_safety',
                'curator.trash'. Pass None to match all actors.
            action: Filter by action verb (exact match). Common values:
                'migration.move', 'compliance.refused', 'trash.send'.
                Pass None to match all actions.
            entity_id: Filter by the entity this event is about (exact
                match -- typically a file UUID). Pass None to match all.
            since: Only return events at or after this time (UTC). Pass
                None to start from the beginning of the audit log.
            limit: Maximum number of events to return. Default 50;
                capped at 1000.

        Returns:
            Up to ``limit`` events matching the filters, most recent
            first. Empty list if no events match.
        """
        if limit > 1000:
            limit = 1000
        if limit < 1:
            limit = 1

        entries = runtime.audit_repo.query(
            actor=actor,
            action=action,
            entity_id=entity_id,
            since=since,
            limit=limit,
        )
        return [
            AuditEvent(
                audit_id=e.audit_id,
                occurred_at=e.occurred_at,
                actor=e.actor,
                action=e.action,
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                details=e.details,
            )
            for e in entries
        ]

    # ---------------------------------------------------------------------
    # P2 stubs (NOT registered in v1.2.0)
    # ---------------------------------------------------------------------
    # The following 6 tools are intentionally NOT registered in v1.2.0.
    # They will be implemented in P2 of CURATOR_MCP_SERVER_DESIGN v0.2:
    #
    #   query_files            -- file_repo.search(...)
    #   inspect_file           -- file_repo.get + lineage + bundle
    #   get_lineage            -- lineage_repo.walk(file_id, max_depth)
    #   find_duplicates        -- file_repo.find_by_hash + grouping
    #   list_trashed           -- trash_repo.list(...)
    #   get_migration_status   -- migration_job_repo.get + summary
    #
    # See DESIGN.md §4.3 for each tool's input schema and return shape.
    # P2's task is to implement them following the same pattern as
    # health_check / list_sources / query_audit_log above:
    #   1. Define a Pydantic return model with LLM-targeted field docs
    #   2. Define an @mcp.tool()-decorated function with type hints + docstring
    #   3. Body queries runtime.<repo>.<method>(...) and maps to the model
    #   4. Add unit tests covering empty / single / multi cases
    #
    # Adding a tool to v1.2.0 P1 unintentionally is a regression -- the
    # P1 acceptance criterion is "exactly 3 tools, the 3 starter ones".
