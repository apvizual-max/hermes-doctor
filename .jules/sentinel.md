## 2026-10-26 - [Hardcoded Paths]
**Vulnerability:** Internal paths and usernames were hardcoded.
**Learning:** Use relative paths or environment variables to resolve paths dynamically to prevent information disclosure.
**Prevention:** Check for absolute paths containing user directories and replace them with path traversal mechanisms like `__file__` combined with `.parent.resolve()`.
## 2026-10-26 - [Hardcoded Paths]
**Vulnerability:** A hardcoded path like `C:\Users\apviz\...` was used as a fallback for `HERMES_HOME`.
**Learning:** Use `Path("~").expanduser()` for robust, OS-independent fallback paths instead of hardcoding specific usernames and OS paths.
**Prevention:** During code review, look out for explicit paths like `/home/user` or `C:\Users\...` and encourage dynamic path resolution.
