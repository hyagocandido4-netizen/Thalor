# Package 4 — Simulação Monte Carlo Realista

Este package adiciona uma trilha completa de projeção Monte Carlo baseada nos
trades históricos realizados do projeto.

## Entregas

- comando novo `runtime_app monte-carlo`
- módulo novo `natbin.monte_carlo`
- export automático de relatório em JSON + HTML + PDF
- três cenários nativos: Conservador, Médio e Agressivo
- smoke test dedicado do package

## Comando principal

```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app monte-carlo --repo-root . --config config/multi_asset.yaml --json
```

## Saídas

Por padrão, os relatórios são gerados em:

- `runs/reports/monte_carlo/monte_carlo_latest.json`
- `runs/reports/monte_carlo/monte_carlo_latest.html`
- `runs/reports/monte_carlo/monte_carlo_latest.pdf`

## Dependências novas

- `matplotlib`
- `reportlab`

Depois de aplicar o overlay, reinstale as dependências da venv:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Validação rápida

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_monte_carlo_package_4.py

$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/monte_carlo_package_4_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
