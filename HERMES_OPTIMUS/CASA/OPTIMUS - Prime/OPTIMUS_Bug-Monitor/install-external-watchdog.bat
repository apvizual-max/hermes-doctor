@echo off
REM ============================================================================
REM  install-external-watchdog.bat
REM  Registers the Ghost-Path External Watchdog as a Windows Scheduled Task.
REM  Run ONCE with admin rights. Survives Hermes updates, restarts, reboots.
REM  Independent of Hermes, scheduled by the OS, not by any Python daemon.
REM
REM  Usage:  install-external-watchdog.bat
REM          (right-click -> Run as administrator)
REM
REM  After install:
REM    schtasks /Query /TN "GhostPathExternalWatchdog"
REM    schtasks /Run /TN "GhostPathExternalWatchdog"
REM ============================================================================

setlocal

set "BM_ROOT=%~dp0"
set "BM_ROOT=%BM_ROOT:~0,-1%"
set "WATCHDOG=%BM_ROOT%\external_watchdog.bat"

if not exist "%WATCHDOG%" (
    echo ERROR: external_watchdog.bat not found at %WATCHDOG%
    exit /b 1
)

REM --- unregister any prior copies ---
schtasks /Delete /TN "GhostPathExternalWatchdog" /F >nul 2>&1
schtasks /Delete /TN "GhostPathExternalWatchdog-15m" /F >nul 2>&1

REM --- create task 1: at logon ---
schtasks /Create ^
    /TN "GhostPathExternalWatchdog" ^
    /TR "\"%WATCHDOG%\"" ^
    /SC ONLOGON ^
    /RL LIMITED ^
    /F

if errorlevel 1 (
    echo ERROR: schtasks /Create (logon) failed. Run as administrator.
    exit /b 1
)

REM --- create task 2: every 15 minutes ---
schtasks /Create ^
    /TN "GhostPathExternalWatchdog-15m" ^
    /TR "\"%WATCHDOG%\"" ^
    /SC MINUTE ^
    /MO 15 ^
    /RL LIMITED ^
    /F

if errorlevel 1 (
    echo ERROR: schtasks /Create (15-min) failed. Run as administrator.
    exit /b 1
)

echo.
echo Ghost-Path External Watchdog installed (2 tasks).
echo.
echo Task 1: GhostPathExternalWatchdog     (at logon)
echo Task 2: GhostPathExternalWatchdog-15m (every 15 minutes)
echo Action : %WATCHDOG%
echo Log    : %BM_ROOT%\logs\external-watchdog.jsonl
echo.
echo Verify:  schtasks /Query /TN "GhostPathExternalWatchdog" /V /FO LIST
exit /b 0
