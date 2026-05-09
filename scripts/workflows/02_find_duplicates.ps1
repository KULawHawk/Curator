<#
.SYNOPSIS
    Workflow: Find duplicate files in the index, generate a report, optionally trash extras.
.DESCRIPTION
    Three phases:
      1. Discover (read-only) - run 'curator group --json'
      2. Review               - show duplicate sets, by ext + size impact
      3. Apply (optional)     - trash non-primary members per --keep strategy
    Always asks for explicit confirmation before any destructive action.
#>
[CmdletBinding()]
param(
    [ValidateSet("oldest", "newest", "shortest_path", "longest_path")]
    [string]$Keep = "oldest"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Test-CuratorAvailable
Show-Banner "Curator workflow: Find duplicates"

# ---- Phase 1: discover ----
Show-Section "Phase 1 - Discover (read-only)"
Write-Host "  Running 'curator group --json'..."
$started = Get-Date
$groups = Invoke-CuratorJson group
$elapsed = (Get-Date) - $started
Write-Host ("  Done in {0}s." -f [Math]::Round($elapsed.TotalSeconds, 1)) -ForegroundColor Green

if (-not $groups -or $groups.Count -eq 0) {
    Write-Host ""
    Write-Host "  No duplicate groups found." -ForegroundColor Green
    Write-Host "  (Either nothing scanned, or no duplicates exist.)"
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 0
}

# ---- Phase 2: review ----
Show-Section "Phase 2 - Review"
$totalGroups = $groups.Count
$totalDuplicateFiles = 0
$totalReclaimable = 0
$byExt = @{}

foreach ($g in $groups) {
    $members = @($g.members)
    $totalDuplicateFiles += ($members.Count - 1)
    $sz = if ($members.Count -gt 0 -and $members[0].size) { [int64]$members[0].size } else { 0 }
    $totalReclaimable += ($sz * ($members.Count - 1))
    foreach ($m in $members) {
        $ext = [System.IO.Path]::GetExtension($m.path).ToLower()
        if (-not $ext) { $ext = "(no ext)" }
        if (-not $byExt.ContainsKey($ext)) { $byExt[$ext] = @{ Count = 0; Bytes = 0 } }
        $byExt[$ext].Count += 1
        $byExt[$ext].Bytes += $sz
    }
}

$reclaimMB = [Math]::Round($totalReclaimable / 1MB, 1)
Write-Host "  Duplicate groups found:           $totalGroups"
Write-Host "  Total redundant copies:           $totalDuplicateFiles"
Write-Host "  Reclaimable space (approx):       $reclaimMB MB"
Write-Host ""
Write-Host "  By extension (top 10 by size):"
$byExt.GetEnumerator() | Sort-Object { $_.Value.Bytes } -Descending | Select-Object -First 10 | ForEach-Object {
    $mb = [Math]::Round($_.Value.Bytes / 1MB, 2)
    Write-Host ("    {0,-12} {1,5} files  {2,8} MB" -f $_.Key, $_.Value.Count, $mb)
}

$reportPath = Join-Path $env:TEMP ("curator_duplicates_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$groups | ConvertTo-Json -Depth 10 | Set-Content -Path $reportPath -Encoding UTF8
Write-Host ""
Write-Host "  Full JSON report saved: $reportPath" -ForegroundColor Cyan

# ---- Phase 3: apply ----
Show-Section "Phase 3 - Apply (optional, REVERSIBLE via Recycle Bin)"
Write-Host "  Strategy: --keep $Keep"
Write-Host "  Each duplicate group keeps ONE member ($Keep), the rest go to Recycle Bin."

if (-not (Read-Confirmation "Trash $totalDuplicateFiles redundant copies (~$reclaimMB MB) now?" -Default "no")) {
    Write-Host "Skipped. Nothing changed." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host ""
Write-Host "  Applying..." -NoNewline
try {
    Invoke-Curator group --apply --keep $Keep | Out-Null
    Write-Host " done." -ForegroundColor Green
} catch {
    Write-Host " FAILED: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Done. To restore: curator restore <id> --apply" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Read-Host "Press Enter to close"