## Package M3
Package M3 introduces a low-risk compatibility bridge so legacy runtime consumers (`config2.py`, `settings.py`) are backed by the new `natbin.config` foundation instead of behaving like a separate operational config system.

Validation:
```powershell
.\.venv\Scripts\python.exe scripts\tools\config_consumers_smoke.py
```
