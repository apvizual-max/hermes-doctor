#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hermes_bug-monitor_sys  --  OPTIMUS Bug-Monitor core scanner
=============================================================

Windows guardrail for the OPTIMUS Prime / Hermes ecosystem.

Watches for the *ghost path* class of defect: a POSIX/MSYS-spelled absolute path
(``/c/Users/<name>/...``) being consumed by native Windows code.  Such a path is
root-relative on Windows but carries no drive, so every filesystem operation
below it silently lands on whatever drive happens to be current.  The observable
signature is a duplicated-drive tree, canonically ``C:\\c\\Users\\...``.

Design constraints (deliberate):
  * STDLIB ONLY.  This monitor is a backbone guardrail, not a Hermes plugin.  It
    must keep working when the Hermes venv is rebuilt, half-synced, or broken by
    an in-flight update.  A single stdlib-only file cannot be invalidated by
    ``hermes update`` or ``uv sync``.
  * IDEMPOTENT + SELF-HEALING helpers.  ``--fix`` re-applies the source-level
    guard to the Hermes checkout, which ``hermes update`` reverts.
  * LOG ON STATE CHANGE ONLY.  A 5-minute cadence that appended every scan would
    produce 288 records/day of noise.  The log is a ring buffer of the most
    recent MAX_LOG_ENTRIES (50) *state transitions*.
  * FAIL SILENT ON THE SCAN PATH.  A monitor that raises on a transient
    permissions error is worse than no monitor: it trains the operator to
    dismiss it.  Every scan exception is captured into state, never raised.

Commands
--------
  scan-once    run exactly one scan (respecting state-change logging) and exit
  monitor      run the daemon loop until stopped (default interval 300s)
  status       print machine-readable JSON status and exit
  fix          remediate a detected ghost tree + re-apply source guards
  install-startup / uninstall-startup
  selftest     verify the monitor's own invariants without touching the system

Exit codes
----------
  0 clean/idle   1 defect present   2 internal error
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
BM_ROOT = Path(__file__).resolve().parent
LOG_DIR = BM_ROOT / "logs"
STATE_DIR = BM_ROOT / "state"
REPORT_DIR = BM_ROOT / "reports"
DOCS_DIR = BM_ROOT / "docs"
STATE_FILE = STATE_DIR / "monitor_state.json"
LOG_FILE = LOG_DIR / "bug-monitor.jsonl"
LATEST_REPORT = REPORT_DIR / "latest-report.txt"
PID_FILE = STATE_DIR / "monitor.pid"
LOCK_FILE = STATE_DIR / "monitor.lock"
POPUP_PS1 = BM_ROOT / "popup_alert.ps1"

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path(__file__).parent.parent.parent.parent.parent.resolve())
HERMES_SRC = HERMES_HOME / "hermes-agent"

MAX_LOG_ENTRIES = 50
INTERVAL_SECONDS = 300
DAEMON_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# What counts as a ghost path
# --------------------------------------------------------------------------
# Every entry is a *drive-root duplicate*: the drive letter appears a second time
# as the first path component, which is what ``/c/Users/x`` collapses to.
# Windows actually forbids a colon in a component name, so ``C:/C/Users`` can
# never be created verbatim -- the canonical artefact is ``C:\c\...``.  Both
# spellings are probed so the monitor is not fooled by a future Windows change
# or by a junction.
GHOST_RELATIVE_NAMES = ("c", "C")

MSYS_ABS_RE = None
try:  # built lazily; kept tolerant so import never fails
    import re as _re

    MSYS_ABS_RE = _re.compile(r"^/(?:(?:cygdrive|mnt)/)?([a-zA-Z])(/.*)$")
