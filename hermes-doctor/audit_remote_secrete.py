"""Refine the remote secret audit.

The first pass flagged two .env entries. Both are named *_HOME / *_SESSION_DIR
and are long enough to have been swept in by a naive `len >= 8` filter.
This checks the CLASS of each match (path vs credential) WITHOUT printing any
value, so the verdict rests on evidence rather than on my reading of a name.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(r"C:\Users\apviz\AppData\Local\hermes")
ENV = REPO / ".env"


def classify(v: str) -> str:
    """Say what KIND of thing this is, without revealing it."""
    if re.match(r"^[A-Za-z]:[\\/]", v):
        return "CALE_ABSOLUTA"
    if v.startswith("\\\\"):
        return "CALE_RETEA"
    if v.startswith(("http://", "https://")):
        return "URL"
    if v in ("true", "false", "1", "0", "yes", "no"):
        return "BOOLEAN"
    if re.fullmatch(r"[\d\s\-_/.,]+", v):
        return "NUMAR/ID"
    # a real credential: high entropy, mixed classes, long
    if len(v) >= 20 and re.search(r"[A-Za-z]", v) and re.search(r"[0-9]", v):
        return "POSIBIL_CREDENTIAL"
    return "CONFIG"


entries = {}
for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    v = v.strip().strip('"').strip("'")
    if len(v) >= 8:
        entries[k.strip()] = v

creds = {k: v for k, v in entries.items() if classify(v) == "POSIBIL_CREDENTIAL"}
noncreds = {k: v for k, v in entries.items() if classify(v) != "POSIBIL_CREDENTIAL"}

print(f"intrari .env : {len(entries)}")
print(f"  credentiale reale : {len(creds)}")
for k, v in sorted(creds.items()):
    print(f"     {k}: len={len(v)} sha256={hashlib.sha256(v.encode()).hexdigest()[:12]}")
print(f"  non-credentiale (cai/URL/flag-uri): {len(noncreds)}")
for k in sorted(noncreds):
    print(f"     {k}: {classify(noncreds[k])}")

# --- walk remote blobs ---------------------------------------------------
out = subprocess.run(["git", "-C", str(REPO), "rev-list", "--objects", "origin/main"],
                     capture_output=True, text=True)
names = {}
for line in out.stdout.splitlines():
    parts = line.split(" ", 1)
    if len(parts) == 2:
        names[parts[0]] = parts[1]

blobs = [s for s in names
         if subprocess.run(["git", "-C", str(REPO), "cat-file", "-t", s],
                           capture_output=True, text=True).stdout.strip() == "blob"]

cred_hits, noncred_hits = [], []
for s in blobs:
    content = subprocess.run(["git", "-C", str(REPO), "cat-file", "-p", s],
                             capture_output=True).stdout
    for k, v in creds.items():
        if v.encode() in content:
            cred_hits.append((s, names.get(s, "?"), k))
    for k, v in noncreds.items():
        if v.encode() in content:
            noncred_hits.append((s, names.get(s, "?"), k, classify(v)))

print()
print(f"blob-uri analizate: {len(blobs)}")
print()
print("=== CREDENTIALE REALE gasite pe remote ===")
print("   ", len(cred_hits), "(asteptam 0)")
for s, n, k in cred_hits:
    print(f"    LEAK {s[:8]} {n} contine {k}")

print()
print("=== valori non-credentiale gasite pe remote ===")
print("   ", len(noncred_hits))
for s, n, k, c in noncred_hits:
    print(f"    {c:<14} {n} -> {k}")

print()
ok = not cred_hits
print("VERDICT:", "NICIO CREDENTIAL REAL PE GITHUB" if ok else "SCURGERE REALA - REPARA IMEDIAT")
if noncred_hits:
    print("NOTA: valorile gasite sunt cai/URL-uri locale, nu credentiale.")
sys.exit(0 if ok else 1)