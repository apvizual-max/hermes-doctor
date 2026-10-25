## 2023-10-25 - Hardcoded Path Config

**Vulnerability:** Path string `r"C:\Users\apviz\AppData\Local\hermes"` containing hardcoded developer username.
**Learning:** Hardcoded paths containing specific usernames can cause errors across different environments and leak internal developer identifiers.
**Prevention:** Use standard libraries (`pathlib.Path.home()` or `os.path.expanduser("~")`) to resolve standard paths like user home dynamically rather than relying on hardcoded environment specific strings.
