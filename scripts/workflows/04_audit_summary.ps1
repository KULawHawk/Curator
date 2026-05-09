<#
.SYNOPSIS
    Workflow: Audit log summary for the last N hours, grouped by action.
.DESCRIPTION
    Read-only. Queries Curator's audit log and renders a grouped report:
    actions taken, by subsystem, with counts and recent samples.
#>
[CmdletBinding()]
param(
    [int]$Hours = 24
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Test-CuratorAvailable
Show-Banner "Curator workflow: Audit summary (last $Hours h)"

# Pull audit entries
$started = Get-Date
$entries = Invoke-CuratorJson audit --since-hours $Hours -n 500
$elapsed = (Get-Date) - $started

if (-not $entries -or $entries.Count -eq 0) {
    Write-Host "  No audit entries in the last $Hours hours." -ForegroundColor Yellow
    Write-Host "  (Either Curator hasn't been used, or the canonical DB is empty.)"
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host ("  Pulled {0} entries in {1}s." -f $entries.Count, [Math]::Round($elapsed.TotalSeconds, 1))
Write-Host ""

# ---- By action ----
Show-Section "By action"
$byAction = $entries | Group-Object action | Sort-Object Count -Descending
$byAction | ForEach-Object {
    Write-Host ("  {0,-40} {1,5}" -f $_.Name, $_.Count)
}

# ---- By actor ----
Show-Section "By actor (subsystem)"
$byActor = $entries | Group-Object actor | Sort-Object Count -Descending
$byActor | ForEach-Object {
    Write-Host ("  {0,-40} {1,5}" -f $_.Name, $_.Count)
}

# ---- By hour ----
Show-Section "By hour (chronological activity)"
$byHour = $entries | ForEach-Object {
    $ts = [DateTime]::Parse($_.occurred_at)
    [PSCustomObject]@{ Hour = $ts.ToString("yyyy-MM-dd HH:00") }
} | Group-Object Hour | Sort-Object Name
$byHour | ForEach-Object {
    $bar = "#" * [Math]::Min(50, $_.Count)
    Write-Host ("  {0}  {1,4}  {2}" -f $_.Name, $_.Count, $bar)
}

# ---- Recent destructive actions ----
Show-Section "Recent destructive actions (trash, migrate.move, delete)"
$destructive = $entries | Where-Object {
    $_.action -match "trash|migrate\.move|migrate\.delete|delete"
} | Select-Object -First 15
if ($destructive) {
    $destructive | ForEach-Object {
        $ts = [DateTime]::Parse($_.occurred_at).ToString("MM-dd HH:mm:ss")
        $entityShort = if ($_.entity_id -and $_.entity_id.Length -gt 12) { $_.entity_id.Substring(0, 12) } else { $_.entity_id }
        Write-Host ("  {0}  {1,-25}  {2}" -f $ts, $_.action, $entityShort)
    }
} else {
    Write-Host "  None in this window." -ForegroundColor Green
}

# ---- Plugin / compliance events ----
Show-Section "Plugin / compliance events"
$pluginEvents = $entries | Where-Object { $_.action -match "compliance\.|plugin\." }
if ($pluginEvents) {
    Write-Host "  $($pluginEvents.Count) plugin/compliance events found:"
    $pluginEvents | Group-Object action | Sort-Object Count -Descending | ForEach-Object {
        Write-Host ("    {0,-40} {1,5}" -f $_.Name, $_.Count)
    }
} else {
    Write-Host "  None in this window." -ForegroundColor Green
}

# Save full report
$reportPath = Join-Path $env:TEMP ("curator_audit_{0}h_{1}.json" -f $Hours, (Get-Date -Format "yyyyMMdd_HHmmss"))
$entries | ConvertTo-Json -Depth 10 | Set-Content -Path $reportPath -Encoding UTF8
Write-Host ""
Write-Host "  Full audit JSON saved: $reportPath" -ForegroundColor Cyan

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Done." -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Read-Host "Press Enter to close"
