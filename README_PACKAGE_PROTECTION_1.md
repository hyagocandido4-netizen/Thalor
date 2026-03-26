# Package PROTECTION-1 — Account protection and responsible pacing

This package adds a dedicated `security.protection` surface around the execution layer.

Main goals:

- apply configurable session windows before broker submit
- compute cadence-aware delays from time-of-day, volatility proxy and recent submit pressure
- enforce global / per-asset pacing caps
- block simultaneous exposure inside the same configured `cluster_key`
- persist structured protection artifacts and JSONL decisions

Files added or changed in this package:

- `src/natbin/security/account_protection.py`
- `src/natbin/state/execution_repo.py`
- `src/natbin/state/control_repo.py`
- `src/natbin/runtime/execution_process.py`
- `src/natbin/runtime/execution_submit.py`
- `src/natbin/control/app.py`
- `src/natbin/control/commands.py`
- `config/base.yaml`
- `config/live_controlled_practice.yaml.example`
- `config/live_controlled_real.yaml.example`
- `config/live_controlled_practice.yaml`
- `docs/PROTECTION_1_RESPONSIBLE_ACCOUNT_GUARDRAILS.md`
- `docs/CONFIGURATION_V2.md`
- `docs/EXECUTION_LAYER.md`
- `docs/V2_PRODUCTION_NEXT_PACKAGES.md`
- `tests/test_protection_package_1.py`
- `scripts/tools/protection_package_1_smoke.py`

Apply the overlay ZIP directly on top of the project root.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_protection_package_1.py
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/protection_package_1_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```

Useful command:

```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app protection --repo-root . --config config/live_controlled_practice.yaml --json
```
