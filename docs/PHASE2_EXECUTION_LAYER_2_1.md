# Pacote 2.1 — Execution Layer Completo

## Objetivo

Fechar a trilha operacional de execução do Thalor com:

- submit real via `iqoptionapi`
- reconciliação por ordem
- logs estruturados e persistência local auditável
- comandos operacionais explícitos
- guard-rail para impedir ativação acidental em conta `REAL`

## Superfície operacional

- `python -m natbin.runtime_app execute-order --repo-root . --config <cfg> --json`
- `python -m natbin.runtime_app check-order-status --repo-root . --config <cfg> --external-order-id <id> --json`
- `python -m natbin.runtime_app orders --repo-root . --config <cfg> --limit 20 --json`
- `python -m natbin.runtime_app reconcile --repo-root . --config <cfg> --json`

Aliases compatíveis:

- `execute_order`
- `check_order_status`

## Segurança

- `execution.account_mode: PRACTICE` continua sendo o default.
- Para `REAL`, o adapter falha fechado a menos que exista `THALOR_EXECUTION_ALLOW_REAL=1`.
- Em `mode != live`, o adapter IQ Option continua bloqueando submit.

## Persistência / auditoria

- `runs/runtime_execution.sqlite3`
- `runs/logs/execution_events.jsonl`
- `runs/control/<scope>/orders.json`
- `runs/control/<scope>/reconcile.json`

## Validação local

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_iqoption_adapter.py tests/test_execution_layer_21.py
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/execution_layer_21_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
