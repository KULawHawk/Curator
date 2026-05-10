# Curator — what to click first when you come back

**Status as of 2026-05-09:** Curator v1.6.3 stable. GUI running. Stack fully verified end-to-end.
**This file is the smoke-test checklist** — go through it in order. If anything fails, that's a real signal worth flagging.

---

## Smoke test 1 — verify the GUI is what you expect (~30 seconds)

The GUI window titled **"Curator 1.6.3"** should be open already (PID 69168 at hand-off).

1. Look at the menu bar. You should see: **File / Edit / Tools / Workflows / Help**
2. Click **Workflows → About these workflows** — confirms the menu loads correctly
3. Click **Tools → Health check** — confirms placeholder dialog appears with "coming in v1.7" message
4. Click around the 8 tabs (Inbox / Browser / Bundles / Trash / Migrate / Audit Log / Settings / Lineage Graph)
   - **Browser tab should show 40 files** (proof of earlier scans)
   - **Audit Log tab should show ~12 entries**
   - Other tabs show empty or minimal data — that's expected

If any of the above doesn't work, something's actually broken and worth investigating.

---

## Smoke test 2 — health check workflow (read-only, ~10 seconds)

Click: **Workflows → Health check**

A new console window opens running `05_health_check.bat`. Expected output:
- 8 sections of `[ OK ]` checks (filesystem layout, Python, packages, GUI dep, DB integrity, doctor, MCP config, MCP probe)
- Final summary: "8 of 8 checks passed" in green
- "Press Enter to close" prompt at the end

If any check shows `[FAIL]`, the dashboard tells you what to do (usually re-run installer).

---

## Smoke test 3 — audit summary (read-only, ~5 seconds)

Click: **Workflows → Audit summary (24h)**

Expected output:
- Pulls audit entries from the canonical DB
- Shows breakdown by action, by actor, by hour
- Lists destructive actions (none currently — only `scan.complete` and `scan.start` and `source.add` etc.)
- Saves full JSON to `%TEMP%\curator_audit_*.json`

---

## Smoke test 4 — try a real scan (writes to canonical DB)

Click: **Workflows → Initial scan**

A console opens prompting:
1. **"Path"** — type a folder path you want indexed (e.g. `C:\Users\jmlee\Documents`) or leave blank to scan AL workspace
2. **"Scan N files now? [Y/n]"** — type `y` and Enter

Watch the scan output. Should complete in seconds for small dirs. Check the GUI's Browser tab afterward — new files appear.

---

## What's safely left as test data in the canonical DB

The canonical DB at `C:\Users\jmlee\Desktop\AL\.curator\curator.db` currently has:
- 40 indexed files (12 from `scripts/workflows/`, 28 from `Curator/docs/`)
- ~12 audit entries (scan + source.add events)
- 1 source registered (`local`, the default)

**This is real data proving Curator works**, not synthetic test data. Safe to keep or wipe.

To wipe: `curator sources remove local --apply` (will fail because files reference it; need a fresh DB instead — easiest is to delete `C:\Users\jmlee\Desktop\AL\.curator\curator.db` and re-run installer).

---

## What's NOT yet implemented (per the design docs)

If any of these would unblock real work, flag them and we'll prioritize:

- **Native GUI dialogs** for Scan / Group / Cleanup / Sources Manager / Health Check (planned for v1.7 per `docs/design/GUI_V2_DESIGN.md`). Currently Tools menu surfaces placeholders.
- **Live Watch tab** in GUI (planned for v1.8). Use `curator watch local --apply` from CLI today.
- **Editable Settings tab** in GUI (planned for v1.8). Currently read-only.
- **Multiple source IDs per plugin type** — v1.6 limitation: only the plugin's default source_id (`local`, `gdrive`) is auto-dispatched. Custom IDs are stored but not scannable. Plugin SDK fix needed.
- **Auto-update with rollback** — designed at `Atrium/design/LIFECYCLE_GOVERNANCE.md`, not implemented. ~3-6 sessions of work via the deferred `atrium-reversibility` package.

---

## Files updated/created this session — quick reference

| Purpose | Path |
|---|---|
| Comprehensive user guide | `docs/USER_GUIDE.md` (700 lines) |
| Single-click installer | `installer/Install-Curator.bat` + `Install-Curator.ps1` + `README.md` |
| Click-to-run batch workflows | `scripts/workflows/01-05` (.bat + .ps1 + README) |
| GUI v2 design (full architecture) | `docs/design/GUI_V2_DESIGN.md` |
| Constitutional governance design | `Atrium/design/LIFECYCLE_GOVERNANCE.md` (vital/active/provisional/junk taxonomy + universal rollback pattern) |
| Lessons learned (install/MCP debug saga) | `docs/lessons/2026-05-09_install_mcp_session.md` |
| This checklist | `docs/NEXT_SESSION_CHECKLIST.md` |

## Where the actual data is (filesystem map)

```
C:\Users\jmlee\Desktop\AL\
├── .curator\
│   ├── curator.db                 # canonical DB, 40 files indexed
│   └── curator.toml               # canonical config (CURATOR_CONFIG points here)
├── Curator\                       # Curator source repo (HEAD on GitHub)
│   ├── .venv\                     # editable-install venv with [gui,mcp,organize]
│   ├── installer\                 # the one-click installer
│   ├── scripts\workflows\         # the batch workflows you click
│   └── docs\                      # USER_GUIDE, design, lessons, this file
├── Atrium\                        # constitutional governance
│   ├── CONSTITUTION.md            # v0.3 RATIFIED 2026-05-08
│   └── design\                    # LIFECYCLE_GOVERNANCE.md captured here
└── session_b_real\                # 249 KB test artifact, preserved
```
