"""CLI command for ``curator mcp cleanup-orphans`` (v1.7.52, closes A4).

Finds and optionally terminates orphaned ``curator-mcp.exe`` processes.
An orphan is a curator-mcp.exe whose parent process has exited --
typically what happens when an MCP client (Claude Desktop, etc.) crashes
or is force-quit without cleanly stopping its MCP servers. Orphans
accumulate over time and each holds an open SQLite handle to the
Curator DB, which eventually exhausts handles.

Design choices:

* **psutil is required.** Detection needs cross-platform process
  enumeration with parent-process visibility. psutil is in [organize]
  and [all] extras; if missing, the command exits 2 with an install hint.
* **Dry-run by default.** Running without --kill just lists orphans
  and total curator-mcp.exe processes. Add --kill to actually
  terminate. Add --yes to skip the confirmation prompt.
* **Honors --json.** When runtime's json_output is set, emits a
  JSON payload. Useful for scripting / automation.
* **Cross-platform.** Targets curator-mcp.exe on Windows; falls back
  to curator-mcp (no extension) on POSIX. Both names checked.
* **Graceful kill.** Sends SIGTERM (Windows: TerminateProcess) first,
  waits 3s, then SIGKILL if the process hasn't exited.

Closes A4 from the bulletproof-live backlog.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import NamedTuple

import typer
from rich.console import Console
from rich.table import Table

from curator.cli.mcp_keys import mcp_app
from curator.cli.runtime import CuratorRuntime


# Process name(s) to match. We check both .exe and bare forms because
# Windows reports with .exe, POSIX without.
_CURATOR_MCP_NAMES = ("curator-mcp.exe", "curator-mcp")


class _ProcInfo(NamedTuple):
    """One enumerated curator-mcp.exe process, with parent liveness."""
    pid: int
    parent_pid: int
    parent_alive: bool
    parent_name: str  # or "<dead>" if parent gone
    create_time: datetime
    cmdline: tuple[str, ...]


def _enumerate_curator_mcp_processes() -> list[_ProcInfo]:
    """Return list of :class:`_ProcInfo` for all curator-mcp.exe processes.

    Requires :mod:`psutil`; raises ``ImportError`` with helpful message
    if missing.
    """
    try:
        import psutil
    except ImportError as e:
        raise ImportError(
            "psutil is required for `curator mcp cleanup-orphans`. "
            "Install it via:\n"
            "  pip install psutil>=5.9\n"
            "Or as part of the [organize] / [all] extras:\n"
            "  pip install -e \".[organize]\"\n"
            "  pip install -e \".[all]\""
        ) from e

    results: list[_ProcInfo] = []
    for proc in psutil.process_iter(["pid", "name", "ppid", "create_time", "cmdline"]):
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            if name not in _CURATOR_MCP_NAMES:
                continue

            ppid = info.get("ppid") or 0
            parent_alive = False
            parent_name = "<dead>"
            if ppid:
                try:
                    parent = psutil.Process(ppid)
                    parent_alive = parent.is_running()
                    parent_name = parent.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    parent_alive = False
                    parent_name = "<dead>"

            create_time_ts = info.get("create_time") or 0
            results.append(_ProcInfo(
                pid=info["pid"],
                parent_pid=ppid,
                parent_alive=parent_alive,
                parent_name=parent_name,
                create_time=datetime.fromtimestamp(create_time_ts),
                cmdline=tuple(info.get("cmdline") or ()),
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Stable ordering: oldest first
    results.sort(key=lambda p: p.create_time)
    return results


def _orphans_only(procs: list[_ProcInfo]) -> list[_ProcInfo]:
    """Filter to processes whose parent is dead."""
    return [p for p in procs if not p.parent_alive]


def _format_age(create_time: datetime, now: datetime) -> str:
    """Format a process age as human-readable."""
    delta = now - create_time
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s ago"
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    return f"{hours}h{minutes:02d}m ago"


def _emit_json(procs: list[_ProcInfo], orphans: list[_ProcInfo]) -> str:
    """Build the JSON payload for --json output."""
    return json.dumps({
        "total": len(procs),
        "orphans": len(orphans),
        "processes": [
            {
                "pid": p.pid,
                "parent_pid": p.parent_pid,
                "parent_alive": p.parent_alive,
                "parent_name": p.parent_name,
                "create_time": p.create_time.isoformat(),
                "is_orphan": not p.parent_alive,
                "cmdline": list(p.cmdline),
            }
            for p in procs
        ],
    }, indent=2)


def _render_table(console: Console, procs: list[_ProcInfo], orphans: list[_ProcInfo]) -> None:
    """Print a Rich table of all curator-mcp.exe processes."""
    table = Table(
        title=f"curator-mcp.exe processes "
              f"({len(procs)} total, {len(orphans)} orphaned)",
    )
    table.add_column("PID", justify="right")
    table.add_column("Parent PID", justify="right")
    table.add_column("Parent", style="cyan")
    table.add_column("Started")
    table.add_column("Status", style="bold")

    now = datetime.now()
    for p in procs:
        status = "[red]ORPHAN[/red]" if not p.parent_alive else "[green]ok[/green]"
        table.add_row(
            str(p.pid),
            str(p.parent_pid),
            p.parent_name,
            f"{p.create_time:%H:%M:%S} ({_format_age(p.create_time, now)})",
            status,
        )
    console.print(table)


def _kill_orphans(orphans: list[_ProcInfo], timeout: float = 3.0) -> tuple[int, list[tuple[int, str]]]:
    """Terminate each orphan; returns (killed_count, failures_list).

    Uses graceful terminate -> wait -> kill, matching common cross-
    platform process cleanup patterns.
    """
    import psutil  # already validated by caller

    killed = 0
    failures: list[tuple[int, str]] = []

    for orphan in orphans:
        try:
            proc = psutil.Process(orphan.pid)
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except psutil.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=timeout)
                except psutil.TimeoutExpired:
                    failures.append((orphan.pid, "process did not exit after kill"))
                    continue
            killed += 1
        except psutil.NoSuchProcess:
            killed += 1  # already gone -- idempotent
        except psutil.AccessDenied as e:
            failures.append((orphan.pid, f"access denied: {e}"))
        except Exception as e:  # noqa: BLE001
            failures.append((orphan.pid, f"{type(e).__name__}: {e}"))

    return killed, failures


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@mcp_app.command("cleanup-orphans")
def cleanup_orphans_cmd(
    ctx: typer.Context,
    kill: bool = typer.Option(
        False, "--kill",
        help="Actually terminate orphaned processes. Default is dry-run.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip the confirmation prompt when --kill is set.",
    ),
) -> None:
    """Find (and optionally kill) orphaned curator-mcp.exe processes.

    An "orphan" is a curator-mcp.exe whose parent process has exited.
    These accumulate when MCP clients (Claude Desktop, etc.) crash or
    are force-quit without cleanly stopping their MCP servers. Each
    orphan holds an open SQLite handle to the Curator DB, which
    eventually exhausts handles.

    By default, this command just LISTS what would be killed (dry-run).
    Pass --kill to actually terminate; --yes to skip the confirmation.

    \b
    Examples:
        curator mcp cleanup-orphans              # list (dry-run)
        curator mcp cleanup-orphans --kill       # confirm then kill
        curator mcp cleanup-orphans --kill --yes # kill without prompt
        curator --json mcp cleanup-orphans       # JSON output

    \b
    Exit codes:
        0 -- success (no orphans, or successfully killed)
        1 -- some kills failed
        2 -- psutil not installed, or --kill in JSON mode without --yes
    """
    rt: CuratorRuntime = ctx.obj
    console = Console(no_color=getattr(rt, "no_color", False))
    err_console = Console(stderr=True, no_color=getattr(rt, "no_color", False))
    json_mode = getattr(rt, "json_output", False)

    # Step 1: enumerate
    try:
        procs = _enumerate_curator_mcp_processes()
    except ImportError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)

    orphans = _orphans_only(procs)

    # Step 2: output (JSON or table)
    if json_mode:
        console.print(_emit_json(procs, orphans))
    else:
        if not procs:
            console.print("[green]No curator-mcp.exe processes found.[/green]")
            return
        _render_table(console, procs, orphans)
        if not orphans:
            console.print("[green]No orphans to clean up.[/green]")
            return

    # If no orphans, done regardless of --kill
    if not orphans:
        return

    # Step 3: kill?
    if not kill:
        if not json_mode:
            console.print(
                f"\n[yellow]Found {len(orphans)} orphan(s). "
                f"Re-run with --kill to terminate them.[/yellow]"
            )
        return

    # Step 4: confirm
    if not yes:
        if json_mode:
            err_console.print(
                "[red]--kill in JSON mode requires --yes "
                "(no interactive prompt available).[/red]"
            )
            raise typer.Exit(2)
        confirmed = typer.confirm(
            f"Terminate {len(orphans)} orphan curator-mcp process(es)?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Aborted.[/yellow]")
            return

    # Step 5: kill
    killed, failures = _kill_orphans(orphans)

    if json_mode:
        console.print(json.dumps({
            "killed": killed,
            "failed": [{"pid": pid, "error": err} for pid, err in failures],
        }, indent=2))
    else:
        if failures:
            console.print(f"[yellow]Terminated {killed} orphan(s); {len(failures)} failed:[/yellow]")
            for pid, err in failures:
                console.print(f"  PID {pid}: {err}")
        else:
            console.print(f"[green]Terminated {killed} orphan(s).[/green]")

    if failures:
        raise typer.Exit(1)
