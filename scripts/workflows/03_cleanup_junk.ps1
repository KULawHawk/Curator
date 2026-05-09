<#
.SYNOPSIS
    Workflow: Find empty dirs, broken symlinks, junk files; report; optionally trash.
.DESCRIPTION
    Runs all 3 cleanup categories under a chosen root, summarizes what's there,
    asks for confirmation before any destructive action, then trashes (Recycle Bin).
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

# ---- Discover (dry-run) ----
Show-Section "Discover (dry-run, no files moved)"
$started = Get-Date

Write-Host "  Looking for junk files (Thumbs.db, .DS_Store, ~`$*, etc.)..."
$junkLines = & $CuratorCli cleanup junk $Path 2>$null | Where-Object { $_ -match "\S" }
$junkCount = ($junkLines | Where-Object { $_ -match "^\s*-" }).Count
Write-Host "    Found: $junkCount junk files"

Write-Host "  Looking for empty directories..."
$emptyLines = & $CuratorCli cleanup empty-dirs $Path 2>$null | Where-Object { $_ -match "\S" }
$emptyCount = ($emptyLines | Where-Object { $_ -match "^\s*-" }).Count
Write-Host "    Found: $emptyCount empty directories"

Write-Host "  Looking for broken symlinks..."
$symLines = & $CuratorCli cleanup broken-symlinks $Path 2>$null | Where-Object { $_ -match "\S" }
$symCount = ($symLines | Where-Object { $_ -match "^\s*-" }).Count
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
if ($junkCount -gt 0) {
    Write-Host "  Junk file examples (showing 5):"
    $junkLines | Where-Object { $_ -match "^\s*-" } | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
}
if ($emptyCount -gt 0) {
    Write-Host "  Empty dir examples (showing 5):"
    $emptyLines | Where-Object { $_ -match "^\s*-" } | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
}
if ($symCount -gt 0) {
    Write-Host "  Broken symlink examples (showing 5):"
    $symLines | Where-Object { $_ -match "^\s*-" } | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
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
$results = @{}
if ($junkCount -gt 0) {
    Write-Host "  Cleaning junk files..." -NoNewline
    try { Invoke-Curator cleanup junk $Path --apply | Out-Null; Write-Host " done." -ForegroundColor Green; $results.junk = "ok" }
    catch { Write-Host " FAILED" -ForegroundColor Red; $results.junk = "fail" }
}
if ($emptyCount -gt 0) {
    Write-Host "  Cleaning empty directories..." -NoNewline
    try { Invoke-Curator cleanup empty-dirs $Path --apply | Out-Null; Write-Host " done." -ForegroundColor Green; $results.empty = "ok" }
    catch { Write-Host " FAILED" -ForegroundColor Red; $results.empty = "fail" }
}
if ($symCount -gt 0) {
    Write-Host "  Cleaning broken symlinks..." -NoNewline
    try { Invoke-Curator cleanup broken-symlinks $Path --apply | Out-Null; Write-Host " done." -ForegroundColor Green; $results.sym = "ok" }
    catch { Write-Host " FAILED" -ForegroundColor Red; $results.sym = "fail" }
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Done. Items are in the Recycle Bin (reversible)." -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Read-Host "Press Enter to close"
