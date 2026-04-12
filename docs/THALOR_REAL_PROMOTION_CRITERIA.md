# Thalor — Critério de promoção para REAL/live

A promoção para REAL **não** acontece por o canary estar fechado.

## Pré-requisitos mínimos
- provider stability não pode estar `unstable`
- o envelope canary precisa estar fechado repetidamente em múltiplos ciclos
- evidência de execução em PRACTICE precisa existir
- dívida de `cp_meta` não pode afetar scope top-1
- sem blockers de provider, guardrail ou artifact

## O que ainda não é permissão para REAL
- `closure_state = healthy_waiting_signal`
- `provider_ready_scopes = 6`
- `provider_state = degraded`
- bundle canary verde apenas para observação

## Regra
Sem evidência operacional adicional e sem provider limpo o bastante para sair de `degraded`, o Thalor permanece em:
- PRACTICE controlado
- top-1
- single-position
