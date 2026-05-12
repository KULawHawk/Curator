# ============================================================================
# scripts/ci_diag.ps1   (v1.7.65)
#
# CI diagnostic helper — one-command access to the latest GitHub Actions run.
#
# Background: the v1.7.42–v1.7.64 CI-red arc taught lesson #66 ("green local
# pytest does not imply green CI") and lesson #67 ("diagnose with logs, not
# with hypotheses"). The PAT-enabled diagnostic loop was a force multiplier:
# without it, v1.7.59 and v1.7.61 shipped speculative fixes; with it, v1.7.62
# and v1.7.63 found the real causes in <10 minutes each.
#
# This script codifies that loop. One command, three modes:
#
#   .\scripts\ci_diag.ps1 status              # Show all 9 cells' pass/fail
#   .\scripts\ci_diag.ps1 logs <name-pattern> # Download log for failing cell
#   .\scripts\ci_diag.ps1 summary             # Failing tests across all cells
#
# Token discovery (in priority order):
#   1. -Token parameter (explicit)
#   2. $env:GH_TOKEN env var
#   3. $env:GITHUB_TOKEN env var
#   4. Stored at $env:USERPROFILE\.curator\github_pat (single-line file, 0600)
#
# Scope: read-only Actions API access. Public repo Curator/KULawHawk.
# ============================================================================

[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet("status", "logs", "summary")]
    [string]$Mode = "status",

    [Parameter(Position=1)]
    [string]$NamePattern = "",

    [Parameter()]
    [string]$Token = "",

    [Parameter()]
    [string]$Repo = "KULawHawk/Curator",

    [Parameter()]
    [string]$OutDir = "$env:USERPROFILE\Desktop\AL\.curator"
)

$ErrorActionPreference = "Stop"

# ----------------------------------------------------------------------------
# Token resolution
# ----------------------------------------------------------------------------

function Get-GitHubToken {
    param([string]$Explicit)

    if ($Explicit) { return $Explicit }
    if ($env:GH_TOKEN) { return $env:GH_TOKEN }
    if ($env:GITHUB_TOKEN) { return $env:GITHUB_TOKEN }

    $tokenFile = Join-Path $env:USERPROFILE ".curator\github_pat"
    if (Test-Path $tokenFile) {
        $t = (Get-Content $tokenFile -Raw).Trim()
        if ($t) { return $t }
    }

    Write-Error "No GitHub token found. Set `$env:GH_TOKEN, pass -Token, or store at $tokenFile (single line, 0600)."
    exit 1
}

$script:GhToken = Get-GitHubToken -Explicit $Token
$script:Headers = @{
    "Accept" = "application/vnd.github+json"
    "Authorization" = "Bearer $script:GhToken"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "Curator-CIDiag/v1.7.65"
}

# ----------------------------------------------------------------------------
# API helpers
# ----------------------------------------------------------------------------

function Get-LatestRun {
    $url = "https://api.github.com/repos/$Repo/actions/runs?per_page=1"
    return (Invoke-RestMethod -Uri $url -Headers $script:Headers).workflow_runs[0]
}

function Get-RunJobs {
    param([string]$RunId)
    $url = "https://api.github.com/repos/$Repo/actions/runs/$RunId/jobs"
    return (Invoke-RestMethod -Uri $url -Headers $script:Headers).jobs
}

function Get-JobLogs {
    param([string]$JobId, [string]$OutputPath)
    $url = "https://api.github.com/repos/$Repo/actions/jobs/$JobId/logs"
    Invoke-WebRequest -Uri $url -Headers $script:Headers -OutFile $OutputPath -ErrorAction Stop
    return (Get-Item $OutputPath).Length
}

# ----------------------------------------------------------------------------
# Mode: status
# ----------------------------------------------------------------------------

