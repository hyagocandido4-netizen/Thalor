# Package DASHBOARD-3 — Dashboard Profissional Thalor

This package upgrades the local dashboard into a production-style control deck.

Main goals:

- modern dark "cyber-dragon" visual layer
- real-time equity / drawdown view from the execution ledger
- KPI deck with win-rate, EV, drawdown, Sharpe and exposure
- unified per-asset status board with multi-asset context
- recent alerts / execution activity feed
- exportable HTML + JSON dashboard reports

Files added or changed in this package:

- `src/natbin/dashboard/app.py`
- `src/natbin/dashboard/analytics.py`
- `src/natbin/dashboard/report.py`
- `src/natbin/dashboard/style.py`
- `src/natbin/config/models.py`
- `src/natbin/config/loader.py`
- `config/base.yaml`
- `docs/DASHBOARD_LOCAL.md`
- `docs/DASHBOARD_PROFESSIONAL_3.md`
- `docs/CONFIGURATION_V2.md`
- `docs/V2_PRODUCTION_NEXT_PACKAGES.md`
- `tests/test_dashboard_package_3.py`
- `scripts/tools/dashboard_package_3_smoke.py`

Apply the overlay ZIP directly on top of the project root.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_dashboard_package_3.py
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/dashboard_package_3_smoke.py
.\.venv\Scripts\python.exe -m natbin.dashboard.report --repo-root . --config config/multi_asset.yaml --json
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```

Useful commands:

```powershell
python -m natbin.dashboard --repo-root . --config config/multi_asset.yaml
python -m natbin.dashboard.report --repo-root . --config config/multi_asset.yaml --json
```
