# Diagnostic kit — peça 3

A peça 3 adiciona quatro auditorias canônicas ao control plane do Thalor.

## Comandos

### Runtime artifacts

```bash
python -m natbin.runtime_app runtime-artifact-audit --repo-root . --config config/multi_asset.yaml --all-scopes --json
```

Audita artifacts de runtime e controle por scope:
- effective config
- market context
- loop status
- health
- doctor
- release
- intelligence
- incidents
- retrain
- practice round

Artifacts gerados:
- `runs/control/_repo/runtime_artifact_audit.json`
- `runs/control/<scope>/runtime_artifact_audit.json`

### Guardrails

```bash
python -m natbin.runtime_app guardrail-audit --repo-root . --config config/live_controlled_real.yaml --json
```

Audita:
- kill-switch
- drain mode
- circuit breaker
- precheck
- execution hardening
- alinhamento `execution.account_mode` vs `broker.balance_mode`

Artifacts gerados:
- `runs/control/_repo/guardrail_audit.json`
- `runs/control/<scope>/guardrail_audit.json`

### Dependências

```bash
python -m natbin.runtime_app dependency-audit --repo-root . --config config/live_controlled_real.yaml --all-scopes --json
```

Audita:
- versão do Python
- imports críticos (`yaml`, `pydantic`, `websocket`, `iqoptionapi`)
- `PySocks` em runtime quando transporte `socks*` estiver configurado
- presença de `PySocks` em `requirements.txt`, `requirements-dev.txt`, `requirements-ci.txt`
- contrato de instalação do Dockerfile

Artifacts gerados:
- `runs/control/_repo/dependency_audit.json`
- `runs/control/<scope>/dependency_audit.json`

### State DB

```bash
python -m natbin.runtime_app state-db-audit --repo-root . --config config/multi_asset.yaml --all-scopes --json
```

Audita SQLite de:
- `runs/runtime_control.sqlite3`
- `runs/runtime_execution.sqlite3`
- market DB por scope
- signals DB por scope
- state DB por scope

Usa `PRAGMA quick_check`, presença de tabelas esperadas e contagens básicas.

Artifacts gerados:
- `runs/control/_repo/state_db_audit.json`
- `runs/control/<scope>/state_db_audit.json`

## Ordem recomendada para multi-asset

Para o objetivo final de operar os 6 assets ao mesmo tempo:

1. `dependency-audit --all-scopes`
2. `runtime-artifact-audit --all-scopes`
3. `guardrail-audit --all-scopes`
4. `state-db-audit --all-scopes`
5. `provider-probe --all-scopes`
6. `production-gate --all-scopes --probe-provider`

## Leitura rápida dos resultados

- `severity=ok`: sem blockers
- `severity=warn`: operação possível, mas com risco operacional/diagnóstico incompleto
- `severity=error`: blocker real para readiness

## Exit code

Todos os comandos seguem a convenção:
- `0` quando `ok=true`
- `2` quando `ok=false`
