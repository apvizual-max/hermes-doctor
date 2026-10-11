"""Adversarial test for secret_gate.py — does the gate actually gate?

A guard that cannot go red is decoration. This proves:
  - a real-looking token in a scanned file IS caught,
  - a plain bearer token IS caught,
  - a private key block IS caught,
  - placeholders are NOT flagged (otherwise it cries wolf and gets switched off),
  - a genuinely clean file passes.

Runs on throwaway content in a temp dir. Never reads the live repo's files.
"""
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

# The Windows console here is cp1252 and cannot print Romanian diacritics.
# Force UTF-8 so a PASS/FAIL line never dies on its own message.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The gate under test must be the one shipped NEXT TO THIS TEST.
#
# This path was absolute once, and it pointed at the SKILLS copy while the
# autocommit actually runs the REPO copy. So the suite validated an object
# nobody depended on: emptying the repo gate's PATTERNS still printed
# "18 passed, 0 failed". A control that does not touch what it protects is
# the failure mode this whole exercise exists to prevent.
GATE = Path(__file__).resolve().parent / "secret_gate.py"
spec = importlib.util.spec_from_file_location("gate", GATE)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

PASS, FAIL = [], []


def check(name, cond, info=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {info}" if info else ""))


print("1. a real-looking token MUST be caught")
hits = g.scan('TOKEN = "ghp_' + "A" * 36 + '"')
check("github token caught", any(h[0] == "github-token" for h in hits), f"got {[h[0] for h in hits]}")

hits = g.scan('key = "AIza' + "b" * 35 + '"')
check("google key caught", any(h[0] == "google-api-key" for h in hits), f"got {[h[0] for h in hits]}")

hits = g.scan("Authorization: Bearer sk-or-v1-" + "c" * 64)
check("bearer/openrouter caught", len(hits) > 0, f"got {[h[0] for h in hits]}")

print("2. private key block MUST be caught")
hits = g.scan("-----BEGIN " + "RSA " + "PRIVATE KEY-----\nMIIEow==\n-----END RSA PRIVATE KEY-----")
check("private key caught", any("private-key" in h[0] for h in hits), f"got {[h[0] for h in hits]}")

print("3. assigned secret MUST be caught")
hits = g.scan('password' + ' = "Tr0ub4dor&3xyz"')
check("assigned password caught", len(hits) > 0, f"got {[h[0] for h in hits]}")

print("4. placeholders MUST NOT be flagged")
for val in ["<your-key-here>", "YOUR_API_KEY", "changeme", "placeholder", "***", "xxx",
            "${SECRET}", "n/a", "redacted", "parolă", "test"]:
    txt = f'api_key = "{val}"'
    hits = g.scan(txt)
    check(f"placeholder '{val}' excused", len(hits) == 0, f"got {[h[0] for h in hits]}")

print("5. a genuinely clean file MUST pass")
clean = 'def scan():\n    return {"clean": True}\n# a comment about nothing\n'
hits = g.scan(clean)
check("clean code passes", len(hits) == 0, f"got {[h[0] for h in hits]}")

print("6. a config-shaped file with real values MUST be caught")
real_cfg = (
    'model:\n  api_key: "AIza' + "z" * 35 + '"\n  fallback: none\n'
)
hits = g.scan(real_cfg)
check("config with a live key caught", len(hits) > 0, f"got {[h[0] for h in hits]}")

print()
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)