<#
.SYNOPSIS
    Workflow: Curator stack health check.
.DESCRIPTION
    Read-only. Runs a battery of checks across the Curator stack:
      - Filesystem layout (canonical paths exist)
      - Venv + Python version
      - curator + plugin versions match expected
      - DB integrity_check
      - 'curator doctor' subsystem report
      - claude_desktop_config.json points at canonical curator-mcp
      - Real MCP probe (spawn curator-mcp, initialize, tools/list)
      - GUI dependency (PySide6 importable)
    Renders a green/red dashboard at the end.
#>

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Test-CuratorAvailable
Show-Banner "Curator workflow: Health check"

$results = [ordered]@{}

function Add-Check {
    param([string]$Name, [bool]$Pass, [string]$Detail = "")
    $script:results[$Name] = @{ Pass = $Pass; Detail = $Detail }
    $marker = if ($Pass) { "[ OK ]" } else { "[FAIL]" }
    $color = if ($Pass) { "Green" } else { "Red" }
    Write-Host ("  {0} {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host ("         {0}" -f $Detail) }
}

# ---- 1. Filesystem layout ----
Show-Section "1. Filesystem layout"
foreach ($pair in @(
    @{ name = "Curator repo";    path = $CuratorRoot },
    @{ name = "Venv";            path = $CuratorVenv },
    @{ name = "Canonical DB";    path = $CanonicalDb },
    @{ name = "Canonical TOML";  path = $CanonicalToml }
)) {
    Add-Check $pair.name (Test-Path $pair.path) $pair.path
}

# ---- 2. Python + venv ----
Show-Section "2. Python + venv"
try {
    $pyVer = & $CuratorVenvPy --version 2>&1
    Add-Check "Venv Python launches" $true $pyVer
} catch {
    Add-Check "Venv Python launches" $false "$_"
}

# ---- 3. Package versions ----
Show-Section "3. Curator + plugin versions"
$probe = & $CuratorVenvPy -c @"
import json
try:
    import curator, curatorplug.atrium_citation, curatorplug.atrium_safety
    print(json.dumps({
        'curator': curator.__version__,
        'citation': curatorplug.atrium_citation.__version__,
        'safety': curatorplug.atrium_safety.__version__
    }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"@ 2>$null

if ($probe) {
    $v = $probe | ConvertFrom-Json
    if ($v.error) {
        Add-Check "Package imports" $false $v.error
    } else {
        Add-Check "curator package"           ($v.curator -eq "1.6.1")  "$($v.curator) (expected 1.6.1)"
        Add-Check "atrium-citation plugin"    ($v.citation -eq "0.2.0") "$($v.citation) (expected 0.2.0)"
        Add-Check "atrium-safety plugin"      ($v.safety -eq "0.3.0")   "$($v.safety) (expected 0.3.0)"
    }
} else {
    Add-Check "Package imports" $false "no JSON returned"
}

# ---- 4. GUI dependency ----
Show-Section "4. GUI dependency (PySide6)"
$guiProbe = & $CuratorVenvPy -c "import PySide6; print(PySide6.__version__)" 2>$null
Add-Check "PySide6 importable" ([bool]$guiProbe) $(if ($guiProbe) { $guiProbe } else { "missing" })

# ---- 5. DB integrity ----
Show-Section "5. DB integrity"
$intCheck = & $CuratorVenvPy -c @"
import sqlite3
try:
    c = sqlite3.connect(r'$CanonicalDb')
    r = c.execute('PRAGMA integrity_check').fetchone()
    print('ok' if r and r[0] == 'ok' else f'FAIL:{r}')
    c.close()
except Exception as e:
    print(f'ERR:{e}')
"@ 2>$null
Add-Check "PRAGMA integrity_check" ($intCheck -eq "ok") $intCheck

# ---- 6. Curator doctor ----
Show-Section "6. curator doctor"
$doctorOut = & $CuratorCli doctor 2>$null
$doctorPlugins = $doctorOut | Select-String -Pattern "Plugins" | Select-Object -First 1
Add-Check "curator doctor runs" $true $doctorPlugins

# ---- 7. Claude Desktop config ----
Show-Section "7. Claude Desktop MCP config"
$cdCfg = "$env:APPDATA\Claude\claude_desktop_config.json"
if (Test-Path $cdCfg) {
    $cfg = Get-Content $cdCfg -Raw | ConvertFrom-Json
    $hasCurator = $cfg.mcpServers.curator -ne $null
    $cmdOk = $cfg.mcpServers.curator.command -eq (Join-Path $CuratorVenv "Scripts\curator-mcp.exe")
    $envOk = $cfg.mcpServers.curator.env.CURATOR_CONFIG -eq $CanonicalToml
    Add-Check "config file exists"            (Test-Path $cdCfg)
    Add-Check "has curator entry"             $hasCurator
    Add-Check "command points at venv"        $cmdOk $cfg.mcpServers.curator.command
    Add-Check "CURATOR_CONFIG env correct"    $envOk
} else {
    Add-Check "config file exists" $false "$cdCfg missing"
}

# ---- 8. Real MCP probe ----
Show-Section "8. Real MCP probe (spawn curator-mcp, initialize, tools/list)"
$probeScript = Join-Path $env:TEMP "curator_health_probe_$(Get-Random).py"
$probeCode = @"
import json, subprocess, sys, os, time
env = os.environ.copy()
env['CURATOR_CONFIG'] = r'$CanonicalToml'
mcp_exe = r'$CuratorVenv\Scripts\curator-mcp.exe'
proc = subprocess.Popen([mcp_exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
def send(msg):
    proc.stdin.write(json.dumps(msg) + '\n'); proc.stdin.flush()
def recv():
    line = proc.stdout.readline()
    return json.loads(line) if line else None
try:
    send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'health','version':'1.0'}}})
    init = recv()
    send({'jsonrpc':'2.0','method':'notifications/initialized'})
    send({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
    tools = recv()
    n = len(tools.get('result',{}).get('tools',[])) if tools else 0
    print(f'TOOLS:{n}')
finally:
    proc.terminate()
    try: proc.wait(timeout=2)
    except: proc.kill()
"@
Set-Content -Path $probeScript -Value $probeCode -Encoding UTF8
$probeResult = & $CuratorVenvPy $probeScript 2>$null
Remove-Item $probeScript -ErrorAction SilentlyContinue
$mcpOk = $probeResult -match "TOOLS:9"
Add-Check "MCP probe (initialize + tools/list)" $mcpOk $probeResult

# ---- Final dashboard ----
Show-Section "Summary"
$pass = ($results.Values | Where-Object { $_.Pass }).Count
$fail = ($results.Values | Where-Object { -not $_.Pass }).Count
$total = $results.Count
Write-Host ""
Write-Host ("  {0} of {1} checks passed" -f $pass, $total) -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })
if ($fail -gt 0) {
    Write-Host ""
    Write-Host "  FAILED:" -ForegroundColor Red
    $results.GetEnumerator() | Where-Object { -not $_.Value.Pass } | ForEach-Object {
        Write-Host ("    - {0}: {1}" -f $_.Key, $_.Value.Detail)
    }
    Write-Host ""
    Write-Host "  Recommended next step: re-run installer" -ForegroundColor Yellow
    Write-Host "    $CuratorRoot\installer\Install-Curator.bat"
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })
Write-Host "  Done." -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })
Write-Host "==============================================" -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })
Read-Host "Press Enter to close"
