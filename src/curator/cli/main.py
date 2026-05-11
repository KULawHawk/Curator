"""Curator CLI — Typer app + all commands.

DESIGN.md §11.

Single-file CLI: keeps the command tree easy to follow as a unit. The
runtime container (DB + plugins + services) is built in
:func:`callback` and stashed in ``ctx.obj``; commands pull what they
need from there.

Subcommand groups:

  * ``curator inspect <path>``               — file detail
  * ``curator scan <source> <root> [opts]``  — run a scan
  * ``curator group [--apply]``              — list/handle duplicate groups
  * ``curator lineage <id-or-path>``         — show edges for a file
  * ``curator bundles ...``                  — bundle subcommands
  * ``curator sources ...``                  — source registration & toggling
  * ``curator trash <id-or-path> [--apply]`` — send to OS trash
  * ``curator restore <id> [--apply]``       — restore from OS trash
  * ``curator audit [--since/--actor/...]``  — query audit log
  * ``curator watch [SOURCE]``               — watch local sources for FS events
  * ``curator safety check <path>``          — inspect a path for organize-safety concerns
  * ``curator organize <source>``            — plan-mode preview: bucket files by safety level
  * ``curator organize <source> --stage <dir>`` — actually move SAFE proposals into staging
  * ``curator organize <source> --apply``    — actually move SAFE proposals into target_root (final)
  * ``curator organize-revert <stage_dir>``  — undo a previous --stage or --apply operation
  * ``curator cleanup empty-dirs <root>``    — find empty directories (--apply removes)
  * ``curator cleanup broken-symlinks <root>`` — find broken symlinks (--apply unlinks)
  * ``curator cleanup junk <root>``          — find platform junk files (--apply trashes)
  * ``curator doctor``                       — integrity / health check

Global options live on the root callback: ``--config`` (path to
curator.toml), ``--db`` (override db path), ``-v`` / ``-q``
(verbosity), ``--json`` (machine-readable output), ``--no-color``.

Mutating commands (``trash``, ``restore``, ``group --apply``,
``sources remove --apply``) require explicit ``--apply``. Without it,
they print what *would* happen.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import UUID

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from curator import __version__
from curator.cli.runtime import CuratorRuntime, build_runtime
from curator.config import Config
from curator.models import LineageKind, SourceConfig
from curator.services import (
    ApplyOutcome as CleanupApplyOutcome,
    ApplyReport as CleanupApplyReport,
    CleanupKind,
    CleanupReport,
    NotInTrashError,
    OrganizePlan,
    RestoreImpossibleError,
    Send2TrashUnavailableError,
    StageOutcome,
    StageReport,
    TrashError,
    TrashVetoed,
)
from curator.storage.queries import FileQuery


# ---------------------------------------------------------------------------
# App + sub-apps
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="curator",
    help="Curator: organize, dedupe, and reason about files across sources.",
    no_args_is_help=True,
    add_completion=False,
)
bundles_app = typer.Typer(help="Bundle management.")
app.add_typer(bundles_app, name="bundles")
sources_app = typer.Typer(help="Source registration & toggling.")
app.add_typer(sources_app, name="sources")
safety_app = typer.Typer(help="Safety primitives for organize actions (Phase Gamma F1).")
app.add_typer(safety_app, name="safety")
status_app = typer.Typer(
    help="Asset classification (vital/active/provisional/junk) - v1.7.3 T-C02.",
)
app.add_typer(status_app, name="status")
gdrive_app = typer.Typer(
    help="Google Drive auth + per-alias credential management.",
    no_args_is_help=True,
)
app.add_typer(gdrive_app, name="gdrive")

# v1.5.0+: MCP server management (HTTP auth keys, etc.). Defined in a
# dedicated module per docs/CURATOR_MCP_HTTP_AUTH_DESIGN.md §4.1 to keep
# main.py from growing further.
from curator.cli.mcp_keys import mcp_app  # noqa: E402
app.add_typer(mcp_app, name="mcp")


def _version_callback(value: bool):
    if value:
        typer.echo(f"curator {__version__}")
        raise typer.Exit()


@app.callback()
def callback(
    ctx: typer.Context,
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c",
        help="Path to curator.toml (default: standard search order).",
        exists=False, dir_okay=False,
    ),
    db_path: Optional[Path] = typer.Option(
        None, "--db",
        help="Override DB path from config.",
        exists=False, dir_okay=False,
    ),
    verbose: int = typer.Option(
        0, "-v", "--verbose",
        count=True,
        help="Increase log verbosity (-v = DEBUG, -vv = TRACE).",
    ),
    quiet: bool = typer.Option(
        False, "-q", "--quiet",
        help="Suppress info-level output (only WARNING and above).",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Machine-readable output (JSON).",
    ),
    no_color: bool = typer.Option(
        False, "--no-color",
        help="Disable color output.",
    ),
    version: Optional[bool] = typer.Option(
        None, "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
):
    """Build the runtime container and stash it in ctx.obj."""
    config = Config.load(explicit_path=config_path)
    verbosity = -1 if quiet else verbose

    runtime = build_runtime(
        config=config,
        db_path_override=db_path,
        json_output=json_output,
        no_color=no_color,
        verbosity=verbosity,
    )
    ctx.obj = runtime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _console(rt: CuratorRuntime) -> Console:
    """Build a Rich console respecting ``--no-color``."""
    return Console(no_color=rt.no_color, soft_wrap=True)


def _err_console(rt: CuratorRuntime) -> Console:
    return Console(no_color=rt.no_color, stderr=True)


def _resolve_file(rt: CuratorRuntime, identifier: str):
    """Resolve a CLI identifier to a FileEntity.

    Accepts: a curator_id (UUID), an absolute path, or a substring of a path
    (if exactly one match).
    """
    # Try UUID first.
    try:
        cid = UUID(identifier)
        return rt.file_repo.get(cid)
    except ValueError:
        pass

    # Try absolute path against any source.
    sources = rt.source_repo.list_all()
    for src in sources:
        f = rt.file_repo.find_by_path(src.source_id, identifier)
        if f is not None:
            return f

    # Substring search via FileQuery (uses LIKE escaping).
    q = FileQuery(source_path_starts_with=identifier, limit=2)
    matches = rt.file_repo.query(q)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Ambiguous; the caller surfaces this.
        return None
    return None


def _emit_json(rt: CuratorRuntime, payload: object) -> None:
    """Write JSON to stdout for ``--json`` mode."""
    typer.echo(json.dumps(payload, default=str, indent=2))


def _err_exit(rt: CuratorRuntime, message: str, code: int = 1) -> "typer.Exit":
    """Print error to stderr and return a typer.Exit (caller raises it)."""
    _err_console(rt).print(f"[bold red]error[/]: {message}")
    return typer.Exit(code=code)


# ===========================================================================
# inspect
# ===========================================================================

@app.command()
def inspect(
    ctx: typer.Context,
    identifier: str = typer.Argument(..., help="curator_id, full path, or path prefix."),
):
    """Show full details for a file (incl. flex attrs and lineage edges)."""
    rt: CuratorRuntime = ctx.obj
    file = _resolve_file(rt, identifier)
    if file is None:
        raise _err_exit(rt, f"No file matches: {identifier!r}")

    edges = rt.lineage.get_edges_for(file.curator_id)
    bundles = rt.bundle_repo.get_memberships_for_file(file.curator_id)

    if rt.json_output:
        _emit_json(rt, {
            "curator_id": str(file.curator_id),
            "source_id": file.source_id,
            "source_path": file.source_path,
            "size": file.size,
            "mtime": file.mtime,
            "extension": file.extension,
            "file_type": file.file_type,
            "file_type_confidence": file.file_type_confidence,
            "xxhash3_128": file.xxhash3_128,
            "md5": file.md5,
            "fuzzy_hash": file.fuzzy_hash,
            "deleted_at": file.deleted_at,
            "flex": dict(file.flex),
            "lineage": [
                {
                    "edge_id": str(e.edge_id),
                    "kind": e.edge_kind.value,
                    "from": str(e.from_curator_id),
                    "to": str(e.to_curator_id),
                    "confidence": e.confidence,
                    "detected_by": e.detected_by,
                }
                for e in edges
            ],
            "bundles": [str(m.bundle_id) for m in bundles],
        })
        return

    console = _console(rt)
    console.print(f"[bold]{file.source_path}[/]")
    console.print(f"  [dim]curator_id[/] {file.curator_id}")
    console.print(f"  [dim]source[/]     {file.source_id}")
    console.print(f"  [dim]size[/]       {file.size:,} bytes")
    console.print(f"  [dim]mtime[/]      {file.mtime}")
    console.print(f"  [dim]extension[/]  {file.extension}")
    console.print(f"  [dim]file_type[/]  {file.file_type} (confidence {file.file_type_confidence:.2f})")
    if file.xxhash3_128:
        console.print(f"  [dim]xxhash3_128[/] {file.xxhash3_128}")
    if file.md5:
        console.print(f"  [dim]md5[/]        {file.md5}")
    if file.fuzzy_hash:
        console.print(f"  [dim]fuzzy[/]      {file.fuzzy_hash}")
    if file.deleted_at:
        console.print(f"  [bold yellow]deleted_at[/] {file.deleted_at}")
    if file.flex:
        console.print("  [bold]flex attrs[/]")
        for k, v in file.flex.items():
            console.print(f"    {k} = {v!r}")
    if edges:
        console.print(f"  [bold]lineage edges[/] ({len(edges)})")
        for e in edges:
            direction = "→" if e.from_curator_id == file.curator_id else "←"
            other = e.to_curator_id if e.from_curator_id == file.curator_id else e.from_curator_id
            console.print(
                f"    {direction} {e.edge_kind.value} ({e.confidence:.2f}, by {e.detected_by}) → {other}"
            )
    if bundles:
        console.print(f"  [bold]bundles[/] ({len(bundles)})")
        for m in bundles:
            b = rt.bundle_repo.get(m.bundle_id)
            console.print(f"    - {b.name if b else '?'} ({m.role})")


# ===========================================================================
# scan
# ===========================================================================

@app.command()
def scan(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Source ID (e.g. 'local')."),
    root: Path = typer.Argument(..., help="Root directory to scan."),
    ignore: list[str] = typer.Option(
        [], "--ignore", "-i",
        help="Glob patterns to skip. Repeatable.",
    ),
):
    """Scan a source root, hash files, detect lineage."""
    rt: CuratorRuntime = ctx.obj

    if not root.exists():
        raise _err_exit(rt, f"Path does not exist: {root}")

    options = {"ignore": ignore} if ignore else {}

    console = _console(rt)
    if not rt.json_output:
        console.print(f"Scanning [bold]{root}[/] (source={source_id})…")

    report = rt.scan.scan(
        source_id=source_id,
        root=str(root),
        options=options,
    )

    if rt.json_output:
        _emit_json(rt, {
            "job_id": str(report.job_id),
            "source_id": report.source_id,
            "root": report.root,
            "duration_seconds": report.duration_seconds,
            "files_seen": report.files_seen,
            "files_new": report.files_new,
            "files_updated": report.files_updated,
            "files_unchanged": report.files_unchanged,
            "files_hashed": report.files_hashed,
            "cache_hits": report.cache_hits,
            "bytes_read": report.bytes_read,
            "fuzzy_hashes_computed": report.fuzzy_hashes_computed,
            "classifications_assigned": report.classifications_assigned,
            "lineage_edges_created": report.lineage_edges_created,
            "errors": report.errors,
        })
        return

    table = Table(title=f"Scan complete in {report.duration_seconds:.2f}s", show_header=False)
    table.add_column("metric", style="dim")
    table.add_column("value", justify="right")
    table.add_row("files seen",       f"{report.files_seen:,}")
    table.add_row("  new",            f"{report.files_new:,}")
    table.add_row("  updated",        f"{report.files_updated:,}")
    table.add_row("  unchanged",      f"{report.files_unchanged:,}")
    table.add_row("files hashed",     f"{report.files_hashed:,}")
    table.add_row("cache hits",       f"{report.cache_hits:,}")
    table.add_row("bytes read",       f"{report.bytes_read:,}")
    table.add_row("classifications",  f"{report.classifications_assigned:,}")
    table.add_row("lineage edges",    f"{report.lineage_edges_created:,}")
    if report.errors:
        table.add_row("[bold red]errors", f"[bold red]{report.errors}")
    console.print(table)


# ===========================================================================
# group (find duplicate groups)
# ===========================================================================

@app.command()
def group(
    ctx: typer.Context,
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually trash non-primary members of each group.",
    ),
    keep: str = typer.Option(
        "oldest", "--keep",
        help="Strategy for selecting primary: oldest|newest|shortest_path|longest_path.",
    ),
):
    """Find groups of duplicate files (same xxhash) and optionally trash extras."""
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)

    # Collect duplicate edges → group by hash.
    edges = rt.lineage_repo.list_by_kind(LineageKind.DUPLICATE)
    groups: dict[str, set[UUID]] = {}
    for e in edges:
        # We don't have hash directly on the edge; resolve via files.
        f_from = rt.file_repo.get(e.from_curator_id)
        f_to = rt.file_repo.get(e.to_curator_id)
        if not f_from or not f_to or not f_from.xxhash3_128:
            continue
        h = f_from.xxhash3_128
        groups.setdefault(h, set()).update([e.from_curator_id, e.to_curator_id])

    if not groups:
        if rt.json_output:
            _emit_json(rt, {"groups": [], "would_trash": 0})
        else:
            console.print("[dim]No duplicate groups found.[/]")
        return

    # Resolve to FileEntity sets and pick primary per strategy.
    resolved_groups = []
    would_trash_total = 0
    for h, ids in groups.items():
        files = [rt.file_repo.get(i) for i in ids]
        files = [f for f in files if f is not None and not f.is_deleted]
        if len(files) < 2:
            continue
        primary = _pick_primary(files, keep)
        non_primary = [f for f in files if f.curator_id != primary.curator_id]
        would_trash_total += len(non_primary)
        resolved_groups.append((h, primary, non_primary))

    if rt.json_output:
        _emit_json(rt, {
            "groups": [
                {
                    "hash": h,
                    "primary": str(p.curator_id),
                    "primary_path": p.source_path,
                    "duplicates": [
                        {"curator_id": str(f.curator_id), "path": f.source_path}
                        for f in dups
                    ],
                }
                for h, p, dups in resolved_groups
            ],
            "would_trash": would_trash_total,
            "applied": apply,
        })
    else:
        for h, primary, dups in resolved_groups:
            console.print(f"[bold]hash[/] {h[:16]}… [dim]({len(dups) + 1} files)[/]")
            console.print(f"  [green]keep:[/]    {primary.source_path}")
            for f in dups:
                marker = "[red]trash:[/]" if apply else "[yellow]would trash:[/]"
                console.print(f"  {marker} {f.source_path}")
        console.print(
            f"\n{would_trash_total} duplicate file(s) "
            f"{'trashed' if apply else 'would be trashed'}."
        )

    if apply:
        for h, primary, dups in resolved_groups:
            for f in dups:
                try:
                    rt.trash.send_to_trash(
                        f.curator_id,
                        reason=f"duplicate of {primary.source_path}",
                        actor="cli.group",
                    )
                except (TrashError, Send2TrashUnavailableError) as e:
                    _err_console(rt).print(f"[red]✗[/] {f.source_path}: {e}")


def _pick_primary(files, strategy: str):
    """Pick the primary file from a duplicate group by strategy."""
    if strategy == "oldest":
        return min(files, key=lambda f: f.mtime)
    if strategy == "newest":
        return max(files, key=lambda f: f.mtime)
    if strategy == "shortest_path":
        return min(files, key=lambda f: len(f.source_path))
    if strategy == "longest_path":
        return max(files, key=lambda f: len(f.source_path))
    raise typer.BadParameter(f"unknown --keep strategy: {strategy!r}")


# ===========================================================================
# lineage
# ===========================================================================

@app.command()
def lineage(
    ctx: typer.Context,
    identifier: str = typer.Argument(..., help="curator_id or path."),
):
    """Show all lineage edges touching a file."""
    rt: CuratorRuntime = ctx.obj
    file = _resolve_file(rt, identifier)
    if file is None:
        raise _err_exit(rt, f"No file matches: {identifier!r}")

    edges = rt.lineage.get_edges_for(file.curator_id)

    if rt.json_output:
        _emit_json(rt, {
            "file": {
                "curator_id": str(file.curator_id),
                "source_path": file.source_path,
            },
            "edges": [
                {
                    "edge_id": str(e.edge_id),
                    "kind": e.edge_kind.value,
                    "from": str(e.from_curator_id),
                    "to": str(e.to_curator_id),
                    "confidence": e.confidence,
                    "detected_by": e.detected_by,
                    "notes": e.notes,
                }
                for e in edges
            ],
        })
        return

    console = _console(rt)
    if not edges:
        console.print(f"[dim]No lineage edges for {file.source_path}[/]")
        return

    table = Table(title=f"Lineage: {file.source_path}")
    table.add_column("kind")
    table.add_column("conf", justify="right")
    table.add_column("direction")
    table.add_column("other file")
    table.add_column("detector", style="dim")
    for e in edges:
        is_from = e.from_curator_id == file.curator_id
        other_id = e.to_curator_id if is_from else e.from_curator_id
        other = rt.file_repo.get(other_id)
        other_path = other.source_path if other else f"<gone:{other_id}>"
        table.add_row(
            e.edge_kind.value,
            f"{e.confidence:.2f}",
            "→" if is_from else "←",
            other_path,
            e.detected_by,
        )
    console.print(table)


# ===========================================================================
# bundles
# ===========================================================================

@bundles_app.command("list")
def bundles_list(ctx: typer.Context):
    """List all bundles."""
    rt: CuratorRuntime = ctx.obj
    items = rt.bundle.list_all()
    if rt.json_output:
        _emit_json(rt, [
            {
                "bundle_id": str(b.bundle_id),
                "name": b.name,
                "type": b.bundle_type,
                "members": rt.bundle.member_count(b.bundle_id),
                "confidence": b.confidence,
            }
            for b in items
        ])
        return
    console = _console(rt)
    if not items:
        console.print("[dim]No bundles.[/]")
        return
    table = Table(title=f"{len(items)} bundle(s)")
    table.add_column("id", style="dim")
    table.add_column("name")
    table.add_column("type")
    table.add_column("members", justify="right")
    table.add_column("conf", justify="right")
    for b in items:
        table.add_row(
            str(b.bundle_id)[:8],
            b.name or "(unnamed)",
            b.bundle_type,
            str(rt.bundle.member_count(b.bundle_id)),
            f"{b.confidence:.2f}",
        )
    console.print(table)


@bundles_app.command("show")
def bundles_show(
    ctx: typer.Context,
    bundle_id: str = typer.Argument(..., help="Bundle UUID (or 8-char prefix)."),
):
    """Show a bundle and its members."""
    rt: CuratorRuntime = ctx.obj
    bundle = _resolve_bundle(rt, bundle_id)
    if bundle is None:
        raise _err_exit(rt, f"No bundle matches: {bundle_id!r}")

    members = rt.bundle.raw_memberships(bundle.bundle_id)
    if rt.json_output:
        member_payload = []
        for m in members:
            f = rt.file_repo.get(m.curator_id)
            member_payload.append({
                "curator_id": str(m.curator_id),
                "role": m.role,
                "confidence": m.confidence,
                "path": f.source_path if f is not None else None,
            })
        _emit_json(rt, {
            "bundle_id": str(bundle.bundle_id),
            "name": bundle.name,
            "description": bundle.description,
            "type": bundle.bundle_type,
            "confidence": bundle.confidence,
            "members": member_payload,
        })
        return
    console = _console(rt)
    console.print(f"[bold]{bundle.name}[/] [dim]({bundle.bundle_id})[/]")
    if bundle.description:
        console.print(f"  {bundle.description}")
    console.print(f"  type: {bundle.bundle_type}, confidence: {bundle.confidence:.2f}")
    console.print(f"  [bold]members[/] ({len(members)}):")
    for m in members:
        f = rt.file_repo.get(m.curator_id)
        console.print(f"    [{m.role}] {f.source_path if f else '<missing>'}")


@bundles_app.command("create")
def bundles_create(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bundle name."),
    files: list[str] = typer.Argument(..., help="curator_ids or paths."),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    primary: Optional[str] = typer.Option(None, "--primary", help="Primary member id/path."),
):
    """Create a manual bundle from a list of files."""
    rt: CuratorRuntime = ctx.obj
    members = []
    for ident in files:
        f = _resolve_file(rt, ident)
        if f is None:
            raise _err_exit(rt, f"Couldn't resolve member: {ident!r}")
        members.append(f.curator_id)

    primary_id = None
    if primary is not None:
        f = _resolve_file(rt, primary)
        if f is None:
            raise _err_exit(rt, f"Couldn't resolve primary: {primary!r}")
        primary_id = f.curator_id

    bundle = rt.bundle.create_manual(
        name=name,
        member_ids=members,
        description=description,
        primary_id=primary_id,
    )
    rt.audit.log(
        actor="cli.bundles",
        action="create_manual",
        entity_type="bundle",
        entity_id=str(bundle.bundle_id),
        details={"name": name, "members": len(members)},
    )

    if rt.json_output:
        _emit_json(rt, {
            "bundle_id": str(bundle.bundle_id),
            "name": bundle.name,
            "members": len(members),
        })
    else:
        _console(rt).print(
            f"[green]✓[/] Created bundle [bold]{name}[/] "
            f"({bundle.bundle_id}) with {len(members)} member(s)."
        )


@bundles_app.command("dissolve")
def bundles_dissolve(
    ctx: typer.Context,
    bundle_id: str = typer.Argument(..., help="Bundle UUID."),
    apply: bool = typer.Option(False, "--apply", help="Actually delete the bundle."),
):
    """Delete a bundle (memberships removed; member files preserved)."""
    rt: CuratorRuntime = ctx.obj
    bundle = _resolve_bundle(rt, bundle_id)
    if bundle is None:
        raise _err_exit(rt, f"No bundle matches: {bundle_id!r}")

    n_members = rt.bundle.member_count(bundle.bundle_id)
    console = _console(rt)
    if not apply:
        console.print(
            f"[yellow]would dissolve[/] {bundle.name} "
            f"({n_members} member(s) would be detached, files kept). "
            f"Re-run with --apply."
        )
        return
    rt.bundle.dissolve(bundle.bundle_id)
    rt.audit.log(
        actor="cli.bundles",
        action="dissolve",
        entity_type="bundle",
        entity_id=str(bundle.bundle_id),
        details={"name": bundle.name, "members": n_members},
    )
    console.print(f"[green]✓[/] Dissolved bundle {bundle.name}.")


def _resolve_bundle(rt: CuratorRuntime, bundle_id: str):
    """Resolve a UUID or 8-char prefix to a BundleEntity."""
    try:
        return rt.bundle.get(UUID(bundle_id))
    except ValueError:
        # Prefix lookup
        all_bundles = rt.bundle.list_all()
        matches = [b for b in all_bundles if str(b.bundle_id).startswith(bundle_id)]
        if len(matches) == 1:
            return matches[0]
        return None


# ===========================================================================
# sources
# ===========================================================================

@sources_app.command("list")
def sources_list(ctx: typer.Context):
    """List all registered sources."""
    rt: CuratorRuntime = ctx.obj
    items = rt.source_repo.list_all()
    if rt.json_output:
        _emit_json(rt, [
            {
                "source_id": s.source_id,
                "source_type": s.source_type,
                "display_name": s.display_name,
                "enabled": s.enabled,
                "files": rt.file_repo.count(source_id=s.source_id),
                "config": s.config,
            }
            for s in items
        ])
        return
    console = _console(rt)
    if not items:
        console.print(
            "[dim]No sources registered. Use `curator sources add <id>` "
            "or just run a scan — the `local` source is auto-created.[/]"
        )
        return
    table = Table(title=f"{len(items)} source(s)")
    table.add_column("source_id")
    table.add_column("type")
    table.add_column("name", style="dim")
    table.add_column("status")
    table.add_column("files", justify="right")
    for s in items:
        n_files = rt.file_repo.count(source_id=s.source_id)
        status = "[green]enabled[/]" if s.enabled else "[red]disabled[/]"
        table.add_row(
            s.source_id,
            s.source_type,
            s.display_name or "",
            status,
            str(n_files),
        )
    console.print(table)


@sources_app.command("show")
def sources_show(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Source ID."),
):
    """Show one source's details (incl. config)."""
    rt: CuratorRuntime = ctx.obj
    src = rt.source_repo.get(source_id)
    if src is None:
        raise _err_exit(rt, f"No source with id: {source_id!r}")
    n_files = rt.file_repo.count(source_id=src.source_id)

    if rt.json_output:
        _emit_json(rt, {
            "source_id": src.source_id,
            "source_type": src.source_type,
            "display_name": src.display_name,
            "enabled": src.enabled,
            "files": n_files,
            "config": src.config,
            "created_at": src.created_at,
        })
        return

    console = _console(rt)
    console.print(f"[bold]{src.source_id}[/] [dim]({src.source_type})[/]")
    if src.display_name:
        console.print(f"  name:     {src.display_name}")
    status = "[green]enabled[/]" if src.enabled else "[red]disabled[/]"
    console.print(f"  status:   {status}")
    console.print(f"  files:    {n_files}")
    console.print(f"  created:  {src.created_at}")
    if src.config:
        console.print("  config:")
        for k, v in src.config.items():
            console.print(f"    {k} = {v!r}")


