## 2026-10-26 - [Hardcoded Paths]
**Vulnerability:** Internal paths and usernames were hardcoded.
**Learning:** Use relative paths or environment variables to resolve paths dynamically to prevent information disclosure.
**Prevention:** Check for absolute paths containing user directories and replace them with path traversal mechanisms like `__file__` combined with `.parent.resolve()`.
