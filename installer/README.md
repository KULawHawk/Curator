# Curator stack installer

A single-script installer for the Curator + atrium-citation + atrium-safety stack on Windows.

## What it does

Idempotently brings the local install to a known-good state. Run it after a fresh clone, after pulling new code, after a broken state, or any time you want to reset.

**End state guarantees:**
- venv at `Curator\.venv` exists
- All three packages (`curator`, `curatorplug-atrium-citation`, `curatorplug-atrium-safety`) installed editable in the venv at the versions their `pyproject.toml` declares
- Each package imports cleanly and reports its expected version
- `claude_desktop_config.json` registers `curator-mcp` at the venv path with `--db` pointing at a known canonical DB
- The canonical DB exists and passes `PRAGMA integrity_check`
- Any prior corrupt DB is backed up (never deleted) before being replaced
- Prior config is backed up before being replaced

**End state non-guarantees:**
- This script does not install Python 3.13 itself — you need that pre-installed.
- This script does not clone the source repos — they need to already exist at `$RepoRoot`.
- This script does not start or stop Claude Desktop. It detects whether it's running and aborts if so (because Claude Desktop file-locks `curator-mcp.exe`).

## How to run

### Easiest: double-click

1. Quit Claude Desktop fully (system tray icon → Quit, not just close the window).
2. Double-click `Install-Curator.bat`.
3. Press a key when prompted; the installer runs.
4. When it finishes, restart Claude Desktop.

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

## What gets touched

| File / dir | Action |
|---|---|
| `$RepoRoot\Curator\.venv\` | Created if missing; otherwise reused |
| `$RepoRoot\Curator\.venv\Lib\site-packages\curator-*.dist-info` | Refreshed |
| `$RepoRoot\Curator\.venv\Lib\site-packages\curatorplug_*-*.dist-info` | Refreshed |
| `$RepoRoot\.curator\curator.db` | Created (or backed up + recreated if corrupt) |
| `%APPDATA%\Claude\claude_desktop_config.json` | Backed up to `.bak.<timestamp>`, then patched |

## Recovery from a broken install

If something goes wrong mid-run, the script:
- Always backs up `claude_desktop_config.json` first; restores it if the new version doesn't parse
- Always quarantines (never deletes) corrupt DBs to a timestamped sibling file
- Aborts cleanly at the first error it can't recover from, with a clear message

To roll back the config:
```powershell
Copy-Item "$env:APPDATA\Claude\claude_desktop_config.json.bak.<timestamp>" `
          "$env:APPDATA\Claude\claude_desktop_config.json" -Force
```

To recover a quarantined DB (only if you're forensic-recovering data):
```powershell
# Quarantined files live next to the canonical DB
Get-ChildItem "$RepoRoot\.curator\curator.db.corrupt-backup-*"
```

## Why this exists

Before this installer, the Curator stack required a manual chain of:
- Clone three repos
- Create venv
- pip install -e for each repo
- Hand-edit `claude_desktop_config.json` to register `curator-mcp`
- Hope the default DB at `%LOCALAPPDATA%\curator\curator\curator.db` isn't corrupt
- Remember to re-run pip install when versions bump

Each of those steps had at least one failure mode that bit us in real use:
- pip install hits `WinError 32` when Claude Desktop has `curator-mcp.exe` open
- `claude_desktop_config.json` gets nuked by Claude Desktop if it can't parse a hand-edit
- The default DB location was a single shared point of failure (corrupted on 2026-05-09 with no obvious cause)
- Two installs (user-site + venv) drift over time

The installer makes all of that a one-click operation that converges on a known-good state regardless of starting state.

## Open questions / future work

- The script assumes Python 3.13. If you need a different version, find/replace `Python313` and `Python 3.13` references.
- It does not check that the source repos are at the expected git tags (e.g. Curator on `v1.6.1`). Could add a `-VerifyTags` flag later.
- It does not handle the OAuth flow for Google Drive — that still has to be done separately the first time.