# ---------------------------------------------------------------------------
# sources config: per-source config mutation (v1.6.0)
# ---------------------------------------------------------------------------
# Bridges the v1.5.0 CLI gap that scripts/setup_gdrive_source.py worked
# around: ``sources add`` registers metadata only and offers no flag for
# the per-source config dict that cloud plugins (gdrive, future onedrive
# / dropbox) need. ``sources config <id> --set k=v`` fills that gap.
#
# Convention: --set values are parsed as JSON first (so booleans, ints,
# floats, lists work as expected), falling back to a literal string when
# JSON parsing fails. ``--set foo=true`` -> True; ``--set foo=hello`` ->
# "hello"; ``--set foo='[1,2,3]'`` -> [1, 2, 3].
#
# All operations route through ``source_repo.update`` which preserves the
# source row's other columns. Audit events are emitted for traceability.

def _parse_set_value(raw: str) -> Any:
    """Parse the right-hand side of ``--set KEY=VALUE``.

    Tries JSON first (catches booleans, ints, floats, null, lists, dicts);
    falls back to literal string. This matches the schema flexibility the
    config dict needs (gdrive's ``include_shared`` is a bool, paths are
    strings, future plugins might use lists).
    """
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


@sources_app.command("config")
def sources_config(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Source ID to configure."),
    set_pairs: list[str] = typer.Option(
        [],
        "--set",
        help=(
            "KEY=VALUE pair to set. Repeatable. Values are parsed as JSON "
            "if possible (so 'true', '42', '[1,2]' work), else used as a "
            "literal string. Example: --set root_folder_id=1abc..."
        ),
    ),
    unset_keys: list[str] = typer.Option(
        [],
        "--unset",
        help="Config key to remove. Repeatable. Silently no-ops if the key isn't present.",
    ),
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Remove ALL config keys. Applied AFTER --unset and BEFORE --set in the same invocation.",
    ),
):
    """View or mutate a source's per-plugin config dict (v1.6.0).

    With no flags: prints the current config (read-only; equivalent to
    the ``config:`` section of ``sources show``).

    With --set / --unset / --clear: mutates the SourceConfig.config
    dict and persists via ``source_repo.update``. Operations within a
    single invocation apply in order: --unset first, then --clear if
    given, then --set. This lets you reset-and-rewrite atomically:

        curator sources config gdrive:src_drive --clear \\
            --set client_secrets_path=/new/path \\
            --set credentials_path=/new/creds \\
            --set root_folder_id=1abc...

    Replaces the helper-script workaround used in v1.5.0
    (scripts/setup_gdrive_source.py) for cloud-source registration.
    """
    rt: CuratorRuntime = ctx.obj
    src = rt.source_repo.get(source_id)
    if src is None:
        raise _err_exit(rt, f"No source with id: {source_id!r}")

    # Read-only path: no mutation flags given
    if not set_pairs and not unset_keys and not clear:
        if rt.json_output:
            _emit_json(rt, {"source_id": source_id, "config": src.config})
            return
        console = _console(rt)
        console.print(f"[bold]{src.source_id}[/] config:")
        if not src.config:
            console.print("  [dim](empty)[/]")
            return
        for k, v in src.config.items():
            console.print(f"  {k} = {v!r}")
        return

    # Mutation path: build the new config dict, then update.
    new_config = dict(src.config)  # shallow copy so we don't mutate in place
    changes: list[tuple[str, str, Any]] = []  # (op, key, value) for audit

    # 1. --unset (remove specific keys, silent if absent)
    for k in unset_keys:
        if k in new_config:
            old = new_config.pop(k)
            changes.append(("unset", k, old))

    # 2. --clear (remove everything)
    if clear:
        if new_config:
            changes.append(("clear", "*", dict(new_config)))
        new_config = {}

    # 3. --set (apply each KEY=VALUE)
    for pair in set_pairs:
        if "=" not in pair:
            raise _err_exit(
                rt,
                f"--set value {pair!r} must be in KEY=VALUE form (no '=' found)",
            )
        key, _, raw_value = pair.partition("=")
        key = key.strip()
        if not key:
            raise _err_exit(
                rt, f"--set value {pair!r} has empty key",
            )
        value = _parse_set_value(raw_value)
        new_config[key] = value
        changes.append(("set", key, value))

    if not changes:
        # Nothing to do (e.g., --unset a key that wasn't there)
        if rt.json_output:
            _emit_json(rt, {
                "source_id": source_id,
                "action": "no-op",
                "config": new_config,
            })
        else:
            _console(rt).print("[dim]No changes to apply.[/]")
        return

    # Persist via update() -- preserves other source fields
    updated = SourceConfig(
        source_id=src.source_id,
        source_type=src.source_type,
        display_name=src.display_name,
        config=new_config,
        enabled=src.enabled,
        created_at=src.created_at,
    )
    rt.source_repo.update(updated)
    rt.audit.log(
        actor="cli.sources",
        action="source.config",
        entity_type="source",
        entity_id=source_id,
        details={
            "changes": [
                {"op": op, "key": k} for (op, k, _v) in changes
            ],
            "config_keys_after": sorted(new_config.keys()),
        },
    )

    if rt.json_output:
        _emit_json(rt, {
            "source_id": source_id,
            "action": "updated",
            "changes": [
                {"op": op, "key": k} for (op, k, _v) in changes
            ],
            "config": new_config,
        })
        return

    console = _console(rt)
    console.print(f"[green]\u2713[/] Updated config for [bold]{source_id}[/]:")
    for op, key, value in changes:
        if op == "set":
            console.print(f"  [cyan]set[/]   {key} = {value!r}")
        elif op == "unset":
            console.print(f"  [yellow]unset[/] {key}")
        elif op == "clear":
            console.print(f"  [red]clear[/] (removed {len(value)} key(s))")


@sources_app.command("add")
def sources_add(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Unique ID for the source (e.g. 'work_drive')."),
    source_type: str = typer.Option("local", "--type", "-t", help="Source plugin type (e.g. 'local')."),
    display_name: Optional[str] = typer.Option(None, "--name", "-n", help="Friendly name."),
    disabled: bool = typer.Option(False, "--disabled", help="Create disabled (default: enabled)."),
):
    """Register a new source.

    Phase Alpha sources are 'local' (filesystem). Phase Beta will add
    cloud source plugins (gdrive, onedrive, dropbox).
    """
    rt: CuratorRuntime = ctx.obj
    if rt.source_repo.get(source_id) is not None:
        raise _err_exit(rt, f"Source already exists: {source_id!r}")

    src = SourceConfig(
        source_id=source_id,
        source_type=source_type,
        display_name=display_name,
        enabled=not disabled,
    )
    rt.source_repo.insert(src)
    rt.audit.log(
        actor="cli.sources",
        action="source.add",
        entity_type="source",
        entity_id=source_id,
        details={"type": source_type, "enabled": not disabled},
    )

    if rt.json_output:
        _emit_json(rt, {"source_id": source_id, "added": True})
    else:
        _console(rt).print(
            f"[green]✓[/] Added source [bold]{source_id}[/] (type={source_type}"
            f"{', disabled' if disabled else ''})."
        )


@sources_app.command("enable")
def sources_enable(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Source ID."),
):
    """Enable a source (so scans + lineage detection use it)."""
    rt: CuratorRuntime = ctx.obj
    src = rt.source_repo.get(source_id)
    if src is None:
        raise _err_exit(rt, f"No source with id: {source_id!r}")
    if src.enabled:
        _console(rt).print(f"[dim]{source_id} is already enabled.[/]")
        return
    rt.source_repo.set_enabled(source_id, True)
    rt.audit.log(
        actor="cli.sources",
        action="source.enable",
        entity_type="source",
        entity_id=source_id,
    )
    _console(rt).print(f"[green]✓[/] Enabled {source_id}.")


@sources_app.command("disable")
def sources_disable(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Source ID."),
):
    """Disable a source (existing files preserved; new scans will skip)."""
    rt: CuratorRuntime = ctx.obj
    src = rt.source_repo.get(source_id)
    if src is None:
        raise _err_exit(rt, f"No source with id: {source_id!r}")
    if not src.enabled:
        _console(rt).print(f"[dim]{source_id} is already disabled.[/]")
        return
    rt.source_repo.set_enabled(source_id, False)
    rt.audit.log(
        actor="cli.sources",
        action="source.disable",
        entity_type="source",
        entity_id=source_id,
    )
    _console(rt).print(f"[yellow]✓[/] Disabled {source_id}.")


@sources_app.command("remove")
def sources_remove(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Source ID."),
    apply: bool = typer.Option(False, "--apply", help="Actually delete the source."),
):
    """Delete a source. Fails if any files still reference it (FK RESTRICT)."""
    rt: CuratorRuntime = ctx.obj
    src = rt.source_repo.get(source_id)
    if src is None:
        raise _err_exit(rt, f"No source with id: {source_id!r}")
    n_files = rt.file_repo.count(source_id=source_id)

    console = _console(rt)
    if not apply:
        if n_files > 0:
            console.print(
                f"[yellow]cannot remove[/] {source_id}: "
                f"{n_files} file(s) still reference this source. "
                f"Trash or scan-delete those first."
            )
        else:
            console.print(
                f"[yellow]would remove[/] {source_id} ({src.source_type}). "
                f"Re-run with --apply."
            )
        return

    if n_files > 0:
        raise _err_exit(
            rt,
            f"Cannot remove {source_id}: {n_files} file(s) still reference it. "
            "Trash or scan-delete those first.",
        )

    rt.source_repo.delete(source_id)
    rt.audit.log(
        actor="cli.sources",
        action="source.remove",
        entity_type="source",
        entity_id=source_id,
    )
    console.print(f"[green]✓[/] Removed source {source_id}.")


# ===========================================================================
# trash + restore
# ===========================================================================

@app.command()
def trash(
    ctx: typer.Context,
    identifier: str = typer.Argument(..., help="curator_id or path."),
    reason: str = typer.Option("manual", "--reason", "-r"),
    apply: bool = typer.Option(False, "--apply", help="Actually send to OS trash."),
):
    """Send a file to the OS trash (snapshots metadata for restore)."""
    rt: CuratorRuntime = ctx.obj
    file = _resolve_file(rt, identifier)
    if file is None:
        raise _err_exit(rt, f"No file matches: {identifier!r}")

    console = _console(rt)
    if not apply:
        console.print(
            f"[yellow]would trash[/] {file.source_path}\n"
            f"  reason: {reason}\n"
            f"  Re-run with --apply."
        )
        return

    try:
        record = rt.trash.send_to_trash(file.curator_id, reason=reason, actor="cli.trash")
    except Send2TrashUnavailableError as e:
        raise _err_exit(rt, str(e))
    except TrashVetoed as e:
        raise _err_exit(rt, str(e))
    except TrashError as e:
        raise _err_exit(rt, str(e))

    if rt.json_output:
        _emit_json(rt, {
            "trashed": str(record.curator_id),
            "original_path": record.original_path,
            "reason": record.reason,
        })
    else:
        console.print(f"[green]✓[/] Trashed {record.original_path}")


