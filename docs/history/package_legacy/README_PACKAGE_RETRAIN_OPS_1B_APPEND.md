# Package RETRAIN-OPS-1B — Post-Review Resync + Rejection Backoff

## Objetivo
Fechar a última milha do retrain operacional:
- ressincronizar `portfolio_cycle_latest.json` e `portfolio_allocation_latest.json` após o verdict final do retrain;
- evitar repetição imediata de retrain rejeitado sem mudança material de dados;
- alinhar `retrain_review`, `retrain_status` e portfolio scoped ao mesmo estado final.

## O que mudou
- `retrain run` agora captura snapshot **antes** do estado `fitting`, evitando restaurar `retrain_status=fitting` em rollbacks.
- Retrain rejeitado agora entra em **rejection backoff** (`cooldown`) com janela explícita.
- Após o verdict final, o runtime executa um **post-review resync** para rematerializar os latest payloads scoped do portfolio.
- Os payloads de `candidates` e `allocation` passam a carregar:
  - `retrain_state`
  - `retrain_priority`
  - `retrain_plan_state`
  - `retrain_plan_priority`
  - `retrain_review_verdict`
  - `retrain_review_reason`
  - `retrain_review_at_utc`

## Resultado esperado
Depois de um retrain rejeitado:
- `retrain_status.state = rejected`
- `retrain_plan.state = cooldown`
- `retrain_review.verdict = rejected`
- `portfolio latest` deixa de ficar em `queued/high` stale e passa a refletir o estado final do review.
