<#
.SYNOPSIS
    Common helper functions used by all Curator workflow scripts.
#>

# Standard locations
$global:CuratorRoot   = "C:\Users\jmlee\Desktop\AL\Curator"
$global:CuratorVenv   = Join-Path $CuratorRoot ".venv"
$global:CuratorVenvPy = Join-Path $CuratorVenv "Scripts\python.exe"
$global:CuratorCli    = Join-Path $CuratorVenv "Scripts\curator.exe"
$global:CanonicalDb   = "C:\Users\jmlee\Desktop\AL\.curator\curator.db"
$global:CanonicalToml = "C:\Users\jmlee\Desktop\AL\.curator\curator.toml"

function Test-CuratorAvailable {
    <#
    .SYNOPSIS Verify Curator stack is installed; abort with helpful message if not.
    #>
    foreach ($p in @($CuratorVenvPy, $CuratorCli, $CanonicalToml)) {
        if (-not (Test-Path $p)) {
            Write-Host ""
            Write-Host "[ERROR] Curator stack not found at expected paths:" -ForegroundColor Red
            Write-Host "  Missing: $p" -ForegroundColor Red
            Write-Host ""
            Write-Host "Run the installer first: $CuratorRoot\installer\Install-Curator.bat"
            exit 1
        }
    }
    $env:CURATOR_CONFIG = $CanonicalToml
    $env:CURATOR_LOG_LEVEL = "ERROR"
}

function Invoke-Curator {
    <#
    .SYNOPSIS Run a curator CLI command, suppressing stderr noise + handling exit codes.
    .PARAMETER Args  Arguments to pass to curator.exe
    #>
    param([Parameter(ValueFromRemainingArguments)] $Args)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $CuratorCli @Args 2>$null
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

function Invoke-CuratorJson {
    <#
    .SYNOPSIS Run a curator CLI command with --json, parse the result.
    #>
    param([Parameter(ValueFromRemainingArguments)] $Args)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = & $CuratorCli --json @Args 2>$null
        if ($raw) {
            try {
                return ($raw | ConvertFrom-Json)
            } catch {
                return $null
            }
        }
        return $null
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

function Read-Confirmation {
    <#
    .SYNOPSIS Prompt user for yes/no confirmation. Returns $true if confirmed.
    .PARAMETER Message  Prompt message
    .PARAMETER Default  Default if user just hits Enter (yes/no)
    #>
    param(
        [string]$Message,
        [string]$Default = "no"
    )
    $opts = if ($Default -eq "yes") { "[Y/n]" } else { "[y/N]" }
    Write-Host ""
    Write-Host "$Message $opts " -NoNewline -ForegroundColor Yellow
    $resp = Read-Host
    if (-not $resp) { $resp = $Default }
    return ($resp -match '^(y|yes)$')
}

function Show-Banner {
    param([string]$Title)
    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host ""
}

function Show-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "--- $Title ---" -ForegroundColor Cyan
}
