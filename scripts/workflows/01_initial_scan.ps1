<#
.SYNOPSIS
    Workflow: Initial scan of a folder.
    Registers a source if not present, scans it, reports what was indexed.

.DESCRIPTION
    Use this to start tracking a new folder. Cautious by default — reports
    what's there before scanning, asks for confirmation, and shows a summary
    after.

.EXAMPLE
    .\01_initial_scan.ps1
    .\01_initial_scan.ps1 -Path "C:\Users\jmlee\Documents" -SourceId local
#>

[CmdletBinding()]
param(
    [string]$Path,
    [string]$SourceId
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

# ---- Source ID ----
if (-not $SourceId) {
    Write-Host ""
    Write-Host "Source ID for this folder (e.g., 'local', 'local:vault')"
    Write-Host "[default: local]"
    $SourceId = Read-Host "Source ID"
    if (-not $SourceId) { $SourceId = "local" }
}

# Check if source already exists
Show-Section "Source registration"
$existing = Invoke-CuratorJson sources show $SourceId
if ($existing) {
    Write-Host "  Source '$SourceId' already exists." -ForegroundColor Green
} else {
    Write-Host "  Registering new source '$SourceId' -> $Path"
    Invoke-Curator sources add $SourceId $Path
    Write-Host "  Registered." -ForegroundColor Green
}

# ---- Confirm before scan ----
if (-not (Read-Confirmation "Scan $fileCount files now?" -Default "yes")) {
    Write-Host "Skipped." -ForegroundColor Yellow
    exit 0
}

# ---- Scan ----
Show-Section "Scanning"
Write-Host "  This populates the index, computes hashes, detects lineage..."
Write-Host "  (Stay patient; large folders can take a few minutes)"
$started = Get-Date
Invoke-Curator scan $SourceId $Path
$elapsed = (Get-Date) - $started
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
Write-Host "  - View in GUI:      curator gui"
Write-Host ""
Read-Host "Press Enter to close"
