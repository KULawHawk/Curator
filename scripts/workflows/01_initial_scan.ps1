<#
.SYNOPSIS
    Workflow: Initial scan of a folder.
    Indexes all files under a chosen path against the default 'local' source.

.DESCRIPTION
    Cautious by default — reports what's there before scanning, asks for
    confirmation, and shows a summary after.

    NOTE on source IDs: Curator's local source plugin auto-registers as
    source_id='local'. Custom source IDs are not first-class in v1.6 (they
    can be created via 'curator sources add' but the plugin won't dispatch
    scans to them). The cautious recommendation is to use 'local' for all
    local-filesystem scans; the 'root' parameter to scan() determines what
    actually gets indexed.

.EXAMPLE
    .\01_initial_scan.ps1
    .\01_initial_scan.ps1 -Path "C:\Users\jmlee\Documents"
#>

[CmdletBinding()]
param(
    [string]$Path
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Test-CuratorAvailable

Show-Banner "Curator workflow: Initial scan"

# ---- Get path to scan ----
if (-not $Path) {
    Write-Host "Which folder do you want Curator to track?"
    Write-Host "(Type a path, or leave blank for AL workspace)"
    $Path = Read-Host "Path"
    if (-not $Path) { $Path = "C:\Users\jmlee\Desktop\AL" }
}
if (-not (Test-Path $Path)) {
    Write-Host "[ERROR] Path does not exist: $Path" -ForegroundColor Red
    exit 1
}

# Pre-flight: how many files would be scanned?
Show-Section "Pre-flight check"
$fileCount = (Get-ChildItem -Path $Path -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
$totalSize = (Get-ChildItem -Path $Path -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
$sizeMB = [Math]::Round($totalSize / 1MB, 1)
Write-Host "  Path:        $Path"
Write-Host "  Files:       $fileCount"
Write-Host "  Total size:  $sizeMB MB"

if ($fileCount -gt 50000) {
    Write-Host ""
    Write-Host "  [WARN] Large folder ($fileCount files). Scan may take a while." -ForegroundColor Yellow
}

# ---- Confirm before scan ----
if (-not (Read-Confirmation "Scan $fileCount files now?" -Default "yes")) {
    Write-Host "Skipped." -ForegroundColor Yellow
    exit 0
}

# ---- Scan ----
Show-Section "Scanning"
Write-Host "  Source: local (Curator's default local-filesystem source)"
Write-Host "  Root:   $Path"
Write-Host "  This populates the index, computes hashes, detects lineage..."
Write-Host "  (Stay patient; large folders can take a few minutes)"
Write-Host ""
$started = Get-Date
Invoke-Curator scan local $Path
$elapsed = (Get-Date) - $started
Write-Host ""
Write-Host "  Scan complete in $([Math]::Round($elapsed.TotalSeconds, 1))s." -ForegroundColor Green

# ---- Summary ----
Show-Section "Post-scan summary"
& $CuratorCli doctor 2>$null | Select-Object -First 18 | ForEach-Object { Write-Host "  $_" }

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Done." -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps you might want:"
Write-Host "  - Find duplicates:  scripts\workflows\02_find_duplicates.bat"
Write-Host "  - Cleanup junk:     scripts\workflows\03_cleanup_junk.bat"
Write-Host "  - View in GUI:      curator gui  (or Workflows menu)"
Write-Host ""
Read-Host "Press Enter to close"