@app.command()
def restore(
    ctx: typer.Context,
    identifier: str = typer.Argument(..., help="curator_id of trashed file."),
    target: Optional[Path] = typer.Option(None, "--to", help="Restore to a different path."),
    apply: bool = typer.Option(False, "--apply", help="Actually attempt restore."),
):
    """Restore a previously-trashed file."""
    rt: CuratorRuntime = ctx.obj

    try:
        cid = UUID(identifier)
    except ValueError:
        raise _err_exit(rt, f"restore takes a curator_id UUID, got: {identifier!r}")

    record = rt.trash_repo.get(cid)
    if record is None:
        raise _err_exit(rt, f"No trash record for {cid}")

    console = _console(rt)
    if not apply:
        target_path = str(target) if target else (
            record.restore_path_override or record.original_path
        )
        console.print(
            f"[yellow]would restore[/] {record.original_path}\n"
            f"  to: {target_path}\n"
            f"  Re-run with --apply."
        )
        return

    try:
        f = rt.trash.restore(
            cid,
            target_path=str(target) if target else None,
            actor="cli.restore",
        )
    except NotInTrashError as e:
        raise _err_exit(rt, str(e))
    except RestoreImpossibleError as e:
        raise _err_exit(rt, str(e), code=2)  # different code: not "user error"
    except TrashError as e:
        raise _err_exit(rt, str(e))

    if rt.json_output:
        _emit_json(rt, {"restored": str(f.curator_id), "path": f.source_path})
    else:
        console.print(f"[green]✓[/] Restored to {f.source_path}")


# ===========================================================================
# audit
# ===========================================================================

@app.command()
def audit(
    ctx: typer.Context,
    since_hours: Optional[int] = typer.Option(
        None, "--since-hours",
        help="Show only entries from the last N hours.",
    ),
    actor: Optional[str] = typer.Option(None, "--actor"),
    action: Optional[str] = typer.Option(None, "--action"),
    limit: int = typer.Option(50, "--limit", "-n"),
):
    """Query the audit log."""
    rt: CuratorRuntime = ctx.obj
    since = (
        datetime.utcnow() - timedelta(hours=since_hours)
        if since_hours else None
    )
    entries = rt.audit_repo.query(
        since=since, actor=actor, action=action, limit=limit,
    )

    if rt.json_output:
        _emit_json(rt, [
            {
                "audit_id": e.audit_id,
                "occurred_at": e.occurred_at,
                "actor": e.actor,
                "action": e.action,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "details": e.details,
            }
            for e in entries
        ])
        return

    console = _console(rt)
    if not entries:
        console.print("[dim]No matching audit entries.[/]")
        return
    table = Table(title=f"{len(entries)} audit entr{'y' if len(entries) == 1 else 'ies'}")
    table.add_column("id", style="dim", justify="right")
    table.add_column("when", style="dim")
    table.add_column("actor")
    table.add_column("action", style="bold")
    table.add_column("entity")
    for e in entries:
        ent = ""
        if e.entity_type and e.entity_id:
            ent = f"{e.entity_type}:{e.entity_id[:8]}…"
        table.add_row(
            str(e.audit_id),
            e.occurred_at.strftime("%Y-%m-%d %H:%M:%S") if e.occurred_at else "?",
            e.actor,
            e.action,
            ent,
        )
    console.print(table)


# ===========================================================================
# watch
# ===========================================================================

@app.command()
def watch(
    ctx: typer.Context,
    source: Optional[str] = typer.Argument(
        None,
        help="Source id to watch (default: all enabled local sources).",
    ),
    debounce_ms: int = typer.Option(
        1000, "--debounce-ms",
        help="Coalescing window for repeated events on the same path/kind.",
    ),
    apply: bool = typer.Option(
        False, "--apply",
        help="v0.17: run an incremental scan_paths() on each event "
             "(otherwise: just print events; no DB writes).",
    ),
):
    """Watch local source roots for filesystem events. Blocks until Ctrl+C.

    Phase Beta gate #3 / Tier 6:
      v0.16 emits one line per event (ADDED / MODIFIED / DELETED).
      v0.17 adds ``--apply`` so each event triggers an incremental
      ``ScanService.scan_paths`` call, keeping the index live.
    ``--json`` produces JSON-lines output suitable for piping.
    """
    rt: CuratorRuntime = ctx.obj
    err = _err_console(rt)

    try:
        from curator.services.watch import (
            NoLocalSourcesError,
            WatchService,
            WatchUnavailableError,
        )
    except ImportError as e:
        err.print(f"[red]watch service unavailable: {e}[/]")
        raise typer.Exit(code=2)

    service = WatchService(rt.source_repo, debounce_ms=debounce_ms)
    source_ids = [source] if source else None

    try:
        events = service.watch(source_ids=source_ids)
    except WatchUnavailableError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    except NoLocalSourcesError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=1)

    if not rt.json_output:
        console = _console(rt)
        mode = "with --apply (incremental scan)" if apply else "(events only)"
        console.print(
            f"[dim]Watching {len(service)} root(s) {mode}. Press Ctrl+C to stop.[/]"
        )

    try:
        for change in events:
            if rt.json_output:
                # JSON-lines: one event per line, no envelope.
                typer.echo(json.dumps(change.to_dict()))
            else:
                # Human-readable: timestamp + colored kind + path.
                kind_style = {
                    "added": "green",
                    "modified": "yellow",
                    "deleted": "red",
                }.get(change.kind.value, "white")
                ts = change.detected_at.strftime("%H:%M:%S")
                console = _console(rt)
                console.print(
                    f"[dim]{ts}[/] [{kind_style}]{change.kind.value:<8}[/] "
                    f"[bold]{change.source_id}[/] {change.path}"
                )

            # v0.17: optionally run an incremental scan on this single path.
            if apply:
                try:
                    report = rt.scan.scan_paths(
                        source_id=change.source_id,
                        paths=[str(change.path)],
                    )
                    if not rt.json_output:
                        suffix = []
                        if report.files_new:
                            suffix.append(f"new={report.files_new}")
                        if report.files_updated:
                            suffix.append(f"updated={report.files_updated}")
                        if report.files_deleted:
                            suffix.append(f"deleted={report.files_deleted}")
                        if report.lineage_edges_created:
                            suffix.append(f"edges={report.lineage_edges_created}")
                        if report.errors:
                            suffix.append(f"[red]errors={report.errors}[/]")
                        if suffix:
                            _console(rt).print(
                                f"  [dim]→ scan_paths: {' '.join(suffix)}[/]"
                            )
                except Exception as e:
                    err.print(f"[red]scan_paths failed: {e}[/]")
                    # Don't exit — keep watching.
    except KeyboardInterrupt:
        if not rt.json_output:
            err.print("\n[dim]watch ended (Ctrl+C)[/]")
        return


# ===========================================================================
# doctor
# ===========================================================================

@app.command()
def doctor(ctx: typer.Context):
    """Run integrity / health checks against the index and the environment."""
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)
    issues: list[str] = []

    # Config + DB paths
    console.print("[bold]Curator doctor[/]")
    console.print(f"  config:    {rt.config.source_path or '(defaults)'}")
    console.print(f"  db:        {rt.db.db_path}")
    console.print(f"  log:       {rt.config.log_path}")

    # Plugin manager
    plugins = list(rt.pm.list_name_plugin())
    console.print(f"  plugins:   {len(plugins)} registered")
    for name, _ in plugins:
        console.print(f"    - {name}")

    # Optional deps. Order matches runtime import order in services
    # (vendored first, then PyPI fallback) so doctor reports the path
    # the code is actually using.
    try:
        from curator._vendored import ppdeep  # noqa: F401
        console.print("  ppdeep:    [green]vendored[/]")
    except ImportError:
        try:
            import ppdeep  # noqa: F401
            console.print("  ppdeep:    [green]installed[/] (PyPI)")
        except ImportError:
            console.print("  ppdeep:    [yellow]missing[/] (fuzzy hashing disabled)")

    try:
        from curator._vendored import send2trash  # noqa: F401
        console.print("  send2trash: [green]vendored[/]")
    except ImportError:
        try:
            import send2trash  # noqa: F401
            console.print("  send2trash: [green]installed[/] (PyPI)")
        except ImportError:
            console.print("  send2trash: [red]missing[/] (trash will refuse)")
            issues.append("send2trash not available")

    # Index stats
    n_files = rt.file_repo.count()
    n_files_total = rt.file_repo.count(include_deleted=True)
    n_bundles = len(rt.bundle.list_all())
    n_trashed = rt.trash_repo.count()
    n_audit = rt.audit_repo.count()
    n_cache = rt.cache_repo.count()

    console.print()
    console.print("[bold]Index stats[/]")
    console.print(f"  files:     {n_files} active ({n_files_total} including deleted)")
    console.print(f"  bundles:   {n_bundles}")
    console.print(f"  trashed:   {n_trashed}")
    console.print(f"  audit log: {n_audit} entries")
    console.print(f"  hash cache:{n_cache} entries")

    # Sources
    sources = rt.source_repo.list_all()
    if sources:
        console.print("\n[bold]Sources[/]")
        for s in sources:
            files_in_source = rt.file_repo.count(source_id=s.source_id)
            tag = "[green]enabled[/]" if s.enabled else "[red]disabled[/]"
            console.print(f"  {s.source_id:20} {tag}  ({files_in_source} files)")

    if issues:
        console.print()
        console.print(f"[bold red]{len(issues)} issue(s) found:[/]")
        for i in issues:
            console.print(f"  - {i}")
        raise typer.Exit(code=1)
    console.print("\n[green]✓[/] No issues detected.")


# ---------------------------------------------------------------------------
# safety subcommands (Phase Gamma F1 — Milestone Gamma-1)
# ---------------------------------------------------------------------------

@safety_app.command("check")
def safety_check(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="File or directory to check."),
    check_handles: bool = typer.Option(
        False, "--handles", help="Also scan running processes for open handles (slow).",
    ),
):
    """Check whether a path is safe to auto-organize.

    Outputs the safety level (SAFE / CAUTION / REFUSE) plus all detected
    concerns. ``--handles`` enables the slow open-file scan via psutil.
    Use this command before any destructive action; the future organize
    command will gate on it.
    """
    rt: CuratorRuntime = ctx.obj
    if not path.exists():
        _err_console(rt).print(f"[red]Path does not exist: {path}[/]")
        raise typer.Exit(code=1)

    report = rt.safety.check_path(path, check_handles=check_handles)

    if rt.json_output:
        out = {
            "path": report.path,
            "level": report.level.value,
            "concerns": [
                {"kind": c.value, "detail": d} for c, d in report.concerns
            ],
            "holders": list(report.holders),
            "project_root": report.project_root,
        }
        typer.echo(json.dumps(out, indent=2))
        return

    console = _console(rt)
    level_style = {
        "safe": "green",
        "caution": "yellow",
        "refuse": "red",
    }[report.level.value]
    console.print(f"\n[bold]{report.path}[/]")
    console.print(f"verdict: [{level_style}]{report.level.value.upper()}[/]")
    if report.project_root:
        console.print(f"project root: [cyan]{report.project_root}[/]")
    if report.concerns:
        console.print("concerns:")
        for kind, detail in report.concerns:
            console.print(f"  - [yellow]{kind.value}[/]: {detail}")
    if report.holders:
        console.print("open by:")
        for h in report.holders:
            console.print(f"  - [magenta]{h}[/]")
    if report.is_safe:
        console.print("[green]✓[/] safe to organize")


@safety_app.command("paths")
def safety_paths(ctx: typer.Context):
    """List the app-data + OS-managed path registries on this platform.

    Useful for sanity-checking what SafetyService would refuse vs caution
    against, and for debugging when the user thinks something is wrongly
    flagged.
    """
    rt: CuratorRuntime = ctx.obj

    if rt.json_output:
        out = {
            "app_data": [str(p) for p in rt.safety.app_data],
            "os_managed": [str(p) for p in rt.safety.os_managed],
            "platform": sys.platform,
        }
        typer.echo(json.dumps(out, indent=2))
        return

    console = _console(rt)
    console.print("\n[bold]App-data paths[/] (CAUTION-level):")
    if rt.safety.app_data:
        for p in rt.safety.app_data:
            console.print(f"  [yellow]•[/] {p}")
    else:
        console.print("  [dim](none)[/]")

    console.print("\n[bold]OS-managed paths[/] (REFUSE-level):")
    if rt.safety.os_managed:
        for p in rt.safety.os_managed:
            console.print(f"  [red]•[/] {p}")
    else:
        console.print("  [dim](none)[/]")


# ---------------------------------------------------------------------------
# organize command (Phase Gamma F1 — plan mode)
# ---------------------------------------------------------------------------

@app.command()
def organize(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source id to plan an organize for."),
    root: Optional[str] = typer.Option(
        None, "--root",
        help="Restrict to files whose source_path starts with this prefix.",
    ),
    organize_type: Optional[str] = typer.Option(
        None, "--type",
        help="Type-specific pipeline: 'music' (extracts ID3/Vorbis tags + "
             "proposes Artist/Album/NN-Title destinations), 'photo' "
             "(extracts EXIF date + proposes YYYY/YYYY-MM-DD destinations), "
             "'document' (extracts PDF/DOCX metadata + proposes "
             "YYYY/YYYY-MM destinations), or 'code' (detects VCS-marked "
             "projects and proposes Language/ProjectName/relpath layout). "
             "Requires --target.",
    ),
    target: Optional[Path] = typer.Option(
        None, "--target",
        help="Target root for organized files (required with --type).",
    ),
    check_handles: bool = typer.Option(
        False, "--handles",
        help="Scan running processes for open handles (slow; F1 safety check).",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit",
        help="Max files to evaluate. Useful for previewing on large sources.",
    ),
    show_files: bool = typer.Option(
        False, "--show-files",
        help="Print individual file paths in each bucket (long output).",
    ),
    stage: Optional[Path] = typer.Option(
        None, "--stage",
        help="Actually move SAFE-bucket files with proposals into <dir>. "
             "Writes a manifest so `curator organize-revert` can undo. "
             "Requires --type and --target.",
    ),
    apply_mode: bool = typer.Option(
        False, "--apply",
        help="Move SAFE-bucket files with proposals to their FINAL "
             "destinations under --target. Equivalent to "
             "--stage <target> but with audit entries tagged 'apply' "
             "to mark this as a final move. Mutually exclusive with --stage. "
             "Requires --type and --target.",
    ),
    enrich_mb: bool = typer.Option(
        False, "--enrich-mb",
        help="v0.32: for music mode, look up missing album/year/track on "
             "MusicBrainz for files where the filename heuristic produced "
             "only artist+title. Network calls happen at MB's 1 req/sec rate "
             "limit — expect ~1 second per untagged file. Requires "
             "musicbrainzngs to be installed (pip install 'curator[organize]').",
    ),
    mb_contact: Optional[str] = typer.Option(
        None, "--mb-contact",
        help="Required when --enrich-mb is used. An email or URL identifying "
             "who to contact about the request. Per MusicBrainz TOS.",
    ),
):
    """Plan-mode preview of an organize operation.

    Walks the indexed files for ``source``, runs each through SafetyService,
    and prints a bucketed summary: how many files are SAFE to organize,
    how many are CAUTION (with reason breakdown), and how many are REFUSE.

    With ``--type music --target <dir>``: SAFE-bucket audio files get a
    proposed destination path under ``<dir>/Artist/Album/NN - Title.ext``
    via :class:`MusicService` tag extraction (requires ``mutagen``).

    With ``--type photo --target <dir>``: SAFE-bucket photo files get a
    proposed destination path under ``<dir>/YYYY/YYYY-MM-DD/<filename>``
    via :class:`PhotoService` EXIF extraction (requires ``Pillow``).

    With ``--type document --target <dir>``: SAFE-bucket document files
    get a proposed destination path under ``<dir>/YYYY/YYYY-MM/<filename>``
    via :class:`DocumentService` PDF/DOCX metadata + filename-pattern
    extraction (requires ``pypdf`` for PDFs).

    Phase Gamma scope: NO MOVES happen — this is a planning preview
    only. Future versions add ``--stage`` (move SAFE files to a staging
    folder) and ``--apply`` (with type-specific organization templates).

    The user should run ``curator scan <source> <root>`` first to
    populate the index. ``curator organize`` operates on what's already
    indexed; it doesn't walk the filesystem.
    """
    rt: CuratorRuntime = ctx.obj

    if organize_type is not None and target is None:
        _err_console(rt).print(
            "[red]--type requires --target <dir>[/]"
        )
        raise typer.Exit(code=2)

    if organize_type is not None and organize_type not in ("music", "photo", "document", "code"):
        _err_console(rt).print(
            f"[red]Unknown --type {organize_type!r}. "
            "Currently supported: 'music', 'photo', 'document', 'code'.[/]"
        )
        raise typer.Exit(code=2)

    if stage is not None and (organize_type is None or target is None):
        _err_console(rt).print(
            "[red]--stage requires --type and --target[/]"
        )
        raise typer.Exit(code=2)

    if apply_mode and (organize_type is None or target is None):
        _err_console(rt).print(
            "[red]--apply requires --type and --target[/]"
        )
        raise typer.Exit(code=2)

    if stage is not None and apply_mode:
        _err_console(rt).print(
            "[red]--stage and --apply are mutually exclusive[/]"
        )
        raise typer.Exit(code=2)

    # v0.32: --enrich-mb wires a MusicBrainzClient into OrganizeService.
    if enrich_mb:
        if organize_type != "music":
            _err_console(rt).print(
                "[red]--enrich-mb only applies with --type music[/]"
            )
            raise typer.Exit(code=2)
        if not mb_contact:
            _err_console(rt).print(
                "[red]--enrich-mb requires --mb-contact <email-or-url> "
                "(MusicBrainz TOS requires a contact in the User-Agent)[/]"
            )
            raise typer.Exit(code=2)
        try:
            from curator.services.musicbrainz import (
                MusicBrainzClient,
                _musicbrainzngs_available,
            )
        except ImportError as e:
            _err_console(rt).print(
                f"[red]MusicBrainz client unavailable: {e}[/]"
            )
            raise typer.Exit(code=2)
        if not _musicbrainzngs_available():
            _err_console(rt).print(
                "[red]musicbrainzngs is not installed. "
                "Install with: pip install 'curator[organize]'[/]"
            )
            raise typer.Exit(code=2)
        # Build the client + attach to the runtime's OrganizeService
        # for the duration of this invocation.
        rt.organize.mb_client = MusicBrainzClient(contact=mb_contact)

    plan = rt.organize.plan(
        source_id=source,
        root_prefix=root,
        check_handles=check_handles,
        limit=limit,
        organize_type=organize_type,
        target_root=target,
        enrich_mb=enrich_mb,
    )

    # Stage mode: actually move SAFE-bucket files with proposals into <stage>.
    stage_report: "StageReport | None" = None
    move_mode: str | None = None  # "stage" or "apply" for renderer wording
    if stage is not None:
        try:
            stage_report = rt.organize.stage(plan, stage_root=stage)
            move_mode = "stage"
        except ValueError as e:
            _err_console(rt).print(f"[red]stage failed: {e}[/]")
            raise typer.Exit(code=2)
    elif apply_mode:
        try:
            stage_report = rt.organize.apply(plan)
            move_mode = "apply"
        except ValueError as e:
            _err_console(rt).print(f"[red]apply failed: {e}[/]")
            raise typer.Exit(code=2)

    if rt.json_output:
        out = _organize_plan_to_dict(plan, include_files=show_files)
        if stage_report is not None:
            d = _stage_report_to_dict(stage_report)
            d["mode"] = move_mode
            out["stage"] = d
        typer.echo(json.dumps(out, indent=2, default=str))
        return

    _render_organize_plan(rt, plan, show_files=show_files)
    if stage_report is not None:
        _render_stage_report(rt, stage_report, mode=move_mode or "stage")


