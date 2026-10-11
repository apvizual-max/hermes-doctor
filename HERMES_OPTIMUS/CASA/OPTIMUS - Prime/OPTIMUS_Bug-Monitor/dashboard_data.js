window.BUGMON_DATA = {
 "generated": "2026-10-11T17:59:26+03:00",
 "version": "1.0.0",
 "root": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor",
 "hermes_home": "C:\\Users\\apviz\\AppData\\Local\\hermes",
 "interval_seconds": 300,
 "log_capacity": 50,
 "log_entries": 5,
 "log_file": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\logs\\bug-monitor.jsonl",
 "log_dir": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\logs",
 "report_dir": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\reports",
 "latest_report_exists": true,
 "latest_report": "==============================================================================\nHERMES BUG-MONITOR -- GHOST PATH INCIDENT REPORT\n==============================================================================\nReport generated : 2026-10-11T01:56:00+03:00\nMonitor version  : 1.0.0\nMachine          : OPTIMUS_PRIME  (Windows 11)\nTarget           : C:\\Users\\apviz\\AppData\\Local\\hermes\n\nSEVERITY         : CRITICAL -- duplicated-drive root present on disk\nTrees detected   : 0\n\nROOT CAUSE -- LIVE CONFIG STILL CARRYING A DRIVE-LESS PATH\n(MSYS ``/c/...`` spellings in active configuration regenerate this tree):\n    <none -- configuration is clean; the writer set its env at runtime>\n\nSOURCE-LEVEL CAUSE\n    hermes_constants._get_platform_default_hermes_home() read LOCALAPPDATA with no\n    drive validation. A POSIX/MSYS value (/c/Users/<name>/AppData/Local) is\n    root-relative on Windows, so Path(...).mkdir() resolved to C:\\c\\Users\\...\n    on the current drive. Same unvalidated pattern in hermes_state_guard.py and\n    hermes_cli/dashboard_procs.py. All three are now guarded by\n    hermes_constants.has_drive(). 'hermes update' reverts these edits -- the\n    monitor's --fix re-applies them; that is why the tree can come back.\n\n==============================================================================\nEND OF REPORT",
 "running": true,
 "pid": 18100,
 "state": {
  "state_key": "cfg=0;clean=True",
  "since": "2026-10-11T01:56:00+03:00",
  "cleared_at": "2026-10-11T01:58:46+03:00",
  "last_scan": "2026-10-11T17:59:26+03:00",
  "clean": true,
  "last_event": "CLEARED",
  "last_summary": "Ghost path absent. Resolved after 2026-10-11T01:56:00+03:00. config hits=0",
  "version": "1.0.0",
  "scanned_count": 98
 },
 "entries": [
  {
   "timestamp": "2026-10-11T01:58:46+03:00",
   "event": "CLEARED",
   "summary": "Ghost path absent. Resolved after 2026-10-11T01:56:00+03:00. config hits=0",
   "has_report": false
  },
  {
   "timestamp": "2026-10-11T01:56:00+03:00",
   "event": "DETECTED",
   "summary": "Ghost path present:  (0 files) | config hits=0 | MODEL DRIFT: config.yaml:default,provider",
   "has_report": true
  },
  {
   "timestamp": "2026-10-04T02:07:03+03:00",
   "event": "CLEARED",
   "summary": "Ghost path absent. Resolved after 2026-10-03T01:10:34+03:00. config hits=0",
   "has_report": false
  },
  {
   "timestamp": "2026-10-03T01:10:34+03:00",
   "event": "DETECTED",
   "summary": "Ghost path present: C:\\c, C:\\C (12 files) | config hits=0",
   "has_report": true
  },
  {
   "timestamp": "2026-10-02T05:03:16+03:00",
   "event": "CLEARED",
   "summary": "Ghost path absent. Resolved after n/a. config hits=0",
   "has_report": false
  }
 ],
 "guards": [
  {
   "file": "hermes_constants.py",
   "guard": "drive validator + LocalAppData shell-API resolver",
   "present": true,
   "exists": true
  },
  {
   "file": "hermes_state_guard.py",
   "guard": "state-db guard drive validation",
   "present": false,
   "exists": true
  },
  {
   "file": "hermes_cli/dashboard_procs.py",
   "guard": "dashboard process-scan drive validation",
   "present": false,
   "exists": true
  },
  {
   "file": "hermes_cli/_launchers.py",
   "guard": "launcher drive-less guard",
   "present": false,
   "exists": true
  },
  {
   "file": "hermes_cli/_launchers.py",
   "guard": "locked-launcher deferral guard",
   "present": false,
   "exists": true
  },
  {
   "file": "pm/workspace.py",
   "guard": "dependency stamp excludes plugin runtime state (state.json churn)",
   "present": true,
   "exists": true
  }
 ],
 "guards_all_present": false,
 "paths": {
  "log_jsonl": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\logs\\bug-monitor.jsonl",
  "log_text": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\logs\\bug-monitor.log",
  "state": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\state\\monitor_state.json",
  "latest_report": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\reports\\latest-report.txt",
  "quarantine": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\reports\\quarantine",
  "spec": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\docs\\00_SPEC.md",
  "bat": "C:\\Users\\apviz\\AppData\\Local\\hermes\\HERMES_OPTIMUS\\CASA\\OPTIMUS - Prime\\OPTIMUS_Bug-Monitor\\hermes_bug-monitor_sys.bat"
 }
};
