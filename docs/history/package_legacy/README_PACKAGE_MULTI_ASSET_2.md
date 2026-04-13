# Package MULTI-ASSET-2 — 6-asset orchestration, shared quota, unified dashboard

This package extends the existing portfolio runtime with a production-safe multi-asset layer.

Main goals:

- stagger execution submits across selected assets
- expose shared quota + per-asset quota explicitly in the control plane
- infer a deterministic correlation group when `cluster_key` is not configured
- provide a unified asset board for the local dashboard
- keep the execution layer and current runtime contracts untouched

Files added or changed in this package:

- `src/natbin/portfolio/correlation.py`
- `src/natbin/portfolio/board.py`
- `src/natbin/portfolio/allocator.py`
- `src/natbin/portfolio/materialize.py`
- `src/natbin/portfolio/models.py`
- `src/natbin/portfolio/quota.py`
- `src/natbin/portfolio/runner.py`
- `src/natbin/runtime/execution_signal.py`
- `src/natbin/security/account_protection.py`
- `src/natbin/control/commands.py`
- `src/natbin/dashboard/app.py`
- `src/natbin/config/models.py`
- `config/base.yaml`
- `config/multi_asset.yaml`
- `config/multi_asset_practice.yaml.example`
- `config/live_controlled_practice.yaml.example`
- `docs/MULTI_ASSET_2_COMPLETE.md`
- `docs/CONFIGURATION_V2.md`
- `docs/DASHBOARD_LOCAL.md`
- `docs/V2_PRODUCTION_NEXT_PACKAGES.md`
- `tests/test_multi_asset_package_2.py`
- `scripts/tools/multi_asset_package_2_smoke.py`

Apply the overlay ZIP directly on top of the project root.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_multi_asset_package_2.py
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/multi_asset_package_2_smoke.py
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio status --repo-root . --config config/multi_asset.yaml --json
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
