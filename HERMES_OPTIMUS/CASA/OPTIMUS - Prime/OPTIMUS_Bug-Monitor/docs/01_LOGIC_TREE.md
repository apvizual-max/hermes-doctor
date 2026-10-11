# Logic Tree — hermes_bug-monitor_sys

Decision structure of one scan cycle. Every branch is annotated with the *reason* it exists,
so the tree doubles as the design rationale.

```
scan-once / monitor cycle
│
├─ _ensure_dirs()                                  logs/ state/ reports/ docs/ must exist
│
├─ probe()                                          ◄── the scan proper
│  │
│  ├─ for drive_letter in C..Y:
│  │    └─ for name in ("c", "C"):
│  │         ├─ _is_drive_letter_dir(cand)?
│  │         │    ├─ is_symlink()  ──TRUE──► SKIP
│  │         │    │                        WHY: a junction is a legitimate directory;
│  │         │    │                             following it would raise false alarms
│  │         │    │                             and destroy operator trust faster than any
│  │         │    │                             false negative could.
│  │         │    ├─ is_dir()      ──FALSE──► SKIP   (normal, fast path, microseconds)
│  │         │    └─ is_dir()      ──TRUE──► RECORD ─► _tree_fingerprint()
│  │         │                                    ├─ files, dirs, total bytes
│  │         │                                    ├─ newest mtime + relative path
│  │         │                                    ├─ oldest ctime  ◄── CREATION MOMENT
│  │         │                                    ├─ sha256 of relative paths (16 hex)
│  │         │                                    └─ truncated flag (>200k entries)
│  │         └─ exception (permissions/vanished) ──► RECORD with .error, DO NOT RAISE
│  │
│  └─ scan_config_for_msys()                      ◄── the CAUSE, not just the symptom
│       └─ _config_watchlist()                    bounded enumeration, depth ≤2
│            ├─ root level files
│            ├─ one level down
│            ├─ two levels down (capped)           mcp-servers/custom/<name>/.env
│            └─ EXCLUDES: venv, node_modules, .git, __pycache__, site-packages,
│                          pycache, state-snapshots, logs, HERMES_OPTIMUS
│       └─ for each line:
│            ├─ blank or starts with '#' ──► SKIP  (commented values cannot write anything)
│            └─ MSYS_ABS_RE match?
│                 ├─ yes ──► RECORD {file, line, text}
│                 └─ no  ──► next
│
├─ _state_key(state)                               identity for change detection
│    "path|files|dirs|bytes|path_digest[;…] cfg=<n> clean=<bool>"
│
├─ compare against state/monitor_state.json
│  │
│  ├─ SAME ────────────────────────────────────────────────────────────────┐
│  │    ├─ touch heartbeat: last_scan, clean, scanned_count++             │
│  │    └─ LOG NOT WRITTEN                                                 │
│  │                                                                       │
│  └─ DIFFERENT ──► log entry ──► branch on clean?                         │
│       │                                                                   │
│       ├─ clean=True   ──► event=CLEARED                                  │
│       │                  summary "absent; resolved after <since>"         │
│       │                  NO popup, NO latest-report rewrite               │
│       │                                                                       │
│       └─ clean=False  ──► event=DETECTED                                  │
│            ├─ build_report()                                               │
│            │    ├─ header: timestamp, version, machine, target             │
│            │    ├─ per ghost tree:                                         │
│            │    │    ├─ counts, bytes, root CTIME                         │
│            │    │    ├─ newest_artifacts()   ◄── names the writing module  │
│            │    │    ├─ suspect_processes() ◄── TEMPORAL, circumstantial   │
│            │    │    │     scoring: hermes +100 · .telegram-agent +60      │
│            │    │    │                telegram +30 · mcp +20                │
│            │    │    │                py/node +10 · ±5s +200 · ±300s +60   │
│            │    │    └─ hermes_log_excerpt("C:\c")                         │
│            │    ├─ ROOT CAUSE: poisoned_config_values with file:line        │
│            │    └─ SOURCE-LEVEL CAUSE: has_drive() + the update caveat     │
│            ├─ write reports/latest-report.txt                              │
│            └─ _popup()                                                     │
│                 ├─ platform != win32 ──► SKIP                               │
│                 ├─ popup_alert.ps1 missing ──► SKIP                         │
│                 ├─ write state/popup_payload.json                          │
│                 └─ rate limit: now - last_popup < 600s ──► SKIP            │
│                      WHY: a persistent defect must not raise a modal every │
│                           5 minutes; that is how alerts get disabled.        │
│                      else Popen(DETACHED_PROCESS)                           │
│
└─ persist state/monitor_state.json (atomic: write .tmp → os.replace)
     WHY: a crash mid-write must not leave an unreadable state file, because that
          would make every subsequent scan look like a state change and flood the log.


apply_fixes()  (manual, or the popup's "Fix It Now")
│
├─ probe() again ──► for each ghost tree:
│    ├─ os.replace(src, quarantine/<name>-<epoch>)      instant, reversible
│    └─ OSError ──► shutil.move fallback
│         └─ both fail ──► append to .errors, CONTINUE (never abort the rest)
│
├─ reapply_guards()
│    ├─ read hermes_constants.py
│    ├─ "def has_drive(" present ──► "already guarded", no write
│    ├─ upstream shape matches     ──► surgical replace, append marker
│    └─ upstream shape changed     ──► REFUSE + "manual review required"
│         WHY: a blind patch against unfamiliar upstream text corrupts the file.
│              Refusing loudly is strictly better than writing something wrong.
│
└─ scrub_live_config()
     ├─ for each watchlist file containing "/c/Users" or "/cygdrive/c/" or "/mnt/c/":
     │    ├─ write <file>.bugmon-bak (once, never overwritten)
     │    ├─ replace "/cygdrive/c/" → "C:/"  then  "/mnt/c/" → "C:/"  then  "/c/" → "C:/"
     │    └─ collapse any "\\C:/" artefact of the substitution
     └─ WHY exact-substitution: secrets and unrelated settings survive byte-for-byte.
```
