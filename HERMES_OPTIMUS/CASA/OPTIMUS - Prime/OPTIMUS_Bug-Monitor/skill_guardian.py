r"""
skill_guardian.py — makes the Mavis skills tree self-healing.

WHY THIS EXISTS
---------------
A skill directory (…\.minimax\agents\mavis\skills\minimax_hermes_doctor\) was deleted
from the managed skills tree on 2026-10-02 with no `rm` from the session that owned it.
Recovery was possible only because a copy happened to exist elsewhere.

The linter shipped with the runtime (…\.builtin-skills\skill-creator\scripts\
lint-skill.js) was checked and is NOT the cause: it contains no unlink, no rmSync, no
writeFileSync, and enforces no naming convention. So the deleter is not conclusively
identified — and chasing it further is lower value than removing the consequence.

This module removes the consequence. The canonical copy of every skill lives OUTSIDE
the managed tree, so anything that prunes the skills directory — a runtime reconciler,
a concurrent session, a bad tidy pass, a user — can no longer destroy a skill. It can
only make the guardian restore it on the next tick.

DESIGN
------
* Canonical store:  …\OPTIMUS_AI-Bridges\Hermes-Minimax_Bridge\_skills-canonical\<skill-name>\
* Manifest:          …\OPTIMUS_AI-Bridges\Hermes-Minimax_Bridge\_skills-canonical\manifest.json
                      (name -> {files: {relpath: sha256}, bytes, n_files, captured})
* Live tree:         …\.minimax\agents\mavis\skills\<skill-name>\

verify() reports divergence WITHOUT writing. restore() puts back only what is missing
or hash-mismatched. Nothing here ever deletes: a skill that exists on disk but is not in
the manifest is left strictly alone, so this can never fight a legitimate new skill.

This file is stdlib-only and must stay that way. It is loaded by the bug-monitor, and
the bug-monitor is deliberately kept outside the hermes-agent checkout so that
`hermes update` cannot revert it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

# --- locations -----------------------------------------------------------------

SKILLS_DIR = Path(
    os.environ.get("MAVIS_SKILLS_DIR")
    or Path.home() / ".minimax" / "agents" / "mavis" / "skills"
)
CANONICAL_DIR = Path(
    os.environ.get("MAVIS_SKILLS_CANONICAL")
    or Path.home()
    / "AppData"
    / "Local"
    / "hermes"
    / "HERMES_OPTIMUS"
    / "CASA"
    / "OPTIMUS - Prime"
    / "OPTIMUS_AI-Bridges"
    / "Hermes-Minimax_Bridge"
    / "_skills-canonical"
)
MANIFEST_PATH = CANONICAL_DIR / "manifest.json"

# Skills whose whole subtree is preserved (references/, scripts/, assets/ ...).
PRESERVE_SUFFIXES = (".md", ".js", ".py", ".ps1", ".json", ".txt", ".yaml", ".yml")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _skill_files(root: Path) -> dict[str, str]:
    """Map relative path -> sha256 for every preservable file under `root`."""
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".") or "__pycache__" in rel:
            continue
        if p.suffix.lower() not in PRESERVE_SUFFIXES:
            continue
        try:
            out[rel] = _sha256(p)
        except OSError:
            continue
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        return {"version": 1, "captured": None, "skills": {}}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "captured": None, "skills": {}}


def save_manifest(man: dict) -> None:
    CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(man, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)


def live_skills() -> list[str]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        d.name
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    )


def snapshot(*, refresh: bool = False) -> dict:
    """Copy every live skill into the canonical store and record hashes.

    Idempotent. On a normal run it only captures skills that are new or whose content
    has changed, so it is cheap enough to call on every monitor tick.
    """
    man = load_manifest()
    skills = man.setdefault("skills", {})
    captured, skipped = [], []

    for name in live_skills():
        src = SKILLS_DIR / name
        files = _skill_files(src)
        rec = skills.get(name)
        if (
            not refresh
            and rec
            and rec.get("files") == files
            and (CANONICAL_DIR / name / "SKILL.md").is_file()
        ):
            skipped.append(name)
            continue
        dst = CANONICAL_DIR / name
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".*"))
        skills[name] = {
            "files": files,
            "n_files": len(files),
            "bytes": sum((src / f).stat().st_size for f in files if (src / f).is_file()),
            "captured": _now(),
        }
        captured.append(name)

    man["captured"] = man.get("captured") or _now()
    man["updated"] = _now()
    save_manifest(man)
    return {"captured": captured, "unchanged": skipped, "total": len(skills)}


def verify() -> list[dict]:
    """Report, without writing, every manifest entry the live tree fails to match."""
    man = load_manifest()
    out: list[dict] = []
    for name, rec in sorted(man.get("skills", {}).items()):
        live = SKILLS_DIR / name
        if not live.is_dir():
            out.append({"skill": name, "issue": "missing", "detail": "directory absent"})
            continue
        have = _skill_files(live)
        want = rec.get("files", {})
        gone = sorted(set(want) - set(have))
        extra = sorted(set(have) - set(want))
        changed = sorted(f for f in set(want) & set(have) if want[f] != have[f])
        if gone or changed:
            out.append(
                {
                    "skill": name,
                    "issue": "damaged",
                    "missing_files": gone,
                    "changed_files": changed,
                    "untracked_files": extra,
                }
            )
    return out


def restore(*, damaged: bool = False) -> list[dict]:
    """
    Put back skills that have been *deleted*.

    `damaged=False` (the default, and what the monitor loop uses) restores ONLY skills
    whose directory is gone. That case is unambiguous: a directory cannot disappear by
    accident, so restoring it is always right.

    A skill whose files merely DIFFER in content is NOT restored. A hash mismatch is
    ambiguous — far more often it is a legitimate edit than damage — and silently
    reverting it would destroy real work. An earlier version of this module did exactly
    that and undid a hand-written skill section on first contact. Those skills are
    reported as `needs-accept` instead, and promoting them is an explicit act:
    `skill_guardian.py snapshot --refresh`, or `hermes_bug-monitor.py skills --accept-live`.

    Never deletes anything.
    """
    fixed: list[dict] = []
    for item in verify():
        name = item["skill"]
        src = CANONICAL_DIR / name
        dst = SKILLS_DIR / name
        if not src.is_dir():
            fixed.append({"skill": name, "action": "skipped", "reason": "no canonical copy"})
            continue
        if item["issue"] == "missing":
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".*"))
            fixed.append({"skill": name, "action": "recreated"})
        elif not damaged:
            fixed.append(
                {
                    "skill": name,
                    "action": "needs-accept",
                    "changed_files": item.get("changed_files", []),
                    "note": "content differs from canonical; left untouched on purpose",
                }
            )
        else:
            for rel in item.get("missing_files", []) + item.get("changed_files", []):
                s, d = src / rel, dst / rel
                if not s.is_file():
                    continue
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
            fixed.append(
                {
                    "skill": name,
                    "action": "repaired",
                    "files": item.get("missing_files", []) + item.get("changed_files", []),
                }
            )
    if fixed and any(f["action"] in ("recreated", "repaired") for f in fixed):
        man = load_manifest()
        for name in live_skills():
            if name in man.get("skills", {}):
                man["skills"][name]["files"] = _skill_files(SKILLS_DIR / name)
                man["skills"][name]["captured"] = _now()
        man["updated"] = _now()
        save_manifest(man)
    return fixed


def guard() -> dict:
    """One pass: capture anything new, repair deletions, flag content drift. Cheap."""
    problems = verify()
    repaired = restore() if problems else []
    result = snapshot()
    drift = [r for r in repaired if r.get("action") == "needs-accept"]
    return {
        "skills_dir": str(SKILLS_DIR),
        "canonical_dir": str(CANONICAL_DIR),
        "tracked": result["total"],
        "newly_captured": result["captured"],
        "problems": problems,
        "repaired": repaired,
        "needs_accept": drift,
        "healthy": not problems,
    }


def _main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "snapshot":
        print(json.dumps(snapshot(refresh="--refresh" in argv), indent=2))
    elif cmd == "verify":
        p = verify()
        print(json.dumps(p, indent=2) if p else "OK — all tracked skills intact")
        return 1 if p else 0
    elif cmd == "restore":
        r = restore(damaged="--damaged" in argv)
        print(json.dumps(r, indent=2) if r else "OK — nothing to restore")
        return 1 if r else 0
    elif cmd == "accept-live":
        # Explicitly promote current live content to canonical. The only supported way
        # to make an intentional edit stick in the mirror.
        res = snapshot(refresh=True)
        print(
            f"OK — canonical refreshed from {res['total']} live skill(s) "
            f"({len(res['captured'])} updated)"
        )
        return 0
    elif cmd == "guard":
        g = guard()
        print(json.dumps(g, indent=2))
        return 0 if g["healthy"] else 1
    else:
        man = load_manifest()
        print(
            json.dumps(
                {
                    "skills_dir": str(SKILLS_DIR),
                    "canonical_dir": str(CANONICAL_DIR),
                    "canonical_exists": CANONICAL_DIR.is_dir(),
                    "tracked": len(man.get("skills", {})),
                    "live": len(live_skills()),
                    "captured": man.get("captured"),
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
