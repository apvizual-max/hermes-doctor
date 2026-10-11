# Process Tree — hermes_bug-monitor_sys

Every OS process the system creates, why it exists, and what it is allowed to touch.

```
WINDOWS LOGON
│
├── [30 s settle]
│   └── HermesBugMonitor (Scheduled Task, RunLevel=Limited, Interactive logon)
│       │
│       ├── cmd.exe /c timeout /t 30 /nobreak >nul & "...\hermes_bug-monitor_sys.bat" daemon
│       │   └── WHY cmd.exe, not the .bat directly: the task scheduler will not
│       │       resolve a .bat, and the 30 s pause lets the logon storm (profile
│       │       load, Defender, Hermes itself) finish before the first scan so the
│       │       monitor does not report churn caused by ordinary startup.
│       │
│       └── python.exe (store interpreter)  ← the daemon
│           exec: tools\python-3.14.7+20260901-win32-x64\python.exe
│                 hermes_bug-monitor.py monitor
│           │
│           │  WHY the store interpreter and not the venv:
│           │  `hermes update` rebuilds hermes-agent\venv. A monitor that lived in
│           │  the venv would die exactly when the thing it monitors is broken.
│           │  The store interpreter is versioned beside Hermes but sits outside
│           │  the venv, so it survives uv sync / pm install.
│           │
│           ├── WRITES  state\monitor.pid        (own PID, for `stop`)
│           ├── WRITES  state\monitor.lock       (O_EXCL single-instance guard)
│           ├── READS   HERMES_HOME             (ghost probe + config watchlist)
│           ├── WRITES  state\monitor_state.json (atomic tmp+replace)
│           ├── WRITES  logs\bug-monitor.jsonl  (ring buffer, ≤50)
│           ├── WRITES  logs\bug-monitor.log    (flattened view)
│           ├── WRITES  reports\latest-report.txt
│           ├── WRITES  dashboard_data.js       (every cycle)
│           └── SLEEPS  300 s between cycles (interruptible, 1 s granularity)
│
│   └── EVERY 300 s, the daemon spawns:
│
│       ├── [only when state CHANGED and a defect is present]
│       │   └── powershell.exe -NoProfile -STA -ExecutionPolicy Bypass
│       │         -File popup_alert.ps1 -Payload state\popup_payload.json
│       │       │
│       │       ├── flags: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
│       │       │   WHY: a blocking modal must never stall the scan loop. If the
│       │       │   operator leaves the dialog open for an hour, scanning must
│       │       │   continue; the loop is the guard, the popup is a courtesy.
│       │       │
│       │       ├── creates a WinForms topmost form
│       │       │   WHY WinForms and not MessageBoxW: MessageBox cannot label its
│       │       │   buttons, and the operator needs "Copy Log Report" / "Fix It Now"
│       │       │   rather than Yes/No.
│       │       │
│       │       ├── [Copy Log Report] ──> reads reports\latest-report.txt
│       │       │                          ──> System.Windows.Forms.Clipboard.SetText
│       │       │                          (no new process)
│       │       │
│       │       ├── [Fix It Now] ───────> spawns ONE child:
│       │       │       cmd.exe /c "<python>" "<script>" fix
│       │       │       (UseShellExecute=false, CreateNoWindow=true,
│       │       │        stdout+stderr → %TEMP%\bugmon-fix.log,
│       │       │        WaitForExit(180000))
│       │       │       │
│       │       │       └── [the fix run — see remediation process tree below]
│       │       │
│       │       └── [X / Close] ─────────> form.Close(), no child
│       │
│       ├── [only when state changed]
│       │   └── NO PROCESS. The report is composed in-process and written to disk.
│       │       WHY: an incident report is not worth a process spawn, and a
│       │       spawned helper would be one more thing that can fail at 03:00.
│       │
│       └── [only when a defect is present, for process attribution]
│           └── powershell.exe -NoProfile -NonInteractive -Command
│                 Get-CimInstance Win32_Process | Select ProcessId, ... | ConvertTo-Json
│             timeout 45 s, output captured, non-zero exit tolerated
│             WHY PowerShell and not psutil: psutil lives in the Hermes venv, which
│             is exactly what must not be a dependency. Get-CimInstance gives
│             CreationDate + CommandLine, which is what temporal attribution needs.
│
│
├── OPERATOR-INITIATED COMMANDS (hermes_bug-monitor_sys.bat <action>)
│   │
│   ├── The .bat itself resolves an interpreter in this order:
│   │     1. tools\python-3.14.7+…\python.exe   (stable, outside the venv)
│   │     2. hermes-agent\venv\Scripts\python.exe (fallback)
│   │     3. py -3
│   │     4. python on PATH
│   │   and prints it with `path` for auditing.
│   │
│   ├── daemon | start | stop | status | scan-once | fix | scrub-archives
│   │   selftest | reset-log | dashboard-data
│   │       └── one short-lived python.exe, exits on completion.
│   │           `start` additionally spawns a DETACHED long-running python.exe.
│   │
│   ├── install-startup | uninstall-startup
│   │       └── schtasks, or Register-ScheduledTask / Remove-ScheduledTask
│   │
│   └── dashboard
│           └── `start "" dashboard.html` — opens the default browser on file://
│               (no server process; the page loads dashboard_data.js as a script)
│
│
└── REMEDIATION PROCESS TREE  (hermes_bug-monitor.py fix)
    │
    ├── probe()                                  in-process
    ├── os.replace(ghost → reports\quarantine\)  in-process, a RENAME
    │   ├── succeeds almost always
    │   └── OSError (cross-device) → shutil.move fallback
    │       └── both fail → record in .errors and CONTINUE to the next tree
    │           WHY continue: one unmovable tree must not prevent the source
    │           guards from being re-applied, which is the part that stops recurrence.
    ├── reapply_guards()                         in-process file rewrite
    ├── scrub_live_config()                      in-process, writes *.bugmon-bak
    ├── append_log(FIXED)                        in-process
    └── do_scan(alert=False, force_log=True)     in-process, no popup
```

## Process budget

| Situation | Concurrent processes |
|---|---|
| Steady state, no change | **1** (the daemon) |
| Every 300 s, no change | 1 |
| State changed, defect present | 1 daemon + 1 popup (≤ once per 600 s) + 1 short-lived CIM query |
| Operator clicks "Fix It Now" | 1 daemon + 1 popup + 1 `cmd.exe fix` + its own children |

The monitor never scales with the size of the tree it watches: probe cost is bounded by
`_ghost_candidates()` (50 `is_dir()` calls), and the config scan is bounded by
`WATCHLIST_CAP` (500 files). A 107,334-file ghost is fingerprinted in one pass, and the
absent case — the common one — is ~27 ms.

## Privilege

Everything runs at **Limited**, in the invoking user's session. The monitor never needs
Administrator: `os.replace`, `mkdir`, file rewrites and `Get-CimInstance Win32_Process` are
all available unelevated. Popup windows appear without the UAC prompt that a
`Start-Process -Verb RunAs` monitor would throw at the operator on every boot.
