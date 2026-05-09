# Lessons learned — 2026-05-09 install/MCP debugging session

## Context

Jake asked Claude to verify the Curator + atrium-citation + atrium-safety stack
was working live and to build a single-click installer. The session that
followed exposed a chain of mistakes that cost real time and trust before the
system reached a verifiable working state. This document captures what
happened, why, and what changed in tooling so the same mistakes can't repeat.

## What was being built

- Curator v1.6.1 + atrium-citation v0.2.0 + atrium-safety v0.3.0 had been
  shipped earlier in the day
- The goal of this session was: prove they work end-to-end through Claude
  Desktop's MCP integration, then wrap the working install into a one-click
  PowerShell installer

## What actually went wrong (chronological)

1. **Started by doing isolated subprocess tests and calling them "verified"**.
   Spawned `curator-mcp.exe` in a Python subprocess, sent MCP handshake, saw
   tools come back, declared the integration working. This proved nothing
   about Claude Desktop's launch path.

2. **Inherited a "BOM theory" from an earlier Claude session** that had been
   debugging a wiped `claude_desktop_config.json` by writing PowerShell to
   strip BOMs. The actual cause of the wipe was never determined; the wipe
   happened ONCE and didn't recur. Treated the BOM workaround as load-bearing
   when it was speculative.

3. **Installer v1: wrote `args: ["--db", "<path>"]` for curator-mcp**.
   The `curator` CLI accepts `--db`. `curator-mcp` does NOT — only
   `--http`, `--no-auth`, `--port`, `--host`. Claude Desktop launched the
   server, the server crashed on every startup with
   `unrecognized arguments: --db`, and the MCP tools never reached chats.
   The bug existed because no test ever spawned curator-mcp the way
   Claude Desktop spawns it.

4. **Falsely declared "verified working" three times without restart-time
   evidence**. Each time, manual subprocess tests passed. None replicated
   what Claude Desktop actually does at startup. Jake had to point out the
   pattern explicitly.

5. **Repeatedly told Jake to "open a new chat to verify"** — this doesn't
   work. New chats inherit a tool snapshot from Claude Desktop's startup
   state. If `curator-mcp` was crashing at startup, no new chat would have
   the tools available. Jake's frustration was the correct signal.

6. **Confused `pip install -e` with no-op when curator-mcp.exe was held
   open**. Pip needs to refresh the entry-point shim; if Claude Desktop has
   the .exe open as a child process, Windows blocks the file replacement
   (`WinError 32`). Failed installs left the venv with deleted-but-not-
   replaced metadata; curator briefly stopped importing. Recovery worked
   but should never have been needed.

7. **Misread the corrupt DB recovery situation**. The default
   `%LOCALAPPDATA%\curator\curator\curator.db` was already partially corrupt
   when the session started (root cause unknown — possibly an interrupted
   write). Tried to dump-and-recover via `iterdump()`, which failed totally
   and produced an empty stub. Quarantined the original safely but reported
   the failure as recoverable when it wasn't.

8. **Kept conflating "the test works" with "the user-facing surface works"**.
   `curator doctor` returns OK against a freshly-initialized DB even when
   Claude Desktop's launch path is broken. The doctor command is necessary
   but not sufficient as a verification gate.

## Specific corrections owed (and made)

- The original Session B at 02:54 CDT today **was real** (audit_id 72 in the
  AppData DB before quarantine showed a real `migration.move` event with a
  Drive file ID `1SWHiyeb5Vz7WxOTbp_l_UkbIVv2vdg-o` as the destination).
  An earlier statement that the compaction summary contained "narrative
  invention" was wrong.
- The April 2026 chat note that "Microsoft Store version doesn't support
  claude_desktop_config.json MCP servers" was true at that time but no
  longer applies. As of late 2025 / today, the Store version does spawn
  servers from that config — verified by main.log lines 83611+ and the
  4:51 AM chat fad78c42 that successfully called `curator:health_check`.

## Lessons (numbered for direct reference in future work)

### L1. The verification gate must replicate the user-facing integration point.
For Claude Desktop's MCP server: spawn the server using the EXACT command +
args + env that Claude Desktop will use, do a real `initialize` +
`tools/list` + `tools/call` MCP handshake over stdio, assert the expected
tools come back. Anything less is unsupported speculation.

**Implementation:** `Install-Curator.ps1` Step 9 now does this. If the
probe fails, the config backup is automatically restored.

### L2. Don't inherit theories from prior sessions. Read the log first.
`mcp-server-curator.log` and `main.log` had the actual error
(`unrecognized arguments: --db`) sitting in plain text the entire session.
A 30-second log read at the start would have eliminated the BOM-theory and
"sandbox virtualization" rabbit holes entirely.

**Implementation:** Future debugging starts with a log scan, not a theory.
Documented in `docs/RUNBOOK_MCP_DEBUG.md` (forthcoming).

### L3. Don't tell the user to "start a new chat to verify."
A new chat's tool surface is determined by the Claude Desktop startup state.
If the server is crashing at startup, no chat will have tools. Always verify
from the current chat using CLI / process / log tools.

**Implementation:** Behavior change. No tooling captures this; it's a habit.

### L4. `pip install -e` while Claude Desktop is running is dangerous.
Smart-skip when nothing changed (avoid the lock entirely). When source
HAS changed and the file is locked, surface the issue clearly and abort
without leaving the venv in a broken state.

