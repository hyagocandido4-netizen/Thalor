# INT-OPS-2 — Retrain + Anti-Overfit Stabilization

## Objetivo

Reduzir ambiguidade operacional entre:

- `retrain_plan.json`
- `retrain_status.json`
- `retrain_review.json`
- `anti_overfit_tuning.json`
- `anti_overfit_tuning_review.json`

A principal dor antes do package era a surface de inteligência interpretar como
"warn" alguns estados que na prática eram esperados após rollback/rejeição.

## O que mudou

### 1. State efetivo materializado

Novo artifact por scope:

- `runs/intelligence/<scope>/intelligence_ops_state.json`

Ele consolida:

- state e priority efetivos de retrain
- plan state / cooldown
- review verdict / reason
- restore de artifacts anteriores
- tuning live vs tuning preservado só no review
- flags de consistência operacional

### 2. Regras de consistência explícitas

O INT-OPS-2 marca como consistentes cenários como:

- `review = rejected`
- `status = rejected`
- `plan = cooldown`
- `cooldown_active = true` ou `cooldown_until_utc` válido
- `anti_overfit_tuning` preservado apenas no review por rollback

Isso evita tratar rollback esperado como incidente falso.

### 3. Surface operacional menos ruidosa

`ops/intelligence_surface.py` agora:

- lê `intelligence_ops_state.json` quando existir
- reconstitui o mesmo state em memória quando o artifact ainda não existir
- usa esse state para classificar `retrain_review`, `anti_overfit_tuning` e
  consistência geral

## Definition of Done

- existe state efetivo único para retrain + anti-overfit
- `retrain_status_payload` expõe esse state por scope
- surface de inteligência deixa de elevar warning falso em rollback esperado
- suíte de testes passa com regressão coberta
