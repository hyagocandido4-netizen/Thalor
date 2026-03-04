# Decision engine (`src/natbin/decision_engine.py`)

O Pacote E introduz uma camada **pura** para a decisão do candle atual.

Objetivo:
- separar a parte **determinística** da política de decisão
- da parte de **I/O** (SQLite, CSV, IQ API, sidecars, scheduler)
- facilitar simulação offline e testes de precedência de reasons

## Entrada

O engine recebe um `DecisionInputs` com:
- arrays por candle (`ts_arr`, `proba`, `conf`, `score`)
- máscaras (`regime_ok_mask`, `candidate_mask`)
- parâmetros de política (`threshold`, `thresh_on`, `k`, `payout`)
- contexto operacional do candle atual:
  - `executed_today`
  - `already_emitted_for_ts`
  - `last_executed_ts`
  - `min_gap_min`
  - `market_open`
  - `market_context_stale_now`
  - `gate_fail_closed_active`
  - `cp_rejected_now`
  - `hard_regime_block`
  - pacing (`pacing_enabled`, `sec_of_day`)

## Saída

O `DecisionResult` devolve:
- `action`
- `reason`
- `blockers`
- `emitted_now`
- `rank_in_day`
- `topk_indices`
- `executed_after`
- `budget_left`
- `pacing_allowed`
- `cooldown_reason`
- `threshold_reason`
- `ev_now`
- `metric_now`

## Regras importantes

- ranking do Top-K usa **sort estável** (`mergesort`)
- `max_k_reached`, `pacing_day_progress`, `market_closed`, `cp_reject`, `gate_fail_closed` etc.
  são resolvidos dentro do engine com precedência explícita
- o engine **não** lê arquivo, DB, env ou relógio do sistema
- toda dependência externa é resolvida pelo caller (`observe_signal_topk_perday.py`)

## Benefício arquitetural

Isso permite:
- smoke tests rápidos de decisão
- simulação 100% offline do candle atual
- redução do acoplamento do observer com detalhes de política
- base melhor para um futuro `decision_snapshot.json` ou replay engine