function Show-Status {
    $run = Get-LatestRun
    $shortSha = $run.head_sha.Substring(0, 7)
    Write-Host ""
    Write-Host "=== Latest run: $($run.display_title) ===" -ForegroundColor Cyan
    Write-Host "SHA:    $shortSha"
    Write-Host "Status: $($run.status) / $($run.conclusion)"
    Write-Host "URL:    $($run.html_url)"
    Write-Host ""

    $jobs = Get-RunJobs -RunId $run.id
    $success = @($jobs | Where-Object { $_.conclusion -eq "success" }).Count
    $failure = @($jobs | Where-Object { $_.conclusion -eq "failure" }).Count
    $running = @($jobs | Where-Object { $_.status -eq "in_progress" -or $_.status -eq "queued" }).Count

    $jobs | Sort-Object { $_.name } | ForEach-Object {
        $conc = if ($_.conclusion) { $_.conclusion } else { "" }
        $marker = switch ($_.conclusion) {
            "success" { "[OK]  " }
            "failure" { "[FAIL]" }
            default { if ($_.status -eq "in_progress") { "..." } else { "?  " } }
        }
        $color = switch ($_.conclusion) {
            "success" { "Green" }
            "failure" { "Red" }
            default { "Yellow" }
        }
        Write-Host ("{0} {1,-50} {2,-13} {3}" -f $marker, $_.name, $_.status, $conc) -ForegroundColor $color
    }
    Write-Host ""
    Write-Host "=== TALLY: success=$success | failure=$failure | running/queued=$running ===" -ForegroundColor Cyan
}

# ----------------------------------------------------------------------------
# Mode: logs
# ----------------------------------------------------------------------------

function Get-FailingLogs {
    param([string]$NameFilter)

    $run = Get-LatestRun
    $shortSha = $run.head_sha.Substring(0, 7)
    $jobs = Get-RunJobs -RunId $run.id

    $failing = $jobs | Where-Object { $_.conclusion -eq "failure" }
    if ($NameFilter) {
        $failing = $failing | Where-Object { $_.name -match $NameFilter }
    }

    if (-not $failing) {
        Write-Host "No failing jobs match pattern '$NameFilter' in run $shortSha." -ForegroundColor Yellow
        return
    }

    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

    Write-Host "=== Downloading $($failing.Count) failing job log(s) ===" -ForegroundColor Cyan
    foreach ($job in $failing) {
        $safeName = $job.name -replace "[^a-zA-Z0-9]", "_"
        $outPath = Join-Path $OutDir "ci_${shortSha}_${safeName}.log"
        try {
            $size = Get-JobLogs -JobId $job.id -OutputPath $outPath
            Write-Host "  [OK] $($job.name) -> $outPath ($size bytes)" -ForegroundColor Green
        } catch {
            Write-Host "  [FAIL] $($job.name): $_" -ForegroundColor Red
        }
    }
}

# ----------------------------------------------------------------------------
# Mode: summary
# ----------------------------------------------------------------------------

function Show-FailingSummary {
    $run = Get-LatestRun
    $shortSha = $run.head_sha.Substring(0, 7)
    $jobs = Get-RunJobs -RunId $run.id
    $failing = $jobs | Where-Object { $_.conclusion -eq "failure" }

    if (-not $failing) {
        Write-Host "All jobs passing in run $shortSha. Nothing to summarize." -ForegroundColor Green
        return
    }

    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

    Write-Host "=== Failing tests across $($failing.Count) cell(s) ===" -ForegroundColor Cyan
    foreach ($job in $failing) {
        $safeName = $job.name -replace "[^a-zA-Z0-9]", "_"
        $outPath = Join-Path $OutDir "ci_${shortSha}_${safeName}.log"
        if (-not (Test-Path $outPath)) {
            try { Get-JobLogs -JobId $job.id -OutputPath $outPath | Out-Null } catch { continue }
        }
        Write-Host ""
        Write-Host "--- $($job.name) ---" -ForegroundColor Yellow
        # Print FAILED lines (test names + brief error)
        $failures = Select-String -Path $outPath -Pattern "^FAILED tests/" -ErrorAction SilentlyContinue
        if ($failures) {
            foreach ($f in $failures) {
                $line = $f.Line -replace "^\S+Z\s+", ""  # strip timestamp
                Write-Host "  $($line.Substring(0, [Math]::Min(180, $line.Length)))"
            }
        }
        # Print test summary line
        $summary = Select-String -Path $outPath -Pattern "passed.*failed|failed.*passed" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($summary) {
            $line = $summary.Line -replace "^\S+Z\s+", ""
            Write-Host "  SUMMARY: $line" -ForegroundColor Cyan
        }
    }
}

# ----------------------------------------------------------------------------
# Main dispatch
# ----------------------------------------------------------------------------

switch ($Mode) {
    "status"  { Show-Status }
    "logs"    { Get-FailingLogs -NameFilter $NamePattern }
    "summary" { Show-FailingSummary }
    default   { Show-Status }  # fallback
}
