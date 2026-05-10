<#
.SYNOPSIS
    Workflow: Find empty dirs, broken symlinks, junk files; report; optionally trash.
.DESCRIPTION
    Runs all 3 cleanup categories under a chosen root, summarizes what's there,
    asks for confirmation before any destructive action, then trashes (Recycle Bin).
    Uses --json output for reliable parsing.
#>
[CmdletBinding()]
param(
    [string]$Path
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Test-CuratorAvailable
Show-Banner "Curator workflow: Cleanup junk"

if (-not $Path) {
    Write-Host "Which folder do you want to scan for junk?"
    Write-Host "(Type a path, or leave blank for AL workspace)"
    $Path = Read-Host "Path"
    if (-not $Path) { $Path = "C:\Users\jmlee\Desktop\AL" }
}
if (-not (Test-Path $Path)) {
    Write-Host "[ERROR] Path does not exist: $Path" -ForegroundColor Red
    exit 1
}

# Helper: run cleanup --json and return parsed plan
function Invoke-CleanupPlan {
    param([string]$Subcommand, [string]$ScanPath)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = & $CuratorCli --json cleanup $Subcommand $ScanPath 2>$null
        if ($raw) {
            $parsed = $raw | ConvertFrom-Json
            return $parsed.plan
        }
        return $null
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

# ---- Discover (dry-run) ----
Show-Section "Discover (dry-run, no files moved)"
$started = Get-Date

Write-Host "  Looking for junk files (Thumbs.db, .DS_Store, ~`$*, etc.)..."
$junkPlan = Invoke-CleanupPlan -Subcommand "junk" -ScanPath $Path
$junkCount = if ($junkPlan) { $junkPlan.count } else { 0 }
$junkBytes = if ($junkPlan -and $junkPlan.size_bytes) { $junkPlan.size_bytes } else { 0 }
Write-Host "    Found: $junkCount junk files ($([Math]::Round($junkBytes / 1KB, 1)) KB)"

Write-Host "  Looking for empty directories..."
$emptyPlan = Invoke-CleanupPlan -Subcommand "empty-dirs" -ScanPath $Path
$emptyCount = if ($emptyPlan) { $emptyPlan.count } else { 0 }
Write-Host "    Found: $emptyCount empty directories"

Write-Host "  Looking for broken symlinks..."
$symPlan = Invoke-CleanupPlan -Subcommand "broken-symlinks" -ScanPath $Path
$symCount = if ($symPlan) { $symPlan.count } else { 0 }
Write-Host "    Found: $symCount broken symlinks"

$elapsed = (Get-Date) - $started
Write-Host ""
Write-Host ("  Discovery complete in {0}s." -f [Math]::Round($elapsed.TotalSeconds, 1)) -ForegroundColor Green

$totalItems = $junkCount + $emptyCount + $symCount
if ($totalItems -eq 0) {
    Write-Host ""
    Write-Host "  Nothing to clean up. The path is already tidy." -ForegroundColor Green
    Read-Host "Press Enter to close"
    exit 0
}

# ---- Show samples ----
Show-Section "Sample of items found"
foreach ($pair in @(
    @{ name = "junk file"; plan = $junkPlan; count = $junkCount },
    @{ name = "empty dir"; plan = $emptyPlan; count = $emptyCount },
    @{ name = "broken symlink"; plan = $symPlan; count = $symCount }
)) {
    if ($pair.count -gt 0 -and $pair.plan -and $pair.plan.items) {
        Write-Host ("  {0} examples (showing 5 of {1}):" -f $pair.name, $pair.count)
        @($pair.plan.items) | Select-Object -First 5 | ForEach-Object {
            $itemPath = if ($_.path) { $_.path } else { $_ }
            Write-Host "    - $itemPath"
        }
    }
}

# ---- Apply ----
Show-Section "Apply (REVERSIBLE via Recycle Bin)"
Write-Host "  Total items to clean: $totalItems"
Write-Host "  All items go to Recycle Bin; nothing is permanently deleted."

if (-not (Read-Confirmation "Clean up $totalItems items now?" -Default "no")) {
    Write-Host "Skipped. Nothing changed." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host ""
if ($junkCount -gt 0) {
    Write-Host "  Cleaning junk files..." -NoNewline
    try { Invoke-Curator cleanup junk $Path --apply | Out-Null; Write-Host " done." -ForegroundColor Green }
    catch { Write-Host " FAILED" -ForegroundColor Red }
}
if ($emptyCount -gt 0) {
    Write-Host "  Cleaning empty directories..." -NoNewline
    try { Invoke-Curator cleanup empty-dirs $Path --apply | Out-Null; Write-Host " done." -ForegroundColor Green }
    catch { Write-Host " FAILED" -ForegroundColor Red }
}
if ($symCount -gt 0) {
    Write-Host "  Cleaning broken symlinks..." -NoNewline
    try { Invoke-Curator cleanup broken-symlinks $Path --apply | Out-Null; Write-Host " done." -ForegroundColor Green }
    catch { Write-Host " FAILED" -ForegroundColor Red }
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Done. Items are in the Recycle Bin (reversible)." -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Read-Host "Press Enter to close"