def _organize_plan_to_dict(plan: "OrganizePlan", *, include_files: bool) -> dict:
    """Convert an :class:`OrganizePlan` to a JSON-serializable dict."""
    def bucket_dict(b):
        d = {
            "count": b.count,
            "total_size": b.total_size,
            "by_concern": {
                concern.value: len(files)
                for concern, files in b.by_concern.items()
            },
        }
        if include_files:
            d["files"] = [
                {
                    "curator_id": str(f.curator_id),
                    "path": f.source_path,
                    "size": f.size,
                    "proposed_destination": b.proposals.get(str(f.curator_id)),
                }
                for f in b.files
            ]
        # Include proposals summary even without --show-files so
        # programmatic consumers can see at a glance how many
        # destinations were proposed.
        if b.proposals:
            d["proposals_count"] = len(b.proposals)
        return d

    return {
        "source_id": plan.source_id,
        "root_prefix": plan.root_prefix,
        "started_at": plan.started_at.isoformat() if plan.started_at else None,
        "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
        "duration_seconds": plan.duration_seconds,
        "total_files": plan.total_files,
        "total_size": plan.total_size,
        "safe": bucket_dict(plan.safe),
        "caution": bucket_dict(plan.caution),
        "refuse": bucket_dict(plan.refuse),
    }


def _render_organize_plan(rt: CuratorRuntime, plan, *, show_files: bool) -> None:
    """Pretty-print an :class:`OrganizePlan` to the console."""
    console = _console(rt)

    def fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    scope = f" under {plan.root_prefix!r}" if plan.root_prefix else ""
    console.print(
        f"\n[bold]Organize plan[/] for source [cyan]{plan.source_id}[/]{scope}"
    )
    console.print(
        f"Scanned: {plan.total_files} files "
        f"({fmt_size(plan.total_size)}) in {plan.duration_seconds:.2f}s\n"
    )

    # REFUSE bucket
    if plan.refuse.count:
        console.print(
            f"[red]REFUSE[/]:  {plan.refuse.count} files "
            f"({fmt_size(plan.refuse.total_size)}) — hard-blocked"
        )
        for concern, n in plan.refuse.concern_counts().items():
            console.print(f"    [red]•[/] {concern.value}: {n}")
        if show_files:
            for f in plan.refuse.files:
                console.print(f"      {f.source_path}")
        console.print()

    # CAUTION bucket
    if plan.caution.count:
        console.print(
            f"[yellow]CAUTION[/]: {plan.caution.count} files "
            f"({fmt_size(plan.caution.total_size)}) — review before moving"
        )
        for concern, n in plan.caution.concern_counts().items():
            console.print(f"    [yellow]•[/] {concern.value}: {n}")
        if show_files:
            for f in plan.caution.files:
                console.print(f"      {f.source_path}")
        console.print()

    # SAFE bucket
    if plan.safe.count:
        console.print(
            f"[green]SAFE[/]:    {plan.safe.count} files "
            f"({fmt_size(plan.safe.total_size)}) — eligible to organize"
        )
        if plan.safe.proposals:
            console.print(
                f"    [green]•[/] {len(plan.safe.proposals)} destinations proposed"
            )
        if show_files:
            for f in plan.safe.files:
                dest = plan.safe.proposals.get(str(f.curator_id))
                if dest:
                    console.print(f"      {f.source_path}")
                    console.print(f"        [dim]-> {dest}[/]")
                else:
                    console.print(f"      {f.source_path}")
        console.print()

    if plan.total_files == 0:
        console.print(
            "[dim]No files indexed for this source. "
            "Run [bold]curator scan[/bold] first.[/]"
        )
    else:
        console.print(
            "[dim]This is a plan preview. Phase Gamma adds "
            "--stage / --apply / --type for actual moves.[/]"
        )


# ---------------------------------------------------------------------------
# Stage helpers + organize-revert (Phase Gamma F2 — v0.22)
# ---------------------------------------------------------------------------


def _stage_report_to_dict(stage_report: "StageReport") -> dict:
    """Convert a :class:`StageReport` to a JSON-serializable dict."""
    return {
        "stage_root": stage_report.stage_root,
        "started_at": stage_report.started_at.isoformat() if stage_report.started_at else None,
        "completed_at": stage_report.completed_at.isoformat() if stage_report.completed_at else None,
        "duration_seconds": stage_report.duration_seconds,
        "moved_count": stage_report.moved_count,
        "skipped_count": stage_report.skipped_count,
        "failed_count": stage_report.failed_count,
        "moves": [
            {
                "curator_id": m.curator_id,
                "original": m.original,
                "staged": m.staged,
                "outcome": m.outcome.value,
                "error": m.error,
            }
            for m in stage_report.moves
        ],
    }


def _render_stage_report(
    rt: CuratorRuntime, stage_report, *, mode: str = "stage"
) -> None:
    """Pretty-print a :class:`StageReport` after the plan summary.

    The ``mode`` parameter ("stage" or "apply") only affects the
    section heading + the trailing hint text. The data shown is
    identical because the on-disk shape is identical.
    """
    console = _console(rt)
    heading = "Stage" if mode == "stage" else "Apply"
    console.print(
        f"\n[bold]{heading}[/]: "
        f"[green]moved={stage_report.moved_count}[/] "
        f"[yellow]skipped={stage_report.skipped_count}[/] "
        f"[red]failed={stage_report.failed_count}[/] "
        f"in {stage_report.duration_seconds or 0.0:.2f}s"
    )
    console.print(f"    -> {stage_report.stage_root}")

    if stage_report.failed_count:
        console.print("\n[red]Failures:[/]")
        for m in stage_report.moves:
            if m.outcome == StageOutcome.FAILED:
                console.print(f"  [red]X[/] {m.original}")
                if m.error:
                    console.print(f"      [dim]{m.error}[/]")

    if stage_report.skipped_count:
        # Show a few skipped reasons but cap the list to avoid noise.
        console.print("\n[yellow]Skipped:[/]")
        shown = 0
        for m in stage_report.moves:
            if m.outcome.value.startswith("skipped"):
                console.print(
                    f"  [yellow]·[/] {m.original} "
                    f"[dim]({m.outcome.value})[/]"
                )
                shown += 1
                if shown >= 5:
                    remaining = stage_report.skipped_count - shown
                    if remaining > 0:
                        console.print(f"  [dim]… and {remaining} more[/]")
                    break

    if stage_report.moved_count > 0:
        if mode == "apply":
            console.print(
                "\n[dim]Files moved to their final destinations.[/]\n"
                "  - run [bold]curator organize-revert[/bold] "
                f"[cyan]{stage_report.stage_root}[/] [dim]to undo[/]"
            )
        else:
            console.print(
                "\n[dim]Review the staged tree, then either:[/]\n"
                "  - move the contents into your library manually, or\n"
                "  - run [bold]curator organize-revert[/bold] "
                f"[cyan]{stage_report.stage_root}[/] [dim]to undo[/]"
            )


@app.command(name="organize-revert")
def organize_revert(
    ctx: typer.Context,
    stage_dir: Path = typer.Argument(
        ...,
        help="Stage directory previously created by `curator organize --stage`.",
    ),
):
    """Undo a previous `curator organize --stage` operation.

    Reads the manifest at ``<stage_dir>/.curator_stage_manifest.json``
    and moves every staged file back to its original location. Files
    whose originals are now occupied are left in staging and reported.
    Files missing from staging are reported as already-reverted.

    The manifest is updated in-place: successfully restored entries
    are removed; the manifest file itself is deleted when empty.
    """
    rt: CuratorRuntime = ctx.obj

    try:
        report = rt.organize.revert_stage(stage_dir)
    except FileNotFoundError as e:
        _err_console(rt).print(f"[red]{e}[/]")
        raise typer.Exit(code=1)
    except RuntimeError as e:
        _err_console(rt).print(f"[red]revert failed: {e}[/]")
        raise typer.Exit(code=2)

    if rt.json_output:
        out = {
            "stage_root": report.stage_root,
            "started_at": report.started_at.isoformat() if report.started_at else None,
            "completed_at": report.completed_at.isoformat() if report.completed_at else None,
            "duration_seconds": report.duration_seconds,
            "restored_count": report.restored_count,
            "skipped_count": report.skipped_count,
            "failed_count": report.failed_count,
            "moves": [
                {
                    "curator_id": m.curator_id,
                    "original": m.original,
                    "staged": m.staged,
                    "outcome": m.outcome.value,
                    "error": m.error,
                }
                for m in report.moves
            ],
        }
        typer.echo(json.dumps(out, indent=2, default=str))
        return

    console = _console(rt)
    console.print(
        f"\n[bold]Revert[/]: "
        f"[green]restored={report.restored_count}[/] "
        f"[yellow]skipped={report.skipped_count}[/] "
        f"[red]failed={report.failed_count}[/] "
        f"in {report.duration_seconds or 0.0:.2f}s"
    )
    console.print(f"    <- {report.stage_root}")

    for m in report.moves:
        if m.outcome.value.startswith("skipped") or m.outcome.value == "failed":
            console.print(
                f"  [yellow]·[/] {m.original} [dim]({m.outcome.value})[/]"
            )
            if m.error:
                console.print(f"      [dim]{m.error}[/]")


# ---------------------------------------------------------------------------
# cleanup subcommands (Phase Gamma F6 — v0.25)
# ---------------------------------------------------------------------------

cleanup_app = typer.Typer(
    name="cleanup",
    help="Find + remove empty dirs, broken symlinks, and junk files.",
    no_args_is_help=True,
)
app.add_typer(cleanup_app, name="cleanup")


def _render_cleanup_report(
    rt: CuratorRuntime, report: CleanupReport
) -> None:
    """Pretty-print a :class:`CleanupReport` (plan-mode output)."""
    console = _console(rt)

    def fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    # Duplicates need grouped rendering because users care about WHICH
    # files in each duplicate set are kept vs. removed.
    if report.kind == CleanupKind.DUPLICATE_FILE:
        _render_duplicate_report(rt, report, fmt_size)
        return

    label = report.kind.value.replace("_", " ")
    console.print(
        f"\n[bold]Cleanup ({label})[/] under [cyan]{report.root}[/]"
    )
    console.print(
        f"Found: {report.count} "
        f"({fmt_size(report.total_size)}) "
        f"in {report.duration_seconds or 0.0:.2f}s\n"
    )

    for f in report.findings[:20]:
        extra = ""
        if f.kind == CleanupKind.JUNK_FILE:
            pat = f.details.get("matched_pattern", "")
            extra = f" [dim]({pat}, {fmt_size(f.size)})[/]"
        elif f.kind == CleanupKind.BROKEN_SYMLINK:
            tgt = f.details.get("target")
            if tgt:
                extra = f" [dim]-> {tgt}[/]"
        elif f.kind == CleanupKind.EMPTY_DIR:
            junks = f.details.get("system_junk_present", []) or []
            if junks:
                extra = f" [dim](contains: {', '.join(junks)})[/]"
        console.print(f"  [yellow]·[/] {f.path}{extra}")

    if report.count > 20:
        console.print(f"  [dim]… and {report.count - 20} more[/]")

    if report.errors:
        console.print(f"\n[red]Errors during walk: {len(report.errors)}[/]")
        for e in report.errors[:5]:
            console.print(f"  [red]·[/] {e}")

    if report.count == 0:
        console.print("[green]Nothing to clean up.[/]")
    else:
        console.print(
            "\n[dim]This is a plan preview. Add [bold]--apply[/bold] "
            "to actually delete.[/]"
        )


def _render_cleanup_apply(
    rt: CuratorRuntime, apply_report: "CleanupApplyReport"
) -> None:
    """Pretty-print an :class:`ApplyReport` from cleanup.apply."""
    console = _console(rt)
    console.print(
        f"\n[bold]Cleanup apply[/]: "
        f"[green]deleted={apply_report.deleted_count}[/] "
        f"[yellow]skipped={apply_report.skipped_count}[/] "
        f"[red]failed={apply_report.failed_count}[/] "
        f"in {apply_report.duration_seconds or 0.0:.2f}s"
    )

    if apply_report.failed_count or apply_report.skipped_count:
        for r in apply_report.results:
            if r.outcome == CleanupApplyOutcome.DELETED:
                continue
            console.print(
                f"  [yellow]·[/] {r.finding.path} "
                f"[dim]({r.outcome.value})[/]"
            )
            if r.error:
                console.print(f"      [dim]{r.error}[/]")


def _cleanup_report_to_dict(report: CleanupReport) -> dict:
    return {
        "kind": report.kind.value,
        "root": report.root,
        "count": report.count,
        "total_size": report.total_size,
        "duration_seconds": report.duration_seconds,
        "errors": report.errors,
        "findings": [
            {
                "path": f.path,
                "kind": f.kind.value,
                "size": f.size,
                "details": f.details,
            }
            for f in report.findings
        ],
    }


def _cleanup_apply_to_dict(apply_report: "CleanupApplyReport") -> dict:
    return {
        "kind": apply_report.kind.value,
        "deleted_count": apply_report.deleted_count,
        "skipped_count": apply_report.skipped_count,
        "failed_count": apply_report.failed_count,
        "duration_seconds": apply_report.duration_seconds,
        "results": [
            {
                "path": r.finding.path,
                "outcome": r.outcome.value,
                "error": r.error,
            }
            for r in apply_report.results
        ],
    }


def _run_cleanup(
    rt: CuratorRuntime,
    *,
    report_factory,
    apply_flag: bool,
    use_trash: bool = True,
) -> None:
    """Shared driver for the three cleanup subcommands.

    Builds the :class:`CleanupReport` via ``report_factory()``, prints
    it (or its JSON), and optionally runs apply.
    """
    report = report_factory()
    apply_report = None
    if apply_flag:
        apply_report = rt.cleanup.apply(report, use_trash=use_trash)

    if rt.json_output:
        out = {"plan": _cleanup_report_to_dict(report)}
        if apply_report is not None:
            out["apply"] = _cleanup_apply_to_dict(apply_report)
        typer.echo(json.dumps(out, indent=2, default=str))
        return

    _render_cleanup_report(rt, report)
    if apply_report is not None:
        _render_cleanup_apply(rt, apply_report)


@cleanup_app.command("empty-dirs")
def cleanup_empty_dirs(
    ctx: typer.Context,
    root: Path = typer.Argument(..., help="Directory to walk."),
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually rmdir the empty directories. Default is plan-only.",
    ),
    strict: bool = typer.Option(
        False, "--strict",
        help="Require zero entries (don't ignore Thumbs.db / .DS_Store etc.).",
    ),
):
    """Find empty directories under ``root``.

    By default, directories that contain only system-junk files
    (Thumbs.db, .DS_Store, desktop.ini, etc.) are also flagged as
    effectively empty. Pass ``--strict`` for a zero-entry-only rule.

    With ``--apply``, junk files inside are unlinked first, then the
    directory is rmdir'd. The walk is bottom-up so directories that
    become empty after their children are removed are also caught.
    """
    rt: CuratorRuntime = ctx.obj
    _run_cleanup(
        rt,
        report_factory=lambda: rt.cleanup.find_empty_dirs(
            root, ignore_system_junk=not strict,
        ),
        apply_flag=apply,
    )