**Implementation:** Step 4 of `Install-Curator.ps1` now probes the venv
state first; if all three packages already report current versions, skips
the install entirely. If install is needed and fails with WinError 32,
restores the deleted .pth before exiting so import keeps working.

### L5. Don't conflate Curator's own healthchecks with integration tests.
`curator doctor` returns OK against any well-formed DB. It does not validate
that Claude Desktop's MCP launch path works. These are different surfaces
and need different tests.

**Implementation:** Step 9 is the integration gate. `curator doctor` was
demoted from "verification" to "informational stack info."

### L6. When something on disk goes wrong, don't try to recover before
inventorying what's intact. The corrupt-DB scare wasted effort on
`iterdump()`/`recover` attempts when the data was already accessible
through curator's normal query path. Direct sqlite tooling is stricter
than curator's reader; a "PRAGMA integrity_check" failure doesn't mean
"all data lost."

**Implementation:** `RUNBOOK_DB_RECOVERY.md` (forthcoming) documents the
correct triage order: try curator's normal path first, then direct sqlite,
then specialist recovery tools as a last resort.

### L7. Keep test data separate from the canonical state.
session_b_real DB is test data (synthetic 18 files used for v1.6.1
validation). It should never become the canonical/production DB.
The canonical DB lives at `$RepoRoot/.curator/curator.db` and starts
empty for new installs.

**Implementation:** Installer creates `.curator/` next to the source
tree; never points at session_b_real or any other ad-hoc DB.

### L8. Document the actual install layout so future debugging has a map.
Today's install paths are spread across:
  - `C:\Users\jmlee\Desktop\AL\Curator\.venv\` — editable-install venv
  - `C:\Users\jmlee\Desktop\AL\.curator\curator.db` — canonical DB
  - `C:\Users\jmlee\Desktop\AL\.curator\curator.toml` — canonical config
  - `%APPDATA%\Claude\claude_desktop_config.json` — Desktop integration
  - `%APPDATA%\Claude\logs\mcp-server-curator.log` — MCP launch log

**Implementation:** Documented in `installer/README.md` and in this file's
"Reference: install layout" section below.

### L9. The installer must surface system context, not assume it.
Multiple Pythons coexist on this machine (3.13 + 3.14.3 + Store launcher).
Defender real-time is on. Claude Desktop is the Store version. The
installer must observe and report these, not silently assume defaults.

**Implementation:** Step 1 now reports system Python, venv Python, and
Claude Desktop install type (Store vs .exe).

## Reference: install layout (post-installer)

```
C:\Users\jmlee\Desktop\AL\
├── .curator\
│   ├── curator.db                  # canonical DB; integrity-clean fresh
│   └── curator.toml                # CURATOR_CONFIG target
│                                   # pinned db_path + log/level
│
├── Curator\
│   ├── .venv\                      # editable install venv
│   │   ├── Lib\site-packages\
│   │   │   ├── curator-1.6.1.dist-info\
│   │   │   ├── curatorplug_atrium_citation-0.2.0.dist-info\
│   │   │   ├── curatorplug_atrium_safety-0.3.0.dist-info\
│   │   │   ├── __editable__.curator-1.6.1.pth
│   │   │   ├── __editable__.curatorplug_atrium_citation-0.2.0.pth
│   │   │   └── __editable__.curatorplug_atrium_safety-0.3.0.pth
│   │   └── Scripts\
│   │       ├── curator.exe         # CLI
│   │       ├── curator-mcp.exe     # MCP server (claude_desktop_config target)
│   │       └── curator-citation.exe
│   ├── installer\
│   │   ├── Install-Curator.ps1     # the installer
│   │   ├── Install-Curator.bat     # double-click wrapper
│   │   └── README.md
│   ├── docs\lessons\               # this file lives here
│   └── src\curator\                # source tree
│
├── curatorplug-atrium-citation\src\curatorplug\atrium_citation\
├── curatorplug-atrium-safety\src\curatorplug\atrium_safety\
└── session_b_real\                 # test artifact (preserved)
    └── curator-test.db             # 18 synthetic test files,
                                    # 44 audit entries from v1.6.1 validation

%APPDATA%\Claude\
├── claude_desktop_config.json      # has curator entry pointing at venv
│                                   # with CURATOR_CONFIG env var
├── claude_desktop_config.json.bak.* (multiple backups)
└── logs\
    ├── mcp-server-curator.log      # curator-mcp launch + JSON-RPC
    └── main.log                    # Desktop's MCP orchestration

%LOCALAPPDATA%\curator\curator\
├── corrupt_backup_20260509-161838\ # forensic artifact (do not touch)
└── curator.db                      # NOT used; canonical is in AL\.curator\
```

## Followups (open items)

- Multi-Python detection via `py --list` (currently hardcoded `Python313`)
- Compact JSON formatting (PowerShell `ConvertTo-Json` is verbose)
- Defender exclusion recommendation for the venv path
- `RUNBOOK_MCP_DEBUG.md` — what to read first when MCP tools don't surface
- `RUNBOOK_DB_RECOVERY.md` — triage order for corrupt Curator DBs
- Forensic recovery of `corrupt_backup_20260509-161838\curator.db`
  using specialist tools (low priority; the original Session B narrative
  is already validated through session_b_real)
