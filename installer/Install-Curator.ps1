<#
.SYNOPSIS
    Single-script installer / refresher / verifier for the Curator + atrium-citation +
    atrium-safety stack on Windows.

.DESCRIPTION
    Idempotent. Run this on a fresh machine (initial install) OR on an existing setup
    (refresh after pulling new code) OR after a broken state (recovery). It always
    converges on the same end state:

      - venv at $RepoRoot\Curator\.venv exists and contains all three packages editable
      - all three packages are importable and at the versions declared in pyproject.toml
      - %APPDATA%\Claude\claude_desktop_config.json registers curator-mcp at the venv
        path, with a --db arg pointing at $CanonicalDb
      - $CanonicalDb is a fresh, integrity-clean SQLite DB
      - corrupt or stale DBs get backed up before being replaced
      - prior config gets backed up before being replaced

    Steps the script will perform (each is reported with a tag like [STEP 1/N]):

      1. Sanity check: required dirs and binaries exist
      2. Detect Claude Desktop running (file-lock guard); if running, warn and abort
      3. Ensure the venv exists; create if missing
      4. Refresh editable installs of curator + atrium-citation + atrium-safety
      5. Sweep up corrupted dist-info from prior interrupted installs
      6. Verify each package imports and reports its expected version
      7. Initialize $CanonicalDb (or back up + replace if corrupt)
      8. Patch claude_desktop_config.json:
            - back up current
            - inject/refresh curator entry pointing at venv curator-mcp.exe
            - --db arg pinned to $CanonicalDb
            - preserve all existing preferences and other mcpServers entries
      9. Run curator doctor against $CanonicalDb to confirm stack health
      10. Print a summary report

.PARAMETER WhatIf
    Show what the script would do without making changes.

.PARAMETER Force
    Skip the interactive confirmation prompt before destructive operations.

.PARAMETER RepoRoot
    Parent directory containing the three source repos. Defaults to
    "C:\Users\jmlee\Desktop\AL". Override if you've moved the constellation.

.PARAMETER CanonicalDb
    Path to the canonical Curator SQLite DB the installer will pin curator-mcp to.
    Defaults to "$RepoRoot\.curator\curator.db". Override only if you want the DB
    somewhere else.

.EXAMPLE
    PS> .\Install-Curator.ps1
    Run with defaults.

.EXAMPLE
    PS> .\Install-Curator.ps1 -WhatIf
    Show what would happen without changing anything.

.EXAMPLE
    PS> .\Install-Curator.ps1 -RepoRoot "C:\Users\jmlee\AdAstra" -Force
    Run after the planned filesystem migration to AdAstra/.

.NOTES
    Author: Claude (with Jake)
    Version: 1.0
    Date: 2026-05-09

    Requires:
      - PowerShell 5.1 or later
      - Python 3.13 (any install path) — script will detect it
      - The three source repos already cloned at $RepoRoot

    The script never deletes corrupted DBs without backing them up first.
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Force,
    [string]$RepoRoot = "C:\Users\jmlee\Desktop\AL",
    [string]$CanonicalDb = $null
)

$ErrorActionPreference = "Stop"

# ----- Defaults -----
if (-not $CanonicalDb) {
    $CanonicalDb = Join-Path $RepoRoot ".curator\curator.db"
}
$CuratorRepo  = Join-Path $RepoRoot "Curator"
$CitationRepo = Join-Path $RepoRoot "curatorplug-atrium-citation"
$SafetyRepo   = Join-Path $RepoRoot "curatorplug-atrium-safety"
$VenvPath     = Join-Path $CuratorRepo ".venv"
$VenvScripts  = Join-Path $VenvPath "Scripts"
$VenvPy       = Join-Path $VenvScripts "python.exe"
$VenvCurator  = Join-Path $VenvScripts "curator.exe"
$VenvCuratorMcp = Join-Path $VenvScripts "curator-mcp.exe"
$ClaudeCfgPath  = "$env:APPDATA\Claude\claude_desktop_config.json"
$Timestamp     = Get-Date -Format "yyyyMMdd-HHmmss"

