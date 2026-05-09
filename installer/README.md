# Curator stack installer

A single-script installer for the Curator + atrium-citation + atrium-safety stack on Windows. Built and tested 2026-05-09.

## TL;DR

1. Quit Claude Desktop fully (system tray → Quit)
2. Double-click `Install-Curator.bat`
3. Press a key when prompted; the installer runs ~10 steps in sequence
4. When it finishes, restart Claude Desktop
5. Curator MCP tools (`health_check`, `query_audit_log`, `list_sources`, etc.) are available in any chat

## What it does

Idempotent. Run on a fresh machine (initial install), after pulling new code (refresh), or after a broken state (recovery). It always converges on the same end state.

**End state guarantees:**
- venv at `Curator\.venv` exists with the right Python (3.11+)
- All three packages (`curator`, `curatorplug-atrium-citation`, `curatorplug-atrium-safety`) installed editable in the venv at the versions their `pyproject.toml` declares
- Each package imports cleanly and reports its expected version
- `claude_desktop_config.json` registers `curator-mcp` at the venv path, with the `CURATOR_CONFIG` env var pointing at a known canonical TOML config
- The canonical TOML pins curator-mcp to a known canonical DB
- The canonical DB exists and passes `PRAGMA integrity_check`
- **Step 9 verifies the actual Claude Desktop launch path works** — spawns `curator-mcp.exe` with the same command + args + env Claude Desktop will use, completes the MCP `initialize` + `tools/list` + `health_check` handshake, asserts ≥9 tools come back. If this fails, the prior config is automatically restored.
- Any prior corrupt DB is backed up (never deleted) before being replaced
- Prior config is backed up before being replaced

