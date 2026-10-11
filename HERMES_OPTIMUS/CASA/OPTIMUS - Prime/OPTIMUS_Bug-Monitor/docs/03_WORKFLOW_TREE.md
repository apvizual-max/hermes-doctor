# Workflow Tree — hermes_bug-monitor_sys

Operational workflows. Each is written as the person who has to run it at 03:00 would
write it, with the branch that matters called out.

---

## W1 — System comes up (automatic, no human)

```
LOGON
 └─> HermesBugMonitor task fires (30 s settle)
      └─> daemon acquires the single-instance lock
           ├─ lock already held by a LIVE pid ──> exit 0 silently
           │     WHY: the operator may have started the daemon by hand earlier in the
           │     session. Two scanners would double-log every transition and could
           │     race on the ring buffer. Exiting quietly is correct; it is not an error.
           ├─ lock held by a DEAD pid ──> reap the lock, continue
           └─ lock free ──> take it, write monitor.pid
                └─> FIRST SCAN IMMEDIATELY (not after 300 s)
                     WHY immediate: if the system is already broken at boot, waiting
                     five minutes to find out is five wasted minutes. Later cycles wait.
                └─> then loop: scan → sleep 300 s → scan → …
```

---

## W2 — Steady state, no change (the 99.9% case)

```
scan
 └─ probe(): C:\c absent, D:\c absent, … , no drive-less config value
      └─ state_key identical to stored
           └─> touch heartbeat only (last_scan, scanned_count++)
                └─> NO log entry. NO report. NO popup.
```

**Operator experience:** silence. The log stays empty. `dashboard_data.js` is refreshed so
the dashboard's "last scan" clock keeps moving. This is the designed outcome, not a
silent failure — an empty log with a live daemon means *the system is stable*.

---

## W3 — The ghost path reappears (the incident)

```
scan at T
 └─ probe(): C:\c EXISTS
      └─ fingerprint the tree (files, dirs, bytes, newest mtime, path digest)
           └─> state_key CHANGED
                └─> compose build_report()
                │     ├─ root CTIME  = the creation moment
                │     ├─ newest 20 artifacts  = which module wrote
                │     ├─ Get-CimInstance → suspect processes (±300 s, scored)
                │     ├─ grep HERMES_HOME\logs for "C:\c"
                │     ├─ live config values still drive-less  (file:line)
                │     └─ the source-level explanation + the update-reversion caveat
                ├─> append DETECTED to the ring buffer (51st evicts the 1st)
                ├─> write reports\latest-report.txt
                ├─> refresh dashboard_data.js
                └─> popup, unless rate-limited (600 s since last popup)
                      └─> modal: [Copy Log Report]  [Fix It Now]  [X]
                           ├─ Copy  → full report to clipboard, no side effects
                           ├─ Fix   → W4
                           └─ X     → nothing changes; the daemon keeps scanning
```

**Note the second occurrence.** If the tree is still there and still growing, the
fingerprint changes again → a **new** log entry, but the popup is suppressed for 600 s.
The log shows continued *mutation* rather than repetition, which tells the operator the
writer is still active rather than a stale directory.

---

## W4 — Fix it now

```
operator clicks [Fix It Now]  (or runs: bat fix)
 └─ apply_fixes()
      ├─ probe() again — re-confirm, never trust the earlier scan
      ├─ for each ghost tree:
      │     os.replace(tree → reports\quarantine\<name>-<epoch>)
      │     ├─ ok ──> instant, same volume, no copy of 4.7 GB
      │     └─ OSError ──> shutil.move
      │           └─ both fail ──> record, CONTINUE (do not abort the other trees)
      ├─ reapply_guards()
      │     ├─ "def has_drive(" present in hermes_constants.py ──> "already guarded"
      │     ├─ upstream function matches exactly ──> surgical replace, write
      │     └─ upstream shape unrecognised ──> REFUSE, report "manual review required"
      │           WHY refuse rather than force: a blind patch against text that has
      │           changed upstream writes a wrong file, and a wrong hermes_constants.py
      │           is far worse than an unpatched one. The monitor reports; a human
      │           decides.
      ├─ scrub_live_config()   (watchlist only; writes *.bugmon-bak beside each file)
      └─ append FIXED, then do_scan(alert=False, force_log=True)
            └─> re-scan silently and record the resulting state
```

**Quarantine, not delete.** The tree is renamed, not erased. `rm --` (mavis-trash) is the
operator's move when they are satisfied; the monitor never destroys data on its own.

---

## W5 — `hermes update` reverts the source fix (the recurring trap)

```
operator runs: hermes update
 └─ git checkout / reset inside hermes-agent\
      └─> hermes_constants.py, hermes_state_guard.py, dashboard_procs.py, _launchers.py
           revert to upstream
                └─> the defect is BACK in the source, silently
                     ├─ next logon  → HermesBugMonitor task → status shows guards MISSING
                     ├─ operator runs `fix` → guards re-applied
                     └─ popup does NOT fire for this
                           WHY: a reverted guard is a *known, understood* condition, not
                           an on-disk incident. The dashboard surfaces it as a red pill;
                           a modal for something with a known one-command remedy would be
                           noise, and noise is how alerts get disabled.
```

**This is the single most important durability property of the design:** the fix is not a
patch that gets lost, it is a *procedure* that gets re-run.

---

## W6 — Operator audit (no changes made)

```
bat status
 └─> JSON: version, root, last state, log count, running pid,
            per-file guard presence, guards_all_present
dashboard
 └─> opens dashboard.html from file://
      ├─ Status tab   → live cards, guard table, banner
      ├─ Logs tab     → ring buffer (newest first) + latest report verbatim
      ├─ Control tab  → the action buttons and what each one does
      ├─ Bug Documentation tab → the full ghost-path investigation
      └─ System Layout tab → every artefact path
           WHY a JS data file and not JSON: browsers refuse fetch() of a sibling file
           over file://, so a zero-server dashboard can only get live data through a
           <script src>. The monitor writes dashboard_data.js every cycle.
```

---

## W7 — Extending to a new defect class

```
1. add  probe_<thing>()              -> {found, identity, ...}
2. fold its identity into _state_key()   so changes are detected
3. add a section to build_report()       so the popup explains it
4. add a table row to SOURCE_GUARDS if it has a source-level fix
5. (optional) add a watcher to _config_watchlist() if it has a config vector
```

Unchanged: the 300 s cadence, the 50-entry ring buffer, the popup, the rate limiter, the
quarantine/`fix` path, the dashboard, this documentation. The system was built to absorb
more defect classes without being redesigned — the operator's stated intent to "expand
this bug-monitor to include many other errors that Hermes keeps repeating".

---

## W8 — Things that are deliberately NOT workflows

| Tempting automation | Why not |
|---|---|
| Delete the ghost tree instead of quarantining | Irreversible. The operator asked for forensics to survive where possible. |
| Popup on a reverted source guard | Known condition with a one-command remedy. Modal fatigue kills alerts. |
| Auto-rotate plaintext secrets | Would break the live Telegram/Discord bots. Requires explicit authorisation. |
| Grant `allow_task_override` to silence the auto-titler warning | A security trust decision, not a bug. Impact is cosmetic (session titles). |
| Re-apply guards from the popup automatically | The popup already offers it as an explicit button. Remediation should be a decision, not a reflex. |
| Rewrite documentation that *mentions* the bug | Destroys the knowledge. See SPEC §"cleanup policy" and dashboard tab §6. |