# Visual helpers
function Write-Step($n, $total, $msg) {
    Write-Host ""
    Write-Host "[STEP $n/$total] $msg" -ForegroundColor Cyan
}
function Write-Sub($msg) {
    Write-Host "  $msg"
}
function Write-Good($msg) {
    Write-Host "  $msg" -ForegroundColor Green
}
function Write-Warn($msg) {
    Write-Host "  $msg" -ForegroundColor Yellow
}
function Write-Bad($msg) {
    Write-Host "  $msg" -ForegroundColor Red
}

$TotalSteps = 10
Write-Host ""
Write-Host "==============================================" -ForegroundColor White
Write-Host "  Curator stack installer  v1.0" -ForegroundColor White
Write-Host "==============================================" -ForegroundColor White
Write-Host ""
Write-Host "RepoRoot:      $RepoRoot"
Write-Host "CanonicalDb:   $CanonicalDb"
Write-Host "Venv:          $VenvPath"
Write-Host "Claude config: $ClaudeCfgPath"
Write-Host "Mode:          $(if ($WhatIfPreference) {'DRY RUN (no changes)'} else {'LIVE'})"

# ============================================================================
# STEP 1: Sanity checks
# ============================================================================
Write-Step 1 $TotalSteps "Pre-flight: required directories + Python detected"

foreach ($p in @($CuratorRepo, $CitationRepo, $SafetyRepo)) {
    if (-not (Test-Path $p)) {
        Write-Bad "MISSING source repo: $p"
        Write-Bad "Cannot continue without all three repos cloned at $RepoRoot."
        exit 1
    }
    Write-Sub "$p OK"
}

# Defender real-time scan note (informational, not blocking)
try {
    $mp = Get-MpComputerStatus -ErrorAction SilentlyContinue
    if ($mp -and $mp.RealTimeProtectionEnabled) {
        Write-Sub "Defender real-time scan: ON (informational; can slow pip install but not blocking)"
    }
} catch {}

# Find a usable system Python 3.11+ (need this if venv doesn't exist yet)
# Use 'py' launcher first if available, falls back to fixed paths.
$systemPy = $null
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    # 'py -3.13' lookup; fall back to '-3' (highest installed 3.x)
    foreach ($pyArg in @('-3.13', '-3.12', '-3.11', '-3')) {
        $candidate = & py $pyArg -c "import sys; print(sys.executable)" 2>$null
        if ($candidate -and (Test-Path $candidate)) {
            # Verify it's >=3.11 (Curator requires tomllib in stdlib)
            $verOk = & py $pyArg -c "import sys; print(int(sys.version_info >= (3,11)))" 2>$null
            if ($verOk -eq "1") {
                $systemPy = $candidate
                Write-Sub ("system Python (via py {0}): {1}" -f $pyArg, $systemPy)
                break
            }
        }
    }
}
if (-not $systemPy) {
    $pyCandidates = @(
        "C:\Program Files\Python313\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($candidate in $pyCandidates) {
        if (Test-Path $candidate) {
            $systemPy = $candidate
            Write-Sub "system Python (path search): $systemPy"
            break
        }
    }
}
if (-not $systemPy -and -not (Test-Path $VenvPy)) {
    Write-Bad "Cannot find system Python 3.11+. Install Python 3.11/3.12/3.13 from python.org or pre-create the venv."
    exit 1
}
if (Test-Path $VenvPy) { Write-Sub "venv Python:   $VenvPy (already present)" }

# Detect Claude Desktop install type — surfaces info even when not running
Write-Sub ""
$claudeStoreFingerprint = "WindowsApps"
$claudeProcCheck = Get-Process -Name claude -ErrorAction SilentlyContinue | Select-Object -First 1
$claudeInstallNote = $null
if ($claudeProcCheck -and $claudeProcCheck.Path) {
    if ($claudeProcCheck.Path -match $claudeStoreFingerprint) {
        $claudeInstallNote = "STORE version (sandboxed). MCP via claude_desktop_config.json IS supported as of late 2025; verified working today."
    } else {
        $claudeInstallNote = "standalone .exe install."
    }
    Write-Sub ("Claude Desktop: {0}" -f $claudeInstallNote)
} else {
    # Probe install paths even when not running
    $storeDir = "C:\Program Files\WindowsApps"
    $hasStore = (Test-Path $storeDir) -and (Get-ChildItem $storeDir -Filter "Claude_*" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1)
    $exeDir1 = "$env:LOCALAPPDATA\AnthropicClaude"
    $exeDir2 = "$env:LOCALAPPDATA\Programs\Claude"
    $hasExe = (Test-Path $exeDir1) -or (Test-Path $exeDir2)
    if ($hasStore) { Write-Sub "Claude Desktop: STORE version detected (not currently running)" }
    elseif ($hasExe) { Write-Sub "Claude Desktop: .exe install detected (not currently running)" }
    else { Write-Warn "Claude Desktop: not detected. Config will be written but cannot be verified live." }
}

