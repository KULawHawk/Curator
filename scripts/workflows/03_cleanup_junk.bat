@echo off
REM Curator workflow: cleanup junk
REM Double-click to run.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp003_cleanup_junk.ps1"
