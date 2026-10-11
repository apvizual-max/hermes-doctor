"""Reproduce the sensitivity question in an isolated copy.

Question: if the gate's PATTERNS were emptied, does test_secret_gate.py
actually FAIL? Earlier the sensitivity test claimed 18/18 passed against a
neutralized gate, which should be impossible. Resolve the contradiction
without touching the live file.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SRC = Path(r"C:\Users\apviz\AppData\Local\hermes\hermes-doctor")
PYTHON = Path(r"C:\Users\apviz\AppData\Local\hermes\tools\python-3.14.7+20260901-win32-x64\python.exe")

print("python folosit de autoproba:", PYTHON.exists())
print("python din PATH          :", sys.executable)
print("aceleasi interpretoare?   :", str(PYTHON).lower() == sys.executable.lower())
print()

tmp = Path(tempfile.mkdtemp(prefix="sens_"))
gate = tmp / "secret_gate.py"
test = tmp / "test_secret_gate.py"
shutil.copy2(SRC / "secret_gate.py", gate)
shutil.copy2(SRC / "test_secret_gate.py", test)

py = str(PYTHON) if PYTHON.exists() else sys.executable

print("--- A. copie intacta (asteptam 18/18, rc=0) ---")
r = subprocess.run([py, str(test)], capture_output=True, text=True, cwd=str(tmp))
print("   rc =", r.returncode, "|", [l for l in r.stdout.splitlines() if "RESULT" in l])

# Now neutralize: comment out the whole dict by renaming it.
t = gate.read_text(encoding="utf-8")
neuter = t.replace("PATTERNS = {", "PATTERNS_INECUT = {", 1)
gate.write_text(neuter, encoding="utf-8")
print()
print("--- B. Patterns golit (asteptam PICA, rc!=0) ---")
r2 = subprocess.run([py, str(test)], capture_output=True, text=True, cwd=str(tmp))
tail = [l for l in r2.stdout.splitlines() if "RESULT" in l or "FAIL" in l]
print("   rc =", r2.returncode, "|", tail[:3])
if r2.stderr.strip():
    print("   stderr:", r2.stderr.strip().splitlines()[-1][:120])

# --- C. the REAL production failure mode: scan() silently stops detecting.
# Break B only proves the module is referenced. C proves the ASSERTIONS bite.
print()
print("--- C. scan() returneaza mereu vid (moarte reala, fara NameError) ---")
gate.write_text(SRC.joinpath("secret_gate.py").read_text(encoding="utf-8"), encoding="utf-8")
ct = gate.read_text(encoding="utf-8")
ct = ct.replace("def scan(text: str):\n    hits = []", "def scan(text: str):\n    return []  # NEUTRALIZAT\n    hits = []", 1)
gate.write_text(ct, encoding="utf-8")
r3 = subprocess.run([py, str(test)], capture_output=True, text=True, cwd=str(tmp))
tail3 = [l for l in r3.stdout.splitlines() if "RESULT" in l or "FAIL " in l]
print("   rc =", r3.returncode, "|", tail3[:4])
if r3.stderr.strip():
    print("   stderr:", r3.stderr.strip().splitlines()[-1][:120])

shutil.rmtree(tmp, ignore_errors=True)

if r.returncode == 0 and r2.returncode != 0 and r3.returncode != 0:
    print()
    print("VERDICT: testul chiar pica la poarta goala. Contradictia de mai sus")
    print("         vine din altceva (interpreter / cache), nu din test inselator.")
else:
    print()
    print("VERDICT: INCONSISTENT - testeaza din nou mecanismul.")