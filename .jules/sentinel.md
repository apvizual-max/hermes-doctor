## 2024-05-23 - Hardcoded Path Config
**Vulnerability:** Hardcoded user profile path (C:\Users\apviz\...) for application home directory fallback.
**Learning:** Hardcoding user-specific paths breaks cross-user compatibility and can cause unexpected system-level writes or crashes when deployed to other environments or user accounts.
**Prevention:** Always use dynamic environment resolution or secure path derivation (e.g. `Path('~').expanduser()`) for user-relative directories.