@cleanup_app.command("broken-symlinks")
def cleanup_broken_symlinks(
    ctx: typer.Context,
    root: Path = typer.Argument(..., help="Directory to walk."),
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually unlink the broken symlinks. Default is plan-only.",
    ),
):
    """Find symlinks under ``root`` whose targets no longer exist.

    Catches both true symlinks and (on Windows) junctions. With
    ``--apply``, ``Path.unlink`` is called on each — the link itself
    is removed, the (nonexistent) target is unaffected.
    """
    rt: CuratorRuntime = ctx.obj
    _run_cleanup(
        rt,
        report_factory=lambda: rt.cleanup.find_broken_symlinks(root),
        apply_flag=apply,
    )


@cleanup_app.command("junk")
def cleanup_junk(
    ctx: typer.Context,
    root: Path = typer.Argument(..., help="Directory to walk."),
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually delete the junk files. Default is plan-only.",
    ),
    no_trash: bool = typer.Option(
        False, "--no-trash",
        help="Permadelete instead of sending to Recycle Bin / Trash.",
    ),
):
    """Find platform junk files (Thumbs.db, .DS_Store, desktop.ini, etc.).

    Uses a curated set of glob patterns matched against file basenames.
    With ``--apply``, files go to the OS Recycle Bin / Trash by default
    (recoverable). Pass ``--no-trash`` to permadelete.

    SafetyService is consulted on every target — REFUSE-tier paths
    (under OS_MANAGED registries) are skipped without touching the
    filesystem.
    """
    rt: CuratorRuntime = ctx.obj
    _run_cleanup(
        rt,
        report_factory=lambda: rt.cleanup.find_junk_files(root),
        apply_flag=apply,
        use_trash=not no_trash,
    )


# ---------------------------------------------------------------------------
# duplicates subcommand (Phase Gamma F7 — v0.28)
# ---------------------------------------------------------------------------


def _render_duplicate_report(
    rt: CuratorRuntime,
    report: CleanupReport,
    fmt_size,
) -> None:
    """Pretty-print a DUPLICATE_FILE report grouped by duplicate set.

    For each set: show the kept file (with the strategy reason), then
    list the duplicates with their sizes. Cap visible groups at 20 with
    a tail summary.
    """
    console = _console(rt)
    # Header tells the user which match mode produced this report so
    # fuzzy mode is impossible to mistake for exact mode at a glance.
    first_match_kind = (
        report.findings[0].details.get("match_kind", "exact")
        if report.findings else "exact"
    )
    mode_label = "fuzzy / near-duplicate" if first_match_kind == "fuzzy" else "exact"
    console.print(
        f"\n[bold]Cleanup (duplicate file, {mode_label})[/] under [cyan]{report.root}[/]"
    )

    # Group findings by dupset_id (the xxhash3_128).
    groups: dict[str, list] = {}
    keepers: dict[str, tuple[str, str]] = {}  # dupset_id -> (kept_path, kept_reason)
    for f in report.findings:
        dupset = f.details.get("dupset_id", "")
        groups.setdefault(dupset, []).append(f)
        if dupset not in keepers:
            keepers[dupset] = (
                f.details.get("kept_path", "?"),
                f.details.get("kept_reason", "?"),
            )

    n_groups = len(groups)
    n_dupes = report.count
    bytes_freed = report.total_size

    console.print(
        f"Found: {n_groups} duplicate group{'s' if n_groups != 1 else ''}, "
        f"{n_dupes} file{'s' if n_dupes != 1 else ''} could be freed "
        f"({fmt_size(bytes_freed)}) "
        f"in {report.duration_seconds or 0.0:.2f}s\n"
    )

    # Render each group, capped at 20.
    shown = 0
    for i, (dupset, dup_findings) in enumerate(groups.items(), start=1):
        if shown >= 20:
            break
        kept_path, kept_reason = keepers[dupset]
        console.print(
            f"  [bold cyan]Set {i}:[/] kept [green]{kept_path}[/] "
            f"[dim]({kept_reason})[/]"
        )
        for f in dup_findings:
            console.print(
                f"      [yellow]·[/] {f.path} [dim]({fmt_size(f.size)})[/]"
            )
        shown += 1

    if n_groups > shown:
        console.print(f"  [dim]… and {n_groups - shown} more duplicate group(s)[/]")

    if report.errors:
        console.print(f"\n[red]Errors during query: {len(report.errors)}[/]")
        for e in report.errors[:5]:
            console.print(f"  [red]·[/] {e}")

    if n_dupes == 0:
        console.print("[green]No duplicates found.[/]")
    else:
        console.print(
            "\n[dim]This is a plan preview. Add [bold]--apply[/bold] to "
            "send the duplicates to the Recycle Bin / Trash. The keeper "
            "in each set is left untouched.[/]"
        )


@cleanup_app.command("duplicates")
def cleanup_duplicates(
    ctx: typer.Context,
    source: Optional[str] = typer.Option(
        None, "--source",
        help="Restrict to files indexed under this source (e.g. 'local'). "
             "Default: all sources.",
    ),
    root: Optional[str] = typer.Option(
        None, "--root",
        help="Restrict to files whose path starts with this prefix. "
             "Useful for narrowing to e.g. C:\\Users\\jmlee\\Downloads.",
    ),
    keep_strategy: str = typer.Option(
        "shortest_path", "--keep-strategy",
        help="How to pick the keeper in each duplicate set: "
             "shortest_path (default), longest_path, oldest, newest.",
    ),
    keep_under: Optional[str] = typer.Option(
        None, "--keep-under",
        help="Path prefix that takes precedence over --keep-strategy. "
             "Files under this prefix are preferred as keepers; the "
             "strategy then breaks ties.",
    ),
    match_kind: str = typer.Option(
        "exact", "--match-kind",
        help="How to match duplicates: 'exact' (default, bit-identical via "
             "xxhash3_128) or 'fuzzy' (near-duplicates via MinHash-LSH on "
             "fuzzy_hash; catches re-encoded media). Fuzzy mode has higher "
             "false-positive risk -- always review the plan output.",
    ),
    similarity_threshold: float = typer.Option(
        0.85, "--similarity-threshold",
        help="For --match-kind fuzzy: jaccard threshold on MinHash signatures "
             "(0.0 to 1.0; default 0.85). Higher = stricter / fewer false "
             "positives but more misses.",
    ),
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually delete the duplicates (keepers untouched). "
             "Default is plan-only.",
    ),
    no_trash: bool = typer.Option(
        False, "--no-trash",
        help="Permadelete instead of sending to Recycle Bin / Trash. "
             "NOT RECOMMENDED for duplicates -- the trash is your safety "
             "net if dedup picks the wrong keeper.",
    ),
):
    """Find duplicate files in the index and propose removing all but one.

    Uses Curator's existing hash index from Phase Alpha -- no
    filesystem walk, no rehashing. Files must have been previously
    scanned + hashed via ``curator scan`` to appear here.

    Default match mode is ``exact`` (bit-identical via xxhash3_128).
    Use ``--match-kind fuzzy`` to find near-duplicates via MinHash-LSH
    on fuzzy_hash (re-encoded JPEGs, re-compressed MP3s, re-OCR'd PDFs).
    Fuzzy mode has higher false-positive risk -- always review the plan
    before --apply.

    The default keep-strategy is ``shortest_path``: the file with the
    least-nested location wins (most-"intentional" location). Override
    with ``--keep-strategy`` and/or ``--keep-under``.

    With ``--apply``, the duplicates (NOT the keepers) are sent to the
    OS Recycle Bin / Trash via the same vendored send2trash used by
    ``cleanup junk``. SafetyService is consulted on every target.
    """
    rt: CuratorRuntime = ctx.obj
    _run_cleanup(
        rt,
        report_factory=lambda: rt.cleanup.find_duplicates(
            source_id=source,
            root_prefix=root,
            keep_strategy=keep_strategy,
            keep_under=keep_under,
            match_kind=match_kind,
            similarity_threshold=similarity_threshold,
        ),
        apply_flag=apply,
        use_trash=not no_trash,
    )


# ===========================================================================
# GUI subcommand (Phase Beta gate 4, v0.34)
# ===========================================================================


@app.command(name="gui")
def gui_cmd(
    ctx: typer.Context,
) -> None:
    """Launch the Curator GUI (PySide6 desktop window).

    Read-only first ship. Three tabs: Browser (every indexed file),
    Bundles (every bundle + member counts), and Trash (every trashed
    file). The GUI shares the same runtime as the CLI; whatever your
    CLI sees, the GUI sees.

    Requires PySide6 (in the ``[gui]`` extra). Install with::

        pip install 'curator[gui]'
    """
    rt: CuratorRuntime = ctx.obj

    # Defer imports so a missing PySide6 produces a helpful message
    # rather than a raw ImportError at the top of this module.
    try:
        from curator.gui.launcher import is_pyside6_available, run_gui
    except ImportError as e:  # pragma: no cover — defensive
        _err_console(rt).print(
            f"[red]GUI launcher unavailable: {e}[/]"
        )
        raise typer.Exit(code=2) from e

    if not is_pyside6_available():
        _err_console(rt).print(
            "[red]PySide6 is not installed. "
            "Install with: pip install 'curator[gui]'[/]"
        )
        raise typer.Exit(code=2)

    exit_code = run_gui(rt)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


# ---------------------------------------------------------------------------
# gdrive subcommands (auth flow + status + paths) — v0.42
# ---------------------------------------------------------------------------


@gdrive_app.command("paths")
def gdrive_paths_cmd(
    ctx: typer.Context,
    alias: str = typer.Argument(
        "default",
        help="Alias (the part after 'gdrive:' in source IDs). Default: 'default'.",
    ),
) -> None:
    """Show where Curator expects this alias's auth files to live.

    Doesn't touch the network. Useful before running ``gdrive auth`` to
    confirm where to drop ``client_secrets.json``.
    """
    from curator.services.gdrive_auth import paths_for_alias

    rt: CuratorRuntime = ctx.obj
    paths = paths_for_alias(alias)
    if rt.json_output:
        payload = {
            "alias": paths.alias,
            "dir": str(paths.dir),
            "client_secrets": str(paths.client_secrets),
            "credentials": str(paths.credentials),
        }
        typer.echo(json.dumps(payload, indent=2))
        return
    console = _console(rt)
    console.print(f"\n[bold]gdrive auth paths for alias [cyan]{paths.alias}[/]:[/]")
    console.print(f"  dir:             [yellow]{paths.dir}[/]")
    console.print(f"  client_secrets:  [yellow]{paths.client_secrets}[/]")
    console.print(f"  credentials:     [yellow]{paths.credentials}[/]")


@gdrive_app.command("status")
def gdrive_status_cmd(
    ctx: typer.Context,
    alias: str = typer.Argument(
        "default",
        help="Alias to check. Default: 'default'.",
    ),
) -> None:
    """Report current auth state for an alias (offline; no network).

    States:
      no_client_secrets    — user must download from Google Cloud Console
      no_credentials       — ready to run ``gdrive auth <alias>``
      credentials_present  — auth complete; PyDrive2 will refresh tokens
    """
    from curator.services.gdrive_auth import auth_status

    rt: CuratorRuntime = ctx.obj
    status = auth_status(alias)
    if rt.json_output:
        typer.echo(json.dumps(status.to_dict(), indent=2))
        return
    console = _console(rt)
    state_color = {
        "no_client_secrets": "red",
        "no_credentials": "yellow",
        "credentials_present": "green",
    }.get(status.state, "white")
    console.print(
        f"\n[bold]gdrive[{status.paths.alias}][/]: "
        f"[{state_color}]{status.state}[/]"
    )
    console.print(f"  client_secrets ({status.paths.client_secrets}): "
                  f"{'[green]found[/]' if status.has_client_secrets else '[red]missing[/]'}")
    console.print(f"  credentials    ({status.paths.credentials}): "
                  f"{'[green]found[/]' if status.has_credentials else '[red]missing[/]'}")
    console.print(f"\n[dim]{status.detail}[/]")


@gdrive_app.command("auth")
def gdrive_auth_cmd(
    ctx: typer.Context,
    alias: str = typer.Argument(
        "default",
        help="Alias to authenticate. Default: 'default'. Aliases map to source IDs as 'gdrive:<alias>'.",
    ),
    auth_method: str = typer.Option(
        "command_line",
        "--method",
        "-m",
        help="OAuth flow: 'command_line' (paste URL into browser, paste code back) or 'local_webserver' (opens browser to localhost callback).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-run auth even if credentials.json already exists.",
    ),
) -> None:
    """Run the PyDrive2 interactive OAuth flow for an alias.

    Prerequisite: download ``client_secrets.json`` from Google Cloud
    Console (Credentials -> OAuth 2.0 Client ID -> Download JSON) and
    place it at the path shown by ``curator gdrive paths <alias>``.

    On success, writes ``credentials.json`` next to it. PyDrive2 will
    auto-refresh access tokens from the saved refresh token.
    """
    from curator.services.gdrive_auth import (
        ClientSecretsMissing,
        PyDrive2NotInstalled,
        auth_status,
        ensure_alias_dir,
        run_interactive_auth,
    )

    rt: CuratorRuntime = ctx.obj
    console = _console(rt)
    err = _err_console(rt)

    paths = ensure_alias_dir(alias)
    pre_status = auth_status(alias)

    if pre_status.state == "credentials_present" and not force:
        if rt.json_output:
            typer.echo(json.dumps({
                "alias": alias,
                "action": "skipped",
                "reason": "credentials_present",
                "hint": "Pass --force to re-run",
            }, indent=2))
            return
        console.print(
            f"\n[yellow]Credentials already present for alias [cyan]{alias}[/].[/]"
        )
        console.print(f"  {paths.credentials}")
        console.print(
            "\n[dim]Pass [bold]--force[/bold] to re-run the auth flow anyway.[/]"
        )
        return

    try:
        run_interactive_auth(paths, auth_method=auth_method)
    except PyDrive2NotInstalled as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2) from e
    except ClientSecretsMissing as e:
        err.print(f"[red]{e}[/]")
        err.print(
            f"[dim]Tip: run 'curator gdrive paths {alias}' to see the expected location.[/]"
        )
        raise typer.Exit(code=1) from e
    except RuntimeError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e

    post_status = auth_status(alias)
    if rt.json_output:
        typer.echo(json.dumps({
            "alias": alias,
            "action": "completed",
            "credentials": str(paths.credentials),
            "state": post_status.state,
        }, indent=2))
        return
    console.print(
        f"\n[green]✓ Auth complete for [cyan]{alias}[/].[/]"
    )
    console.print(f"  Credentials saved to: {paths.credentials}")
    console.print(
        f"\n[dim]Next: 'curator sources add --type gdrive --name gdrive:{alias}' "
        "and provide the source config (use "
        f"curator.services.gdrive_auth.source_config_for_alias({alias!r}) "
        "to construct it).[/]"
    )


# ---------------------------------------------------------------------------
# v1.1.0a1: Tracer (Migration tool) -- Phase 1 + Phase 2
# ---------------------------------------------------------------------------


