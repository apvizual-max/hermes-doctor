@echo off
REM ===========================================================================
REM  hermes_bug-monitor_sys.bat
REM  OPTIMUS Prime - Hermes Bug-Monitor launcher
REM
REM  Usage:
REM    hermes_bug-monitor_sys.bat daemon           run the 5-minute daemon (foreground)
REM    hermes_bug-monitor_sys.bat start            start the daemon detached + hidden
REM    hermes_bug-monitor_sys.bat stop             stop a running daemon
REM    hermes_bug-monitor_sys.bat status           print JSON status
REM    hermes_bug-monitor_sys.bat scan             run one scan now
REM    hermes_bug-monitor_sys.bat fix              remediate + re-apply source guards
REM    hermes_bug-monitor_sys.bat selftest         verify the monitor's own invariants
REM    hermes_bug-monitor_sys.bat install-startup  register autostart at Windows logon
REM    hermes_bug-monitor_sys.bat uninstall-startup
REM    hermes_bug-monitor_sys.bat dashboard        open the HTML dashboard
REM    hermes_bug-monitor_sys.bat path             print the resolved interpreter
REM
REM  Exit code 0 = clean, 1 = ghost path detected, 2 = error.
REM ===========================================================================
setlocal EnableDelayedExpansion
title Hermes Bug-Monitor

set "BM_ROOT=%~dp0"
set "BM_ROOT=%BM_ROOT:~0,-1%"
set "BM_SCRIPT=%BM_ROOT%\hermes_bug-monitor.py"

REM --- locate a Python ------------------------------------------------------
REM Order matters: the bundled store interpreter is preferred over the venv
REM because `hermes update` rebuilds the venv, and this monitor must keep
REM working while an update is in flight or has half-failed.
set "PY="
set "PYEXE="

for %%P in (
  "%BM_ROOT%..\..\..\..\tools\python-3.14.7+20260901-win32-x64\python.exe"
  "%LOCALAPPDATA%\hermes\tools\python-3.14.7+20260901-win32-x64\python.exe"
) do (
  if not defined PY if exist %%P (
    "%%P" -c "import sys" >nul 2>&1 && set "PY=%%~P"
  )
)

if not defined PY (
  for %%P in ("%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe") do (
    if exist %%P (
      "%%P" -c "import sys" >nul 2>&1 && set "PY=%%~P"
    )
  )
)

if not defined PY (
  where py >nul 2>&1 && ( py -3 -c "import sys" >nul 2>&1 ) && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>&1 && ( python -c "import sys" >nul 2>&1 ) && set "PY=python"
)

if not defined PY (
  echo [bug-monitor] FATAL: no usable Python interpreter found.
  echo [bug-monitor] Expected the bundled store Python under %LOCALAPPDATA%\hermes\tools\.
  exit /b 2
)

if /i "%~1"=="path" (
  echo %PY%
  exit /b 0
)

if not exist "%BM_SCRIPT%" (
  echo [bug-monitor] FATAL: hermes_bug-monitor.py not found at "%BM_SCRIPT%".
  exit /b 2
)

if "%~1"=="" set "ACTION=daemon"
if not "%~1"=="" set "ACTION=%~1"

if /i "%ACTION%"=="start" (
  REM Detached + hidden so no console window lingers after logon.
  start "" /b %PY% "%BM_SCRIPT%" monitor
  echo [bug-monitor] daemon starting in background.
  timeout /t 3 /nobreak >nul
  %PY% "%BM_SCRIPT%" status
  exit /b %ERRORLEVEL%
)

if /i "%ACTION%"=="stop" (
  for /f "usebackq tokens=2" %%P in (`%PY% "%BM_SCRIPT%" status`) do set "PID=%%P"
  REM Simpler and more reliable: read the pid file directly.
  if exist "%BM_ROOT%\state\monitor.pid" (
    set /p DPID=<"%BM_ROOT%\state\monitor.pid"
    if defined DPID (
      taskkill /PID !DPID! /F >nul 2>&1
      if errorlevel 1 (
        echo [bug-monitor] no running daemon with PID !DPID!.
      ) else (
        echo [bug-monitor] daemon !DPID! stopped.
      )
      del /q "%BM_ROOT%\state\monitor.pid" >nul 2>&1
      del /q "%BM_ROOT%\state\monitor.lock" >nul 2>&1
    )
  ) else (
    echo [bug-monitor] no pid file; daemon not running via this launcher.
  )
  exit /b 0
)

if /i "%ACTION%"=="dashboard" (
  start "" "%BM_ROOT%\dashboard.html"
  exit /b 0
)

%PY% "%BM_SCRIPT%" %ACTION% %2 %3 %4
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
