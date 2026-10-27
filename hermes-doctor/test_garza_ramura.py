"""Prove the non-main branch guard actually fires.

Builds a throwaway repo, puts it on a non-main branch with dirty files,
runs the REAL autocommit script against it, and expects refusal (exit 1)
and ZERO commits on that branch. The live repo is never involved.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import os
DOCTOR = Path(os.environ.get("HERMES_DOCTOR_DIR", Path(__file__).parent.resolve()))
PS = shutil.which("pwsh") or "powershell"

def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)

tmp = Path(tempfile.mkdtemp(prefix="garda_"))
repo = tmp / "repo"
repo.mkdir()

git(repo, "init", "-b", "main")
git(repo, "config", "user.email", "t@t.t")
git(repo, "config", "user.name", "t")
git(repo, "config", "core.hooksPath", ".git/hooks-doctor")  # neutralize global LiveReview
(repo / "hooks-doctor").mkdir(exist_ok=True)
(repo / "a.txt").write_text("unu", encoding="utf-8")
git(repo, "add", "-A")
git(repo, "commit", "-m", "init")

git(repo, "checkout", "-q", "-b", "ramura-test")
(repo / "b.txt").write_text("doi", encoding="utf-8")
git(repo, "add", "-A")

head_before = git(repo, "rev-parse", "HEAD").stdout.strip()
print("ramura curenta :", git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip())
print("HEAD inainte   :", head_before[:7])

r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(DOCTOR / "hermes-doctor-autocommit.ps1"), "-Repo", str(repo)],
                   capture_output=True, text=True)

out = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
print()
for l in out:
    print("   ", l)
print()
print("exit =", r.returncode, "(asteptam 1)")

head_after = git(repo, "rev-parse", "HEAD").stdout.strip()
branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
staged = git(repo, "diff", "--cached", "--name-only").stdout.strip()

print("HEAD dupa      :", head_after[:7], "(trebuie sa fie IDENTIC)")
print("ramura dupa    :", branch)
print("comit pe ramura non-main:", "DA - DEFECT" if head_after != head_before else "NU - corect")

shutil.rmtree(tmp, ignore_errors=True)

ok = (r.returncode == 1) and (head_after == head_before) and (branch == "ramura-test")
print()
print("VERDICT:", "GARDA TRAGE CORECT" if ok else "GARDA NU TRAGE - defect ramas")
sys.exit(0 if ok else 1)