@app.command(name="migrate")
def migrate_cmd(
    ctx: typer.Context,
    src_source_id: Optional[str] = typer.Argument(
        None,
        help="Source plugin id whose files to migrate (e.g. 'local'). "
             "Required for new-job creation; omit when using "
             "--list / --status / --abort / --resume.",
    ),
    src_root: Optional[str] = typer.Argument(
        None,
        help="Path prefix at the source. Only files under this prefix "
             "are candidates. Required for new-job creation.",
    ),
    dst_root: Optional[str] = typer.Argument(
        None,
        help="Path prefix at the destination. Subpaths are preserved. "
             "Required for new-job creation.",
    ),
    # ---- Plan-time filters ---------------------------------------------
    extensions: Optional[str] = typer.Option(
        None, "--ext",
        help="Comma-separated extension filter (e.g. '.mp3,.flac'). "
             "Case-insensitive.",
    ),
    includes: list[str] = typer.Option(
        [], "--include",
        help="Glob whitelist (relative to src_root). Repeatable. File must "
             "match AT LEAST ONE include if any are specified. Phase 2.",
    ),
    excludes: list[str] = typer.Option(
        [], "--exclude",
        help="Glob blacklist (relative to src_root). Repeatable. File "
             "must match NO excludes. Phase 2.",
    ),
    path_prefix: Optional[str] = typer.Option(
        None, "--path-prefix",
        help="Sub-path under src_root to narrow selection "
             "(e.g. 'Pink Floyd' under src_root='C:/Music'). Phase 2.",
    ),
    dst_source_id: Optional[str] = typer.Option(
        None, "--dst-source-id",
        help="Destination source id. Defaults to src_source_id. "
             "Cross-source migration is Session B (not yet shipped).",
    ),
    # ---- Apply / job behavior ------------------------------------------
    apply: bool = typer.Option(
        False, "--apply",
        help="Required to actually perform the moves. Without this, plan-only.",
    ),
    workers: int = typer.Option(
        1, "--workers", "-w",
        help="Number of concurrent workers (default 1). When >1, the "
             "job is persisted as a migration_jobs row so it can be "
             "--resume'd later. Phase 2.",
    ),
    max_retries: int = typer.Option(
        3, "--max-retries",
        help="Per-file retry budget for transient cloud errors "
             "(HTTP 4xx/5xx, ConnectionError, Timeout). Default 3; "
             "0 disables retry; capped at 10. Exponential backoff "
             "capped at 60s, with Retry-After header honored when "
             "present. Affects cross-source migrations only "
             "(same-source local-FS errors are mostly permanent and "
             "don't benefit from retry). Phase 3.",
    ),
    on_conflict: str = typer.Option(
        "skip", "--on-conflict",
        help="Destination-collision policy. 'skip' (default) preserves "
             "v1.2.0 behavior. 'fail' aborts the migration on the first "
             "collision. 'overwrite-with-backup' renames existing dst "
             "to <name>.curator-backup-<UTC-iso8601><ext> then proceeds. "
             "'rename-with-suffix' migrates to <name>.curator-N<ext> "
             "(N in [1, 9999]) instead. Cross-source collisions support "
             "skip+fail; overwrite/rename modes degrade to skip with a "
             "warning (no atomic-rename hook in the source plugin "
             "contract yet). Phase 3 P2.",
    ),
    keep_source: bool = typer.Option(
        False, "--keep-source/--trash-source",
        help="--keep-source: dst created+verified, src untouched, index "
             "NOT updated. The next 'curator scan' picks up dst as a "
             "new file. Default --trash-source: index re-pointed + src "
             "sent to OS trash. Phase 2.",
    ),
    include_caution: bool = typer.Option(
        False, "--include-caution",
        help="Include CAUTION-level files in the migration. Default "
             "False (only SAFE migrates). REFUSE is always skipped. "
             "Phase 2.",
    ),
    verify_hash: bool = typer.Option(
        True, "--verify-hash/--no-verify-hash",
        help="Recompute xxhash3_128 of the destination after copy and "
             "require a match. Default ON (Constitutional discipline).",
    ),
    # ---- Lifecycle commands (Phase 2) ----------------------------------
    list_jobs_flag: bool = typer.Option(
        False, "--list",
        help="List recent migration jobs and exit. "
             "Filter with --status-filter. Phase 2.",
    ),
    status_filter: Optional[str] = typer.Option(
        None, "--status-filter",
        help="For --list: filter by job status (queued|running|"
             "completed|failed|cancelled|partial).",
    ),
    status: Optional[str] = typer.Option(
        None, "--status",
        help="Show full status for a job by id and exit. Phase 2.",
    ),
    abort: Optional[str] = typer.Option(
        None, "--abort",
        help="Signal a running job to abort gracefully. Workers finish "
             "their current file then exit. Phase 2.",
    ),
    resume: Optional[str] = typer.Option(
        None, "--resume",
        help="Resume a previously-created (or interrupted) job by id. "
             "Phase 2.",
    ),
) -> None:
    """Relocate files across paths/sources with index integrity (Tracer).

    Two execution paths:

    * **Phase 1 (default):** in-memory plan + apply, single-threaded.
      Triggered when ``--workers`` is 1 (default) and ``--resume`` is
      not used. Fast for small migrations.

    * **Phase 2:** persisted job with worker pool. Triggered when
      ``--workers > 1`` OR ``--resume`` is specified. Plan + per-file
      progress are persisted to ``migration_jobs`` / ``migration_progress``
      so the migration can be resumed after an interruption.

    Lifecycle commands (Phase 2 jobs only):

    * ``curator migrate --list [--status-filter X]``
    * ``curator migrate --status <job_id>``
    * ``curator migrate --abort <job_id>``
    * ``curator migrate --resume <job_id> [--workers N]``

    Examples:
      curator migrate local C:/Music D:/Music                     # plan only
      curator migrate local C:/Music D:/Music --apply             # Phase 1
      curator migrate local C:/Music D:/Music --apply -w 4        # Phase 2
      curator migrate local C:/Music D:/Music --apply --include '**/*.mp3'
      curator migrate local C:/Music D:/Music --apply --keep-source
      curator migrate --list --status-filter running
      curator migrate --status <job_id>
      curator migrate --resume <job_id> --workers 4
    """
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)
    err = _err_console(rt)

    # ---- Lifecycle dispatch first (no positional args needed) --------
    if list_jobs_flag:
        _migrate_list(rt, status_filter=status_filter, console=console)
        return
    if status is not None:
        _migrate_status(rt, status, console=console, err=err)
        return
    if abort is not None:
        _migrate_abort(rt, abort, console=console, err=err)
        return
    if resume is not None:
        _migrate_resume(
            rt, resume,
            workers=workers, verify_hash=verify_hash,
            keep_source=keep_source,
            console=console, err=err,
        )
        return

    # ---- New-job creation (positional args required) -----------------
    if not (src_source_id and src_root and dst_root):
        err.print(
            "[red]src_source_id, src_root, and dst_root are required for "
            "new migrations. Use --resume / --list / --status / --abort "
            "for lifecycle operations.[/]"
        )
        raise typer.Exit(code=2)

    # Cross-source guard: Session B ships cross-source via
    # curator_source_write hook. Refuse only if the destination plugin
    # doesn't advertise supports_write capability.
    effective_dst_source = dst_source_id or src_source_id
    if effective_dst_source != src_source_id:
        if not rt.migration._can_write_to_source(effective_dst_source):
            err.print(
                f"[red]Cross-source migration to dst_source_id={effective_dst_source!r} "
                "is not supported: no registered plugin advertises "
                "supports_write for that source. Run 'curator sources list' "
                "to see what's available, or install a plugin that supports "
                "writing to this source.[/]"
            )
            raise typer.Exit(code=2)

    ext_list: list[str] | None = None
    if extensions:
        ext_list = [e.strip() for e in extensions.split(",") if e.strip()]

    include_list = list(includes) if includes else None
    exclude_list = list(excludes) if excludes else None

    try:
        plan = rt.migration.plan(
            src_source_id=src_source_id,
            src_root=src_root,
            dst_root=dst_root,
            dst_source_id=effective_dst_source,
            extensions=ext_list,
            includes=include_list,
            excludes=exclude_list,
            path_prefix=path_prefix,
        )
    except ValueError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2) from e

    # Plan-only mode
    if not apply:
        _render_migration_plan(rt, plan, verify_hash=verify_hash, console=console)
        return

    # Routing: --workers > 1 -> Phase 2 persisted path; otherwise Phase 1
    use_phase2 = workers > 1

    eligible_count = (
        plan.safe_count + (plan.caution_count if include_caution else 0)
    )
    if eligible_count == 0:
        if rt.json_output:
            typer.echo(json.dumps({
                "action": "migrate.apply",
                "moved": 0,
                "skipped": plan.total_count,
                "failed": 0,
                "reason": "no eligible files in plan",
            }, indent=2))
        else:
            console.print(
                "[yellow]No eligible files to migrate. "
                f"({plan.safe_count} SAFE, {plan.caution_count} CAUTION, "
                f"{plan.refuse_count} REFUSE; include_caution={include_caution})[/]"
            )
        return

    db_guard = Path(rt.db.db_path) if rt.db.db_path else None

    if use_phase2:
        # Phase 2: create_job + run_job
        options = {
            "workers": workers, "verify_hash": verify_hash,
            "keep_source": keep_source, "include_caution": include_caution,
            "ext": ext_list, "includes": include_list, "excludes": exclude_list,
            "path_prefix": path_prefix,
            "max_retries": max_retries,
            "on_conflict": on_conflict,
        }
        job_id = rt.migration.create_job(
            plan, options=options,
            db_path_guard=db_guard,
            include_caution=include_caution,
        )
        if not rt.json_output:
            console.print(
                f"\n[bold cyan]Migration job created[/]: [cyan]{job_id}[/] "
                f"({eligible_count} eligible files, {workers} workers)"
            )
        report = rt.migration.run_job(
            job_id,
            workers=workers,
            verify_hash=verify_hash,
            keep_source=keep_source,
            max_retries=max_retries,
            on_conflict=on_conflict,
        )
        _render_migration_report(
            rt, report, console=console, job_id=job_id, keep_source=keep_source,
        )
        if report.failed_count:
            raise typer.Exit(code=1)
        return

    # Phase 1: apply()
    try:
        report = rt.migration.apply(
            plan,
            verify_hash=verify_hash,
            db_path_guard=db_guard,
            keep_source=keep_source,
            include_caution=include_caution,
            max_retries=max_retries,
            on_conflict=on_conflict,
        )
    except ValueError as e:
        # set_on_conflict_mode raised on an unknown mode
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2) from e
    except Exception as e:
        # Phase 3 P2: --on-conflict=fail aborts via MigrationConflictError.
        # Import inside the except to avoid a cycle if migration.py is
        # not yet importable (e.g. service-init failure).
        from curator.services.migration import MigrationConflictError
        if isinstance(e, MigrationConflictError):
            err.print(
                f"[red]Migration aborted: destination already exists "
                f"with --on-conflict=fail[/]\n"
                f"  dst: [yellow]{e.dst_path}[/]\n"
                f"  src: [yellow]{e.src_path}[/]"
            )
            raise typer.Exit(code=1) from e
        raise
    _render_migration_report(
        rt, report, console=console, keep_source=keep_source,
    )
    if report.failed_count:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Lifecycle dispatch helpers (Phase 2)
# ---------------------------------------------------------------------------

def _parse_job_id(rt, raw: str, err) -> UUID:
    """Parse a UUID string; surface a clean CLI error on bad input."""
    try:
        return UUID(raw)
    except ValueError:
        err.print(f"[red]Not a valid job_id (UUID): {raw!r}[/]")
        raise typer.Exit(code=2) from None


def _migrate_list(rt, *, status_filter: str | None, console) -> None:
    """`curator migrate --list` -- show recent migration jobs."""
    jobs = rt.migration.list_jobs(status=status_filter, limit=50)
    if rt.json_output:
        typer.echo(json.dumps([
            {
                "job_id": str(j.job_id),
                "status": j.status,
                "src_source_id": j.src_source_id,
                "src_root": j.src_root,
                "dst_source_id": j.dst_source_id,
                "dst_root": j.dst_root,
                "files_total": j.files_total,
                "files_copied": j.files_copied,
                "files_skipped": j.files_skipped,
                "files_failed": j.files_failed,
                "bytes_copied": j.bytes_copied,
                "started_at": j.started_at,
                "completed_at": j.completed_at,
                "duration_seconds": j.duration_seconds,
            }
            for j in jobs
        ], indent=2, default=str))
        return
    if not jobs:
        suffix = f" (status={status_filter})" if status_filter else ""
        console.print(f"[dim]No migration jobs found{suffix}.[/]")
        return
    table = Table(title=f"{len(jobs)} migration job(s)")
    table.add_column("job_id", style="dim")
    table.add_column("status")
    table.add_column("src -> dst")
    table.add_column("files", justify="right")
    table.add_column("copied", justify="right")
    table.add_column("failed", justify="right")
    table.add_column("started", style="dim")
    for j in jobs:
        status_color = {
            "queued": "dim", "running": "cyan", "completed": "green",
            "failed": "red", "cancelled": "yellow", "partial": "yellow",
        }.get(j.status, "white")
        table.add_row(
            str(j.job_id)[:8],
            f"[{status_color}]{j.status}[/]",
            f"{j.src_source_id}:{j.src_root[:30]} -> {j.dst_source_id}:{j.dst_root[:30]}",
            str(j.files_total),
            str(j.files_copied),
            f"[red]{j.files_failed}[/]" if j.files_failed else "0",
            j.started_at.strftime("%Y-%m-%d %H:%M") if j.started_at else "-",
        )
    console.print(table)


def _migrate_status(rt, raw_id: str, *, console, err) -> None:
    """`curator migrate --status <job_id>` -- show one job's full status."""
    job_id = _parse_job_id(rt, raw_id, err)
    try:
        info = rt.migration.get_job_status(job_id)
    except ValueError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e
    if rt.json_output:
        typer.echo(json.dumps(info, indent=2, default=str))
        return
    console.print(f"\n[bold cyan]Migration job[/] [cyan]{info['job_id']}[/]")
    console.print(f"  status:        {info['status']}")
    console.print(
        f"  src -> dst:    {info['src_source_id']}:{info['src_root']}"
        f"  ->  {info['dst_source_id']}:{info['dst_root']}"
    )
    console.print(f"  files total:   {info['files_total']}")
    console.print(f"  files copied:  [green]{info['files_copied']}[/]")
    console.print(f"  files skipped: [yellow]{info['files_skipped']}[/]")
    console.print(f"  files failed:  [red]{info['files_failed']}[/]")
    console.print(f"  bytes copied:  {info['bytes_copied']:,}")
    if info["started_at"]:
        console.print(f"  started:       {info['started_at']}")
    if info["completed_at"]:
        console.print(f"  completed:     {info['completed_at']}")
    if info["duration_seconds"] is not None:
        console.print(f"  duration:      {info['duration_seconds']:.2f}s")
    if info["progress_histogram"]:
        console.print("  progress:")
        for k, v in info["progress_histogram"].items():
            console.print(f"    {k}: {v}")
    if info["options"]:
        console.print("  options:")
        for k, v in info["options"].items():
            console.print(f"    {k} = {v!r}")
    if info["error"]:
        console.print(f"  [red]error:[/] {info['error']}")


def _migrate_abort(rt, raw_id: str, *, console, err) -> None:
    """`curator migrate --abort <job_id>` -- signal a running job to stop."""
    job_id = _parse_job_id(rt, raw_id, err)
    rt.migration.abort_job(job_id)
    if rt.json_output:
        typer.echo(json.dumps({
            "action": "migrate.abort", "job_id": str(job_id),
            "sent": True,
        }, indent=2))
        return
    console.print(
        f"[yellow]✓[/] Abort signal sent to job [cyan]{job_id}[/]. "
        "Workers will finish their current file then exit."
    )


def _migrate_resume(
    rt, raw_id: str, *,
    workers: int, verify_hash: bool, keep_source: bool,
    console, err,
) -> None:
    """`curator migrate --resume <job_id>` -- re-execute pending rows."""
    job_id = _parse_job_id(rt, raw_id, err)
    try:
        # Sanity check: job must exist before we spawn workers
        existing = rt.migration_job_repo.get_job(job_id)
    except Exception as e:
        err.print(f"[red]Failed to look up job: {e}[/]")
        raise typer.Exit(code=1) from e
    if existing is None:
        err.print(f"[red]Migration job not found: {job_id}[/]")
        raise typer.Exit(code=1)
    if not rt.json_output:
        console.print(
            f"\n[bold cyan]Resuming migration job[/] [cyan]{job_id}[/] "
            f"(status={existing.status}, files_total={existing.files_total})"
        )
    report = rt.migration.run_job(
        job_id,
        workers=max(1, workers),
        verify_hash=verify_hash,
        keep_source=keep_source,
    )
    _render_migration_report(
        rt, report, console=console, job_id=job_id, keep_source=keep_source,
    )
    if report.failed_count:
        raise typer.Exit(code=1)


def _render_migration_plan(rt, plan, *, verify_hash: bool, console=None) -> None:
    """Render a migration plan (no mutations)."""
    if console is None:
        console = _console(rt)
    if rt.json_output:
        typer.echo(json.dumps({
            "action": "migrate.plan",
            "src_source_id": plan.src_source_id,
            "src_root": plan.src_root,
            "dst_source_id": plan.dst_source_id,
            "dst_root": plan.dst_root,
            "total": plan.total_count,
            "safe": plan.safe_count,
            "caution": plan.caution_count,
            "refuse": plan.refuse_count,
            "planned_bytes": plan.planned_bytes,
            "verify_hash": verify_hash,
            "moves": [
                {
                    "curator_id": str(m.curator_id),
                    "src_path": m.src_path,
                    "dst_path": m.dst_path,
                    "safety_level": m.safety_level.value,
                    "size": m.size,
                }
                for m in plan.moves
            ],
        }, indent=2, default=str))
        return

    console.print(
        f"\n[bold cyan]Migration plan[/]: "
        f"{plan.src_source_id}:{plan.src_root} -> "
        f"{plan.dst_source_id}:{plan.dst_root}"
    )
    console.print(
        f"  [green]SAFE[/]: {plan.safe_count}    "
        f"[yellow]CAUTION[/]: {plan.caution_count}    "
        f"[red]REFUSE[/]: {plan.refuse_count}    "
        f"Total: {plan.total_count}"
    )
    console.print(f"  Planned bytes: {plan.planned_bytes:,}")
    console.print(
        f"  Hash verify: {'[green]on[/]' if verify_hash else '[red]off[/]'}"
    )
    console.print()

    safe_moves = [m for m in plan.moves if m.safety_level.value == "safe"]
    if safe_moves:
        console.print("[bold]Files that would move (SAFE):[/]")
        for m in safe_moves[:20]:
            console.print(f"  {m.src_path}  ->  {m.dst_path}")
        if len(safe_moves) > 20:
            console.print(f"  [dim]... and {len(safe_moves) - 20} more[/]")
    skipped = [m for m in plan.moves if m.safety_level.value != "safe"]
    if skipped:
        console.print(f"\n[yellow]Skipped (not SAFE): {len(skipped)} files[/]")

    if plan.safe_count > 0:
        console.print(
            "\n[dim]Re-run with [bold]--apply[/bold] to perform the moves. "
            "Add [bold]--workers N[/bold] (N>1) for the resumable Phase 2 path.[/]"
        )
    else:
        console.print("\n[yellow]Nothing to do.[/]")


def _render_migration_report(
    rt, report, *, console=None, job_id: UUID | None = None,
    keep_source: bool = False,
) -> None:
    """Render a migration apply / run_job report.

    The ``job_id`` argument signals Phase 2 mode (persisted job) and
    is included in JSON output + the human heading. The ``keep_source``
    argument changes the heading word from MOVED to COPIED.
    """
    moves = report.moves
    moved = [
        m for m in moves
        if m.outcome and m.outcome.value in ("moved", "copied")
    ]
    skipped = [m for m in moves if m.outcome and m.outcome.value.startswith("skipped")]
    failed = [m for m in moves if m.outcome and m.outcome.value in ("failed", "hash_mismatch")]

    if console is None:
        console = _console(rt)
    if rt.json_output:
        payload = {
            "action": "migrate.apply",
            "moved": len(moved),
            "skipped": len(skipped),
            "failed": len(failed),
            "bytes_moved": report.bytes_moved,
            "duration_seconds": report.duration_seconds,
            "keep_source": keep_source,
            "results": [
                {
                    "curator_id": str(m.curator_id),
                    "src_path": m.src_path,
                    "dst_path": m.dst_path,
                    "outcome": m.outcome.value if m.outcome else None,
                    "error": m.error,
                }
                for m in moves
            ],
        }
        if job_id is not None:
            payload["job_id"] = str(job_id)
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    action_word = "copied" if keep_source else "applied"
    moved_label = "COPIED" if keep_source else "MOVED"
    duration = report.duration_seconds or 0.0
    heading = f"\n[bold green]Migration {action_word}[/] in {duration:.2f}s"
    if job_id is not None:
        heading += f"  [dim](job {job_id})[/]"
    console.print(heading)
    console.print(
        f"  [green]{moved_label}[/]: {len(moved)}    "
        f"[yellow]SKIPPED[/]: {len(skipped)}    "
        f"[red]FAILED[/]: {len(failed)}"
    )
    console.print(f"  Bytes moved: {report.bytes_moved:,}")

    if failed:
        console.print("\n[bold red]Failures:[/]")
        for m in failed[:20]:
            console.print(f"  [red]{m.outcome.value}[/]  {m.src_path}")
            if m.error:
                console.print(f"    [dim]{m.error}[/]")
        if len(failed) > 20:
            console.print(f"  [dim]... and {len(failed) - 20} more[/]")


