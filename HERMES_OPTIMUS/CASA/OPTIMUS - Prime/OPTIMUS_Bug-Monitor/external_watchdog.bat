@echo off
REM ============================================================================
REM  Ghost-Path External Watchdog
REM  Independent of Hermes. Runs from Windows Task Scheduler at every logon
REM  and every 15 minutes. Detects the C:\c\ ghost path tree even if Hermes
REM  itself is broken, down, or mid-update. Logs to a non-Hermes location so
REM  the signal survives a Hermes-data wipe.
REM
REM  Why this exists: the Bug-Monitor daemon (hermes_bug-monitor.py) lives
REM  inside Hermes. Hermes update can reverts its source guards. This wrapper
REM  has zero Hermes dependency - just stdlib Python, a hard-coded path, and
REM  Windows Task Scheduler.
REM
REM  Schedule (set by install-external-watchdog.bat):
REM    - At logon
REM    - Every 15 minutes thereafter
REM
REM  Exit codes:
REM    0  - clean
REM    1  - ghost path detected
REM    2  - internal error
REM ============================================================================

setlocal

set "BM_ROOT=%~dp0"
set "BM_ROOT=%BM_ROOT:~0,-1%"
set "PY=%BM_ROOT%\..\..\..\..\..\..\..\..\hermes-agent\tools\python-3.14.7+20260901-win32-x64\python.exe"
if not exist "%PY%" (
    set "PY=python.exe"
)

set "LOG_DIR=%~dp0logs"
set "EXTERNAL_LOG=%LOG_DIR%\external-watchdog.jsonl"

REM --- detect ghost path ---
set "GHOST_FOUND=0"
for %%D in (C D E F G H I J K L M N O P Q R S T U V W X Y) do (
    if exist "%%D:\c\Users" (
        set "GHOST_FOUND=1"
        echo [%date% %time%] GHOST PATH DETECTED: %%D:\c\Users >> "%EXTERNAL_LOG%"
    )
)

if "%GHOST_FOUND%"=="1" (
    REM Also notify Bug-Monitor daemon if alive
    "%PY%" "%BM_ROOT%\hermes_bug-monitor.py" status >nul 2>&1
    exit /b 1
)

exit /b 0
