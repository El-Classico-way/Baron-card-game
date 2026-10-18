@echo off
rem Baron na Windows: dvojklik nebo "start.bat" (volitelne "start.bat --lan"). Vsechno dela start.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