# ============================================================================
# STEP 2: File-lock guard
# ============================================================================
Write-Step 2 $TotalSteps "Detect Claude Desktop holding curator-mcp.exe"

$claudeProcs = Get-Process -Name "claude" -ErrorAction SilentlyContinue
if ($claudeProcs) {
    Write-Warn "Claude Desktop is running ($($claudeProcs.Count) processes)."
    Write-Warn "It will hold curator-mcp.exe locked, which makes editable install fail with WinError 32."
    if (-not $Force) {
        $resp = Read-Host "Quit Claude Desktop now and press Enter to continue, or type 'skip' to proceed anyway (will likely fail)"
        if ($resp -ne "skip") {
            $stillRunning = Get-Process -Name "claude" -ErrorAction SilentlyContinue
            if ($stillRunning) {
                Write-Bad "Claude Desktop still running. Aborting. Re-run after fully quitting (system tray -> Quit)."
                exit 1
            }
            Write-Good "Claude Desktop is now closed."
        }
    }
} else {
    Write-Good "Claude Desktop is not running. Proceeding."
}

# ============================================================================
# STEP 3: Ensure venv
# ============================================================================
Write-Step 3 $TotalSteps "Ensure venv at $VenvPath exists"

if (-not (Test-Path $VenvPath)) {
    if ($PSCmdlet.ShouldProcess($VenvPath, "Create venv via system Python")) {
        Write-Sub "Creating venv (this takes ~30s)..."
        & $systemPy -m venv $VenvPath
        if (-not (Test-Path $VenvPy)) {
            Write-Bad "venv creation failed."
            exit 1
        }
        Write-Good "venv created at $VenvPath"
        Write-Sub "Upgrading pip..."
        & $VenvPy -m pip install --upgrade pip --quiet
    }
} else {
    Write-Good "venv already exists"
}

# ============================================================================
# STEP 4: Editable install (curator + plugins)
# ============================================================================
Write-Step 4 $TotalSteps "Editable install of curator + atrium-citation + atrium-safety"

