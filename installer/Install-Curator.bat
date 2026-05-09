@echo off
REM ===================================================================
REM   Curator stack installer - double-click wrapper
REM
REM   This .bat file invokes the PowerShell installer with execution
REM   policy bypass so it runs even if your default policy is Restricted.
REM
REM   For options, run from a PowerShell prompt instead:
REM     .\Install-Curator.ps1 -WhatIf            (dry run)
REM     .\Install-Curator.ps1 -Force             (skip prompts)
REM     .\Install-Curator.ps1 -RepoRoot "C:\..." (custom repo location)
REM ===================================================================

setlocal
set SCRIPT_DIR=%~dp0
set PS_SCRIPT=%SCRIPT_DIR%Install-Curator.ps1

if not exist "%PS_SCRIPT%" (
    echo ERROR: Cannot find %PS_SCRIPT%
    pause
    exit /b 1
)

REM Make sure Claude Desktop is closed before continuing
echo.
echo ============================================================
echo  Curator stack installer
echo ============================================================
echo.
echo This installer will refresh the curator + atrium-citation +
echo atrium-safety stack and update Claude Desktop's MCP config.
echo.
echo IMPORTANT: Quit Claude Desktop fully before continuing.
echo (System tray icon -^> Quit, NOT just close the window.)
echo.
pause

powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%PS_SCRIPT%"

echo.
echo ============================================================
echo  Installer finished. Press any key to close this window.
echo ============================================================
pause >nul
