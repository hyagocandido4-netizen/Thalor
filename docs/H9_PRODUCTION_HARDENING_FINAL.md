# H9 — Production Hardening Final

O H9 fecha a camada final de endurecimento operacional do Thalor sem alterar a lógica core de sinal.

## Entregas

- `runtime_app doctor`
  - valida dataset, market context, effective config, gates, circuit breaker, paths de runtime e preflight passivo/ativo do broker
  - grava `runs/control/<scope>/doctor.json`
- `runtime_app retention`
  - faz preview/apply de retenção de artefatos antigos em `runs/`
  - grava `runs/control/<scope>/retention.json`
- `release_readiness`
  - agora inclui resumo do production doctor em modo relaxado
- `IQOptionAdapter.healthcheck()`
  - normaliza reasons operacionais (`iqoption_invalid_credentials`, `iqoption_missing_credentials`, etc.)

## Comandos

```powershell
python -m natbin.runtime_app doctor --repo-root . --json
python -m natbin.runtime_app doctor --repo-root . --probe-broker --json
python -m natbin.runtime_app retention --repo-root . --json
python -m natbin.runtime_app retention --repo-root . --apply --days 30 --json
```

## Interpretação rápida

- `doctor.severity == ok`
  - runtime apto para ciclo local no estado atual
- `doctor.ready_for_live == true`
  - live IQ apto no momento do check (inclui preflight do broker quando `--probe-broker` é usado)
- `retention.candidates_total > 0`
  - existe backlog de artefatos antigos elegíveis para limpeza

## Observação

O production doctor em modo **relaxado** é usado pelo checklist de release para não exigir dataset/market context frescos no momento do empacotamento do repo.