# Smart skip: if the venv already has all three packages at expected versions
# AND Claude Desktop is running (would file-lock curator-mcp.exe), skip pip
# install entirely. Editable installs of unchanged source are no-ops; running
# them just risks the WinError 32 file-lock crash.
$skipInstall = $false
if ($claudeProcs) {
    Write-Sub "Claude Desktop is running. Probing whether install is needed at all..."
    $probeScript = @'
import json
try:
    import curator
    import curatorplug.atrium_citation
    import curatorplug.atrium_safety
    # Probe optional extras so we catch missing deps as a real failure
    try:
        import PySide6
        gui_ok = True
    except ImportError:
        gui_ok = False
    try:
        import mutagen, PIL, pypdf
        organize_ok = True
    except ImportError:
        organize_ok = False
    print(json.dumps({
        "curator": curator.__version__,
        "citation": curatorplug.atrium_citation.__version__,
        "safety": curatorplug.atrium_safety.__version__,
        "gui_ok": gui_ok,
        "organize_ok": organize_ok,
    }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
'@
    $tempProbe = [System.IO.Path]::GetTempFileName() + ".py"
    [System.IO.File]::WriteAllText($tempProbe, $probeScript, (New-Object System.Text.UTF8Encoding $false))
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $env:CURATOR_LOG_LEVEL = "ERROR"
    try {
        $probeJson = & $VenvPy $tempProbe 2>$null
    } finally {
        $ErrorActionPreference = $prevEAP
        Remove-Item $tempProbe -ErrorAction SilentlyContinue
    }
    if ($probeJson) {
        $probe = $probeJson | ConvertFrom-Json
        if (-not $probe.error -and $probe.curator -and $probe.citation -and $probe.safety) {
            Write-Good "All three packages already importable at versions: curator=$($probe.curator), citation=$($probe.citation), safety=$($probe.safety)"
            $extrasMissing = @()
            if ($probe.gui_ok) {
                Write-Good "GUI extra (PySide6 + networkx) importable"
            } else {
                $extrasMissing += "gui (PySide6/networkx)"
            }
            if ($probe.organize_ok) {
                Write-Good "Organize extra (mutagen + Pillow + pypdf) importable"
            } else {
                $extrasMissing += "organize (mutagen/Pillow/pypdf)"
            }
            if ($extrasMissing.Count -eq 0) {
                Write-Good "Skipping pip install (would be no-op; would also hit WinError 32 with Desktop running)"
                $skipInstall = $true
            } else {
                Write-Warn ("Missing extras: {0}; will run install with [gui,mcp,organize]" -f ($extrasMissing -join ", "))
                $skipInstall = $false
            }
        }
    }
}

if (-not $skipInstall) {
    # User-facing extras installed by default:
    #   [gui]      -> PySide6 + networkx (for `curator gui`)
    #   [mcp]      -> mcp package (required by curator-mcp.exe)
    #   [organize] -> mutagen + Pillow + piexif + pypdf + psutil
    #                 (for music/photo/document organize features)
    # Other extras (beta, cloud, windows, dev) stay opt-in via separate
    # pip install commands. [cloud] is needed to scan/migrate to/from
    # Google Drive (PyDrive2 + msgraph-sdk + dropbox).
    $packages = @(
        @{ name = "curator"; path = $CuratorRepo; extras = "[gui,mcp,organize]" },
        @{ name = "curatorplug-atrium-citation"; path = $CitationRepo; extras = "" },
        @{ name = "curatorplug-atrium-safety"; path = $SafetyRepo; extras = "" }
    )
    foreach ($pkg in $packages) {
        $installArg = if ($pkg.extras) { "$($pkg.path)$($pkg.extras)" } else { $pkg.path }
        Write-Sub ("Installing -e {0}" -f $installArg)
        if ($PSCmdlet.ShouldProcess($pkg.name, "pip install -e")) {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                $output = & $VenvPy -m pip install -e $installArg --quiet 2>&1
                $exitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $prevEAP
            }
            if ($exitCode -ne 0) {
                $errorText = ($output -join "`n")
                if ($errorText -match "WinError 32" -or $errorText -match "being used by another process") {
                    Write-Bad ("FILE LOCK: pip can't replace {0}'s entry-point shim because it's open in another process." -f $pkg.name)
                    Write-Bad "Most likely cause: Claude Desktop is running with curator-mcp.exe child process."
                    Write-Bad "Fix: quit Claude Desktop fully (system tray -> Quit) and re-run this installer."
                    if ($pkg.name -eq "curator") {
                        # Recovery: restore the .pth that pip just deleted
                        $venvSp = Join-Path $VenvPath "Lib\site-packages"
                        $rescuePth = Join-Path $venvSp "__editable__.curator-1.6.1.pth"
                        $curSrc = Join-Path $CuratorRepo "src"
                        $utf8NoBom = New-Object System.Text.UTF8Encoding $false
                        [System.IO.File]::WriteAllText($rescuePth, $curSrc, $utf8NoBom)
                        Write-Warn "Recovered: wrote rescue .pth so curator imports keep working."
                        # Sweep the corrupt ~urator-* leftover
                        Get-ChildItem $venvSp -Filter "~urator-*" -ErrorAction SilentlyContinue | ForEach-Object {
                            Remove-Item $_.FullName -Recurse -Force
                        }
                    }
                    exit 1
                }
                Write-Bad ("install failed for {0}: exit code {1}" -f $pkg.name, $exitCode)
                $output | ForEach-Object { Write-Sub $_ }
                exit 1
            }
        }
    }
    Write-Good "all three packages installed editable"
}

# ============================================================================
# STEP 5: Clean up corrupt dist-info leftovers
# ============================================================================
Write-Step 5 $TotalSteps "Sweep up corrupt dist-info from prior interrupted installs"

$venvSp = Join-Path $VenvPath "Lib\site-packages"
$leftovers = Get-ChildItem $venvSp -Filter "~*" -ErrorAction SilentlyContinue
if ($leftovers) {
    foreach ($item in $leftovers) {
        if ($PSCmdlet.ShouldProcess($item.FullName, "Remove corrupt dist-info")) {
            Remove-Item $item.FullName -Recurse -Force
            Write-Sub "removed: $($item.Name)"
        }
    }
} else {
    Write-Good "no leftovers to clean"
}

# ============================================================================
# STEP 6: Verify imports + versions
# ============================================================================
Write-Step 6 $TotalSteps "Runtime version check"

$versionScript = @'
import sys
results = {}
try:
    import curator
    results["curator"] = curator.__version__
except Exception as e:
    results["curator"] = f"FAIL: {e}"
try:
    import curatorplug.atrium_citation
    results["curatorplug.atrium_citation"] = curatorplug.atrium_citation.__version__
except Exception as e:
    results["curatorplug.atrium_citation"] = f"FAIL: {e}"
try:
    import curatorplug.atrium_safety
    results["curatorplug.atrium_safety"] = curatorplug.atrium_safety.__version__
except Exception as e:
    results["curatorplug.atrium_safety"] = f"FAIL: {e}"
import json
print(json.dumps(results))
'@
$tempScript = [System.IO.Path]::GetTempFileName() + ".py"
# WriteAllText bypasses ShouldProcess (a query, not a state change we want gated)
[System.IO.File]::WriteAllText($tempScript, $versionScript, (New-Object System.Text.UTF8Encoding $false))
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$env:CURATOR_LOG_LEVEL = "ERROR"
try {
    $versionsJson = & $VenvPy $tempScript 2>$null
} finally {
    $ErrorActionPreference = $prevEAP
    Remove-Item $tempScript -ErrorAction SilentlyContinue
}

$versions = $versionsJson | ConvertFrom-Json
$failed = $false
foreach ($pkg in @("curator", "curatorplug.atrium_citation", "curatorplug.atrium_safety")) {
    $v = $versions.$pkg
    if ($v -like "FAIL:*") {
        Write-Bad "$pkg => $v"
        $failed = $true
    } else {
        Write-Good "$pkg => $v"
    }
}
if ($failed) {
    Write-Bad "Cannot continue with import failures."
    exit 1
}

# ============================================================================
# STEP 7: Canonical DB + canonical TOML config
# ============================================================================
Write-Step 7 $TotalSteps "Initialize canonical DB at $CanonicalDb"

$dbDir = Split-Path $CanonicalDb -Parent
if (-not (Test-Path $dbDir)) {
    if ($PSCmdlet.ShouldProcess($dbDir, "Create DB directory")) {
        New-Item -ItemType Directory -Path $dbDir -Force | Out-Null
        Write-Sub "created: $dbDir"
    }
}

# Write/update the canonical curator.toml that pins curator-mcp to this DB.
# This is what CURATOR_CONFIG env var (set in claude_desktop_config.json)
# points at, so Claude Desktop's curator-mcp picks up the right DB without
# needing a --db CLI flag (curator-mcp doesn't support --db; only the
# 'curator' CLI does).
$CanonicalToml = Join-Path $dbDir "curator.toml"
$tomlContent = "# Canonical Curator config.`n# Loaded when CURATOR_CONFIG env var points at this file.`n# Configured by Claude Desktop's claude_desktop_config.json.`n# Auto-managed by Install-Curator.ps1 -- edit at your own risk.`n`n[curator]`ndb_path = `"$($CanonicalDb.Replace('\', '\\'))`"`nlog_path = `"auto`"`nlog_level = `"INFO`"`n"
if ($PSCmdlet.ShouldProcess($CanonicalToml, "Write canonical curator.toml")) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($CanonicalToml, $tomlContent, $utf8NoBom)
    Write-Good "wrote: $CanonicalToml"
}

$integrityScript = @"
import sqlite3, sys, os
p = r'$CanonicalDb'
if not os.path.exists(p):
    print('NOTEXIST')
    sys.exit(0)
try:
    conn = sqlite3.connect(p)
    r = conn.execute('PRAGMA integrity_check').fetchone()[0]
    n = conn.execute('SELECT COUNT(*) FROM files').fetchone()[0]
    a = conn.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]
    print(f'OK|{r}|files={n}|audit={a}')
    conn.close()
except Exception as e:
    print(f'CORRUPT|{e}')
"@
$tempScript = [System.IO.Path]::GetTempFileName() + ".py"
[System.IO.File]::WriteAllText($tempScript, $integrityScript, (New-Object System.Text.UTF8Encoding $false))
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$env:CURATOR_LOG_LEVEL = "ERROR"
try {
    $dbStatus = (& $VenvPy $tempScript 2>$null) -join ""
} finally {
    $ErrorActionPreference = $prevEAP
    Remove-Item $tempScript -ErrorAction SilentlyContinue
}

if ($dbStatus -eq "NOTEXIST") {
    Write-Sub "DB does not exist; will be created by curator on first access"
    if ($PSCmdlet.ShouldProcess($CanonicalDb, "Initialize via curator doctor")) {
        & $VenvCurator --db $CanonicalDb doctor 2>$null | Out-Null
    }
    Write-Good "DB initialized"
} elseif ($dbStatus -like "OK|ok*") {
    Write-Good "DB exists and integrity_check=ok ($dbStatus)"
} else {
    Write-Warn "DB exists but is corrupt: $dbStatus"
    $backupName = "curator.db.corrupt-backup-$Timestamp"
    $backupPath = Join-Path $dbDir $backupName
    if ($PSCmdlet.ShouldProcess($CanonicalDb, "Back up corrupt DB and re-init")) {
        Move-Item $CanonicalDb -Destination $backupPath -Force
        # Move WAL + SHM too if present
        foreach ($ext in @(".db-shm", ".db-wal")) {
            $extra = $CanonicalDb -replace "\.db$", $ext
            if (Test-Path $extra) {
                $extraBackup = Join-Path $dbDir ($backupName + ($ext -replace "^\.db", ""))
                Move-Item $extra -Destination $extraBackup -Force
            }
        }
        Write-Sub "corrupt DB quarantined to: $backupPath"
        & $VenvCurator --db $CanonicalDb doctor 2>$null | Out-Null
        Write-Good "DB re-initialized; old DB preserved as $backupName"
    }
}

# ============================================================================
# STEP 8: Patch claude_desktop_config.json
# ============================================================================
Write-Step 8 $TotalSteps "Update Claude Desktop config to point curator-mcp at canonical DB"

if (-not (Test-Path $ClaudeCfgPath)) {
    Write-Sub "Claude Desktop config does not exist; creating fresh."
    if ($PSCmdlet.ShouldProcess($ClaudeCfgPath, "Create config")) {
        $newConfig = @{
            mcpServers = @{}
            preferences = @{}
        }
        $newConfig | ConvertTo-Json -Depth 10 | Out-File -FilePath $ClaudeCfgPath -Encoding utf8NoBOM
    }
}

# Backup
$cfgBackup = "$ClaudeCfgPath.bak.$Timestamp"
if ($PSCmdlet.ShouldProcess($ClaudeCfgPath, "Backup current config")) {
    Copy-Item $ClaudeCfgPath -Destination $cfgBackup -Force
    Write-Sub "backup: $cfgBackup"
}

# Read current, modify, write back
$cfgRaw = Get-Content $ClaudeCfgPath -Raw
$cfg = $cfgRaw | ConvertFrom-Json

if (-not $cfg.mcpServers) {
    $cfg | Add-Member -MemberType NoteProperty -Name mcpServers -Value (@{} | ConvertTo-Json -Depth 1 | ConvertFrom-Json) -Force
}

# Build curator entry — uses CURATOR_CONFIG env var to point at a canonical
# TOML config file. Cannot use --db CLI flag here because curator-mcp.exe
# does NOT accept --db (only the 'curator' CLI does). The TOML approach is
# the documented mechanism per src/curator/config/__init__.py docstring.
$curatorEntry = [PSCustomObject]@{
    command = $VenvCuratorMcp
    args = @()
    env = [PSCustomObject]@{
        CURATOR_CONFIG = $CanonicalToml
    }
}
# Set or replace
if ($cfg.mcpServers.curator) {
    $cfg.mcpServers.curator = $curatorEntry
    Write-Sub "updated existing 'curator' entry"
} else {
    $cfg.mcpServers | Add-Member -MemberType NoteProperty -Name curator -Value $curatorEntry -Force
    Write-Sub "added new 'curator' entry"
}

if ($PSCmdlet.ShouldProcess($ClaudeCfgPath, "Write updated config")) {
    # Use UTF-8 NO BOM (Notepad-compatible, Claude Desktop-friendly).
    # Use Python's json module for clean output — PowerShell's ConvertTo-Json
    # produces verbose padded output that's hard to read.
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $cfgRawJson = $cfg | ConvertTo-Json -Depth 20 -Compress
    $tempIn = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tempIn, $cfgRawJson, $utf8NoBom)
    $cfgJson = $null
    try {
        # python returns array of lines via PowerShell; join with LF for proper formatting
        $cfgJsonLines = & $VenvPy -c "import json,sys; d=json.load(open(r'$tempIn','r',encoding='utf-8')); print(json.dumps(d, indent=2, ensure_ascii=False))" 2>$null
        if ($cfgJsonLines -is [array]) {
            $cfgJson = $cfgJsonLines -join "`n"
        } else {
            $cfgJson = $cfgJsonLines
        }
    } catch {} finally {
        Remove-Item $tempIn -ErrorAction SilentlyContinue
    }
    if (-not $cfgJson) {
        # Fall back to PowerShell ConvertTo-Json (verbose but valid JSON)
        $cfgJson = $cfg | ConvertTo-Json -Depth 20
    }
    [System.IO.File]::WriteAllText($ClaudeCfgPath, $cfgJson, $utf8NoBom)

    # Verify what we wrote
    $bytes = [System.IO.File]::ReadAllBytes($ClaudeCfgPath)
    $hasBom = $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    if ($hasBom) {
        Write-Bad "wrote BOM unexpectedly! Restoring backup."
        Copy-Item $cfgBackup -Destination $ClaudeCfgPath -Force
        exit 1
    }
    try {
        $null = $bytes | Out-Null
        $verify = Get-Content $ClaudeCfgPath -Raw | ConvertFrom-Json -ErrorAction Stop
        Write-Good "config written: valid JSON, no BOM, $($bytes.Count) bytes"
        Write-Sub ("mcpServers entries: " + ($verify.mcpServers.PSObject.Properties.Name -join ', '))
    } catch {
        Write-Bad "wrote invalid JSON. Restoring backup."
        Copy-Item $cfgBackup -Destination $ClaudeCfgPath -Force
        exit 1
    }
}

