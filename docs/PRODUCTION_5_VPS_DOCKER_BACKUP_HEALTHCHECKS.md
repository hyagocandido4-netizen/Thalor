# Production 5 — VPS, Docker, Backups e Healthchecks

## O que o pacote entrega

O pacote adiciona uma camada explícita de preparação para produção sem alterar a semântica do runtime atual:

- imagem Docker com usuário não-root, `tini`, `bash` e `PYTHONPATH=/app/src`
- scripts de loop para runtime e backup
- healthcheck baseado em `runtime_app healthcheck`
- backup arquivado por `runtime_app backup`
- compose local, compose prod e compose VPS

## Filosofia de segurança

Mesmo nos arquivos de compose “prod” e “vps”, o modo de execução continua seguro por padrão:

- `THALOR__EXECUTION__MODE=practice`
- `THALOR__EXECUTION__ENABLED=1`

Ou seja: o pacote **não liga REAL automaticamente**.

## Backup

A configuração fica em `production.backup`.

Campos principais:

- `output_dir`
- `archive_prefix`
- `format`
- `interval_minutes`
- `retention_days`
- `max_archives`
- `include_globs`
- `exclude_globs`
- `latest_manifest_path`

O backup gera:

- archive (`.tar.gz` ou `.zip`)
- manifest sidecar (`.json`)
- latest manifest em caminho estável
- artefato repo-level `runs/control/_repo/backup.json`

## Healthcheck

A configuração fica em `production.healthcheck`.

Campos principais:

- `require_loop_status`
- `max_loop_status_age_sec`
- `check_kill_switch`
- `check_drain_mode`
- `require_execution_repo`
- `scope_sample_limit`

O healthcheck gera `runs/control/_repo/healthcheck.json` e retorna exit code `0` quando saudável e `2` quando há blocker.

## Comandos úteis

```powershell
python -m natbin.runtime_app backup --repo-root . --config config/multi_asset.yaml --json
python -m natbin.runtime_app healthcheck --repo-root . --config config/multi_asset.yaml --json
```

## Docker compose

### Local

```powershell
docker compose up --build thalor-runtime thalor-backup
```

### Dashboard

```powershell
docker compose --profile dashboard up --build thalor-dashboard
```

### VPS

```powershell
docker compose -f docker-compose.vps.yml up --build -d
```

### Overrides de produção

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```