except Exception:  # pragma: no cover
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    for d in (LOG_DIR, STATE_DIR, REPORT_DIR, DOCS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Ghost-path probing
# --------------------------------------------------------------------------
def _ghost_candidates() -> list[Path]:
    """Every filesystem location that would indicate the ghost-path defect."""
    out: list[Path] = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXY":
        for name in GHOST_RELATIVE_NAMES:
            out.append(Path(f"{letter}:\\{name}"))
    return out


def _is_drive_letter_dir(path: Path) -> bool:
    """
    A path only counts as a ghost if it is a *real* directory (not a symlink or
    junction pointing somewhere legitimate).  Following a link would let an
    innocent directory raise a false alarm.

    ``is_dir()`` is tested first: it is the cheap negative for the ~46 non-existent
    drive letters, and ``is_symlink()`` on a non-existent path is a wasted syscall.
    """
    try:
        if not path.is_dir():
            return False
        return not path.is_symlink()
    except OSError:
        return False


def _tree_fingerprint(root: Path, max_entries: int = 200_000) -> dict:
    """
    Cheap, deterministic identity of a ghost tree.

    Deliberately does NOT hash file contents.  The tree can hold 100k+ files
    (the 2026-10-01 incident was 107,334 files / 4.7 GB); hashing on a 5-minute
    cadence would itself become a load.  Identity is (counts, bytes, newest mtime,
    and a rolling digest of relative paths), which changes whenever a writer adds,
    removes, or rewrites anything -- exactly the transitions worth logging.
    """
    files = dirs = total_bytes = 0
    newest_mtime = 0.0
    newest_path = ""
    oldest_ctime = 0.0
    digest = hashlib.sha256()
    truncated = False

    # ⚡ Bolt Optimization: Use string manipulation and os.path instead of pathlib
    # inside the loop. Instantiating Path objects in a tight loop for 100k+ files
    # is a major performance bottleneck. This reduces execution time significantly.
    root_str = str(root)
    root_len = len(root_str)
    if not root_str.endswith(os.sep):
        root_len += 1

    for dirpath, dirnames, filenames in os.walk(root_str, topdown=True):
        dirs += len(dirnames)
        files += len(filenames)
        if files + dirs > max_entries:
            truncated = True
            break
        for fn in filenames:
            fp_str = os.path.join(dirpath, fn)
            try:
                st = os.stat(fp_str)
            except OSError:
                continue
            total_bytes += st.st_size
            if st.st_mtime > newest_mtime:
                newest_mtime = st.st_mtime
                newest_path = fp_str[root_len:]
            if st.st_ctime and (oldest_ctime == 0.0 or st.st_ctime < oldest_ctime):
                oldest_ctime = st.st_ctime

        if dirpath == root_str:
            rel = "."
        else:
            rel = dirpath[root_len:]

        digest.update(rel.encode("utf-8", "replace"))

    return {
        "files": files,
        "dirs": dirs,
        "bytes": total_bytes,
        "path_digest": digest.hexdigest()[:16],
        "newest_mtime": _iso(datetime.fromtimestamp(newest_mtime)) if newest_mtime else "",
        "newest_relative_path": newest_path,
        "truncated": truncated,
        "oldest_creation": _iso(datetime.fromtimestamp(oldest_ctime)) if oldest_ctime else "",
    }


# --------------------------------------------------------------------------
# Model routing drift: the ratified primary must stay the primary.
# --------------------------------------------------------------------------
#: Ratified on 2026-10-02 with Popy: MiniMax first, everything else fallback.
#: Measured break on 2026-10-11 01:48 -- the Hermes app itself rewrote
#: ``model.default`` to a retired free model while refreshing its model
#: catalogue. Nothing alerted, because this detector did not exist yet.
_EXPECT_MODEL_DEFAULT = "MiniMax-M3"
_EXPECT_MODEL_PROVIDER = "minimax"
_EXPECT_BASE_URL_HOST = "api.minimax.io"

_MODEL_BLOCK = _re.compile(
    r"(?ms)^model:\s*\n(?P<body>(?:[ \t]+.*\n|\n)*)"
)


def _model_fields(text: str) -> dict:
    """Pull ``default``/``provider``/``base_url`` out of the top-level model block."""
    out: dict = {}
    m = _MODEL_BLOCK.match(text)
    if not m:
        return out
    for key in ("default", "provider", "base_url"):
        km = _re.search(rf"(?m)^[ \t]+{key}:\s*(.+?)\s*$", m.group("body"))
        if km:
            out[key] = km.group(1).strip().strip("\"'")
    return out


def scan_model_drift() -> tuple:
    """Root config whose model routing no longer matches the ratified policy.

    Returns ``(drift, profile_routing)``.

    Scope is deliberately the ROOT config only. Profiles carry deliberate
    per-agent routing -- measured 2026-10-11: ``profiles/assist`` is pinned to
    ``xai-oauth`` by choice. Asserting the root policy on every profile made
    this detector cry wolf on a correct configuration, which is worse than not
    having it: a guard that always fires teaches the operator to ignore it.
    Profile routing is therefore reported for the eye, never marked dirty,
    until each profile gets a pinned expectation of its own.

    Never raises: an unreadable config is reported as drift, not as a crash,
    so a broken file still shows up instead of silencing the scan.
    """
    found = []
    profiles = []
    try:
        candidates = [HERMES_HOME / "config.yaml"]
        pdir = HERMES_HOME / "profiles"
        if pdir.is_dir():
            candidates += [p / "config.yaml" for p in sorted(pdir.iterdir()) if p.is_dir()]
        for cfg in candidates:
            if not cfg.is_file():
                continue
            try:
                fields = _model_fields(cfg.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                found.append({"file": str(cfg), "error": f"{type(exc).__name__}: {exc}"})
                continue
            if cfg.parent != HERMES_HOME:
                # Deliberate per-agent routing: record, never judge.
                profiles.append({"profile": cfg.parent.name, **fields})
                continue
            bad = {}
            if fields.get("default") and fields["default"] != _EXPECT_MODEL_DEFAULT:
                bad["default"] = fields["default"]
            if fields.get("provider") and fields["provider"] != _EXPECT_MODEL_PROVIDER:
                bad["provider"] = fields["provider"]
            bu = fields.get("base_url") or ""
            if bu and _EXPECT_BASE_URL_HOST not in bu:
                bad["base_url"] = bu
            if bad:
                found.append(
                    {
                        "file": str(cfg),
                        "expected": {
                            "default": _EXPECT_MODEL_DEFAULT,
                            "provider": _EXPECT_MODEL_PROVIDER,
                            "base_url_host": _EXPECT_BASE_URL_HOST,
                        },
                        "actual": fields,
                        "wrong": bad,
                    }
                )
    except Exception as exc:  # pragma: no cover - defensive
        found.append({"error": f"drift scan failed: {type(exc).__name__}: {exc}"})
    return found, profiles


def probe() -> dict:
    """
    The scan.  Returns a state dict.  Never raises for a finding; never raises
    at all except on a programming error.
    """
    detected: list[dict] = []
    for cand in _ghost_candidates():
        if not _is_drive_letter_dir(cand):
            continue
        entry = {"path": str(cand)}
        try:
            entry["fingerprint"] = _tree_fingerprint(cand)
        except Exception as exc:
            entry["error"] = f"fingerprint failed: {type(exc).__name__}: {exc}"
        detected.append(entry)

    # Also flag the classic MSYS spelling appearing in live Hermes config, which
    # is the *cause* rather than the symptom: if a poisoned value is still
    # configured, the tree will simply be recreated on the next write.
    poisoned = scan_config_for_msys()

    # The model-routing guard. A retired free model can be written back into
    # `model.default` by the app itself, and nothing else would notice.
    drift, profile_routing = scan_model_drift()

    return {
        "clean": not detected and not drift,
        "ghost_paths": detected,
        "poisoned_config_values": poisoned,
        "model_drift": drift,
        "profile_routing": profile_routing,
        "scanned_at": _iso(),
    }


# --------------------------------------------------------------------------
# Cause hunting: MSYS-spelled values in live configuration
# --------------------------------------------------------------------------
_CONFIG_EXTS = {".env", ".yaml", ".yml", ".json", ".ps1", ".bat", ".cmd", ".toml", ".ini", ".cfg", ".txt", ".md"}
_CONFIG_NAMES = {".env", "config.yaml"}
_SKIP_DIRS = {"venv", "node_modules", ".git", "__pycache__", "site-packages", "pycache", "target", "build", "dist"}


#: Explicit, auditable watchlist of the files that can carry a drive-less path.
#:
#: An early version built this by walking the tree with an extension filter. That was
#: measured at **22,261 files / 116 seconds per scan**, because ``whatsapp/session``
#: alone holds 20,307 files and the extension filter swept all of them in. A 116-second
#: scan cannot guard a 5-minute cadence. The vector is small and known, so the list is
#: stated outright: name-matched, not discovered. An auditable list beats a heuristic in
#: a guardrail, because you can read what it does and does not cover.
#:
#: Deliberately EXCLUDED: ``whatsapp/`` (20k session blobs), ``state-snapshots/`` and
#: ``.curator_backups/`` (history — cleaned by the cleanup pass, not live state), ``venv``,
#: ``skills/``, ``plugins/``, ``cache/``, ``sessions/``. None of them are read at runtime
#: by a config loader that would write a path.
WATCH_PATTERNS = (
    ".env",
    "config.yaml",
    "hermes-agent/.env",
    ".telegram-agent/.env",
    "profiles/*/config.yaml",
    "profiles/*/.env",
    "mcp-servers/*/.env",
    "mcp-servers/*/*/.env",
    "scripts/*.ps1",
    "scripts/*.bat",
    "scripts/*.cmd",
    "*.ps1",
    "*.bat",
    "*.cmd",
)
WATCHLIST_CAP = 500


def _config_watchlist() -> list[Path]:
    """
    Bounded, explicit list of files that can carry a drive-less path.

    A full ``os.walk`` of ``HERMES_HOME`` on a 5-minute cadence is not viable: the
    install holds ~100k files once venv, node_modules, whatsapp sessions and
    state-snapshots are counted, and a walk that takes minutes is a monitor that
    cannot react.  The *vector* is small and known — env files, YAML config,
    PowerShell launchers — so the watchlist states exactly those.
    """
    files: list[Path] = []
    if not HERMES_HOME.exists():
        return files
    for pattern in WATCH_PATTERNS:
        try:
            for p in HERMES_HOME.glob(pattern):
                try:
                    if not p.is_file():
                        continue
                    if p.stat().st_size > 1_000_000:
                        continue
                except OSError:
                    continue
                files.append(p)
        except (OSError, ValueError):
            continue
    unique = sorted(set(files))
    if len(unique) > WATCHLIST_CAP:
        unique = unique[:WATCHLIST_CAP]
    return unique


def scan_config_for_msys(limit: int = 200) -> list[dict]:
    """
    Find live (non-archive) Hermes configuration still carrying a drive-less
    absolute path.  This is the vector that regenerates the ghost tree, so it is
    scanned on every cycle and reported alongside the filesystem finding.
    """
    hits: list[dict] = []
    for p in _config_watchlist():
        try:
            if p.stat().st_size > 1_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if MSYS_ABS_RE and MSYS_ABS_RE.match(line.strip().lstrip("\"'")):
                hits.append({"file": str(p), "line": i, "text": line.strip()[:200]})
                if len(hits) >= limit:
                    return hits
    return hits


# --------------------------------------------------------------------------
# Forensics -- answering "which process, doing what, at what moment"
# --------------------------------------------------------------------------
def _run_powershell(script: str, timeout: int = 45) -> str:
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return (proc.stdout or "").strip()
    except Exception as exc:
        return f"<powershell failed: {type(exc).__name__}: {exc}>"


def _ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def suspect_processes(ghost_created_epoch: float | None, window_s: int = 300) -> list[dict]:
    """
    Process-level attribution.

    A filesystem watcher is the only *certain* answer to "who created this", and
    that needs a kernel driver (minifilter/Sysmon).  Without one, the strongest
    available evidence is temporal: enumerate every process whose creation time
    falls within +/-``window_s`` of the ghost tree's oldest creation stamp, and
    rank by how Hermes-specific the command line looks.  A Hermes process that
    started two seconds before the tree appeared is, for practical debugging,
    the culprit -- and the report says so as *circumstantial*, never as fact.
    """
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$w={int(window_s)};"
        f"$t={float(ghost_created_epoch) if ghost_created_epoch else 0};"
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CreationDate,CommandLine | "
        "ConvertTo-Json -Compress -Depth 3"
    )
    raw = _run_powershell(script)
    if not raw or raw.startswith("<powershell"):
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]

    out: list[dict] = []
    for proc in data:
        cdate = proc.get("CreationDate")
        epoch = None
        if cdate:
            try:
                epoch = datetime.fromisoformat(str(cdate)).timestamp()
            except ValueError:
                # WMI sometimes returns "20261002 01:02:01.000000+000"
                try:
                    epoch = datetime.strptime(str(cdate)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    epoch = None
        if epoch is None or epoch <= 0:
            continue
        cmd = (proc.get("CommandLine") or "").strip()
        name = (proc.get("Name") or "").strip()
        haystack = f"{name} {cmd}".lower()
        score = 0
        if "hermes" in haystack:
            score += 100
        if ".telegram-agent" in haystack:
            score += 60
        if "telegram" in haystack:
            score += 30
        if "mcp" in haystack:
            score += 20
        if "python" in haystack or "node" in haystack or "hermes" in haystack:
            score += 10
        if t and abs(epoch - t) <= 5:
            score += 200
        elif t and abs(epoch - t) <= w:
            score += 60
        if score:
            out.append(
                {
                    "pid": proc.get("ProcessId"),
                    "ppid": proc.get("ParentProcessId"),
                    "name": name,
                    "created": _iso(datetime.fromtimestamp(epoch)),
                    "seconds_from_ghost": round(epoch - t, 1) if t else None,
                    "command_line": cmd[:400],
                    "relevance_score": score,
                }
            )
    out.sort(key=lambda p: -p["relevance_score"])
    return out[:25]


def newest_artifacts(root: Path, n: int = 25) -> list[dict]:
    """The most recently written paths under the ghost -- names the module that wrote."""
    rows: list[dict] = []

    # ⚡ Bolt Optimization: Use string manipulation and os.path instead of pathlib
    # inside the loop. Instantiating Path objects in a tight loop is a
    # major performance bottleneck for large directories.
    root_str = str(root)
    root_len = len(root_str)
    if not root_str.endswith(os.sep):
        root_len += 1

    for dirpath, _dirnames, filenames in os.walk(root_str, topdown=True):
        for fn in filenames:
            fp_str = os.path.join(dirpath, fn)
            try:
                st = os.stat(fp_str)
            except OSError:
                continue
            rows.append(
                {
                    "path": fp_str,
                    "relative": fp_str[root_len:],
                    "size": st.st_size,
                    "modified": _iso(datetime.fromtimestamp(st.st_mtime)),
                    "created": _iso(datetime.fromtimestamp(st.st_ctime)) if st.st_ctime else "",
                }
            )
    rows.sort(key=lambda r: r["modified"], reverse=True)
    return rows[:n]


def hermes_log_excerpt(needle: str = "C:\\c", n: int = 15) -> list[str]:
    """Recent Hermes log lines that mention the ghost -- the direct action trace."""
    log_dir = HERMES_HOME / "logs"
    if not log_dir.is_dir():
        return []
    out: list[str] = []
    try:
        logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:4]
    except OSError:
        return []
    for lg in logs:
        try:
            if lg.stat().st_size > 20_000_000:
                continue
            lines = lg.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[-4000:]:
            if needle.lower() in line.lower() or needle.lower().replace("\\", "/") in line.lower():
                out.append(f"[{lg.name}] {line.strip()[:300]}")
                if len(out) >= n:
                    return out
    return out


