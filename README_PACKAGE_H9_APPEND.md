# Package H9 — Production Hardening Final

## O que entrou

- `runtime_app doctor`
- `runtime_app retention`
- artifactos canônicos `doctor.json` e `retention.json`
- normalização de health reason no adapter IQ
- integração do `release` com resumo do doctor
- smoke novo: `scripts/tools/h9_production_hardening_smoke.py`

## Validação esperada

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\tools\h9_production_hardening_smoke.py
.\.venv\Scripts\python.exe -m natbin.runtime_app doctor --repo-root . --json
.\.venv\Scripts\python.exe -m natbin.runtime_app retention --repo-root . --json
```
