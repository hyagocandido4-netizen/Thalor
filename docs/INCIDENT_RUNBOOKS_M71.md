# Incident runbooks (M7.1)

O M7.1 formaliza a camada de **incident operations** em cima dos artefatos já
existentes (`runs/incidents/*.jsonl`, `runs/control/<scope>/...`, alert outbox e
release/security payloads).

## Novos comandos

```powershell
python -m natbin.runtime_app incidents status --repo-root . --config config/multi_asset.yaml --json
python -m natbin.runtime_app incidents report --repo-root . --config config/multi_asset.yaml --json
python -m natbin.runtime_app incidents alert --repo-root . --config config/multi_asset.yaml --json
python -m natbin.runtime_app incidents drill --repo-root . --config config/multi_asset.yaml --scenario broker_down --json
```

## Fluxo recomendado

1. `incidents status` para ver severidade, surface atual e ações sugeridas.
2. `incidents report` para persistir um relatório em `runs/incidents/reports/`.
3. `incidents alert` para enfileirar/enviar um resumo via Telegram.
4. `incidents drill` para revisar os playbooks sem side effects.

## Cenários cobertos

### broker_down
- ativar `drain mode`
- rodar `reconcile` e revisar `orders`
- validar broker guard / credenciais / rate limit antes de remover drain

### db_lock
- confirmar owner do lock
- revisar `guard.json`, `lifecycle.json` e stale artifacts
- garantir apenas um loop por scope antes de retomar

### market_context_stale
- revisar `precheck` + `health`
- confirmar refresh de dataset/market context
- só voltar ao live depois de um ciclo saudável

### alert_queue
- inspecionar `alerts status`
- rodar `alerts flush`
- só considerar release limpo depois de zerar failures/queued críticos
