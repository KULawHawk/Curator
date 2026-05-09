@echo off
REM Curator workflow: initial scan
REM Double-click to run.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp001_initial_scan.ps1"
