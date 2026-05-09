# Curator workflow scripts

Double-clickable batch workflows that combine multiple `curator` CLI commands
into common multi-step operations. Always cautious: every destructive action
asks for explicit confirmation, and every change is reversible via the
Recycle Bin.

## Available workflows

| Script | What it does | Destructive? |
|---|---|---|
| `01_initial_scan.bat` | Register a folder as a source and scan it (index files, hash, detect lineage). | No — just indexes. |
| `02_find_duplicates.bat` | Find duplicate-content files; show report; ask before trashing extras. | Optional (you confirm). |
| `03_cleanup_junk.bat` | Find empty dirs, broken symlinks, junk files; show report; ask before trashing. | Optional (you confirm). |
| `04_audit_summary.bat` | Read-only report of audit log activity for the last N hours. | No. |
| `05_health_check.bat` | Read-only stack health check (versions, DB integrity, MCP probe, GUI dep). | No. |

## How to use

**Easiest:** double-click the `.bat` file.

**From PowerShell:** `.\01_initial_scan.ps1` (or pass parameters,
e.g. `.\02_find_duplicates.ps1 -Keep newest`).

## What each script asks before doing anything destructive

- `02_find_duplicates`: shows total groups + reclaimable space + samples,
  asks "trash N redundant copies?" before any --apply.
- `03_cleanup_junk`: shows item counts + samples per category,
  asks "clean up N items?" before any --apply.

## Reversibility

Every destructive action goes through the OS Recycle Bin. Nothing is
permanently deleted unless you empty the bin manually. To restore an
individual file Curator trashed, use:

```powershell
curator restore <curator_id> --apply
```

Find the `curator_id` via `curator audit -n 50` (look for `trash` actions).

## Common parameters

All scripts that take a path will prompt if you don't pass `-Path`. Default
is `C:\Users\jmlee\Desktop\AL` (the Ad Astra workspace root).

## Why these exist

Curator's CLI is powerful but composing common multi-step workflows
(scan → group → review → apply) is awkward to do manually. These
scripts capture the workflows as one-click operations while keeping
all the safety rails (plan-mode preview, explicit confirmations,
audit log, recycle bin reversibility).

The GUI eventually subsumes these (see `docs/design/GUI_V2_DESIGN.md`),
but until that ships, these scripts are the click-to-run interface.