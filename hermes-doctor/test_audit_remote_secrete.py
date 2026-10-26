"""Test edge cases for classify() from audit_remote_secrete.py"""

import sys
from pathlib import Path
from audit_remote_secrete import classify

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS, FAIL = [], []

def check(name, cond, info=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {info}" if info else ""))

print("1. CALE_ABSOLUTA")
for val in [r"C:\Windows\System32", r"D:/data/backup", r"Z:\foo\bar"]:
    res = classify(val)
    check(f"'{val}'", res == "CALE_ABSOLUTA", f"got {res}")

print("2. CALE_RETEA")
for val in [r"\\nas\share", r"\\10.0.0.1\public"]:
    res = classify(val)
    check(f"'{val}'", res == "CALE_RETEA", f"got {res}")

print("3. URL")
for val in ["http://localhost:8080", "https://api.github.com/v3"]:
    res = classify(val)
    check(f"'{val}'", res == "URL", f"got {res}")

print("4. BOOLEAN")
for val in ["true", "false", "1", "0", "yes", "no"]:
    res = classify(val)
    check(f"'{val}'", res == "BOOLEAN", f"got {res}")

# Verify case sensitivity - it seems currently it only expects exact matches like "true"
for val in ["True", "FALSE", "Yes"]:
    res = classify(val)
    # the existing logic does exact string match for 'true', 'false', etc., so it falls through to CONFIG.
    # We will just verify the current behavior.
    check(f"uppercase/titlecase '{val}'", res == "CONFIG", f"got {res}")

print("5. NUMAR/ID")
for val in ["123", "123.45", "192.168.0.1", "2023-10-26", "1,000.50", "a" * 0 + "12-34_56/78"]: # "a"*0 just to show it's only digits/punct
    res = classify(val)
    check(f"'{val}'", res == "NUMAR/ID", f"got {res}")

print("6. POSIBIL_CREDENTIAL")
for val in [
    "AIzaSyB-abcdefghijklmnopqrstuvwxyz012345", # length > 20, mix of letters/digits
    "sk-or-v1-0123456789abcdef0123456789abcdef",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "a1b2c3d4e5f6g7h8i9j0k1", # 22 chars, mixed
]:
    res = classify(val)
    check(f"'{val}'", res == "POSIBIL_CREDENTIAL", f"got {res}")

# Check negative credential scenarios
for val in [
    "a1b2c3d4e5f6g7h8i9j", # 19 chars (too short)
    "abcdefghijklmnopqrstuvwxyz", # no digits
    "012345678901234567890123456789", # no letters
]:
    res = classify(val)
    check(f"negative credential '{val}'", res != "POSIBIL_CREDENTIAL", f"got {res}")


print("7. CONFIG (fallback)")
for val in ["localhost", "production", "debug_mode", "user@domain.com", "random_string_that_is_long_but_no_digits"]:
    res = classify(val)
    check(f"'{val}'", res == "CONFIG", f"got {res}")

print()
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