# ------------------------------------------------------------------
# v1.7.2 (T-B01): curator forecast - drive capacity prediction
# ------------------------------------------------------------------

@app.command(name="forecast")
def forecast_cmd(
    ctx: typer.Context,
    drive: str = typer.Argument(
        None,
        help="Drive mount point (e.g. 'C:\\' on Windows, '/' on Unix). "
             "Omit to forecast every mounted fixed disk.",
    ),
) -> None:
    """Predict when local drives reach capacity.

    Linear-fits monthly indexing rate from the files table and projects
    when each drive hits 95% / 99% capacity. With <2 months of history,
    reports 'insufficient data' and asks you to check back later.

    Already-past-threshold drives are flagged with no projection - they
    need cleanup now, not later.
    """
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)

    if drive:
        forecasts = [rt.forecast.compute_disk_forecast(drive)]
    else:
        forecasts = rt.forecast.compute_all_drives()

    if not forecasts:
        console.print("[yellow]No fixed drives found.[/]")
        return

    if rt.json_output:
        import json
        payload = []
        for f in forecasts:
            payload.append({
                "drive_path": f.drive_path,
                "current_used_gb": round(f.current_used_gb, 2),
                "current_total_gb": round(f.current_total_gb, 2),
                "current_free_gb": round(f.current_free_gb, 2),
                "current_pct": round(f.current_pct, 1),
                "slope_gb_per_day": (
                    round(f.slope_gb_per_day, 3)
                    if f.slope_gb_per_day is not None else None
                ),
                "fit_r_squared": (
                    round(f.fit_r_squared, 3)
                    if f.fit_r_squared is not None else None
                ),
                "days_to_95pct": f.days_to_95pct,
                "days_to_99pct": f.days_to_99pct,
                "eta_95pct": f.eta_95pct.isoformat() if f.eta_95pct else None,
                "eta_99pct": f.eta_99pct.isoformat() if f.eta_99pct else None,
                "status": f.status,
                "status_message": f.status_message,
            })
        typer.echo(json.dumps(payload, indent=2))
        return

    for f in forecasts:
        # Color-code by status
        status_colors = {
            "fit_ok": "green",
            "past_95pct": "yellow",
            "past_99pct": "red",
            "insufficient_data": "dim",
            "no_growth": "dim",
        }
        color = status_colors.get(f.status, "white")

        console.print(f"\n[bold]{f.drive_path}[/]")
        console.print(
            f"  Used:  {f.current_used_gb:>8.1f} GB / {f.current_total_gb:.1f} GB"
            f"  ([{color}]{f.current_pct:.1f}%[/])"
        )
        console.print(f"  Free:  {f.current_free_gb:>8.1f} GB")

        if f.slope_gb_per_day is not None:
            console.print(
                f"  Rate:  {f.slope_gb_per_day:>8.3f} GB/day"
                f"  (R²={f.fit_r_squared:.3f})"
            )

        console.print(f"  [{color}]{f.status_message}[/]")

        if f.monthly_history:
            console.print(f"  [dim]History ({len(f.monthly_history)} month(s)):[/]")
            for b in f.monthly_history[-6:]:  # last 6 months at most
                console.print(
                    f"    [dim]{b.month}: +{b.file_count:>6,} files, "
                    f"+{b.gb_added:>6.2f} GB[/]"
                )


# ------------------------------------------------------------------
# v1.7.3 (T-C02): curator status set/get/report
# ------------------------------------------------------------------

def _resolve_file(rt: "CuratorRuntime", target: str):
    """Resolve a path-or-UUID target to a FileEntity.

    Tries UUID first (cheap); falls back to find_by_path against every
    registered source. Returns None if not found.
    """
    from uuid import UUID as _UUID
    # Try UUID first
    try:
        cid = _UUID(target)
        return rt.file_repo.get(cid)
    except (ValueError, AttributeError):
        pass
    # Try path lookup across sources
    for source in rt.source_repo.list_all():
        f = rt.file_repo.find_by_path(source.source_id, target)
        if f is not None:
            return f
    return None


@status_app.command("set")
def status_set(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="File path or curator_id (UUID)"),
    status: str = typer.Argument(..., help="vital | active | provisional | junk"),
    expires_in_days: int = typer.Option(
        None, "--expires-in-days",
        help="Set expires_at to now + N days. Useful for provisional/junk classifications.",
    ),
    clear_expires: bool = typer.Option(
        False, "--clear-expires",
        help="Explicitly clear any existing expires_at value.",
    ),
) -> None:
    """Set a file's classification status.

    Examples:
        curator status set /path/to/file.txt vital
        curator status set abc123-... junk --expires-in-days 30
    """
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)

    f = _resolve_file(rt, target)
    if f is None:
        console.print(f"[red]File not found: {target!r}[/]")
        raise typer.Exit(code=1)

    from datetime import datetime as _dt, timedelta as _td
    expires_at = None
    if expires_in_days is not None:
        expires_at = _dt.utcnow() + _td(days=expires_in_days)

    try:
        rt.file_repo.update_status(
            f.curator_id, status,
            expires_at=expires_at,
            clear_expires=clear_expires,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1)

    rt.audit.log(
        actor="cli.status",
        action="file.status_change",
        entity_type="file",
        entity_id=str(f.curator_id),
        details={
            "from": f.status, "to": status,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )

    if rt.json_output:
        import json
        typer.echo(json.dumps({
            "curator_id": str(f.curator_id),
            "source_path": f.source_path,
            "from": f.status, "to": status,
            "expires_at": expires_at.isoformat() if expires_at else None,
        }))
    else:
        console.print(
            f"[green]Updated[/] {f.source_path}: {f.status} -> [bold]{status}[/]"
        )
        if expires_at:
            console.print(f"  expires_at: {expires_at.isoformat()}")


@status_app.command("get")
def status_get(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="File path or curator_id (UUID)"),
) -> None:
    """Get a file's classification status."""
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)

    f = _resolve_file(rt, target)
    if f is None:
        console.print(f"[red]File not found: {target!r}[/]")
        raise typer.Exit(code=1)

    if rt.json_output:
        import json
        typer.echo(json.dumps({
            "curator_id": str(f.curator_id),
            "source_id": f.source_id,
            "source_path": f.source_path,
            "status": f.status,
            "supersedes_id": str(f.supersedes_id) if f.supersedes_id else None,
            "expires_at": f.expires_at.isoformat() if f.expires_at else None,
        }))
    else:
        # Color by bucket
        colors = {"vital": "bright_green", "active": "cyan",
                  "provisional": "yellow", "junk": "red"}
        c = colors.get(f.status, "white")
        console.print(f"[bold]{f.source_path}[/]")
        console.print(f"  curator_id: {f.curator_id}")
        console.print(f"  status:     [{c}]{f.status}[/]")
        if f.supersedes_id:
            console.print(f"  supersedes: {f.supersedes_id}")
        if f.expires_at:
            console.print(f"  expires_at: {f.expires_at.isoformat()}")


@status_app.command("report")
def status_report(
    ctx: typer.Context,
    source_id: str = typer.Option(
        None, "--source",
        help="Limit report to a specific source_id (default: all sources combined).",
    ),
) -> None:
    """Show count of files by classification status bucket."""
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)

    counts = rt.file_repo.count_by_status(source_id=source_id)
    total = sum(counts.values())

    if rt.json_output:
        import json
        typer.echo(json.dumps({
            "source_id": source_id,
            "total": total,
            "counts": counts,
        }, indent=2))
        return

    title = f"Status report ({source_id or 'all sources'})"
    console.print(f"\n[bold]{title}[/]")
    console.print(f"  Total files: {total:,}")
    if total == 0:
        return

    # Display in priority order with color
    ordered = [("vital", "bright_green"), ("active", "cyan"),
               ("provisional", "yellow"), ("junk", "red")]
    for bucket, color in ordered:
        n = counts.get(bucket, 0)
        pct = 100.0 * n / total if total else 0.0
        bar_len = int(pct / 2)  # 50-char max
        bar = "#" * bar_len
        console.print(
            f"  [{color}]{bucket:>11}[/]: {n:>7,} ({pct:>5.1f}%)  [{color}]{bar}[/]"
        )


# ------------------------------------------------------------------
# v1.7.6 (T-B04): curator scan-pii - PII regex scanner
# ------------------------------------------------------------------

@app.command(name="scan-pii")
def scan_pii_cmd(
    ctx: typer.Context,
    target: str = typer.Argument(
        ..., help="File or directory to scan for PII patterns.",
    ),
    recursive: bool = typer.Option(
        True, "--recursive/--no-recursive",
        help="For directory targets, walk subdirectories (default: yes).",
    ),
    extension: list[str] = typer.Option(
        None, "--ext",
        help="File extension filter (with dot, lowercase). Repeatable. "
             "Example: --ext .txt --ext .csv",
    ),
    head_bytes: int = typer.Option(
        None, "--head-bytes",
        help="Override the per-file byte cap (default: 2 MB).",
    ),
    show_matches: bool = typer.Option(
        False, "--show-matches",
        help="Print every individual match (redacted form). Default "
             "shows per-file summary only.",
    ),
    high_only: bool = typer.Option(
        False, "--high-only",
        help="Only report files containing HIGH-severity matches (SSN / credit card).",
    ),
    csv_output: bool = typer.Option(
        False, "--csv",
        help="Emit CSV instead of the pretty table or JSON. With "
             "--show-matches the CSV is one row per match (source, "
             "line, pattern, severity, redacted); without it, one "
             "row per file (source, match_count, has_high, by_pattern). "
             "Mutually exclusive with --json (JSON wins).",
    ),
    no_header: bool = typer.Option(
        False, "--no-header",
        help="Suppress the CSV header row. Only meaningful with --csv.",
    ),
) -> None:
    """Scan a file or directory for PII patterns (T-B04, v1.7.6).

    Detects SSN, credit card, US phone, and email patterns via regex.
    Reports per-file match counts and (optionally) individual matches
    in REDACTED form (last 4 chars visible; the rest masked).

    Examples:
        curator scan-pii ./my_docs --ext .txt --ext .md
        curator scan-pii ./client_files --high-only --show-matches
        curator --json scan-pii ./report.csv
    """
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)

    # Optionally rebuild the scanner with a custom head_bytes
    if head_bytes is not None:
        from curator.services.pii_scanner import PIIScanner as _PIIScanner
        scanner = _PIIScanner(head_bytes=head_bytes)
    else:
        scanner = rt.pii_scanner

    from pathlib import Path as _Path
    target_path = _Path(target)

    if target_path.is_file():
        reports = [scanner.scan_file(target_path)]
    elif target_path.is_dir():
        reports = scanner.scan_directory(
            target_path,
            recursive=recursive,
            extensions=list(extension) if extension else None,
        )
    else:
        console.print(f"[red]Target not found: {target}[/]")
        raise typer.Exit(code=1)

    if high_only:
        reports = [r for r in reports if r.has_high_severity]

    if rt.json_output:
        import json as _json
        payload = []
        for r in reports:
            payload.append({
                "source": r.source,
                "bytes_scanned": r.bytes_scanned,
                "truncated": r.truncated,
                "error": r.error,
                "match_count": r.match_count,
                "has_high_severity": r.has_high_severity,
                "by_pattern": r.by_pattern(),
                "matches": (
                    [
                        {
                            "pattern": m.pattern_name,
                            "severity": m.severity.value,
                            "redacted": m.redacted,
                            "line": m.line,
                            "offset": m.offset,
                        }
                        for m in r.matches
                    ] if show_matches else None
                ),
            })
        typer.echo(_json.dumps(payload, indent=2))
        return

    # CSV output (v1.7.22) - one row per match if --show-matches, else
    # one row per file. Mirrors v1.7.19's audit-summary --csv pattern.
    if csv_output:
        import csv as _csv
        import sys as _sys
        writer = _csv.writer(_sys.stdout, lineterminator="\n")
        if show_matches:
            # Per-match rows: lets users grep / sort / pivot by pattern
            if not no_header:
                writer.writerow(["source", "line", "offset", "pattern", "severity", "redacted"])
            for r in reports:
                for m in r.matches:
                    writer.writerow([
                        r.source,
                        m.line,
                        m.offset,
                        m.pattern_name,
                        m.severity.value,
                        m.redacted,
                    ])
        else:
            # Per-file rows: high-level summary. by_pattern as a
            # semicolon-joined "name=count;name=count" string keeps it
            # in a single CSV cell that Excel can still pivot on.
            if not no_header:
                writer.writerow(["source", "match_count", "has_high", "by_pattern", "truncated", "error"])
            for r in reports:
                by_pat = r.by_pattern()
                by_pat_str = ";".join(f"{k}={v}" for k, v in sorted(by_pat.items()))
                writer.writerow([
                    r.source,
                    r.match_count,
                    "yes" if r.has_high_severity else "no",
                    by_pat_str,
                    "yes" if r.truncated else "no",
                    r.error or "",
                ])
        return

    # Rich pretty-print
    total_files = len(reports)
    files_with_matches = sum(1 for r in reports if r.match_count > 0)
    high_files = sum(1 for r in reports if r.has_high_severity)
    total_matches = sum(r.match_count for r in reports)
    errors = sum(1 for r in reports if r.error is not None)

    console.print(f"\n[bold]PII scan results[/]")
    console.print(f"  Target:           {target}")
    console.print(f"  Files scanned:    {total_files:,}")
    if errors:
        console.print(f"  [red]Errors:[/]           {errors:,}")
    console.print(f"  Files with PII:   {files_with_matches:,}")
    if high_files:
        console.print(f"  [red]HIGH severity:[/]    {high_files:,}")
    console.print(f"  Total matches:    {total_matches:,}")

    if total_matches == 0:
        console.print("\n[green]No PII patterns detected.[/]")
        return

    console.print("\n[bold]Per-file findings:[/]")
    for r in reports:
        if r.error:
            console.print(f"  [yellow]⚠  {r.source}[/]: {r.error}")
            continue
        if r.match_count == 0:
            continue
        color = "red" if r.has_high_severity else "yellow"
        truncation = " [dim](truncated)[/]" if r.truncated else ""
        console.print(
            f"  [{color}]{r.source}[/]{truncation}"
        )
        counts = r.by_pattern()
        pattern_strs = []
        for name, n in sorted(counts.items()):
            pattern_strs.append(f"{name}={n}")
        console.print(f"    [{color}]{', '.join(pattern_strs)}[/]")
        if show_matches:
            for m in r.matches:
                sev_color = "red" if m.severity.value == "high" else "yellow"
                console.print(
                    f"      L{m.line:>4}  [{sev_color}]{m.pattern_name:>12}[/]  "
                    f"{m.redacted}"
                )




# ------------------------------------------------------------------
# v1.7.7 (T-B07): curator export-clean - metadata stripping for sharing
# ------------------------------------------------------------------

