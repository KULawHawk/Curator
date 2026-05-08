"""v1.2.0 read-only tool implementations for the Curator MCP server.

Tools are factory-registered via :func:`register_tools(mcp, runtime)`.
The runtime is captured by closure; tools are pure functions of
``(input args) -> Pydantic model``, querying the runtime's repos /
services without mutating state.

See ``Curator/docs/CURATOR_MCP_SERVER_DESIGN.md`` v0.2 for the per-tool
specification.

v1.2.0 implements all 9 designed tools (P1 shipped 3; P2 adds 6):

P1 (already in v1.2.0 release):
* :func:`health_check`        (DESIGN.md §4.3 #1)
* :func:`list_sources`        (DESIGN.md §4.3 #2)
* :func:`query_audit_log`     (DESIGN.md §4.3 #3)

P2 (this commit):
* :func:`query_files`         (DESIGN.md §4.3 #4)
* :func:`inspect_file`        (DESIGN.md §4.3 #5)
* :func:`get_lineage`         (DESIGN.md §4.3 #6)
* :func:`find_duplicates`     (DESIGN.md §4.3 #7)
* :func:`list_trashed`        (DESIGN.md §4.3 #8)
* :func:`get_migration_status` (DESIGN.md §4.3 #9)

Implementation notes (where the actual repo API differs from the v0.2
design's optimistic assumptions, captured here for future maintainers):

* ``file_repo.search(...)`` doesn't exist; the actual API is
  ``file_repo.query(FileQuery(...))``. ``query_files`` builds a FileQuery
  from the tool's flat params.
* ``file_repo.get_by_id(file_id)`` doesn't exist; the actual API is
  ``file_repo.get(curator_id: UUID)``. Tool params are strings (LLMs
  work with strings); we convert at the boundary.
* ``bundle_repo.get_memberships(file_id)`` doesn't exist; the actual
  API for "memberships for a file" is ``get_memberships_for_file``.
* ``lineage_repo.walk(...)`` doesn't exist; we BFS manually using
  ``get_edges_for`` to walk N hops.
* ``lineage_repo.get_neighbors(...)`` doesn't exist; replaced with
  ``get_edges_for`` (returns LineageEdge objects, not Files).
* ``trash_repo.list(...)`` takes ``actor`` (= ``trashed_by``), not
  ``source_id``. ``list_trashed`` exposes the actor filter and
  filters source_id client-side after fetch.
* ``migration_job_repo.get(...)`` is actually ``get_job(...)``;
  ``list_recent(...)`` is actually ``list_jobs(...)``.
* MigrationJob field names are ``files_copied/_skipped/_failed``,
  not ``moved_count/failed_count`` as the design abbreviated.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, Field

from curator.storage.queries import FileQuery

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from curator.cli.runtime import CuratorRuntime


# ===========================================================================
# Return-shape Pydantic models
# ===========================================================================


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

    audit_id: int = Field(..., description="Unique identifier for this audit event.")
    occurred_at: datetime = Field(..., description="When this event was logged (UTC).")
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
    entity_type: str | None = Field(None)
    entity_id: str | None = Field(None)
    details: dict[str, Any] = Field(
        ...,
        description=(
            "Structured event-specific data. Schema varies by action."
        ),
    )


class FileSummary(BaseModel):
    """One row in the response from query_files. Minimal file metadata
    suitable for LLM browsing; for full details call inspect_file."""

    file_id: str = Field(
        ...,
        description=(
            "The Curator-assigned stable identifier (UUID, hyphenated). "
            "Use this with `inspect_file`, `get_lineage`, or `find_duplicates`."
        ),
    )
    source_id: str = Field(..., description="Which source this file lives in.")
    source_path: str = Field(
        ..., description="Path within the source (NOT necessarily a local filesystem path).",
    )
    size: int = Field(..., description="File size in bytes.")
    mtime: datetime = Field(..., description="Last-modified time of the file.")
    xxhash3_128: str | None = Field(
        None,
        description=(
            "xxh3_128 fingerprint as hex. Identical files have identical "
            "hashes; use with `find_duplicates`. May be None if the file "
            "hasn't been hashed yet."
        ),
    )
    extension: str | None = Field(
        None,
        description="Lowercased extension including the dot, e.g. '.pdf'.",
    )
    file_type: str | None = Field(
        None, description="MIME type detected by Curator's file-type classifier.",
    )


class LineageEdgeInfo(BaseModel):
    """One edge in the lineage graph."""

    from_file_id: str = Field(..., description="Source file (UUID).")
    to_file_id: str = Field(..., description="Target file (UUID).")
    edge_kind: str = Field(
        ...,
        description=(
            "Relationship type. Examples: 'duplicate', 'derivative', "
            "'parent', 'thumbnail_of', 'compressed_from'."
        ),
    )
    confidence: float = Field(..., description="0.0-1.0 confidence in the relationship.")
    detected_by: str = Field(..., description="Plugin that detected this edge.")


class BundleMembershipInfo(BaseModel):
    """One bundle membership."""

    bundle_id: str = Field(..., description="The bundle's UUID.")
    role: str | None = Field(
        None,
        description="The file's role within the bundle, e.g. 'cover', 'main'.",
    )


class FileDetail(BaseModel):
    """Comprehensive file information returned by inspect_file."""

    file: FileSummary
    lineage_edges: list[LineageEdgeInfo] = Field(
        default_factory=list,
        description=(
            "All lineage edges where this file is either source or target."
        ),
    )
    bundles: list[BundleMembershipInfo] = Field(
        default_factory=list,
        description="Bundles this file is a member of.",
    )


class LineageGraph(BaseModel):
    """Multi-hop lineage walk result."""

    root_file_id: str = Field(
        ..., description="The file_id this walk started from.",
    )
    nodes: list[FileSummary] = Field(
        ...,
        description=(
            "All files reached during the walk, including the root. "
            "Deduplicated by file_id."
        ),
    )
    edges: list[LineageEdgeInfo] = Field(
        ...,
        description="All edges traversed during the walk, deduplicated.",
    )
    max_depth_reached: int = Field(
        ...,
        description=(
            "Actual depth reached. May be less than the requested "
            "max_depth if the graph has fewer hops."
        ),
    )


class DuplicateGroup(BaseModel):
    """A set of files sharing the same content hash."""

    xxhash3_128: str = Field(
        ..., description="The shared xxh3_128 fingerprint.",
    )
    files: list[FileSummary] = Field(
        ...,
        description=(
            "All files with this hash (excluding soft-deleted files by "
            "default). A 'group' may have just 1 file if you queried by "
            "an exact hash that has no duplicates."
        ),
    )


class TrashedFile(BaseModel):
    """One row in the response from list_trashed."""

    file_id: str = Field(..., description="The trashed file's curator_id.")
    original_source_id: str = Field(
        ..., description="Where the file lived before being trashed.",
    )
    original_path: str = Field(
        ..., description="The file's path within its original source.",
    )
    file_hash: str | None = Field(
        None, description="xxh3_128 of the file at trash time, if known.",
    )
    trashed_at: datetime = Field(..., description="When the trash operation occurred.")
    trashed_by: str = Field(
        ...,
        description=(
            "Who/what initiated the trash. Examples: 'user.cli' (manual), "
            "'curator.cleanup' (automatic), or a plugin actor name."
        ),
    )
    reason: str = Field(..., description="Human-readable reason given at trash time.")


class MigrationJobInfo(BaseModel):
    """One row in the response from get_migration_status."""

    job_id: str = Field(..., description="The migration job's UUID.")
    src_source_id: str
    src_root: str
    dst_source_id: str
    dst_root: str
    status: str = Field(
        ...,
        description=(
            "Current job state. Common values: 'queued', 'running', "
            "'completed', 'failed', 'cancelled'."
        ),
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None
    files_total: int = Field(..., description="Number of files planned for migration.")
    files_copied: int = Field(..., description="Number of files successfully migrated.")
    files_skipped: int = Field(..., description="Number of files skipped (already at dst, etc.).")
    files_failed: int = Field(
        ...,
        description=(
            "Number of files that failed migration. atrium-safety strict-mode "
            "refusals show up here."
        ),
    )
    bytes_copied: int = Field(..., description="Total bytes successfully transferred.")
    error: str | None = Field(
        None,
        description="Job-level error message if status='failed', otherwise None.",
    )


# ===========================================================================
# Helpers (file_id <-> UUID bridge for LLM-friendly string params)
# ===========================================================================


def _parse_file_id(file_id: str) -> UUID | None:
    """Parse a file_id string to UUID; returns None on invalid input.

    Tools accept file_id as a string (LLMs work with strings); the
    underlying repos take UUID. This helper bridges the gap and lets
    tools return cleanly-formed empty results on bad input rather than
    crashing.
    """
    try:
        return UUID(file_id)
    except (ValueError, AttributeError, TypeError):
        return None


def _file_to_summary(file) -> FileSummary:
    """Convert a FileEntity to its FileSummary projection."""
    return FileSummary(
        file_id=str(file.curator_id),
        source_id=file.source_id,
        source_path=file.source_path,
        size=file.size,
        mtime=file.mtime,
        xxhash3_128=file.xxhash3_128,
        extension=file.extension,
        file_type=file.file_type,
    )


def _edge_to_info(edge) -> LineageEdgeInfo:
    """Convert a LineageEdge to its LineageEdgeInfo projection."""
    return LineageEdgeInfo(
        from_file_id=str(edge.from_curator_id),
        to_file_id=str(edge.to_curator_id),
        edge_kind=edge.edge_kind.value if hasattr(edge.edge_kind, "value") else str(edge.edge_kind),
        confidence=edge.confidence,
        detected_by=edge.detected_by,
    )


# ===========================================================================
# Tool registration factory
# ===========================================================================


def register_tools(mcp: "FastMCP", runtime: "CuratorRuntime") -> None:
    """Register all 9 v1.2.0 read-only tools on the given FastMCP server.

    Tools close over ``runtime``; multiple servers with different
    runtimes can coexist (each call to register_tools binds a separate
    set of closures to a separate FastMCP instance).
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
        """
        from curator import __version__

        plugin_count = 0
        try:
            plugin_count = len(list(runtime.pm.list_name_plugin()))
        except Exception:
            pass

        db_path = "unknown"
        try:
            if hasattr(runtime.db, "path"):
                db_path = str(runtime.db.path)
            elif hasattr(runtime.config, "db_path"):
                db_path = str(runtime.config.db_path)
        except Exception:
            pass

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

        A "source" is a place files come from: local filesystem, Google
        Drive account, OneDrive, Dropbox, etc. Returns all sources,
        both enabled and disabled.
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
        and more. Filters are AND-combined.

        Common queries:

        - All atrium-safety enforcement decisions:
          ``actor='curatorplug.atrium_safety'``
        - Compliance refusals only:
          ``actor='curatorplug.atrium_safety', action='compliance.refused'``
        - All events for a specific file:
          ``entity_id='<file_id>'``
        - Recent migrations:
          ``action='migration.move', limit=20``
        """
        if limit > 1000:
            limit = 1000
        if limit < 1:
            limit = 1

        entries = runtime.audit_repo.query(
            actor=actor, action=action, entity_id=entity_id,
            since=since, limit=limit,
        )
        return [
            AuditEvent(
                audit_id=e.audit_id, occurred_at=e.occurred_at,
                actor=e.actor, action=e.action,
                entity_type=e.entity_type, entity_id=e.entity_id,
                details=e.details,
            )
            for e in entries
        ]

    # ---------------------------------------------------------------------
    # Tool 4: query_files (P2)
    # ---------------------------------------------------------------------
    @mcp.tool()
    def query_files(
        source_ids: list[str] | None = None,
        extensions: list[str] | None = None,
        path_starts_with: str | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
        limit: int = 50,
    ) -> list[FileSummary]:
        """Query the file index with simple filters.

        Filters are AND-combined; no filter is a "match all" wildcard.
        Returns up to ``limit`` files ordered by most-recently-seen.

        Common queries:

        - All PDFs from a specific source:
          ``source_ids=['local'], extensions=['.pdf']``
        - Files larger than 100MB:
          ``min_size=100_000_000``
        - Files under a specific path:
          ``source_ids=['local'], path_starts_with='/Users/jake/Projects'``
        - Multiple file types:
          ``extensions=['.docx', '.pdf', '.txt']``

        Args:
            source_ids: Filter by source (any of the listed). Pass None
                for all sources.
            extensions: Filter by extension (lowercase including dot,
                e.g. '.pdf'). Pass None for any extension.
            path_starts_with: Prefix-match on source_path. Useful for
                scoping to a directory. (Not a glob; exact prefix only.)
            min_size: Minimum file size in bytes (inclusive). Pass None
                for no minimum.
            max_size: Maximum file size in bytes (inclusive). Pass None
                for no maximum.
            limit: Max results. Default 50; capped at 1000.

        Returns:
            Up to ``limit`` FileSummary entries, most-recently-seen
            first. Empty list if nothing matches. Use file_id from any
            result with ``inspect_file``, ``get_lineage``, or
            ``find_duplicates`` for follow-up.
        """
        if limit > 1000:
            limit = 1000
        if limit < 1:
            limit = 1

        q = FileQuery(
            source_ids=source_ids,
            extensions=extensions,
            source_path_starts_with=path_starts_with,
            min_size=min_size,
            max_size=max_size,
            deleted=False,  # exclude soft-deleted by default
            limit=limit,
        )
        files = runtime.file_repo.query(q)
        return [_file_to_summary(f) for f in files]

    # ---------------------------------------------------------------------
    # Tool 5: inspect_file (P2)
    # ---------------------------------------------------------------------
    @mcp.tool()
    def inspect_file(file_id: str) -> FileDetail | None:
        """Get comprehensive metadata for a single file.

        Returns the file's basic metadata (path, size, hash, etc.)
        plus all lineage edges where it's source or target plus all
        bundle memberships. The single-call answer to "what does
        Curator know about this file?".

        Args:
            file_id: The file's curator_id (UUID, hyphenated). Get
                this from ``query_files``, ``find_duplicates``, or
                a previous ``query_audit_log`` event's entity_id.

        Returns:
            FileDetail with file + lineage_edges + bundles, or None
            if no file exists with that file_id (or if the file_id is
            malformed).
        """
        cid = _parse_file_id(file_id)
        if cid is None:
            return None

        file = runtime.file_repo.get(cid)
        if file is None:
            return None

        edges = runtime.lineage_repo.get_edges_for(cid)
        memberships = runtime.bundle_repo.get_memberships_for_file(cid)

        return FileDetail(
            file=_file_to_summary(file),
            lineage_edges=[_edge_to_info(e) for e in edges],
            bundles=[
                BundleMembershipInfo(
                    bundle_id=str(m.bundle_id),
                    role=getattr(m, "role", None),
                )
                for m in memberships
            ],
        )

    # ---------------------------------------------------------------------
    # Tool 6: get_lineage (P2)
    # ---------------------------------------------------------------------
    @mcp.tool()
    def get_lineage(file_id: str, max_depth: int = 1) -> LineageGraph | None:
        """Walk the lineage graph from a starting file.

        BFS to up to ``max_depth`` hops. Returns all reached files
        (nodes) plus all traversed edges (deduplicated). Useful for
        questions like "what's related to this file?" or "find the
        original ancestor of this derivative."

        Args:
            file_id: Starting file's curator_id (UUID).
            max_depth: Number of hops to walk. 1 = immediate
                neighbors; 2 = neighbors-of-neighbors. Default 1;
                capped at 5 to prevent runaway walks.

        Returns:
            LineageGraph with nodes (including the root) and edges,
            or None if the starting file_id is invalid / not found.
            Empty edges + single-node nodes if the file has no lineage
            relationships.
        """
        if max_depth < 1:
            max_depth = 1
        if max_depth > 5:
            max_depth = 5

        root_uuid = _parse_file_id(file_id)
        if root_uuid is None:
            return None

        root_file = runtime.file_repo.get(root_uuid)
        if root_file is None:
            return None

        # BFS: track visited file UUIDs and collected edges (by edge_id)
        visited_files: dict[UUID, Any] = {root_uuid: root_file}
        visited_edge_ids: set[UUID] = set()
        all_edges = []

        frontier = [root_uuid]
        depth_reached = 0
        for depth in range(max_depth):
            next_frontier: list[UUID] = []
            for cid in frontier:
                edges = runtime.lineage_repo.get_edges_for(cid)
                for edge in edges:
                    if edge.edge_id in visited_edge_ids:
                        continue
                    visited_edge_ids.add(edge.edge_id)
                    all_edges.append(edge)
                    # Walk to whichever side we haven't visited
                    other_cid = (
                        edge.to_curator_id
                        if edge.from_curator_id == cid
                        else edge.from_curator_id
                    )
                    if other_cid not in visited_files:
                        other_file = runtime.file_repo.get(other_cid)
                        if other_file is not None:
                            visited_files[other_cid] = other_file
                            next_frontier.append(other_cid)
            if not next_frontier:
                # No new nodes; walk converged before max_depth
                depth_reached = depth + 1
                break
            depth_reached = depth + 1
            frontier = next_frontier

        return LineageGraph(
            root_file_id=str(root_uuid),
            nodes=[_file_to_summary(f) for f in visited_files.values()],
            edges=[_edge_to_info(e) for e in all_edges],
            max_depth_reached=depth_reached,
        )

    # ---------------------------------------------------------------------
    # Tool 7: find_duplicates (P2)
    # ---------------------------------------------------------------------
    @mcp.tool()
    def find_duplicates(
        file_id: str | None = None,
        xxhash3_128: str | None = None,
    ) -> list[DuplicateGroup]:
        """Find files with identical content (matching xxh3_128 hash).

        Two ways to query:

        - ``file_id`` given: look up the file's hash, then return all
          files sharing that hash (including the original).
        - ``xxhash3_128`` given: directly return all files with that hash.

        Pass exactly one of these. Passing neither returns an empty
        list (use ``query_files`` for unfiltered browsing).

        Soft-deleted files are excluded.

        Args:
            file_id: A file's curator_id (UUID). Look up its hash, then
                find all files with that hash.
            xxhash3_128: A specific hash to search for (hex string).

        Returns:
            A list with 0 or 1 DuplicateGroup. Empty if no files match
            the hash; one group otherwise. The group's ``files`` list
            includes the original file (when querying by ``file_id``) so
            ``len(files) == 1`` means "no duplicates exist for this file."
        """
        target_hash: str | None = None
        if xxhash3_128:
            target_hash = xxhash3_128
        elif file_id:
            cid = _parse_file_id(file_id)
            if cid is None:
                return []
            file = runtime.file_repo.get(cid)
            if file is None or file.xxhash3_128 is None:
                return []
            target_hash = file.xxhash3_128

        if target_hash is None:
            return []  # neither input provided

        files = runtime.file_repo.find_by_hash(target_hash)
        if not files:
            return []
        return [DuplicateGroup(
            xxhash3_128=target_hash,
            files=[_file_to_summary(f) for f in files],
        )]

    # ---------------------------------------------------------------------
    # Tool 8: list_trashed (P2)
    # ---------------------------------------------------------------------
    @mcp.tool()
    def list_trashed(
        since: datetime | None = None,
        trashed_by: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
    ) -> list[TrashedFile]:
        """List files in Curator's trash registry, with optional filters.

        Args:
            since: Only return files trashed at or after this time (UTC).
                Pass None for the full history.
            trashed_by: Filter by who/what initiated the trash. Examples:
                'user.cli', 'curator.cleanup', or a plugin actor name.
            source_id: Filter by the file's original source. Applied
                client-side after fetch (the underlying repo doesn't
                support source filtering directly).
            limit: Max results. Default 50; capped at 1000.

        Returns:
            Up to ``limit`` TrashedFile entries, most-recently-trashed
            first. Empty list if nothing matches.
        """
        if limit > 1000:
            limit = 1000
        if limit < 1:
            limit = 1

        records = runtime.trash_repo.list(
            since=since, actor=trashed_by, limit=limit,
        )
        if source_id is not None:
            records = [r for r in records if r.original_source_id == source_id]

        return [
            TrashedFile(
                file_id=str(r.curator_id),
                original_source_id=r.original_source_id,
                original_path=r.original_path,
                file_hash=r.file_hash,
                trashed_at=r.trashed_at,
                trashed_by=r.trashed_by,
                reason=r.reason,
            )
            for r in records
        ]

    # ---------------------------------------------------------------------
    # Tool 9: get_migration_status (P2)
    # ---------------------------------------------------------------------
    @mcp.tool()
    def get_migration_status(
        job_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[MigrationJobInfo]:
        """Query Curator's migration jobs.

        Two modes:

        - ``job_id`` given: returns a single-item list with that job's
          full status (or empty list if not found / malformed).
        - ``job_id`` not given: returns recent jobs, optionally filtered
          by ``status`` (e.g. 'running', 'completed', 'failed').

        Args:
            job_id: Specific job UUID to look up. Returns just that one.
            status: Filter recent jobs by status. Common values:
                'queued', 'running', 'completed', 'failed', 'cancelled'.
                Ignored when ``job_id`` is given.
            limit: Max results when listing recent jobs. Default 20;
                capped at 200. Ignored when ``job_id`` is given.

        Returns:
            List of MigrationJobInfo, most-recent first. Single-item
            list when ``job_id`` is given.
        """
        if job_id:
            cid = _parse_file_id(job_id)
            if cid is None:
                return []
            job = runtime.migration_job_repo.get_job(cid)
            if job is None:
                return []
            return [_job_to_info(job)]

        if limit > 200:
            limit = 200
        if limit < 1:
            limit = 1

        jobs = runtime.migration_job_repo.list_jobs(status=status, limit=limit)
        return [_job_to_info(j) for j in jobs]


def _job_to_info(job) -> MigrationJobInfo:
    """Convert a MigrationJob to MigrationJobInfo."""
    return MigrationJobInfo(
        job_id=str(job.job_id),
        src_source_id=job.src_source_id,
        src_root=job.src_root,
        dst_source_id=job.dst_source_id,
        dst_root=job.dst_root,
        status=job.status,
        started_at=job.started_at,
        completed_at=job.completed_at,
        files_total=job.files_total,
        files_copied=job.files_copied,
        files_skipped=job.files_skipped,
        files_failed=job.files_failed,
        bytes_copied=job.bytes_copied,
        error=job.error,
    )
