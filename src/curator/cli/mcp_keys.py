"""CLI commands for ``curator mcp keys ...`` (v1.5.0 P2).

Implements ``generate`` / ``list`` / ``revoke`` / ``show`` subcommands
under the ``curator mcp keys`` group per
``docs/CURATOR_MCP_HTTP_AUTH_DESIGN.md`` v0.2 RATIFIED \u00a74.4.

Design choices specific to this layer:

* **Plaintext key shown once.** ``generate`` is the ONLY command that
  prints the full plaintext key. ``list`` and ``show`` print metadata
  only; ``revoke`` doesn't print anything secret.
* **JSON output supported.** Honors the global ``--json`` flag for
  scripting use.
* **Confirmation on revoke.** ``revoke`` prompts before removing
  unless ``--yes`` is passed (matches the CLI convention for other
  destructive operations).
"""
from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from curator.cli.runtime import CuratorRuntime
from curator.mcp.auth import (
    DuplicateNameError,
    KeyFileError,
    add_key,
    default_keys_file,
    load_keys,
    remove_key,
)


# ---------------------------------------------------------------------------
# Typer app structure
# ---------------------------------------------------------------------------


mcp_app = typer.Typer(
    help="MCP server management (HTTP auth keys, etc.).",
    no_args_is_help=True,
)
keys_app = typer.Typer(
    help="MCP API key management (generate/list/revoke/show).",
    no_args_is_help=True,
)
mcp_app.add_typer(keys_app, name="keys")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _console_for(rt: CuratorRuntime) -> Console:
    """Build a Rich console honoring the runtime's --no-color preference."""
    return Console(no_color=getattr(rt, "no_color", False))