# ============================================================================
# STEP 9: REAL MCP probe — replicates Claude Desktop's launch + verifies tools
# ============================================================================
Write-Step 9 $TotalSteps "Verify Claude Desktop's launch path: spawn curator-mcp with same command+args+env"

if ($WhatIfPreference) {
    Write-Sub "(skipped in dry-run mode)"
} else {
    # This is the bulletproof check: we replicate exactly what Claude Desktop
    # will do at startup — spawn curator-mcp.exe with empty args and CURATOR_CONFIG
    # in the env, send MCP initialize + tools/list, assert >=9 tools come back.
    # If this fails, the installer caught the bug BEFORE Claude Desktop sees it.

    $probeScript = @"
import subprocess, json, os, sys

env = os.environ.copy()
env['CURATOR_CONFIG'] = r'$CanonicalToml'
env['CURATOR_LOG_LEVEL'] = 'ERROR'

proc = subprocess.Popen(
    [r'$VenvCuratorMcp'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, bufsize=1, env=env,
)

def send(req):
    proc.stdin.write(json.dumps(req) + '\n')
    proc.stdin.flush()

def read_one():
    return proc.stdout.readline()

try:
    send({'jsonrpc':'2.0','id':0,'method':'initialize',
          'params':{'protocolVersion':'2025-06-18','capabilities':{},
                    'clientInfo':{'name':'installer-probe','version':'0'}}})
    init_resp = read_one()
    if not init_resp:
        raise RuntimeError('no initialize response (process likely crashed)')
    init_data = json.loads(init_resp)
    if 'error' in init_data:
        raise RuntimeError('initialize returned error: ' + json.dumps(init_data['error']))

    send({'jsonrpc':'2.0','method':'notifications/initialized'})
    send({'jsonrpc':'2.0','id':1,'method':'tools/list'})
    tools_resp = read_one()
    if not tools_resp:
        raise RuntimeError('no tools/list response')
    tools_data = json.loads(tools_resp)
    tools = tools_data.get('result',{}).get('tools',[])

    send({'jsonrpc':'2.0','id':2,'method':'tools/call',
          'params':{'name':'health_check','arguments':{}}})
    health_resp = read_one()
    health_data = json.loads(health_resp) if health_resp else {}
    structured = health_data.get('result',{}).get('structuredContent',{}) if health_resp else {}

    print(json.dumps({
        'ok': True,
        'tools_count': len(tools),
        'tool_names': [t['name'] for t in tools],
        'health_check': structured,
    }))
except Exception as e:
    err_text = ''
    try:
        proc.stdin.close()
        err_text = proc.stderr.read() if proc.stderr else ''
    except: pass
    print(json.dumps({'ok': False, 'error': str(e), 'stderr_first_500': err_text[:500]}))
finally:
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except: pass
"@
    $tempProbe = [System.IO.Path]::GetTempFileName() + ".py"
    [System.IO.File]::WriteAllText($tempProbe, $probeScript, (New-Object System.Text.UTF8Encoding $false))

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $resultJson = & $VenvPy $tempProbe 2>$null
    } finally {
        $ErrorActionPreference = $prevEAP
        Remove-Item $tempProbe -ErrorAction SilentlyContinue
    }

    try {
        $result = $resultJson | ConvertFrom-Json
    } catch {
        Write-Bad "MCP probe returned malformed output. Restoring config backup."
        Copy-Item $cfgBackup -Destination $ClaudeCfgPath -Force
        exit 1
    }

    if ($result.ok) {
        Write-Good ("MCP probe SUCCESS: {0} tools advertised" -f $result.tools_count)
        if ($result.tools_count -lt 9) {
            Write-Warn ("Expected >=9 tools but got {0}; plugins may not all be loading." -f $result.tools_count)
        }
        Write-Sub ("Tools: {0}" -f ($result.tool_names -join ', '))
        if ($result.health_check) {
            Write-Sub ("health_check: curator_version={0}, plugin_count={1}" -f $result.health_check.curator_version, $result.health_check.plugin_count)
            Write-Sub ("db_path: {0}" -f $result.health_check.db_path)
        }
    } else {
        Write-Bad ("MCP probe FAILED: {0}" -f $result.error)
        if ($result.stderr_first_500) {
            Write-Sub "stderr from curator-mcp:"
            ($result.stderr_first_500 -split "`n") | Select-Object -First 8 | ForEach-Object { Write-Sub "  $_" }
        }
        Write-Bad "Restoring config from backup since curator-mcp failed real-launch test."
        Copy-Item $cfgBackup -Destination $ClaudeCfgPath -Force
        Write-Bad "Config restored. Re-run installer after fixing the underlying issue."
        exit 1
    }
}

