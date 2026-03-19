# Phase 1 Intelligence v2 (H10)

O H10 abre a Fase 1 do backlog do Grok em cima da base já entregue no M5.

## Objetivo

Sair do `M5 = intelligence baseline` e entrar em `Phase 1 = intelligence operacional`.

A diferença principal é que os componentes deixam de ser apenas artefatos passivos e passam a carregar política explícita:

- P18 — slot-aware tuning com recomendação por slot
- P19 — learned gating/stacking com decisão de promote/neutral/suppress/abstain
- P20 — drift + regime monitor com cooldown de retrain
- P21 — coverage regulator 2.0 com curva alvo e pressão under/over target
- P22 — anti-overfitting tuning com penalidade de generalization gap

## P18 — Slot-aware tuning v2

Arquivo:
- `src/natbin/intelligence/slot_profile.py`

Novidades:
- `recommendation.state`
- `recommendation.score_delta`
- `recommendation.threshold_delta`
- `recommendation.alpha_delta`
- `confidence` por slot

Leitura prática:
- slots bons deixam de ser apenas `multiplier > 1.0`
- agora também carregam recomendação auditável para score/threshold

## P19 — Learned gating / stacking v2

Arquivo:
- `src/natbin/intelligence/learned_gate.py`

Novidades:
- diagnóstico de calibração (`calibration_bins`, `train_brier`, `lift_vs_base`)
- helper `stack_decision(...)`
- decisão explícita: `promote`, `neutral`, `suppress`, `abstain`

Leitura prática:
- o gate não é mais só uma probabilidade
- ele agora produz política de stacking consumível no runtime

## P20 — Drift / regime monitor + retrain trigger v2

Arquivo:
- `src/natbin/intelligence/drift.py`

Novidades:
- `assess_regime(...)`
- `drift_report.regime`
- `update_drift_state(..., cooldown_hours=...)`
- cooldown explícito para não disparar retrain repetidamente

Leitura prática:
- drift e regime deixam de ser a mesma coisa
- a recomendação de retrain fica mais estável e menos ruidosa

## P21 — Coverage regulator 2.0

Arquivo:
- `src/natbin/intelligence/coverage.py`

Novidades:
- `target_curve_share`
- `curve_power`
- `pressure = under_target | balanced | over_target`
- limites explícitos de bonus e penalty

Leitura prática:
- o regulador deixa de empurrar cobertura de forma linear e ingênua
- agora a pressão depende da curva alvo acumulada do dia

## P22 — Anti-overfitting tuning v2

Arquivo:
- `src/natbin/intelligence/anti_overfit.py`

Novidades:
- `generalization_gap`
- `gap_penalty`
- `stability_score`
- `min_windows`

Leitura prática:
- o guard não olha só para robustez agregada
- ele também penaliza overfit clássico entre train e validation

## Runtime

Arquivo:
- `src/natbin/intelligence/runtime.py`

O enrichment agora escreve também:
- `stack_decision`
- `regime_level`
- `intelligence.stack`
- `intelligence.regime`

O score final passa a considerar:
- delta do stacking
- delta do slot-aware
- ajuste do coverage 2.0
- penalidade de drift/regime
- penalidade de anti-overfit

## Configuração nova

Arquivo:
- `src/natbin/config/models.py`

Knobs novos:
- `slot_aware_score_delta_cap`
- `slot_aware_threshold_delta_cap`
- `learned_stacking_enable`
- `learned_promote_above`
- `learned_suppress_below`
- `learned_abstain_band`
- `learned_fail_closed`
- `regime_warn_shift`
- `regime_block_shift`
- `retrain_cooldown_hours`
- `coverage_curve_power`
- `coverage_max_bonus`
- `coverage_max_penalty`
- `anti_overfit_min_windows`
- `anti_overfit_gap_penalty_weight`

## Testes

Cobertura nova/atualizada:
- `tests/test_intelligence_slot_profile.py`
- `tests/test_intelligence_learned_gate.py`
- `tests/test_intelligence_drift.py`
- `tests/test_intelligence_runtime.py`
- `tests/test_intelligence_fit.py`
- `tests/test_intelligence_anti_overfit.py`

## Smoke

- `scripts/tools/intelligence_pack_smoke.py`
- `scripts/tools/phase1_intelligence_v2_smoke.py`

## Resultado esperado

O H10 não fecha a Fase 1 inteira sozinho.
Ele entrega a versão `v2 foundation` dos P18–P22 para que os próximos packages foquem em:
- calibração real por asset/scope
- stacking mais forte com múltiplas heads
- retrain orquestrado
- tuning orientado por portfolio KPIs
