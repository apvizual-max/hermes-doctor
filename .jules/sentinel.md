## 2023-10-25 - Hardcoded Absolute Path

**Vulnerability:** Path configuration used a hardcoded absolute path containing a username (`C:\Users\apviz\AppData\Local\hermes`).
**Learning:** Hardcoding paths with user information poses an information disclosure risk and breaks portability.
**Prevention:** Use environment variables (like `os.environ.get`) with secure fallbacks relative to the script location (e.g., `Path(__file__).resolve().parent.parent`).
