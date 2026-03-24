# Phase 1 Intelligence H12

H12 fecha a próxima camada da Fase 1 com três eixos:

- orquestração mais forte de retrain (`retrain_plan.json` + `retrain_status.json`)
- feedback de regime/coverage no allocator via `portfolio_feedback`
- score policy mais próxima do portfólio real via `portfolio_score`

## Entregas

- `src/natbin/intelligence/retrain.py`
- `src/natbin/intelligence/runtime.py`
- `src/natbin/intelligence/policy.py`
- `src/natbin/portfolio/allocator.py`
- `src/natbin/portfolio/models.py`
- `src/natbin/runtime/execution.py`
- extensões de `config.models.IntelligenceSettings`
- smoke `scripts/tools/phase1_h12_retrain_allocator_smoke.py`

## Novos artefatos

- `runs/intelligence/<scope>/retrain_plan.json`
- `runs/intelligence/<scope>/retrain_status.json`

## Comportamento

- `retrain_trigger.json` continua existindo para eventos de drift.
- `retrain_plan.json` agrega trigger, regime, cobertura, confiabilidade e anti-overfit.
- `portfolio_score` passa a ser a prioridade principal do allocator quando disponível.
- `portfolio_feedback_block:*` permite bloquear um candidato no allocator sem forçar HOLD no runtime.
- quando a execução nasce de uma seleção de portfolio, o `OrderIntent` agora herda `allocation_batch_id`, `cluster_key` e `portfolio_score` do allocation latest.

## Knobs novos / relevantes

No bloco `intelligence:`:

- `portfolio_weight`
- `allocator_block_regime`
- `allocator_warn_penalty`
- `allocator_block_penalty`
- `allocator_under_target_bonus`
- `allocator_over_target_penalty`
- `allocator_retrain_penalty`
- `allocator_reliability_penalty`
- `retrain_plan_cooldown_hours`
- `retrain_watch_reliability_below`
- `retrain_queue_on_regime_block`
- `retrain_queue_on_anti_overfit_reject`

## Observabilidade

- `latest_eval.json` agora carrega `portfolio_score`, `portfolio_feedback` e `retrain_orchestration`.
- o dashboard local mostra resumo de `retrain_plan.json` e `retrain_status.json`.
- o pack grava em `metadata.phase1` os knobs H11/H12 mais relevantes usados no build.

## Smoke / testes

```powershell
python scripts/tools/phase1_h12_retrain_allocator_smoke.py
pytest -q tests/test_intelligence_policy_h12.py tests/test_intelligence_retrain.py tests/test_portfolio_phase1_h12.py tests/test_intelligence_runtime.py
```