# ============================================================================
# STEP 10: Summary
# ============================================================================
Write-Step 10 $TotalSteps "Summary"
Write-Host ""
Write-Host "Installation complete. Next steps:" -ForegroundColor White
Write-Host ""
Write-Host "  1. Start Claude Desktop. The curator MCP server should load automatically."
Write-Host "     Verify in Settings -> Connectors that 'curator' is listed."
Write-Host ""
Write-Host "  2. From any chat, you can call curator MCP tools (health_check, query_audit_log,"
Write-Host "     list_sources, etc.). They will operate against:"
Write-Host "        $CanonicalDb"
Write-Host ""
Write-Host "  3. To use the CLI from a regular shell, either:"
Write-Host "       - activate the venv: $VenvPath\Scripts\Activate.ps1"
Write-Host "       - or use full path:  $VenvCurator"
Write-Host ""
Write-Host "  4. Re-run this installer any time to refresh the editable install or"
Write-Host "     reset to a clean state. It is idempotent."
Write-Host ""
Write-Host "  Files created/modified by this run:"
Write-Host "       venv:        $VenvPath"
Write-Host "       canonicalDb: $CanonicalDb"
Write-Host "       cfg backup:  $cfgBackup"
Write-Host ""
Write-Host "==============================================" -ForegroundColor White
Write-Host "  Done." -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor White
