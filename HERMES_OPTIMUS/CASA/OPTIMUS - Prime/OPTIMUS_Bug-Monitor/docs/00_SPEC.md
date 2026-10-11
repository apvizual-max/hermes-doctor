# hermes_bug-monitor_sys — Technical Specification

**Component:** OPTIMUS Prime → Hermes Bug-Monitor
**Version:** 1.0.0
**Location:** `C:\Users\apviz\AppData\Local\hermes\HERMES_OPTIMUS\CASA\OPTIMUS - Prime\OPTIMUS_Bug-Monitor`
**Status:** Operational
**Classification:** System backbone guardrail — deliberately *outside* the Hermes plugin/MCP surface

---

## 1. Purpose

The **ghost path** is a class of defect, not a single bug. A POSIX/MSYS-spelled absolute
path — `/c/Users/apviz/AppData/Local` — is consumed by native Windows code. On Windows
that string is *rooted* (`Path.is_absolute()` returns `True`) but carries **no drive**.
Every filesystem operation beneath it therefore resolves against **whatever drive happens
to be current**, which in practice is `C:`. The observable signature is a duplicated-drive
tree:

```
C:\c\Users\apviz\AppData\Local\hermes\...
```

Measured impact of one occurrence: **4.7 GB, 107,334 files**, a second competing Hermes
install claiming the *same* install key as the real one, and a `state.db` corrupted by two
processes writing the same SQLite file.

This system exists to answer three questions, continuously and without the operator
having to be looking:

1. **Is the ghost present right now?**
2. **If it appeared — exactly when, and which process did it?**
3. **What exactly must be changed to make it stop recurring?**

---

## 2. Design constraints (and why each one exists)

| Constraint | Rationale |
|---|---|
| **Stdlib-only Python, single file** | `hermes update` rebuilds the venv and reverts the git checkout. A monitor that imports `hermes_constants` or `psutil` from that venv dies with the thing it monitors. One stdlib-only file cannot be invalidated by `uv sync`, `pm install`, or a half-completed update. |
| **Store interpreter preferred over venv** | The `tools\python-3.14.7+…` interpreter is versioned beside Hermes but *not* inside the venv that `uv sync` rewrites. `hermes_bug-monitor_sys.bat` tries it first for exactly this reason. |
| **Log on state change only** | A 5-minute cadence appending every scan yields 288 records/day of pure noise. The log is a **ring buffer of transitions**, not of polls. |
| **Ring buffer, 50 entries** | Hard cap. Entry 51 evicts entry 1. Bounded disk, bounded parsing cost in the dashboard, and a log that can never grow into a performance problem. |
| **Quarantine, not delete** | Deletion is irreversible. The tree is `os.replace`d into `reports\quarantine\` — instant (a rename, not a 4.7 GB copy) and fully recoverable. Purging is a separate explicit act. |
| **Never raise on the scan path** | A monitor that throws on a transient permissions error trains the operator to dismiss it. Every scan exception is captured *into state*, never propagated. |
| **Single-instance lock** | The startup task and a manual run must not both scan. `O_EXCL` lock file with a liveness check on the recorded PID. |
| **Re-appliable source fix** | The guard in `hermes_constants.py` is a source edit, and `hermes update` reverts source edits. So the fix must be *re-appliable*, not one-shot. `--fix` is that mechanism. |

---

## 3. File inventory

```
OPTIMUS_Bug-Monitor/
├── hermes_bug-monitor.py        core scanner / daemon / fixer        (43 KB, stdlib only)
├── hermes_bug-monitor_sys.bat   launcher + interpreter resolution   (3.9 KB)
├── popup_alert.ps1              WinForms critical alert             (7.3 KB)
├── dashboard.html               control + audit dashboard
├── config.json                  tunables
├── docs/                        spec, logic/process/workflow trees, investigation, runbook
├── logs/
│   ├── bug-monitor.jsonl        ring buffer, ≤50 entries  (machine-readable, canonical)
│   └── bug-monitor.log          flattened one-line-per-entry view    (human scannable)
├── state/
│   ├── monitor_state.json       last state key, since, heartbeat, scan count
│   ├── monitor.pid              live daemon PID
│   ├── monitor.lock             single-instance guard
│   ├── popup_payload.json       payload handed to the popup
│   └── last_popup.txt           popup rate limiter (600 s)
└── reports/
    ├── latest-report.txt        full incident report (what the popup shows)
    └── quarantine/              ghost trees moved aside by --fix
