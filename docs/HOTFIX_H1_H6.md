# Hotfix H1-H6

This bundle closes the contract drift identified after the strict cleanup pass.

Included fixes:
- `src/natbin/config/legacy.py`: sibling import fixed (`.compat_runtime`) and preserved legacy constants.
- `src/natbin/config/settings.py`: compatibility facade for callers that still import `natbin.config.settings`.
- `src/natbin/config/compat_runtime.py`: schema and resolution errors now fail loud; fallback to the legacy env-shaped dict only happens when the new loader is unavailable.
- authoritative contract files refreshed from the fixed codebase:
  - `src/natbin/config/models.py`
  - `src/natbin/config/loader.py`
  - `src/natbin/portfolio/models.py`
  - `src/natbin/intelligence/runtime.py`
  - `src/natbin/state/control_repo.py`
  - `src/natbin/brokers/iqoption.py`
- `scripts/tools/config_consumers_smoke.py` now checks `natbin.config.legacy` and `natbin.config.settings`.
- `scripts/tools/h1_h6_hotfix_smoke.py` validates the core H1-H6 contracts.

Recommended validation:
```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m pytest -q tests\test_security_loader.py tests\test_intelligence_runtime.py tests\test_iqoption_adapter.py tests\test_portfolio_risk_m4.py tests\test_runtime_hardening.py
.\.venv\Scripts\python.exe scripts\tools\config_consumers_smoke.py
.\.venv\Scripts\python.exe scripts\tools\h1_h6_hotfix_smoke.py
.\.venv\Scripts\python.exe -m natbin.runtime_app observe --repo-root . --once --topk 1
```
