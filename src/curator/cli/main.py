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
gdrive_app = typer.Typer(
    help="Google Drive auth + per-alias credential management.",
    no_args_is_help=True,
)
app.add_typer(gdrive_app, name="gdrive")


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
# v1.0.0a1: Migration tool (Feature M Phase 1)
# ---------------------------------------------------------------------------


@app.command(name="migrate")
def migrate_cmd(
    ctx: typer.Context,
    src_source_id: str = typer.Argument(
        ...,
        help="Source plugin id whose files to migrate (e.g. 'local').",
    ),
    src_root: str = typer.Argument(
        ...,
        help="Path prefix at the source. Only files under this prefix are candidates.",
    ),
    dst_root: str = typer.Argument(
        ...,
        help="Path prefix at the destination. Subpaths are preserved.",
    ),
    extensions: Optional[str] = typer.Option(
        None, "--ext",
        help="Comma-separated extension filter (e.g. '.mp3,.flac'). Case-insensitive.",
    ),
    apply: bool = typer.Option(
        False, "--apply",
        help="Required to actually perform the moves. Without this, plan-only.",
    ),
    verify_hash: bool = typer.Option(
        True, "--verify-hash/--no-verify-hash",
        help="Recompute xxhash3_128 of the destination after copy and require "
             "a match before updating the index. Default ON (Hash-Verify-Before-Move "
             "Constitutional discipline).",
    ),
) -> None:
    """Relocate files across paths/sources with index integrity (Phase 1).

    Same-source local-to-local migration with hash-verify-before-move per file.
    The curator_id stays constant so lineage edges + bundle memberships persist.
    Source files are trashed (recoverable via OS Recycle Bin) only after the
    destination is verified.

    Examples:
      curator migrate local C:/Music D:/Music             # plan only
      curator migrate local C:/Music D:/Music --apply     # actually move
      curator migrate local C:/Music D:/Music --apply --ext .mp3,.flac

    Phase 2 will add cross-source migration (local <-> gdrive), --resume,
    worker concurrency, and a GUI Migrate tab.
    """
    rt: CuratorRuntime = ctx.obj
    console = _console(rt)
    err = _err_console(rt)
    ext_list: list[str] | None = None
    if extensions:
        ext_list = [e.strip() for e in extensions.split(",") if e.strip()]

    try:
        plan = rt.migration.plan(
            src_source_id=src_source_id,
            src_root=src_root,
            dst_root=dst_root,
            extensions=ext_list,
        )
    except ValueError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2) from e

    # --- Plan rendering -------------------------------------------------
    if not apply:
        _render_migration_plan(rt, plan, verify_hash=verify_hash, console=console)
        return

    if plan.safe_count == 0:
        if rt.json_output:
            typer.echo(json.dumps({
                "action": "migrate.apply",
                "moved": 0,
                "skipped": plan.caution_count + plan.refuse_count,
                "failed": 0,
                "reason": "no SAFE files in plan",
            }, indent=2))
        else:
            console.print(
                "[yellow]No SAFE files to migrate. "
                f"({plan.caution_count} CAUTION, {plan.refuse_count} REFUSE skipped.)[/]"
            )
        return

    # --- Apply rendering ------------------------------------------------
    db_guard = Path(rt.db.db_path) if rt.db.db_path else None
    report = rt.migration.apply(
        plan, verify_hash=verify_hash, db_path_guard=db_guard,
    )
    _render_migration_report(rt, report, console=console)
    # Non-zero exit if anything failed
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
            "\n[dim]Re-run with [bold]--apply[/bold] to perform the moves.[/]"
        )
    else:
        console.print("\n[yellow]Nothing to do.[/]")


def _render_migration_report(rt, report, *, console=None) -> None:
    """Render a migration apply report."""
    moves = report.moves
    moved = [m for m in moves if m.outcome and m.outcome.value == "moved"]
    skipped = [m for m in moves if m.outcome and m.outcome.value.startswith("skipped")]
    failed = [m for m in moves if m.outcome and m.outcome.value in ("failed", "hash_mismatch")]

    if console is None:
        console = _console(rt)
    if rt.json_output:
        typer.echo(json.dumps({
            "action": "migrate.apply",
            "moved": len(moved),
            "skipped": len(skipped),
            "failed": len(failed),
            "bytes_moved": report.bytes_moved,
            "duration_seconds": report.duration_seconds,
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
        }, indent=2, default=str))
        return

    console.print(
        f"\n[bold green]Migration applied[/] in {report.duration_seconds:.2f}s"
    )
    console.print(
        f"  [green]MOVED[/]: {len(moved)}    "
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


if __name__ == "__main__":  # pragma: no cover
    app()