```

---

## 4. Detection algorithm

### 4.1 Filesystem probe — `probe()`

For every drive letter `C`–`Y`, test `X:\c` and `X:\C`:

```python
if path.is_symlink():      skip   # a junction is a legitimate directory
if not path.is_dir():      skip
→ recording it as a detection
```

Symlinks are skipped deliberately: following one would let an innocent directory raise a
false alarm and destroy the operator's trust in the monitor faster than any false negative.

Note on the literal `C:\C\...` spelling: Windows forbids a colon in a path component, so
that form **cannot exist** and the canonical artefact is always the lowercase `C:\c\...`.
Both are probed so the monitor is robust to a future NTFS namespace change or a junction
named `C`.

### 4.2 Cause probe — `scan_config_for_msys()`

The filesystem check finds the *symptom*. The cause is a drive-less absolute path still
present in **live configuration**, which regenerates the tree on the next write. Every
cycle therefore also walks `HERMES_HOME` for active config files (`.env`, `.yaml`, `.yml`,
`.json`, `.ps1`, `.bat`, `.cmd`, `.toml`, `.ini`, `.cfg`, `.txt`, `.md`) and flags any line
beginning with an MSYS absolute path:

```
^/(?:(?:cygdrive|mnt)/)?([a-zA-Z])(/.*)$
```

Commented-out lines are skipped. Archives, snapshots, logs and build trees are excluded —
those are history, handled by the cleanup pass, not live state.

**Why this matters:** if the tree is quarantined but the poisoned config value survives,
it returns within minutes. Finding the cause and the symptom in the same report is the
difference between reacting twice and reacting once.

### 4.3 Tree fingerprint — `_tree_fingerprint()`

Identity of a detected tree: `(file count, dir count, total bytes, newest mtime, sha256 of
sorted relative paths, truncated flag)`.

Deliberately **not** content-hashed. The incident tree held 107,334 files; hashing on a
5-minute cadence would itself become a load. Counts, bytes and a path digest change whenever
a writer adds, removes, or relocates anything — exactly the transitions worth recording.

---

## 5. State-change detection

```
state_key = "path|files|dirs|bytes|path_digest[; …]" + "cfg=<count>" + "clean=<bool>"
```

Compare against the persisted `monitor_state.json`:

* **Unchanged** → touch the heartbeat only. **Nothing is written to the log.**
* **Changed** → append a log entry, write `latest-report.txt`, and (if a defect) fire the popup.

Including the per-tree fingerprint means *"still broken but still growing"* is recorded as
a **distinct** state from *"still broken, unchanged"*. The log therefore shows continued
mutation rather than pure repetition — which is the difference between an operator
understanding the attacker and an operator muting the alert.

A scan that errors sets `scan_error` in state rather than raising, so a failing probe is
itself a state change and gets logged.

---

## 6. Incident report

`build_report()` renders the exact text the popup displays and the *Copy Log Report* button
places on the clipboard. Sections:

1. Header — timestamp, monitor version, machine, target
2. **Per ghost tree** — file/dir counts, bytes, root `CTIME` (*the creation moment*),
   newest artifact list (*names the writing module*), suspect processes, matching log lines
3. **Root cause** — live config values still carrying a drive-less path, with `file:line`
4. **Source-level cause** — the `has_drive()` explanation, including the `hermes update`
   reversion caveat

### 6.1 Process attribution — `suspect_processes()`

A filesystem watcher is the only *certain* answer to "who created this", and that requires a
kernel minifilter or Sysmon. Without one, the strongest available evidence is **temporal**:
enumerate every process whose creation time falls within ±300 s of the ghost tree's oldest
creation stamp, score it, and report the top 25.

| Signal | Score |
|---|---|
| command line or name contains `hermes` | +100 |
| contains `.telegram-agent` | +60 |
| contains `telegram` | +30 |
| contains `mcp` | +20 |
| is `python` / `node` | +10 |
| started within **5 s** of the tree | +200 |
| started within the 300 s window | +60 |

The report labels this **"temporal attribution — circumstantial, not proof"**. Claiming
certainty here would be the cardinal sin: it would send the operator after the wrong
process with false confidence, and the real writer would keep creating trees.

Obtained via `Get-CimInstance Win32_Process` (PID, PPID, name, `CreationDate`, command line),
which is stdlib-reachable through `subprocess` + PowerShell and needs no third-party module.

---

## 7. Alerting

`popup_alert.ps1` — WinForms, launched **detached** (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`)
so a blocking modal can never stall the scan loop.

WinForms rather than `MessageBoxW` because `MessageBox` cannot label its buttons, and the
operator needs the actions named rather than `Yes`/`No`.

| Button | Action |
|---|---|
| **Copy Log Report** | Full report → clipboard |
| **Fix It Now** | Runs `hermes_bug-monitor.py fix`, live progress in the status line |
| *X / Close* | Dismiss without acting |

**Rate limit:** one popup per **600 s** per run, enforced by `state\last_popup.txt`. A
persistent defect must not spawn a modal every 5 minutes — that is how alerts get disabled.

---

## 8. Remediation — `apply_fixes()`

1. **Quarantine** each detected tree via `os.replace` into `reports\quarantine\<name>-<epoch>`,
   falling back to `shutil.move` cross-device. Instant, reversible.
2. **Re-apply source guards** — `reapply_guards()` checks for the `def has_drive(` marker in
   `hermes_constants.py` and, if absent, surgically rewrites the single upstream
   `_get_platform_default_hermes_home()` function. Marker-based rather than a stored diff, so
   it stays correct across minor upstream edits. If upstream's shape changed beyond
   recognition it refuses and says so instead of corrupting the file.
3. **Scrub live config** — `scrub_live_config()` rewrites `/cygdrive/c/`, `/mnt/c/` and `/c/`
   to `C:/` in active config only, writing a `.bugmon-bak` beside each touched file. The
   substitution is exact and reversible; nothing else in the file is touched, so secrets and
   unrelated settings survive byte-for-byte.
4. **Log a `FIXED` entry** and re-scan.

---

## 9. Known limits (stated, not hidden)

* **Attribution is circumstantial.** No minifilter is loaded; see §6.1.
* **Source guards do not survive `hermes update` on their own.** That is precisely why
  `reapply_guards()` exists and why `install-startup` is part of the deployment — the
  startup task re-checks the guards on every logon.
* **Config scanning is textual.** A drive-less path assembled at runtime in memory will not
  be found by the value scan; the startup-guard layer and `has_drive()` are what cover that.
* **Windows-only.** Startup registration and the popup require Windows. `probe()` and
  `selftest()` degrade cleanly elsewhere.
