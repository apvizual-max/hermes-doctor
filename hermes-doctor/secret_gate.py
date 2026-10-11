"""Secret gate for the Hermes Doctor repo.

Runs over exactly the files `git add -A` would stage — not a hypothetical tree.
A whitelist can open a file whose CONTENT holds a token; the path check never
sees that. So the check is on bytes, per file, on the real staged set.

Reports shape only: field name, length, sha256[:12]. Never a value.
Exit 1 if anything smells; 0 otherwise.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

HOME = Path(r"C:\Users\apviz\AppData\Local\hermes")

# name -> regex. Order matters only for reporting.
PATTERNS = {
    "google-api-key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "openrouter-key": re.compile(r"sk-or-v1-[0-9a-f]{64}"),
    "anthropic-key": re.compile(r"sk-ant-[0-9a-z\-]{24,}"),
    "github-token": re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"),
    "aws-access-key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "slack-token": re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
    "telegram-bot-token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
    "private-key-block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    "bearer-token": re.compile(r"(?i)authorization:\s*bearer\s+([A-Za-z0-9._\-]{20,})"),
    "assigned-secret": re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|client[_-]?secret)\b"
        r"\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"
    ),
}

# Values that are structure, not secrets — otherwise the gate cries wolf and
# gets disabled, which is the same failure mode as a guard that never fires.
#
# Each entry must be checked with FULL match. An earlier version used
# `re.match(r"(?i)^(|your|...)", value)`: the empty alternative matched every
# string, so every secret was excused and the gate could never go red. A gate
# that cannot fail is decoration.
PLACEHOLDERS = [
    re.compile(r"(?i)^your[_-]?[\w.\-]*$"),
    re.compile(r"^<[^>]+>$"),
    re.compile(r"^\{\{.*\}\}$"),
    re.compile(r"^\$\{.*\}$"),
    re.compile(r"(?i)^(change[_-]?me|placeholder|example|example[_-]?\w*|dummy|dummy\w*|sample|redacted|insert[_-]?\w*|replace[_-]?\w*|todo|none|null|nil|undefined|false|true)$"),
    re.compile(r"^x{3,}$"),
    re.compile(r"^\.{3,}$"),
    re.compile(r"^-{3,}$"),
    re.compile(r"^\*{3,}$"),
    re.compile(r"^(?:\*{3,}|x{3,})$"),
    re.compile(r"(?i)^(secret|parola|parolă|password|token|api[_-]?key|credential|set[_-]?me|default|test|local|localhost)$"),
]


def is_placeholder(value: str) -> bool:
    v = value.strip()
    if len(v) < 3:
        return True
    return any(rx.fullmatch(v) for rx in PLACEHOLDERS)


# ---------------------------------------------------------------------------
# DE CE TESTELE NU CONȚIN LITERALE
# ---------------------------------------------------------------------------
# Detectorul caută prin definiție tipare pe care un test care le verifică
# trebuie să le aibă. Un test care le scrie literal ar urlă poarta pe
# propriile fișiere — iar o poartă care urlă mereu e oprită, adică devine
# decor. Așa s-a întâmplat: prima versiune se auto-flaga.
#
# SOLUȚIA: niciuna dintre fixturi nu e scrisă ca literal în sursă. Se
# construiește la runtime prin concatenare ("ghp_" + "A"*36), deci tiparul
# nu există niciodată în fișier.
#
# Explicit: NU se maschează nimic. O mască înlocuiește un literal exact
# oriunde apare — deci o cheie privată adevărată, care începe cu același
# header, ar fi ascunsă tocmai de ea. Suprimarea e mai periculoasă decât
# alarma. Constrângerea e mai bună: cine nu scrie literal nu are ce fi
# mascat, iar poarta rămâne roșie.
#
# Test de integritate: rulează `secret_gate.py` peste `test_secret_gate.py`.
# Un singur hit înseamnă că cineva a scris un literal înapoi.


def staged_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(HOME), "add", "-A", "--dry-run"],
        capture_output=True, text=True,
    )
    files = []
    for line in out.stdout.splitlines():
        m = re.search(r"^add '(.+)'$", line.strip())
        if m:
            files.append(HOME / m.group(1))
    return files


def fingerprint(value: str) -> str:
    b = value.encode("utf-8", "replace")
    return f"len={len(value)} sha256={hashlib.sha256(b).hexdigest()[:12]}"


def scan(text: str):
    hits = []
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            val = m.group(m.lastindex) if m.groups() else m.group(0)
            if is_placeholder(val):
                continue
            hits.append((name, fingerprint(val)))
    return hits


def main() -> int:
    files = staged_files()
    print(f"files a fi stocate: {len(files)}")
    print()
    total = 0
    for f in files:
        try:
            raw = f.read_bytes()
        except OSError as exc:
            print(f"  NU POT CITI {f.name}: {exc}")
            total += 1
            continue
        text = raw.decode("utf-8", "replace")
        hits = scan(text)
        if hits:
            print(f"  ALERTA  {f.relative_to(HOME)}")
            for name, fp in hits:
                print(f"            {name}: {fp}")
            total += len(hits)
    print()
    if total:
        print(f"REZULTAT: {total} posibil(e) secret(e). Nu se comita.")
        return 1
    print("REZULTAT: curat. Portile sunt deschise, continutul e verificat.")
    print("Poarta chiar poate deveni rosie? ", end="")
    # Prove the gate can fail: inject a fake value into a throwaway copy.
    probe = "token = \"ghp_" + "A" * 36 + "\""
    if scan(probe):
        print("DA — un token injectat e prins.")
        return 0
    print("NU — poarta nu poate deveni rosie. Decor.")
    return 1


if __name__ == "__main__":
    sys.exit(main())