@app.command(name="export-clean")
def export_clean_cmd(
    ctx: typer.Context,
    source: str = typer.Argument(
        ..., help="Source file or directory.",
    ),
    destination: str = typer.Argument(
        ..., help="Destination path. Parent dirs are created as needed.",
    ),
    recursive: bool = typer.Option(
        True, "--recursive/--no-recursive",
        help="For directory sources, walk subdirectories (default: yes).",
    ),
    extension: list[str] = typer.Option(
        None, "--ext",
        help="File extension filter (with dot, lowercase). Repeatable.",
    ),
    drop_icc: bool = typer.Option(
        False, "--drop-icc",
        help="Also strip ICC color profiles from images (default: keep them; "
             "without ICC profile, color rendering breaks on wide-gamut monitors).",
    ),
    show_files: bool = typer.Option(
        False, "--show-files",
        help="Print per-file outcome (every file). Default: only summary + failures.",
    ),
) -> None:
    """Strip embedded metadata from files during export (T-B07, v1.7.7).

    Copies files from SOURCE to DESTINATION, removing privacy-leaking
    metadata along the way:

      - Images (.jpg/.jpeg/.png/.tiff/.webp): EXIF (incl. GPS coords),
        XMP, IPTC, PNG text chunks. ICC color profile is kept by default.
      - DOCX (.docx/.docm/.dotx/.dotm): docProps/core.xml + app.xml
        author/company metadata. Document content is preserved.
      - PDF: /Author /Creator /Producer /Title /Subject /Keywords
        /CreationDate /ModDate. Pages are preserved.
      - Other types: byte-for-byte passthrough copy.

    SOURCE files are NEVER modified. Output goes to DESTINATION.

    Examples:
        curator export-clean ./photos ./photos_clean
        curator export-clean ./client_notes.docx ./shareable.docx
        curator export-clean ./client_data ./public --ext .pdf --ext .docx
    """
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)

    # Build a custom stripper if --drop-icc was specified
    if drop_icc:
        from curator.services.metadata_stripper import MetadataStripper as _MS
        stripper = _MS(keep_icc_profile=False)
    else:
        stripper = rt.metadata_stripper

    from pathlib import Path as _Path
    src_path = _Path(source)
    dst_path = _Path(destination)

    if src_path.is_file():
        # Single-file mode
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        result = stripper.strip_file(src_path, dst_path)
        results = [result]
        from datetime import datetime as _dt
        from curator.services.metadata_stripper import StripReport as _SR
        report = _SR(
            started_at=_dt.utcnow(), completed_at=_dt.utcnow(),
            results=results,
        )
    elif src_path.is_dir():
        report = stripper.strip_directory(
            src_path, dst_path,
            recursive=recursive,
            extensions=list(extension) if extension else None,
        )
    else:
        console.print(f"[red]Source not found: {source}[/]")
        raise typer.Exit(code=1)

    if rt.json_output:
        import json as _json
        payload = {
            "duration_seconds": report.duration_seconds,
            "total_count": report.total_count,
            "stripped_count": report.stripped_count,
            "passthrough_count": report.passthrough_count,
            "skipped_count": report.skipped_count,
            "failed_count": report.failed_count,
            "results": [
                {
                    "source": r.source,
                    "destination": r.destination,
                    "outcome": r.outcome.value,
                    "bytes_in": r.bytes_in,
                    "bytes_out": r.bytes_out,
                    "metadata_fields_removed": r.metadata_fields_removed,
                    "error": r.error,
                }
                for r in report.results
            ],
        }
        typer.echo(_json.dumps(payload, indent=2))
        return

    # Rich pretty-print
    console.print(f"\n[bold]Metadata export-clean[/]")
    console.print(f"  Source:           {source}")
    console.print(f"  Destination:      {destination}")
    console.print(f"  Duration:         {report.duration_seconds:.2f}s")
    console.print(f"  Total files:      {report.total_count:,}")
    if report.stripped_count:
        console.print(f"  [green]Stripped:[/]         {report.stripped_count:,}")
    if report.passthrough_count:
        console.print(f"  [dim]Passthrough:[/]      {report.passthrough_count:,}")
    if report.skipped_count:
        console.print(f"  [yellow]Skipped (filter):[/] {report.skipped_count:,}")
    if report.failed_count:
        console.print(f"  [red]Failed:[/]           {report.failed_count:,}")

    # Always show failures
    failures = [r for r in report.results if r.outcome.value == "failed"]
    if failures:
        console.print("\n[red]Failures:[/]")
        for r in failures[:20]:
            console.print(f"  [red]X[/] {r.source}")
            if r.error:
                console.print(f"      [dim]{r.error}[/]")
        if len(failures) > 20:
            console.print(f"  [dim]... and {len(failures) - 20} more[/]")

    # Optionally show per-file outcomes for the stripped/passthrough set
    if show_files:
        console.print("\n[bold]Per-file outcomes:[/]")
        for r in report.results:
            if r.outcome.value == "failed":
                continue  # already shown above
            color = {"stripped": "green", "passthrough": "dim",
                     "skipped": "yellow"}.get(r.outcome.value, "white")
            fields = ""
            if r.metadata_fields_removed:
                fields = f" [dim]({', '.join(r.metadata_fields_removed)})[/]"
            console.print(
                f"  [{color}]{r.outcome.value:>11}[/]  {r.source}{fields}"
            )

    if report.failed_count > 0:
        raise typer.Exit(code=1)



# ------------------------------------------------------------------
# v1.7.8 (T-B05): curator tier - Tiered Storage Manager
# ------------------------------------------------------------------

@app.command(name="tier")
def tier_cmd(
    ctx: typer.Context,
    recipe: str = typer.Argument(
        ..., help="Tier recipe: 'cold' (stale provisional), 'expired' "
                  "(past expires_at), or 'archive' (stale vital).",
    ),
    min_age_days: int = typer.Option(
        None, "--min-age-days",
        help="Override default staleness threshold. Default 90 for "
             "cold, 365 for archive. Ignored for expired (uses expires_at).",
    ),
    source_id: str = typer.Option(
        None, "--source-id",
        help="Restrict scan to one source (e.g. 'local').",
    ),
    root_prefix: str = typer.Option(
        None, "--root",
        help="Restrict to files whose source_path starts with this prefix.",
    ),
    show_files: bool = typer.Option(
        False, "--show-files",
        help="Print every candidate path. Default: summary only.",
    ),
    limit: int = typer.Option(
        None, "--limit",
        help="Limit displayed candidates (after sort, oldest-first). "
             "Doesn't affect counts or aggregate sizes in the summary.",
    ),
) -> None:
    """Tiered storage manager — identify files for cold-tier migration (T-B05).

    Three named recipes match the most common transitions:

      cold     -- status='provisional' AND last_scanned_at older than
                  --min-age-days (default 90). These files aren't active
                  work but haven't been trashed; they're cold-storage
                  candidates.
      expired  -- expires_at is set AND in the past. Files explicitly
                  marked with a TTL via 'curator status set --expires-in-days'.
      archive  -- status='vital' AND last_scanned_at older than
                  --min-age-days (default 365). Long-stable vital files
                  belong in an immutable archive store.

    This command is detect-only. To migrate candidates, run:

        curator tier cold --root C:/Work --show-files | grep -oP ...
        curator migrate <src_source_id> <src_root> <dst_root> --apply

    A future v1.8 will add --apply --target <dst> for one-step move.

    Examples:
        curator tier cold
        curator tier cold --min-age-days 180 --root C:/Users/jmlee
        curator tier expired --show-files
        curator tier archive --min-age-days 730 --source-id local
    """
    from curator.services.tier import TierCriteria, TierRecipe

    rt: CuratorRuntime = ctx.obj
    console = _console(rt)

    # Parse recipe
    try:
        recipe_enum = TierRecipe.from_string(recipe)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)

    # Default min_age_days per recipe
    if min_age_days is None:
        defaults = {
            TierRecipe.COLD: 90,
            TierRecipe.EXPIRED: 0,    # not used
            TierRecipe.ARCHIVE: 365,
        }
        min_age_days = defaults[recipe_enum]

    criteria = TierCriteria(
        recipe=recipe_enum,
        min_age_days=min_age_days,
        source_id=source_id,
        root_prefix=root_prefix,
    )

    report = rt.tier.scan(criteria)

    # Audit event: log the suggest action with criteria
    rt.audit.log(
        actor="cli.tier",
        action="tier.suggest",
        entity_type="tier_scan",
        entity_id=recipe_enum.value,
        details={
            "recipe": recipe_enum.value,
            "min_age_days": min_age_days,
            "source_id": source_id,
            "root_prefix": root_prefix,
            "candidate_count": report.candidate_count,
            "total_size_bytes": report.total_size,
        },
    )

    if rt.json_output:
        import json as _json
        payload = {
            "recipe": recipe_enum.value,
            "criteria": {
                "min_age_days": min_age_days,
                "source_id": source_id,
                "root_prefix": root_prefix,
            },
            "candidate_count": report.candidate_count,
            "total_size_bytes": report.total_size,
            "duration_seconds": report.duration_seconds,
            "by_source": report.by_source(),
            "candidates": [
                {
                    "curator_id": str(c.file.curator_id),
                    "source_id": c.file.source_id,
                    "source_path": c.file.source_path,
                    "size": c.file.size,
                    "status": c.file.status,
                    "last_scanned_at": c.file.last_scanned_at.isoformat() if c.file.last_scanned_at else None,
                    "expires_at": c.file.expires_at.isoformat() if c.file.expires_at else None,
                    "reason": c.reason,
                }
                for c in (report.candidates[:limit] if limit else report.candidates)
            ],
        }
        typer.echo(_json.dumps(payload, indent=2))
        return

    # Rich pretty-print
    color = {"cold": "cyan", "expired": "red", "archive": "blue"}[recipe_enum.value]
    console.print(f"\n[bold]Tier scan: [{color}]{recipe_enum.value}[/][/]")
    console.print(f"  Recipe:          [{color}]{recipe_enum.value}[/]")
    if recipe_enum != TierRecipe.EXPIRED:
        console.print(f"  Min age:         {min_age_days} days")
    if source_id:
        console.print(f"  Source filter:   {source_id}")
    if root_prefix:
        console.print(f"  Root filter:     {root_prefix}")
    console.print(f"  Duration:        {report.duration_seconds:.3f}s")
    console.print(f"  Candidates:      [{color}]{report.candidate_count:,}[/]")

    def _fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    console.print(f"  Total size:      {_fmt_size(report.total_size)}")
    if report.by_source():
        console.print("  By source:")
        for sid, n in sorted(report.by_source().items()):
            console.print(f"    {sid}: {n}")

    if report.candidate_count == 0:
        console.print(f"\n[green]No {recipe_enum.value} candidates found.[/]")
        return

    if show_files:
        console.print(f"\n[bold]Candidates (oldest-staleness first):[/]")
        display = report.candidates[:limit] if limit else report.candidates
        for c in display:
            console.print(
                f"  [{color}]{c.file.source_path}[/]"
            )
            console.print(
                f"      [dim]{_fmt_size(c.file.size or 0)}  -  {c.reason}[/]"
            )
        if limit and len(report.candidates) > limit:
            remaining = len(report.candidates) - limit
            console.print(f"  [dim]... and {remaining:,} more (use --limit to expand)[/]")
    else:
        console.print(
            f"\n[dim]Run with --show-files to see paths. "
            f"To migrate, pipe candidate paths into [bold]curator migrate[/bold].[/]"
        )



# ------------------------------------------------------------------
# v1.7.18: curator audit-summary - aggregate audit events by actor/action
# ------------------------------------------------------------------

@app.command(name="audit-summary")
def audit_summary_cmd(
    ctx: typer.Context,
    days: int = typer.Option(
        7, "--days",
        help="Look back this many days from now. Ignored if --since is set.",
    ),
    since: str = typer.Option(
        None, "--since",
        help="ISO datetime to filter events from (e.g. '2026-05-01'). "
             "Overrides --days when provided.",
    ),
    actor: str = typer.Option(
        None, "--actor",
        help="Filter to a single actor (e.g. 'cli.tier', 'gui.tier').",
    ),
    action: str = typer.Option(
        None, "--action",
        help="Filter to a single action (e.g. 'tier.suggest', 'scan.start').",
    ),
    limit: int = typer.Option(
        20, "--limit",
        help="Cap the number of (actor, action) groups displayed.",
    ),
    csv_output: bool = typer.Option(
        False, "--csv",
        help="Emit CSV (actor,action,count,first,last) instead of the "
             "pretty table. Useful for spreadsheet imports. Mutually "
             "exclusive with --json (JSON wins if both are set).",
    ),
    no_header: bool = typer.Option(
        False, "--no-header",
        help="Suppress the CSV header row. Useful when piping into "
             "another tool or appending to an existing CSV file. "
             "Only meaningful with --csv (ignored otherwise).",
    ),
    local: bool = typer.Option(
        False, "--local",
        help="Render timestamps in the system's local timezone instead "
             "of UTC. Affects the period header, JSON ISO timestamps, "
             "and CSV first/last columns. Relative-time deltas in the "
             "Rich table (e.g. '2m ago') are unaffected.",
    ),
    no_bars: bool = typer.Option(
        False, "--no-bars",
        help="Suppress the ASCII histogram column in the Rich table "
             "output. The histogram shows each group's count as a "
             "unicode bar normalized to the largest group. Affects "
             "pretty-print only; JSON and CSV outputs never include it.",
    ),
) -> None:
    """Aggregate recent audit events by actor and action (T-B04-adjacent, v1.7.18).

    Surfaces a forensic-grade summary of what the system has been doing
    by grouping audit events into (actor, action) pairs and showing
    counts + most recent timestamps. Useful for:

      * Reviewing what migrations, trash operations, or status changes
        have happened across CLI + GUI sessions
      * Spotting unusual actor activity (e.g. unexpected gui.tier
        events when you weren't using the GUI)
      * Lineage investigations ("when was the last time we trashed
        anything? what triggered it?")

    Read-only; doesn't modify the audit log. Emits no new audit events
    (would create a recursive loop). Output is pure summary.

    Examples:
        curator audit-summary
        curator audit-summary --days 30
        curator audit-summary --actor gui.tier
        curator audit-summary --action tier.suggest --days 1
        curator audit-summary --since 2026-05-01 --limit 50
    """
    from datetime import datetime, timedelta

    rt: CuratorRuntime = ctx.obj
    console = _console(rt)

    # Resolve the lookback window
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError as e:
            console.print(f"[red]Bad --since value: {e}[/]")
            raise typer.Exit(code=2)
    else:
        since_dt = datetime.utcnow() - timedelta(days=days)

    # Query
    entries = rt.audit_repo.query(
        since=since_dt,
        actor=actor,
        action=action,
        limit=50000,  # generous; we aggregate below
    )

    # Aggregate by (actor, action)
    groups: dict[tuple[str, str], dict] = {}
    for e in entries:
        key = (e.actor, e.action)
        g = groups.setdefault(key, {"count": 0, "last": None, "first": None})
        g["count"] += 1
        if g["last"] is None or e.occurred_at > g["last"]:
            g["last"] = e.occurred_at
        if g["first"] is None or e.occurred_at < g["first"]:
            g["first"] = e.occurred_at

    # Sort: most-active groups first
    sorted_groups = sorted(
        groups.items(),
        key=lambda kv: kv[1]["count"],
        reverse=True,
    )

    # v1.7.20: timezone-aware formatting helper.
    # The audit_repo stores naive UTC datetimes; when --local is set we
    # attach UTC tzinfo then convert to system local before formatting.
    def _fmt_ts(dt):
        if dt is None:
            return None
        if local:
            from datetime import timezone
            return dt.replace(tzinfo=timezone.utc).astimezone().isoformat()
        return dt.isoformat()

    # JSON output (machine-readable)
    if rt.json_output:
        import json as _json
        payload = {
            "since": _fmt_ts(since_dt) or since_dt.isoformat(),
            "total_events": len(entries),
            "group_count": len(groups),
            "filters": {
                "actor": actor,
                "action": action,
            },
            "timezone": "local" if local else "utc",
            "groups": [
                {
                    "actor": k[0],
                    "action": k[1],
                    "count": v["count"],
                    "first": _fmt_ts(v["first"]),
                    "last": _fmt_ts(v["last"]),
                }
                for k, v in sorted_groups[:limit]
            ],
        }
        typer.echo(_json.dumps(payload, indent=2))
        return

    # CSV output (v1.7.19) - simple flat table for spreadsheet imports.
    # JSON wins if both flags are set (the early-return above means we
    # never reach this branch when --json was set).
    if csv_output:
        import csv as _csv
        import sys as _sys
        writer = _csv.writer(_sys.stdout, lineterminator="\n")
        # v1.7.20: --no-header suppresses the header row
        if not no_header:
            writer.writerow(["actor", "action", "count", "first", "last"])
        for (actor_v, action_v), g in sorted_groups[:limit]:
            # v1.7.20: --local converts timestamps to system local TZ
            writer.writerow([
                actor_v,
                action_v,
                g["count"],
                _fmt_ts(g["first"]) or "",
                _fmt_ts(g["last"]) or "",
            ])
        return

    # Rich pretty-print
    period_end = datetime.utcnow()
    period_start = since_dt
    # v1.7.20: --local converts the header timestamps to system local TZ
    if local:
        from datetime import timezone
        period_end_display = period_end.replace(tzinfo=timezone.utc).astimezone()
        period_start_display = period_start.replace(tzinfo=timezone.utc).astimezone()
        tz_label = "local"
    else:
        period_end_display = period_end
        period_start_display = period_start
        tz_label = "UTC"
    console.print(f"\n[bold]Audit summary[/]")
    console.print(f"  Period:        {period_start_display:%Y-%m-%d %H:%M} -> {period_end_display:%Y-%m-%d %H:%M}  [dim]({tz_label})[/]")
    if actor:
        console.print(f"  Actor filter:  {actor}")
    if action:
        console.print(f"  Action filter: {action}")
    console.print(f"  Total events:  [cyan]{len(entries):,}[/]")
    console.print(f"  Unique groups: [cyan]{len(groups):,}[/]")

    if not groups:
        console.print("\n[green]No events in this window.[/]")
        return

    # Friendly relative-time formatter
    def _ago(dt) -> str:
        if dt is None:
            return ""
        delta = period_end - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"

    # Table output
    from rich.table import Table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Actor", style="cyan")
    table.add_column("Action", style="magenta")
    table.add_column("Count", justify="right", style="bold")
    # v1.7.21: ASCII histogram column (opt-out via --no-bars). Width
    # normalized to the largest count in the displayed slice so the
    # most-active group always has the longest bar.
    if not no_bars:
        # Fixed width and no_wrap so a 20-char bar always fits on one
        # line even when piped to a narrow non-TTY destination. Rich
        # would otherwise auto-size the column based on content and
        # wrap bars across multiple lines.
        table.add_column("Activity", style="green", width=22, no_wrap=True)
    table.add_column("First seen")
    table.add_column("Last seen")

    # Compute max count for bar normalization (only over the displayed
    # slice; otherwise small groups would always look tiny next to
    # outliers that were cut off by --limit).
    displayed = sorted_groups[:limit]
    max_count = max((g["count"] for _, g in displayed), default=1)
    BAR_WIDTH = 20  # max bar length in characters

    for (actor_v, action_v), g in displayed:
        row = [
            actor_v,
            action_v,
            f"{g['count']:,}",
        ]
        if not no_bars:
            # v1.7.24: TTY-aware bar character.
            # In an interactive UTF-8 terminal use U+2588 FULL BLOCK for
            # prettier rendering; when piped (or on a non-UTF-8 console)
            # fall back to ASCII '#' to avoid the cp1252 encoder crash
            # diagnosed in v1.7.21 (lesson #50).
            import sys as _sys
            _enc = (_sys.stdout.encoding or "").lower().replace("-", "")
            _bar_ch = (
                "\u2588"
                if _sys.stdout.isatty() and _enc.startswith("utf")
                else "#"
            )
            bar_len = max(1, round(g["count"] / max_count * BAR_WIDTH))
            row.append(_bar_ch * bar_len)
        row.extend([_ago(g["first"]), _ago(g["last"])])
        table.add_row(*row)
    console.print(table)

    if len(sorted_groups) > limit:
        remaining = len(sorted_groups) - limit
        console.print(f"\n[dim]... and {remaining:,} more groups (use --limit to expand)[/]")

if __name__ == "__main__":  # pragma: no cover
    app()