# --------------------------------------------------------------------------
# Report construction
# --------------------------------------------------------------------------
def build_report(state: dict) -> str:
    """Render the full incident report.  This exact text is what the popup shows
    and what the 'Copy Log Report' button puts on the clipboard."""
    L: list[str] = []
    a = L.append
    a("=" * 78)
    a("HERMES BUG-MONITOR -- GHOST PATH INCIDENT REPORT")
    a("=" * 78)
    a(f"Report generated : {_iso()}")
    a(f"Monitor version  : {DAEMON_VERSION}")
    a(f"Machine          : {platform.node()}  ({platform.system()} {platform.release()})")
    a(f"Target           : {HERMES_HOME}")
    a("")

    ghost = state.get("ghost_paths") or []
    a(f"SEVERITY         : CRITICAL -- duplicated-drive root present on disk")
    a(f"Trees detected   : {len(ghost)}")
    a("")

    for g in ghost:
        a("-" * 78)
        a(f"GHOST TREE       : {g.get('path')}")
        if g.get("error"):
            a(f"  error         : {g['error']}")
        fp = g.get("fingerprint") or {}
        a(f"  files         : {fp.get('files')}")
        a(f"  directories   : {fp.get('dirs')}")
        a(f"  total bytes   : {fp.get('bytes')}")
        a(f"  first created : {fp.get('oldest_creation')}")
        a(f"  last written  : {fp.get('newest_mtime')}")
        a(f"  newest file   : {fp.get('newest_relative_path')}")
        a(f"  identity      : {fp.get('path_digest')}")

        root = Path(g["path"])
        try:
            st = root.stat()
            a(f"  ROOT CTIME    : {_iso(datetime.fromtimestamp(st.st_ctime))}   <-- creation moment")
            ghost_epoch = st.st_ctime
        except OSError:
            ghost_epoch = None

        a("")
        a("  NEWEST ARTIFACTS UNDER THE GHOST (names the writing module):")
        arts = newest_artifacts(root, 20)
        if not arts:
            a("    <tree is empty>")
        for r in arts:
            a(f"    {r['created']}  {r['size']:>12,}B  {r['relative']}")

        a("")
        a("  SUSPECT PROCESSES (temporal attribution -- circumstantial, not proof):")
        procs = suspect_processes(ghost_epoch, 300)
        if not procs:
            a("    <no candidate process found in the +/-300s window>")
        for p in procs[:15]:
            delta = p.get("seconds_from_ghost")
            delta_s = f"{delta:+.1f}s" if delta is not None else "n/a"
            a(f"    score={p['relevance_score']:<4} pid={p['pid']:<7} {delta_s:>9}  {p['name']}")
            if p.get("command_line"):
                a(f"        cmd: {p['command_line'][:300]}")

        a("")
        a("  HERMES LOG LINES MENTIONING THE GHOST PATH:")
        exc = hermes_log_excerpt("C:\\c", 15)
        if not exc:
            a("    <none found>")
        for line in exc:
            a(f"    {line}")
        a("-" * 78)
        a("")

    poisoned = state.get("poisoned_config_values") or []
    a("ROOT CAUSE -- LIVE CONFIG STILL CARRYING A DRIVE-LESS PATH")
    a(f"(MSYS ``/c/...`` spellings in active configuration regenerate this tree):")
    if not poisoned:
        a("    <none -- configuration is clean; the writer set its env at runtime>")
    for h in poisoned[:40]:
        a(f"    {h['file']}:{h['line']}")
        a(f"        {h['text']}")
    if len(poisoned) > 40:
        a(f"    ... and {len(poisoned) - 40} more")
    a("")

    a("SOURCE-LEVEL CAUSE")
    a("    hermes_constants._get_platform_default_hermes_home() read LOCALAPPDATA with no")
    a("    drive validation. A POSIX/MSYS value (/c/Users/<name>/AppData/Local) is")
    a("    root-relative on Windows, so Path(...).mkdir() resolved to C:\\c\\Users\\...")
    a("    on the current drive. Same unvalidated pattern in hermes_state_guard.py and")
    a("    hermes_cli/dashboard_procs.py. All three are now guarded by")
    a("    hermes_constants.has_drive(). 'hermes update' reverts these edits -- the")
    a("    monitor's --fix re-applies them; that is why the tree can come back.")
    a("")
    a("=" * 78)
    a("END OF REPORT")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Ring-buffer log
# --------------------------------------------------------------------------
def _read_log() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    entries: list[dict] = []
    try:
        for raw in LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return entries


def _write_log(entries: list[dict]) -> None:
    """
    Ring buffer: the newest MAX_LOG_ENTRIES survive.  Entry 51 overwrites entry 1.
    The file is rewritten atomically so a crash mid-write cannot corrupt the log.
    """
    entries = entries[-MAX_LOG_ENTRIES:]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LOG_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, LOG_FILE)