**End state non-guarantees:**
- Does NOT install Python 3.11+ itself (you need that pre-installed; detected via `py` launcher or path search)
- Does NOT clone the source repos (they need to already exist at `$RepoRoot`)
- Does NOT start or stop Claude Desktop (detects whether it's running and adapts)

## How to run

### Easiest: double-click

1. Quit Claude Desktop fully (system tray icon → Quit, not just close the window)
2. Double-click `Install-Curator.bat`
3. Press a key when prompted; the installer runs
4. When it finishes, restart Claude Desktop

### From a PowerShell prompt

```powershell
cd C:\Users\jmlee\Desktop\AL\Curator\installer
.\Install-Curator.ps1
```

### Useful flags

```powershell
# Dry run — report what would happen, change nothing
.\Install-Curator.ps1 -WhatIf

# Skip the interactive Claude-Desktop-running confirmation
.\Install-Curator.ps1 -Force

# Override defaults (after a future filesystem migration)
.\Install-Curator.ps1 -RepoRoot "C:\Users\jmlee\AdAstra"

# Pin DB to a specific path
.\Install-Curator.ps1 -CanonicalDb "C:\some\other\path\curator.db"
```

## The 10 steps

| # | What it does | Failure mode |
|---|---|---|
| 1 | Pre-flight: required directories + Python detection (multi-version: 3.11/3.12/3.13 via `py` launcher); Defender note; Claude Desktop install type (Store vs .exe) | Missing source repo or no Python 3.11+ → abort |
| 2 | Detect Claude Desktop file-lock on `curator-mcp.exe` | If running and -Force not set, prompts to quit |
| 3 | Ensure venv exists at `Curator\.venv` | If venv creation fails, abort |
| 4 | Editable install of all 3 packages (smart-skips if already current and Desktop running, to avoid WinError 32) | If install fails with WinError 32 mid-way, restores deleted .pth and exits cleanly |
| 5 | Sweep corrupt `~urator-*` dist-info from prior interrupted installs | (idempotent; just removes leftovers) |
| 6 | Runtime version check via subprocess Python import | If imports fail, abort with version info |
| 7 | Initialize canonical DB at `$RepoRoot\.curator\curator.db` + write `curator.toml` | If DB exists and is corrupt, quarantines it (never deletes) and re-initializes |
| 8 | Patch `claude_desktop_config.json`: backs up first, injects/refreshes curator entry pointing at venv `curator-mcp.exe` with `CURATOR_CONFIG` env var, preserves all other entries + preferences. Output is clean JSON via Python's json formatter. | If JSON is invalid after write, restores backup |
| 9 | **Real MCP probe** — spawns `curator-mcp.exe` with the same command+args+env Claude Desktop will use; sends `initialize` + `tools/list` + `tools/call health_check`; asserts ≥9 tools come back | If probe fails, restores config from backup automatically |
| 10 | Summary report | (always succeeds if got this far) |

Step 9 is the bulletproof gate. Catches the class of bug that took a chunk of 5/9 to debug — the installer used to write valid JSON config without verifying that Claude Desktop's launch of curator-mcp actually succeeded with that config.

## What gets touched

| File / dir | Action |
|---|---|
| `$RepoRoot\Curator\.venv\` | Created if missing; otherwise reused |
| `$RepoRoot\Curator\.venv\Lib\site-packages\curator-*.dist-info` | Refreshed (only if needed) |
| `$RepoRoot\Curator\.venv\Lib\site-packages\curatorplug_*-*.dist-info` | Refreshed (only if needed) |
| `$RepoRoot\.curator\curator.db` | Created (or backed up + recreated if corrupt) |
| `$RepoRoot\.curator\curator.toml` | Always written/refreshed |
| `%APPDATA%\Claude\claude_desktop_config.json` | Backed up to `.bak.<timestamp>`, then patched |

## Recovery from a broken install

If something goes wrong mid-run, the script:
- Always backs up `claude_desktop_config.json` first; restores it if Step 9's probe fails
- Always quarantines (never deletes) corrupt DBs to a timestamped sibling file
- Aborts cleanly at the first error it can't recover from, with a clear message and remediation

To roll back the config manually:
```powershell
Copy-Item "$env:APPDATA\Claude\claude_desktop_config.json.bak.<timestamp>" `
          "$env:APPDATA\Claude\claude_desktop_config.json" -Force
```

To recover a quarantined DB (only if you're forensic-recovering data):
```powershell
Get-ChildItem "$RepoRoot\.curator\curator.db.corrupt-backup-*"
```

## After install: register your sources

The canonical DB starts empty. To start tracking files:

```powershell
# Activate the venv
& C:\Users\jmlee\Desktop\AL\Curator\.venv\Scripts\Activate.ps1

# Register a source (example: track Curator repo itself)
curator sources add local "C:\Users\jmlee\Desktop\AL\Curator"
curator scan local
```

Or from any Claude Desktop chat:

> Use curator to add a local source pointing at C:\... and scan it.

## Why this exists

Before this installer, the Curator stack required a manual chain of:
- Clone three repos
- Create venv
- pip install -e for each repo
- Hand-edit `claude_desktop_config.json` to register `curator-mcp`
- Hope the default DB at `%LOCALAPPDATA%\curator\curator\curator.db` isn't corrupt
- Remember to re-run pip install when versions bump
- Restart Claude Desktop manually

Each of those steps had at least one failure mode that bit us in real use:
- pip install hits `WinError 32` when Claude Desktop has `curator-mcp.exe` open
- `claude_desktop_config.json` got nuked once by Claude Desktop with no obvious cause
- The default DB location at `%LOCALAPPDATA%` was a single shared point of failure (corrupted on 2026-05-09)
- Two installs (user-site + venv) drifted over time
- The `curator` CLI accepts `--db <path>`; `curator-mcp` does NOT — using `--db` in the JSON config crashed curator-mcp at startup with `unrecognized arguments`

The installer makes all of that a one-click operation that converges on a known-good state regardless of starting state, and **verifies via real MCP probe that Claude Desktop's launch path will succeed before declaring done**.

## Open enhancements

- More robust source-registration helper (currently you do this post-install)
- Optional `-VerifyTags` flag to ensure repos are at expected git tags
- Cross-machine config templating for CI/dev shared environments

## See also

- `docs/lessons/2026-05-09_install_mcp_session.md` — the bug taxonomy and lessons learned that drove this installer's design
- `Curator/src/curator/config/__init__.py` — the TOML config resolution order the installer relies on