def _err_console_for(rt: CuratorRuntime) -> Console:
    """Stderr console honoring --no-color."""
    return Console(stderr=True, no_color=getattr(rt, "no_color", False))


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@keys_app.command("generate")
def keys_generate_cmd(
    ctx: typer.Context,
    name: str = typer.Argument(
        ...,
        help=(
            "Unique name for the new key (e.g. 'claude-desktop-home', "
            "'scripts-prod'). Must not collide with an existing key."
        ),
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        "-d",
        help="Optional human-readable note (e.g. 'Home laptop integration').",
    ),
) -> None:
    """Generate a new MCP API key.

    Prints the full plaintext key to stdout. This is the ONLY time you
    can see the key value -- copy it now into your integration's config
    (Claude Desktop's MCP server settings, a script's env var, etc.).
    Curator stores only the SHA-256 hash; the plaintext cannot be
    recovered after this command exits.

    Examples::

        curator mcp keys generate claude-desktop-home
        curator mcp keys generate scripts-prod --description "Production scripts"
    """
    rt: CuratorRuntime = ctx.obj
    err = _err_console_for(rt)
    keys_path = default_keys_file()

    try:
        plaintext = add_key(name, description=description, path=keys_path)
    except DuplicateNameError as e:
        if rt.json_output:
            typer.echo(
                json.dumps({
                    "ok": False,
                    "error": "duplicate_name",
                    "name": name,
                    "message": str(e),
                }, indent=2),
                err=True,
            )
        else:
            err.print(f"[red]Error: {e}[/]")
            err.print(
                "\n[dim]Use [bold]curator mcp keys revoke <name>[/bold] "
                "to remove the existing key first, or pick a different name.[/]"
            )
        raise typer.Exit(code=1) from e
    except KeyFileError as e:
        if rt.json_output:
            typer.echo(
                json.dumps({
                    "ok": False,
                    "error": "key_file_error",
                    "message": str(e),
                }, indent=2),
                err=True,
            )
        else:
            err.print(f"[red]Error reading keys file: {e}[/]")
        raise typer.Exit(code=2) from e

    if rt.json_output:
        typer.echo(json.dumps({
            "ok": True,
            "name": name,
            "key": plaintext,
            "description": description,
            "keys_file": str(keys_path),
        }, indent=2))
        return

    console = _console_for(rt)
    console.print(f"\n[green]\u2713[/] Generated key [cyan]{name}[/]")
    console.print(f"\n[bold yellow]Save this key now -- it will not be shown again:[/]\n")
    console.print(f"    [bold cyan]{plaintext}[/]\n")
    console.print(f"[dim]Stored at: {keys_path}[/]")
    console.print(
        "\n[dim]Use this key in the [bold]Authorization: Bearer[/bold] header "
        "when connecting to [bold]curator-mcp --http[/bold].[/]"
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@keys_app.command("list")
def keys_list_cmd(ctx: typer.Context) -> None:
    """List all configured MCP API keys.

    Shows name, creation timestamp, last-used timestamp, and description
    for each key. Does NOT show the key values themselves -- those are
    only available at generation time.
    """
    rt: CuratorRuntime = ctx.obj
    err = _err_console_for(rt)
    keys_path = default_keys_file()

    try:
        keys = load_keys(keys_path)
    except KeyFileError as e:
        if rt.json_output:
            typer.echo(
                json.dumps({
                    "ok": False,
                    "error": "key_file_error",
                    "message": str(e),
                }, indent=2),
                err=True,
            )
        else:
            err.print(f"[red]Error reading keys file: {e}[/]")
        raise typer.Exit(code=2) from e

    if rt.json_output:
        typer.echo(json.dumps({
            "ok": True,
            "keys_file": str(keys_path),
            "keys": [
                {
                    "name": k.name,
                    "created_at": k.created_at,
                    "last_used_at": k.last_used_at,
                    "description": k.description,
                }
                for k in keys
            ],
        }, indent=2))
        return

    console = _console_for(rt)
    if not keys:
        console.print(
            f"\nNo API keys configured (file: [dim]{keys_path}[/])."
        )
        console.print(
            "\n[dim]Generate one with: "
            "[bold]curator mcp keys generate <name>[/bold][/]"
        )
        return

    table = Table(title=f"{len(keys)} API key(s)")
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("created", style="dim")
    table.add_column("last used", style="dim")
    table.add_column("description")

    for k in keys:
        table.add_row(
            k.name,
            k.created_at,
            k.last_used_at or "[never used]",
            k.description or "",
        )

    console.print(table)
    console.print(f"\n[dim]Keys file: {keys_path}[/]")


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


@keys_app.command("revoke")
def keys_revoke_cmd(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name of the key to revoke."),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt.",
    ),
) -> None:
    """Revoke (delete) an MCP API key.

    Once revoked, the key can no longer authenticate. Other keys are
    unaffected. This action cannot be undone -- generate a new key if
    you change your mind.
    """
    rt: CuratorRuntime = ctx.obj
    err = _err_console_for(rt)
    keys_path = default_keys_file()

    # Verify the key exists before prompting
    try:
        existing = load_keys(keys_path)
    except KeyFileError as e:
        if rt.json_output:
            typer.echo(
                json.dumps({
                    "ok": False,
                    "error": "key_file_error",
                    "message": str(e),
                }, indent=2),
                err=True,
            )
        else:
            err.print(f"[red]Error reading keys file: {e}[/]")
        raise typer.Exit(code=2) from e

    if not any(k.name == name for k in existing):
        if rt.json_output:
            typer.echo(
                json.dumps({
                    "ok": False,
                    "error": "not_found",
                    "name": name,
                }, indent=2),
                err=True,
            )
        else:
            err.print(f"[red]No key named [cyan]{name}[/] found.[/]")
            err.print(
                "\n[dim]Use [bold]curator mcp keys list[/bold] "
                "to see available keys.[/]"
            )
        raise typer.Exit(code=1)

    if not yes and not rt.json_output:
        console = _console_for(rt)
        confirmed = typer.confirm(
            f"Revoke key '{name}'? This cannot be undone.",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Cancelled.[/]")
            raise typer.Exit(code=0)

    removed = remove_key(name, path=keys_path)

    if rt.json_output:
        typer.echo(json.dumps({
            "ok": removed,
            "name": name,
            "removed": removed,
        }, indent=2))
        return

    console = _console_for(rt)
    if removed:
        console.print(f"\n[green]\u2713[/] Revoked key [cyan]{name}[/]")
    else:
        # Should be unreachable given the check above; defensive.
        console.print(f"\n[yellow]Key [cyan]{name}[/] was not found.[/]")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@keys_app.command("show")
def keys_show_cmd(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name of the key to show."),
) -> None:
    """Show metadata for one MCP API key.

    Shows name, creation timestamp, last-used timestamp, and
    description. Does NOT show the key value or its hash.
    """
    rt: CuratorRuntime = ctx.obj
    err = _err_console_for(rt)
    keys_path = default_keys_file()

    try:
        keys = load_keys(keys_path)
    except KeyFileError as e:
        if rt.json_output:
            typer.echo(
                json.dumps({
                    "ok": False,
                    "error": "key_file_error",
                    "message": str(e),
                }, indent=2),
                err=True,
            )
        else:
            err.print(f"[red]Error reading keys file: {e}[/]")
        raise typer.Exit(code=2) from e

    found = next((k for k in keys if k.name == name), None)
    if found is None:
        if rt.json_output:
            typer.echo(
                json.dumps({
                    "ok": False,
                    "error": "not_found",
                    "name": name,
                }, indent=2),
                err=True,
            )
        else:
            err.print(f"[red]No key named [cyan]{name}[/] found.[/]")
        raise typer.Exit(code=1)

    if rt.json_output:
        typer.echo(json.dumps({
            "ok": True,
            "name": found.name,
            "created_at": found.created_at,
            "last_used_at": found.last_used_at,
            "description": found.description,
        }, indent=2))
        return

    console = _console_for(rt)
    console.print(f"\n[bold]MCP API key [cyan]{found.name}[/]:[/]")
    console.print(f"  created:    [dim]{found.created_at}[/]")
    console.print(
        f"  last used:  [dim]{found.last_used_at or '[never used]'}[/]"
    )
    if found.description:
        console.print(f"  description: {found.description}")