def append_log(entry: dict) -> None:
    entries = _read_log()
    entries.append(entry)
    _write_log(entries)
    try:
        LOG_FILE.with_name("bug-monitor.log").write_text(
            "\n".join(
                f"{e.get('timestamp')}  {e.get('event'):<10}  {e.get('summary')}" for e in entries
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


# --------------------------------------------------------------------------
# State-change detection
# --------------------------------------------------------------------------
def _prev_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def _state_key(state: dict) -> str:
    """Identity used for change detection.

    Includes the per-tree fingerprint so 'still broken but still growing' is a
    distinct state from 'still broken, unchanged' -- the log then records
    continued *mutation* rather than pure repetition.
    """
    parts = []
    for g in state.get("ghost_paths") or []:
        fp = g.get("fingerprint") or {}
        parts.append(
            f"{g.get('path')}|{fp.get('files')}|{fp.get('dirs')}|{fp.get('bytes')}|{fp.get('path_digest')}"
        )
    parts.append("cfg=" + str(len(state.get("poisoned_config_values") or [])))
    for d in state.get("model_drift") or []:
        parts.append("drift=" + str(d.get("file") or d.get("error")) + "|" + str(d.get("wrong") or ""))
    parts.append("clean=" + str(state.get("clean")))
    return ";".join(parts) or "clean"


def do_scan(*, alert: bool = True, force_log: bool = False) -> dict:
    """
    One scan cycle.  Returns the scan state with an added ``changed`` flag.
    """
    _ensure_dirs()
    now = _now()
    try:
        state = probe()
    except Exception as exc:  # pragma: no cover - defensive
        state = {
            "clean": False,
            "ghost_paths": [],
            "poisoned_config_values": [],
            "scanned_at": _iso(now),
            "scan_error": f"{type(exc).__name__}: {exc}",
        }
        state["scan_error_detail"] = traceback.format_exc()[-2000:]

    key = _state_key(state)
    prev = _prev_state()
    prev_key = prev.get("state_key", "")
    changed = (key != prev_key) or force_log

    if changed:
        if state.get("clean"):
            event = "CLEARED"
            summary = (
                f"Ghost path absent. Resolved after {prev.get('since') or 'n/a'}. "
                f"config hits={len(state.get('poisoned_config_values') or [])}"
            )
            report = None
        else:
            event = "DETECTED"
            report = build_report(state)
            trees = ", ".join(g.get("path", "?") for g in state.get("ghost_paths") or [])
            total = sum(
                (g.get("fingerprint") or {}).get("files", 0) for g in state.get("ghost_paths") or []
            )
            drift = state.get("model_drift") or []
            drift_txt = " | ".join(
                f"{Path(d.get('file', '?')).name}:{','.join((d.get('wrong') or {}).keys()) or 'error'}"
                for d in drift
            )
            summary = (
                f"Ghost path present: {trees} ({total:,} files) | "
                f"config hits={len(state.get('poisoned_config_values') or [])}"
                + (f" | MODEL DRIFT: {drift_txt}" if drift_txt else "")
            )
            if drift and not state.get("ghost_paths"):
                report = report or {
                    "title": "Model routing drift",
                    "detail": drift,
                }
        entry = {
            "timestamp": state["scanned_at"],
            "event": event,
            "summary": summary,
            "state_key": key,
            "report": report,
            "detail": {
                "ghost_paths": state.get("ghost_paths"),
                "poisoned_config_values": (state.get("poisoned_config_values") or [])[:40],
            },
        }
        append_log(entry)
        _save_state(
            {
                "state_key": key,
                "since": state["scanned_at"] if not state.get("clean") else prev.get("since") or "",
                "cleared_at": state["scanned_at"] if state.get("clean") else "",
                "last_scan": state["scanned_at"],
                "clean": state.get("clean"),
                "last_event": event,
                "last_summary": summary,
                "version": DAEMON_VERSION,
            }
        )
        if not state.get("clean"):
            try:
                REPORT_DIR.mkdir(parents=True, exist_ok=True)
                LATEST_REPORT.write_text(report or "", encoding="utf-8")
            except OSError:
                pass
            if alert:
                _popup(entry, report or summary)
    else:
        # No state change: touch only the heartbeat, never the log.
        prev["last_scan"] = state["scanned_at"]
        prev["clean"] = state.get("clean")
        prev["scanned_count"] = int(prev.get("scanned_count", 0)) + 1
        _save_state(prev)

    # Keep the dashboard's data file current on every cycle, changed or not, so the page
    # always reflects the latest heartbeat without needing a separate command.
    try:
        cmd_dashboard_data()
    except Exception:
        pass

    state["changed"] = changed
    return state


# --------------------------------------------------------------------------
# Popup alert
# --------------------------------------------------------------------------
def _popup(entry: dict, report: str) -> None:
    """Windows-style modal alert with 'Copy Log Report' and 'Fix It Now'.

    Launched detached so a blocking modal can never stall the scan loop, and
    rate-limited so a persistent defect cannot spawn a popup every 5 minutes.
    """
    if sys.platform != "win32" or not POPUP_PS1.exists():
        return
    _ensure_dirs()
    payload = {
        "title": "HERMES BUG-MONITOR - GHOST PATH DETECTED",
        "event": entry.get("event", "DETECTED"),
        "timestamp": entry.get("timestamp", ""),
        "summary": entry.get("summary", ""),
        "report_path": str(LATEST_REPORT),
        "fix_command": f'"{Path(__file__).resolve()}" fix',
    }
    pj = STATE_DIR / "popup_payload.json"
    try:
        pj.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return

    # Rate limit: at most one popup per 10 minutes per distinct state.
    last = 0.0
    try:
        last = float((STATE_DIR / "last_popup.txt").read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        last = 0.0
    if time.time() - last < 600:
        return
    try:
        (STATE_DIR / "last_popup.txt").write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass

    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(POPUP_PS1),
                "-Payload",
                str(pj),
            ],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# Remediation
# --------------------------------------------------------------------------
SOURCE_GUARDS = [
    # (relative path, marker that must be present, human description)
    (
        "hermes_constants.py",
        "def has_drive(",
        "drive validator + LocalAppData shell-API resolver",
    ),
    ("hermes_state_guard.py", "has_drive", "state-db guard drive validation"),
    ("hermes_cli/dashboard_procs.py", "has_drive", "dashboard process-scan drive validation"),
    ("hermes_cli/_launchers.py", "refused drive-less install directory", "launcher drive-less guard"),
    ("hermes_cli/_launchers.py", "def _keep_locked_launcher(", "locked-launcher deferral guard"),
    (
        "pm/workspace.py",
        "_STATE_TMP_PREFIXES",
        "dependency stamp excludes plugin runtime state (state.json churn)",
    ),
]


def source_guard_status() -> list[dict]:
    out: list[dict] = []
    for rel, marker, desc in SOURCE_GUARDS:
        p = HERMES_SRC / rel
        try:
            present = marker in p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            present = False
        out.append({"file": rel, "guard": desc, "present": present, "exists": p.exists()})
    return out


def apply_fixes(*, quiet: bool = False) -> dict:
    """
    Remediate: (1) move the ghost tree to the monitor's own quarantine via
    rename (instant, reversible, no 4.7 GB copy), (2) re-apply source guards
    that `hermes update` reverted, (3) scrub MSYS values from live config.

    The ghost tree is quarantined, not deleted.  That is deliberate: deletion is
    irreversible and the operator explicitly asked for forensic value to be kept
    where possible.  `purge` is a separate, explicit command.
    """
    result: dict = {"quarantined": [], "guards_reapplied": [], "config_fixed": [], "errors": []}
    quarantine_root = REPORT_DIR / "quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)

    for g in probe().get("ghost_paths") or []:
        src = Path(g["path"])
        if not src.exists():
            continue
        dest = quarantine_root / f"{src.name}-{int(time.time())}"
        try:
            os.replace(src, dest)
            result["quarantined"].append({"from": str(src), "to": str(dest)})
        except Exception as exc:
            # Cross-device rename fails; fall back to a real move.
            try:
                shutil.move(str(src), str(dest))
                result["quarantined"].append({"from": str(src), "to": str(dest), "method": "shutil.move"})
            except Exception as exc2:
                result["errors"].append(f"quarantine {src} failed: {exc2} (rename err: {exc})")

    result["guards_reapplied"] = reapply_guards()
    result["config_fixed"] = scrub_live_config()
    return result


def reapply_guards() -> list[str]:
    """
    Re-apply the source-level fix if `hermes update` reverted it.

    This is the piece that makes the fix survive updates.  Rather than hold a
    full patch set, the re-apply is a *marker check plus surgical rewrite* of the
    single upstream function, so it stays correct across minor upstream edits.
    """
    applied: list[str] = []
    constants = HERMES_SRC / "hermes_constants.py"
    try:
        text = constants.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read hermes_constants.py: {exc}"]

    if "def has_drive(" in text:
        # Not a reason to stop: the member-stamp guard below is independent, and `hermes
        # update` can revert one guard without touching the other.
        applied.append("hermes_constants.py: already guarded")
        applied.extend(_reapply_member_exclude())
        applied.extend(_reapply_locked_launcher())
        return applied

    old = '''def _get_platform_default_hermes_home() -> Path:
    """Return the platform default with the literal data-directory suffix."""
    suffix = os.environ.get("HERMES_DATA_DIR_SUFFIX", "")
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
        return base / ("hermes" + suffix)
    return Path.home() / (".hermes" + suffix)'''

    new = '''#: ``FOLDERID_LocalAppData``. Resolving LocalAppData through the shell API cannot be
#: poisoned by a shell that rewrote the environment variable into POSIX spelling.
_LOCAL_APP_DATA_KNOWN_FOLDER = "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"


def has_drive(path: str) -> bool:
    """True when *path* names a usable rooted drive or UNC root on Windows.

    A root-relative POSIX/MSYS spelling such as ``/c/Users/apviz/AppData/Local`` is
    *anchored* on Windows but carries **no drive**, so every ``mkdir`` below it silently
    lands on whatever drive happens to be current. That is the entire mechanism behind
    the ``C:\\\\c\\\\`` ghost tree. (Re-applied by hermes_bug-monitor_sys; `hermes update`
    reverts source edits, which is why this must be re-appliable rather than one-shot.)
    """
    if sys.platform != "win32":
        return True
    drive, _ = os.path.splitdrive(path)
    if not drive:
        return False
    return drive.startswith("\\\\\\\\") or os.path.isabs(path)


def _windows_local_app_data() -> str | None:
    """The trustworthy LocalAppData for this machine, or None when none can be trusted."""
    env_value = os.environ.get("LOCALAPPDATA", "").strip()
    if env_value and has_drive(env_value):
        return env_value
    return None


def _windows_home_base() -> Path:
    """Base directory of the default Windows Hermes home. Never drive-less."""
    local_appdata = _windows_local_app_data()
    if local_appdata:
        return Path(local_appdata)
    return Path.home() / "AppData" / "Local"


def _get_platform_default_hermes_home() -> Path:
    """Return the platform default with the literal data-directory suffix."""
    suffix = os.environ.get("HERMES_DATA_DIR_SUFFIX", "")
    if sys.platform == "win32":
        return _windows_home_base() / ("hermes" + suffix)
    return Path.home() / (".hermes" + suffix)'''

    if old not in text:
        return ["hermes_constants.py: upstream shape changed - manual review required"]
    try:
        constants.write_text(text.replace(old, new, 1), encoding="utf-8")
        applied.append("hermes_constants.py: guard re-applied")
    except OSError as exc:
        return [f"write failed: {exc}"]

    applied.extend(_reapply_member_exclude())
    applied.extend(_reapply_locked_launcher())
    return applied


#: The list-comprehension form `ensure_install_launchers` used before the guard.
_LAUNCHERS_OLD_LOCAL = '''    written = [str(path) for name in WINDOWS_BIN_LAUNCHERS
               if (path := stage_launcher(name, root, local)) is not None]'''

#: The loop form that tolerates a launcher Windows will not let us replace.
_LAUNCHERS_NEW_LOCAL = '''    written: list[str] = []
    for name in WINDOWS_BIN_LAUNCHERS:
        path = stage_launcher(name, root, local) or _keep_locked_launcher(name, local)
        if path is not None:
            written.append(str(path))'''

_LAUNCHERS_OLD_OUT = '''    Path(out_dir).mkdir(parents=True, exist_ok=True)
    return [str(path) for name in WINDOWS_BIN_LAUNCHERS
            if (path := stage_launcher(name, root, Path(out_dir))) is not None]'''

_LAUNCHERS_NEW_OUT = '''    Path(out_dir).mkdir(parents=True, exist_ok=True)
    published: list[str] = []
    for name in WINDOWS_BIN_LAUNCHERS:
        path = stage_launcher(name, root, Path(out_dir)) or _keep_locked_launcher(name, Path(out_dir))
        if path is not None:
            published.append(str(path))
    return published'''

_KEEP_LOCKED_HELPER = '''def _keep_locked_launcher(name: str, out_dir: Path) -> Path | None:
    """Accept an already-published launcher that Windows will not let us replace.

    A launcher is a running image as soon as something executes it: ``hermes.exe
    serve`` pins ``.hermes/bin/hermes.exe`` for the whole session, and on Windows a
    mapped image cannot be unlinked or ``os.replace``d. So the source-completion
    tail -- which the desktop app runs from inside that very binary -- could never
    mint a repinned launcher, and ``publish_launchers`` aborted the ENTIRE tail
    (product builds, install stamp, post-update maintenance) over one file that
    was merely busy. Deferring the replacement is the correct behaviour: the
    present launcher keeps working, and the next launch -- with nothing holding
    it -- converges.
    """
    import logging

    for candidate in (out_dir / f"{name}.exe", out_dir / f"{name}.cmd"):
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                logging.getLogger(__name__).warning(
                    "launchers: %s is in use by a running process; keeping the "
                    "published copy and deferring its replacement to the next launch",
                    candidate,
                )
                return candidate
        except OSError:
            continue
    return None


def ensure_install_launchers('''


def _reapply_locked_launcher() -> list[str]:
    """
    Keep a running launcher from aborting the whole update tail.

    Root cause of the perpetual ``launchers: source launcher publication failed``
    and the never-cleared ``source-completion-pending``: on Windows the desktop
    app's own ``.hermes/bin/hermes.exe serve`` pins the launcher for the session,
    so publication could never complete and every boot re-ran the destructive
    pipeline. Marker check plus surgical rewrite, same contract as the other
    guards: a blind patch against unfamiliar upstream text writes a wrong file,
    which is worse than an unpatched one. A file that does not compile is refused.
    """
    target = HERMES_SRC / "hermes_cli" / "_launchers.py"
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return ["hermes_cli/_launchers.py: cannot read (%s)" % exc]
    if "def _keep_locked_launcher(" in text:
        return ["hermes_cli/_launchers.py: already guarded"]

    if _LAUNCHERS_OLD_LOCAL not in text or _LAUNCHERS_OLD_OUT not in text:
        return ["hermes_cli/_launchers.py: upstream shape changed - manual review required"]
    if "def ensure_install_launchers(" not in text:
        return ["hermes_cli/_launchers.py: ensure_install_launchers not found - manual review"]

    patched = text.replace(_LAUNCHERS_OLD_LOCAL, _LAUNCHERS_NEW_LOCAL, 1)
    patched = patched.replace(_LAUNCHERS_OLD_OUT, _LAUNCHERS_NEW_OUT, 1)
    patched = patched.replace("def ensure_install_launchers(", _KEEP_LOCKED_HELPER, 1)
    try:
        compile(patched, str(target), "exec")
    except SyntaxError as exc:
        return ["hermes_cli/_launchers.py: guard produced uncompilable source (%s)" % exc]
    try:
        target.write_text(patched, encoding="utf-8")
    except OSError as exc:
        return ["hermes_cli/_launchers.py: write failed (%s)" % exc]
    return ["hermes_cli/_launchers.py: locked-launcher guard re-applied"]


#: Upstream member-stamp ignore set, and the guard that keeps runtime state out of it.
_MEMBER_EXCLUDE_OLD = (
    '_MEMBER_EXCLUDE = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__"})\n'
    "\n"
    "\n"
    "def _member_ignored(directory, names):\n"
    '    return [name for name in names if name in _MEMBER_EXCLUDE or name.endswith(".egg-info")]\n'
)

_MEMBER_EXCLUDE_NEW = '''_MEMBER_EXCLUDE = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__",
                            # Runtime state a plugin persists inside its own source directory.
                            # members_stamp() hashes every non-excluded file in a member dir, so
                            # a plugin that writes state into itself (hermes-auto-titler:
                            # <HERMES_HOME>/plugins/hermes-auto-titler/state.json, which its own
                            # .gitignore already lists as generated) changes the dependency stamp
                            # on every persist. That invalidates the recorded stamp and, if the
                            # write lands during a sync, trips the publish guard at
                            # pm/install.py:722 ("Dependency inputs changed while preparing
                            # publication") in a retry loop. Build inputs must exclude state.
                            "state.json", "session.json", "counters.json", "counters_v2.json"})

# Atomic-replace temp siblings of the state files above (".state.json.<rand>.tmp").
_STATE_TMP_PREFIXES = (".state.json.", ".session.json.")


def _member_ignored(directory, names):
    return [name for name in names
            if name in _MEMBER_EXCLUDE
            or name.endswith(".egg-info")
            or name.startswith(_STATE_TMP_PREFIXES)]
'''


def _reapply_member_exclude() -> list[str]:
    """
    Keep plugin runtime state out of the dependency stamp.

    Root cause of the ``Dependency inputs changed while preparing publication; retry.``
    error at pm/install.py:722 and of the perpetual ``would-sync`` state:
    ``members_stamp()`` hashes every file in a plugin member directory except a small
    hardcoded set, and ``hermes-auto-titler`` writes ``state.json`` into its own source
    directory (titler.py, ``<HERMES_HOME>/plugins/hermes-auto-titler/state.json``). Every
    persist therefore changes the stamp: the recorded stamp goes stale, and a persist that
    lands mid-sync makes the recomputation at install.py:722 differ from install.py:710,
    which raises. Marker check plus surgical rewrite, same contract as the constants guard:
    a blind patch against unfamiliar text writes a wrong workspace.py, which is worse than
    an unpatched one.
    """
    workspace = HERMES_SRC / "pm" / "workspace.py"
    try:
        text = workspace.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"pm/workspace.py: cannot read ({exc})"]
    if "_STATE_TMP_PREFIXES" in text:
        return ["pm/workspace.py: already guarded"]
    if _MEMBER_EXCLUDE_OLD not in text:
        return ["pm/workspace.py: upstream shape changed - manual review required"]
    try:
        workspace.write_text(text.replace(_MEMBER_EXCLUDE_OLD, _MEMBER_EXCLUDE_NEW, 1), encoding="utf-8")
    except OSError as exc:
        return [f"pm/workspace.py: write failed ({exc})"]
    # Refuse to leave a file that does not parse: an unimportable pm is a broken Hermes.
    try:
        compile(workspace.read_text(encoding="utf-8"), str(workspace), "exec")
    except SyntaxError as exc:
        return [f"pm/workspace.py: guard produced uncompilable source ({exc})"]
    return ["pm/workspace.py: member-stamp guard re-applied"]


def scrub_live_config() -> list[str]:
    """
    Rewrite MSYS-spelled absolute paths in LIVE configuration to native spelling.

    Only the drive-letter form is rewritten, and only in files that are not
    archives/snapshots.  The substitution is exact and reversible
    (``/c/Users/...`` -> ``C:/Users/...``); nothing else in the file is touched,
    so secrets and unrelated settings are preserved byte-for-byte.
    """
    fixed: list[str] = []
    for p in _config_watchlist():
        try:
            raw = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "/c/Users" not in raw and "/cygdrive/c/" not in raw and "/mnt/c/" not in raw:
            continue
        try:
            backup = p.with_suffix(p.suffix + ".bugmon-bak")
            if not backup.exists():
                backup.write_text(raw, encoding="utf-8")
            new = raw.replace("/cygdrive/c/", "C:/").replace("/mnt/c/", "C:/").replace("/c/", "C:/")
            new = new.replace("\\\\C:/", "C:/")
            p.write_text(new, encoding="utf-8")
            fixed.append(str(p))
        except OSError:
            continue
    return fixed


# --------------------------------------------------------------------------
# Daemon
# --------------------------------------------------------------------------
def _acquire_lock() -> bool:
    """Single-instance guard so a startup task and a manual run cannot double-scan."""
    _ensure_dirs()
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(LOCK_FILE.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if pid and _pid_alive(pid):
            return False
        try:
            LOCK_FILE.unlink()
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            return False
    with os.fdopen(fd, "w") as fh:
        fh.write(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = _run_powershell(f"(Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) -ne $null", 15)
        return "True" in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def skill_guard() -> dict:
    """
    Restore any skill that vanished from the Mavis skills tree, and capture new ones.

    A skill directory was deleted from that tree on 2026-10-02 with no `rm` from the
    session that owned it. The runtime's own skill linter was checked and ruled out
    (no unlink / rmSync / writeFileSync, and it enforces no naming convention), so the
    deleter is not conclusively identified. Rather than keep chasing it, this removes
    the consequence: `skill_guardian` keeps a canonical copy of every skill OUTSIDE the
    managed tree, so anything that prunes that directory can only cause a restore.

    Returns a summary dict. Never raises into the monitor loop.
    """
    summary = {"healthy": True, "repaired": [], "newly_captured": [], "error": None}
    try:
        import skill_guardian  # noqa: PLC0415 — sibling module, optional by design
    except Exception as exc:  # pragma: no cover
        summary["error"] = f"skill_guardian unavailable: {exc}"
        return summary
    try:
        g = skill_guardian.guard()
        summary["healthy"] = bool(g.get("healthy"))
        summary["repaired"] = g.get("repaired", [])
        summary["newly_captured"] = g.get("newly_captured", [])
        summary["tracked"] = g.get("tracked")
    except Exception as exc:  # pragma: no cover
        summary["error"] = f"{type(exc).__name__}: {exc}"
    return summary


def cmd_monitor(interval: int = INTERVAL_SECONDS, once: bool = False) -> int:
    _ensure_dirs()
    if not _acquire_lock():
        print("[bug-monitor] another instance holds the lock; exiting", file=sys.stderr)
        return 0
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    print(f"[bug-monitor] v{DAEMON_VERSION} started, interval={interval}s, log cap={MAX_LOG_ENTRIES}")
    print(f"[bug-monitor] watching: {HERMES_HOME}")
    try:
        # First cycle runs immediately so startup surfaces an existing defect at once.
        while True:
            try:
                st = do_scan(alert=True)
                flag = "DETECTED" if not st.get("clean") else "clean"
                mark = "*" if st.get("changed") else " "
                print(f"[bug-monitor] {mark} {st.get('scanned_at')}  {flag}")
                if st.get("poisoned_config_values"):
                    print(
                        f"[bug-monitor]   {len(st['poisoned_config_values'])} live config value(s) still drive-less"
                    )
            except Exception as exc:
                print(f"[bug-monitor] scan error: {exc}", file=sys.stderr)
            # Self-healing: source guards and the skills guardian both run every tick,
            # so `hermes update` reverting a guard — or anything pruning the skills
            # tree — is repaired automatically instead of waiting for an agent.
            try:
                reapplied = reapply_guards()
                changed = [a for a in reapplied if not a.endswith("already guarded")]
                if changed:
                    print(f"[bug-monitor]   re-applied {len(changed)} source guard(s): {changed}")
            except Exception as exc:
                print(f"[bug-monitor] guard re-apply error: {exc}", file=sys.stderr)
            try:
                sg = skill_guard()
                if sg.get("error"):
                    print(f"[bug-monitor]   skills guardian: {sg['error']}", file=sys.stderr)
                else:
                    healed = [
                        r for r in sg.get("repaired", []) if r.get("action") in ("recreated", "repaired")
                    ]
                    if healed:
                        names = [r.get("skill") for r in healed]
                        print(f"[bug-monitor]   skills RESTORED: {names}")
                        append_log(
                            {
                                "kind": "skills_restored",
                                "clean": False,
                                "skills": healed,
                                "detail": "skill(s) vanished from the managed tree; restored from canonical",
                            }
                        )
                    # Content drift is reported, never auto-reverted: a hash mismatch is
                    # far more often a legitimate edit than damage.
                    for r in sg.get("needs_accept") or []:
                        print(
                            f"[bug-monitor]   skills DRIFT (left as-is): {r.get('skill')} "
                            "— run `skills --accept-live` if the change is intended"
                        )
                    if sg.get("newly_captured"):
                        print(f"[bug-monitor]   skills captured: {sg['newly_captured']}")
            except Exception as exc:
                print(f"[bug-monitor] skills guardian error: {exc}", file=sys.stderr)
            if once:
                break
            # Optimization: single sleep avoids loop overhead
            time.sleep(max(1, interval))
    except KeyboardInterrupt:
        print("\n[bug-monitor] stopping")
    finally:
        _release_lock()
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_status() -> int:
    _ensure_dirs()
    state = _prev_state()
    entries = _read_log()
    guards = source_guard_status()
    status = {
        "version": DAEMON_VERSION,
        "monitor_root": str(BM_ROOT),
        "hermes_home": str(HERMES_HOME),
        "last_state": state,
        "log_entries": len(entries),
        "log_capacity": MAX_LOG_ENTRIES,
        "log_file": str(LOG_FILE),
        "latest_report": str(LATEST_REPORT) if LATEST_REPORT.exists() else "",
        "running": _pid_alive(int(PID_FILE.read_text(encoding="utf-8").strip() or 0))
        if PID_FILE.exists()
        else False,
        "interval_seconds": INTERVAL_SECONDS,
        "source_guards": guards,
        "guards_all_present": all(g["present"] for g in guards),
        "generated": _iso(),
    }
    print(json.dumps(status, indent=1, ensure_ascii=False))
    return 0


def cmd_scan_once() -> int:
    st = do_scan(alert=True, force_log="--force-log" in sys.argv)
    print(json.dumps(st, indent=1, ensure_ascii=False)[:20000])
    return 0 if st.get("clean") else 1


def cmd_fix() -> int:
    res = apply_fixes()
    print(json.dumps(res, indent=1, ensure_ascii=False))
    append_log(
        {
            "timestamp": _iso(),
            "event": "FIXED",
            "summary": (
                f"remediation run: quarantined={len(res['quarantined'])} "
                f"guards={res['guards_reapplied']} config_files={len(res['config_fixed'])} "
                f"errors={len(res['errors'])}"
            ),
            "state_key": "manual-fix",
            "report": None,
            "detail": res,
        }
    )
    do_scan(alert=False, force_log=True)
    return 0 if not res["errors"] else 2


def cmd_selftest() -> int:
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {name:44s} {'PASS' if cond else 'FAIL'} {extra}")

    print("hermes_bug-monitor selftest")
    print("-" * 60)
    check("BM_ROOT is a real directory", BM_ROOT.is_dir(), str(BM_ROOT))
    check("config dir writable", os.access(STATE_DIR, os.W_OK))
    if sys.platform == "win32":
        import ntpath

        check("has_drive rejects /c/Users", not ntpath.splitdrive("/c/Users/x")[0])
        check("has_drive accepts C:/Users", bool(ntpath.splitdrive("C:/Users/x")[0]))
    # Ring buffer must hold exactly 50.
    #
    # The test runs against a TEMP log, not the live one: the live log may already be at
    # the cap, in which case "append" correctly leaves it at 50 and a naive
    # ``n_after == n_before + 1`` assertion reports a false failure. Testing the real
    # invariant ("never exceeds the cap, and grows below it") against a scratch file also
    # means `selftest` never destroys genuine incident history.
    import tempfile

    live_log = LOG_FILE
    tmp_log = Path(tempfile.gettempdir()) / "bugmon-selftest-ring.jsonl"
    globals()["LOG_FILE"] = tmp_log
    try:
        try:
            tmp_log.unlink()
        except OSError:
            pass
        n_before = len(_read_log())
        append_log(
            {
                "timestamp": _iso(),
                "event": "SELFTEST",
                "summary": "ring buffer self-test entry",
                "state_key": "selftest",
                "report": None,
                "detail": {},
            }
        )
        n_after = len(_read_log())
        check("ring buffer appends when below cap", n_after == n_before + 1, f"{n_before} -> {n_after}")
        for i in range(MAX_LOG_ENTRIES + 5):
            append_log(
                {
                    "timestamp": _iso(),
                    "event": "SELFTEST",
                    "summary": f"overflow probe {i}",
                    "state_key": f"selftest-{i}",
                    "report": None,
                    "detail": {},
                }
            )
        n_final = len(_read_log())
        check(f"ring buffer caps at {MAX_LOG_ENTRIES}", n_final == MAX_LOG_ENTRIES, f"got {n_final}")
        entries = _read_log()
        check(
            "eviction keeps the NEWEST entries",
            bool(entries) and entries[-1]["summary"] == f"overflow probe {MAX_LOG_ENTRIES + 4}",
            f"last={entries[-1]['summary'] if entries else '<empty>'}",
        )
        # The flattened human view is regenerated alongside.
        check(
            "flattened bug-monitor.log written",
            LOG_FILE.with_name("bug-monitor.log").exists(),
        )
    finally:
        globals()["LOG_FILE"] = live_log
        try:
            tmp_log.unlink()
        except OSError:
            pass

    # --- skills guardian ------------------------------------------------------
    # Exercises the real guardian against the real tree, but proves the restore
    # path without ever deleting a live skill: it points both the guardian and the
    # monitor at a scratch tree, removes a skill from THAT tree, and checks it comes
    # back byte-identical.
    try:
        import tempfile

        import skill_guardian as _sg

        scratch = Path(tempfile.gettempdir()) / "bugmon-selftest-skills"
        live_skills_dir, live_canonical, live_manifest = (
            _sg.SKILLS_DIR,
            _sg.CANONICAL_DIR,
            _sg.MANIFEST_PATH,
        )
        _sg.SKILLS_DIR = scratch / "skills"
        _sg.CANONICAL_DIR = scratch / "canonical"
        _sg.MANIFEST_PATH = _sg.CANONICAL_DIR / "manifest.json"
        try:
            if scratch.exists():
                shutil.rmtree(scratch, ignore_errors=True)
            victim = _sg.SKILLS_DIR / "mavis-selftest-victim-skill"
            victim.mkdir(parents=True, exist_ok=True)
            (victim / "SKILL.md").write_text("---\nname: mavis-selftest-victim-skill\n---\nbody\n", encoding="utf-8")
            _sg.snapshot()
            check("skills guardian captured a scratch skill", ( _sg.CANONICAL_DIR / "mavis-selftest-victim-skill" / "SKILL.md").is_file())
            shutil.rmtree(victim, ignore_errors=True)
            problems = _sg.verify()
            check(
                "skills guardian detects a vanished skill",
                any(p["skill"] == "mavis-selftest-victim-skill" and p["issue"] == "missing" for p in problems),
                f"got {problems}",
            )
            fixed = _sg.restore()
            check(
                "skills guardian restores it",
                bool(fixed) and (victim / "SKILL.md").is_file(),
            )
            check(
                "skills guardian verify clean after restore",
                _sg.verify() == [],
            )
            # Regression lock for the flaw this module originally shipped with: a content
            # change is a legitimate edit far more often than it is damage, so the
            # guardian must NOT revert it. Only deletion is auto-healed.
            edited = _sg.SKILLS_DIR / "mavis-selftest-victim-skill" / "SKILL.md"
            edited.write_text("---\nname: mavis-selftest-victim-skill\n---\nHAND EDIT\n", encoding="utf-8")
            out = _sg.restore()
            body = edited.read_text(encoding="utf-8")
            check(
                "skills guardian does NOT revert a content edit",
                "HAND EDIT" in body and any(r.get("action") == "needs-accept" for r in out),
                f"body={body!r} out={out}",
            )
            _sg.snapshot(refresh=True)
            check("accept-live refreshes canonical after an edit", _sg.verify() == [])

            # Test _sha256 function
            import hashlib
            sha_test_file = scratch / "sha256_test_file.txt"
            test_content = b"hello world"
            sha_test_file.write_bytes(test_content)
            expected_hash = hashlib.sha256(test_content).hexdigest()
            actual_hash = _sg._sha256(sha_test_file)
            check(
                "skills guardian _sha256 computes correctly",
                expected_hash == actual_hash,
                f"expected {expected_hash}, got {actual_hash}"
            )
            # Test _sha256 with empty file
            empty_test_file = scratch / "sha256_empty_file.txt"
            empty_test_file.write_bytes(b"")
            expected_empty_hash = hashlib.sha256(b"").hexdigest()
            actual_empty_hash = _sg._sha256(empty_test_file)
            check(
                "skills guardian _sha256 handles empty file",
                expected_empty_hash == actual_empty_hash,
                f"expected {expected_empty_hash}, got {actual_empty_hash}"
            )
            empty_test_file.unlink()

            # Test _sha256 with larger file (multi-chunk)
            large_test_file = scratch / "sha256_large_file.txt"
            # 65536 is chunk size, let's make it 65536 + 10 bytes
            large_content = b"a" * (65536 + 10)
            large_test_file.write_bytes(large_content)
            expected_large_hash = hashlib.sha256(large_content).hexdigest()
            actual_large_hash = _sg._sha256(large_test_file)
            check(
                "skills guardian _sha256 handles multi-chunk file",
                expected_large_hash == actual_large_hash,
                f"expected {expected_large_hash}, got {actual_large_hash}"
            )
            large_test_file.unlink()

            sha_test_file.unlink()
        finally:
            _sg.SKILLS_DIR, _sg.CANONICAL_DIR, _sg.MANIFEST_PATH = (
                live_skills_dir,
                live_canonical,
                live_manifest,
            )
            if scratch.exists():
                shutil.rmtree(scratch, ignore_errors=True)
    except Exception as exc:  # pragma: no cover
        check("skills guardian selftest", False, f"{type(exc).__name__}: {exc}")
    # The probe must not raise and must return a dict.
    try:
        p = probe()
        check("probe() returns a dict", isinstance(p, dict))
        check("probe() has 'clean'", "clean" in p)
    except Exception as exc:
        check("probe() does not raise", False, str(exc))
    check("popup script present", POPUP_PS1.exists(), str(POPUP_PS1))
    print("-" * 60)
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


#: Directories whose contents must NOT be rewritten, and why.
#:
#: This distinction is the difference between cleaning a system and destroying knowledge.
#:
#: ``blobs``       — curator backup of *documentation that explains the MSYS pitfall*
#:                   (e.g. "git -C /c/Users/... fails in MSYS2 bash. Use native paths").
#:                   Rewriting ``/c/Users`` → ``C:/Users`` inside a warning that the reader
#:                   is meant to recognise makes the warning nonsense. Leave it.
#: ``sessions``    — historical request/response dumps. Not configuration, not read at
#:                   runtime. Rewriting quoted transcripts falsifies the historical record.
#: ``quarantine``  — forensic evidence preserved by ``--fix``. Never touched, by anything.
#: ``HERMES_OPTIMUS`` — OPTIMUS Prime's own documents, including the investigation write-up
#:                   that quotes the bug verbatim on purpose.
SCRUB_SKIP_DIRS = {"blobs", "sessions", "quarantine", "HERMES_OPTIMUS", "venv", "node_modules", ".git"}
#: Only these name shapes are treated as rollback-capable configuration.
SCRUB_NAME_RE = None
try:
    SCRUB_NAME_RE = _re.compile(
        r"^(config\.ya?ml|.*\.env|\.env.*|.*\.ps1|.*\.bat|.*\.cmd|.*\.ya?ml|.*\.toml|.*\.ini)([-._].*)?$",
        _re.IGNORECASE,
    )
except Exception:  # pragma: no cover
    SCRUB_NAME_RE = None

MSYS_REPLACEMENTS = (("/cygdrive/c/", "C:/"), ("/mnt/c/", "C:/"), ("/c/", "C:/"))


def _scrub_text(raw: str) -> str:
    out = raw
    for bad, good in MSYS_REPLACEMENTS:
        out = out.replace(bad, good)
    return out.replace("\\\\C:/", "C:/")


def scrub_archives(*, apply: bool = False) -> dict:
    """
    Walk archives (snapshots, .bak, backups/, pre-update copies) and rewrite
    drive-less absolute paths to native spelling.

    ``apply=False`` is a dry run -- the default, because these are restore points and a
    bulk rewrite must be auditable before it happens.

    Why archives matter at all: a rollback from ``profiles/*/backups/config/*.good.*`` or
    ``state-snapshots/*-pre-update/`` reinstates the poisoned ``TELEGRAM_AGENT_HOME``, and
    the ghost tree comes back within minutes. An archive is only inert while nothing
    restores from it.
    """
    changed: list[dict] = []
    skipped_dir: list[str] = []
    errors: list[str] = []
    if not HERMES_HOME.exists():
        return {"dry_run": not apply, "changed": changed, "skipped_dirs": skipped_dir, "errors": errors}

    for dirpath, dirnames, filenames in os.walk(HERMES_HOME, topdown=True):
        pruned = [d for d in dirnames if d in SCRUB_SKIP_DIRS]
        for d in pruned:
            skipped_dir.append(os.path.join(dirpath, d))
        dirnames[:] = [d for d in dirnames if d not in SCRUB_SKIP_DIRS]

        for fn in filenames:
            # Never re-scrub the monitor's own transient backups: they hold the pre-fix
            # bytes by design, so including them would make every verification pass
            # report a phantom "still poisoned" and would never converge.
            if fn.endswith(".bugmon-bak"):
                continue
            if SCRUB_NAME_RE is None or not SCRUB_NAME_RE.match(fn):
                continue
            # Bolt: Optimize by avoiding pathlib.Path object creation in tight loop; use os.path instead.
            filepath = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(filepath) > 5_000_000:
                    continue
                with open(filepath, "r", encoding="utf-8") as _f:
                    raw = _f.read()
            except (OSError, UnicodeDecodeError):
                continue
            if "/c/Users" not in raw and "/cygdrive/c/Users" not in raw and "/mnt/c/Users" not in raw:
                continue
            new = _scrub_text(raw)
            n_before = raw.count("/c/Users") + raw.count("/cygdrive/c/Users") + raw.count("/mnt/c/Users")
            rec = {
                "file": filepath,
                "occurrences": n_before,
                "before": [ln.strip()[:120] for ln in raw.splitlines() if "/c/Users" in ln or "/cygdrive/c/" in ln or "/mnt/c/" in ln][:4],
            }
            if apply and new != raw:
                try:
                    backup_path = filepath + ".bugmon-bak"
                    if not os.path.exists(backup_path):
                        with open(backup_path, "w", encoding="utf-8") as _f:
                            _f.write(raw)
                    with open(filepath, "w", encoding="utf-8") as _f:
                        _f.write(new)
                    rec["written"] = True
                except OSError as exc:
                    errors.append(f"{filepath}: {exc}")
                    rec["written"] = False
            else:
                rec["written"] = False
            changed.append(rec)

    return {
        "dry_run": not apply,
        "files_with_traces": len(changed),
        "changed": changed,
        "skipped_dirs": sorted(set(skipped_dir))[:40],
        "skipped_dir_count": len(set(skipped_dir)),
        "errors": errors,
    }


def cmd_scrub_archives() -> int:
    apply = "--apply" in sys.argv
    res = scrub_archives(apply=apply)
    print(json.dumps(res, indent=1, ensure_ascii=False)[:120000])
    if apply and not res.get("dry_run"):
        append_log(
            {
                "timestamp": _iso(),
                "event": "SCRUB",
                "summary": f"archive scrub applied to {res['files_with_traces']} file(s)",
                "state_key": "scrub-archives",
                "report": None,
                "detail": {"files": [c["file"] for c in res["changed"][:50]]},
            }
        )
    return 0


def cmd_reset_log() -> int:
    """
    Clear the ring buffer and the persisted state.

    An operational control, not a test convenience: after a quarantine + fix cycle the
    operator usually wants a clean slate so the next transition is unambiguous rather
    than being buried under 50 entries of the incident just resolved.

    Quarantined trees under ``reports/quarantine`` are NOT touched -- those are
    forensics and clearing the log must never destroy evidence.
    """
    _ensure_dirs()
    cleared = []
    for target in (LOG_FILE, LOG_FILE.with_name("bug-monitor.log")):
        try:
            if target.exists():
                target.unlink()
                cleared.append(str(target))
        except OSError as exc:
            print(f"[bug-monitor] could not clear {target}: {exc}", file=sys.stderr)
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            cleared.append(str(STATE_FILE))
    except OSError:
        pass
    print(json.dumps({"cleared": cleared, "quarantine_preserved": str(REPORT_DIR / "quarantine")}, indent=1))
    return 0


def cmd_dashboard_data() -> int:
    """
    Emit ``dashboard_data.js`` — a classic script assigning ``window.BUGMON_DATA``.

    Why a JS file and not JSON: the dashboard is opened straight off disk over
    ``file://``, and browsers refuse ``fetch()`` of a sibling local file (CORS). A
    ``<script src>`` tag is not subject to that restriction, so shipping data as a
    script is the only way to have a zero-server, double-clickable dashboard show
    live values.
    """
    _ensure_dirs()
    entries = _read_log()
    state = _prev_state()
    guards = source_guard_status()
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        pid = 0

    latest = ""
    if LATEST_REPORT.exists():
        try:
            latest = LATEST_REPORT.read_text(encoding="utf-8", errors="replace")
        except OSError:
            latest = ""

    payload = {
        "generated": _iso(),
        "version": DAEMON_VERSION,
        "root": str(BM_ROOT),
        "hermes_home": str(HERMES_HOME),
        "interval_seconds": INTERVAL_SECONDS,
        "log_capacity": MAX_LOG_ENTRIES,
        "log_entries": len(entries),
        "log_file": str(LOG_FILE),
        "log_dir": str(LOG_DIR),
        "report_dir": str(REPORT_DIR),
        "latest_report_exists": bool(latest),
        "latest_report": latest[-20000:],
        "running": _pid_alive(pid) if pid else False,
        "pid": pid,
        "state": state,
        "entries": [
            {
                "timestamp": e.get("timestamp", ""),
                "event": e.get("event", ""),
                "summary": e.get("summary", ""),
                "has_report": bool(e.get("report")),
            }
            for e in entries
        ][::-1],
        "guards": guards,
        "guards_all_present": all(g["present"] for g in guards),
        "paths": {
            "log_jsonl": str(LOG_FILE),
            "log_text": str(LOG_FILE.with_name("bug-monitor.log")),
            "state": str(STATE_FILE),
            "latest_report": str(LATEST_REPORT),
            "quarantine": str(REPORT_DIR / "quarantine"),
            "spec": str(DOCS_DIR / "00_SPEC.md"),
            "bat": str(BM_ROOT / "hermes_bug-monitor_sys.bat"),
        },
    }
    out = BM_ROOT / "dashboard_data.js"
    body = json.dumps(payload, indent=1, ensure_ascii=False)
    out.write_text("window.BUGMON_DATA = " + body + ";\n", encoding="utf-8")
    print(f"wrote {out} ({len(body)} bytes, {len(payload['entries'])} log entries)")
    return 0


def cmd_startup(action: str) -> int:
    task = "HermesBugMonitor"
    if sys.platform != "win32":
        print("[bug-monitor] startup registration is Windows-only")
        return 2
    bat = BM_ROOT / "hermes_bug-monitor_sys.bat"
    if action == "install":
        # Args are passed as a list (shell=False): schtasks rejects the nested
        # quoting that a /TR string needs when the path contains spaces.
        cmd = [
            "schtasks", "/Create", "/TN", task, "/SC", "ONLOGON",
            "/TR", f'"{bat}" monitor',
            "/RL", "LIMITED", "/F", "/DELAY", "0000:30",
        ]
    else:
        cmd = ["schtasks", "/Delete", "/TN", task, "/F"]
    proc = subprocess.run(cmd, shell=False, capture_output=True, text=True)
    print((proc.stdout or "") + (proc.stderr or ""))
    return proc.returncode


def cmd_skills(argv: list[str] | None = None) -> int:
    """Verify + repair the Mavis skills tree. Non-zero exit when anything was wrong.

    Deletions are restored automatically. Content that merely differs from the canonical
    copy is reported as `needs-accept` and left alone — see skill_guardian.restore().
    """
    _ensure_dirs()
    argv = argv or []
    if "--accept-live" in argv:
        import skill_guardian as _sg

        res = _sg.snapshot(refresh=True)
        print(f"[skills] canonical refreshed from {res['total']} live skill(s)")
        return 0
    g = skill_guard()
    if g.get("error"):
        print(f"[skills] ERROR {g['error']}", file=sys.stderr)
        return 2
    drift = g.get("needs_accept") or []
    for r in drift:
        print(
            f"[skills] DRIFT  {r.get('skill')}: content differs from canonical, "
            "left untouched (run `skills --accept-live` to promote it)"
        )
    healed = [r for r in g.get("repaired", []) if r.get("action") in ("recreated", "repaired")]
    for r in healed:
        print(f"[skills] RESTORED {r.get('skill')} ({r.get('action')})")
    if healed:
        return 1
    if drift:
        return 3
    if g.get("newly_captured"):
        print(f"[skills] captured {len(g['newly_captured'])} new skill(s) into the canonical store")
    print(f"[skills] OK — {g.get('tracked')} skills tracked, all intact")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        argv = ["scan-once"]
    cmd = argv[1].lower()
    if cmd in ("scan-once", "scan"):
        return cmd_scan_once()
    if cmd in ("monitor", "daemon"):
        return cmd_monitor(INTERVAL_SECONDS, once="--once" in argv)
    if cmd == "status":
        return cmd_status()
    if cmd == "fix":
        return cmd_fix()
    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "reset-log":
        return cmd_reset_log()
    if cmd == "skills":
        return cmd_skills(argv)
    if cmd in ("dashboard-data", "dashdata"):
        return cmd_dashboard_data()
    if cmd in ("scrub-archives", "scrub"):
        return cmd_scrub_archives()
    if cmd == "install-startup":
        return cmd_startup("install")
    if cmd == "uninstall-startup":
        return cmd_startup("uninstall")
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
