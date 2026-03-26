# Package 5 — Preparação para Produção

Este pacote fecha a trilha de preparação operacional para VPS/containers com foco em:

- `Dockerfile` pronto para runtime, dashboard e backup
- `docker-compose.yml`, `docker-compose.prod.yml` e `docker-compose.vps.yml`
- command `runtime_app backup`
- command `runtime_app healthcheck`
- backup automático de `runs/`, `logs/`, databases e relatórios
- healthcheck Docker-friendly com exit code
- restart automático e loops operacionais dedicados

## Comandos novos

```powershell
python -m natbin.runtime_app backup --repo-root . --config config/multi_asset.yaml --json
python -m natbin.runtime_app healthcheck --repo-root . --config config/multi_asset.yaml --json
```

## Smoke

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_production_package_5.py
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/production_package_5_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
