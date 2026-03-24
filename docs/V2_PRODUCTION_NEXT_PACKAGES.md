# Thalor v2.0 — Próximos Packages pós-SYNC-1

Data: **2026-03-23**

Este documento registra a trilha que passa a valer depois do congelamento do
estado atual com o SYNC-1.

## Status

- **SYNC-1 — done**
- **RCF-2 — done**
- **RCF-3 — done**
- **INT-OPS-2 — done**
- **READY-2 — done**
- **HANDOFF — next (Grok / phase 2+)**

---

## SYNC-1 — Canonicalizar o estado atual ✅

### Objetivo

Parar de depender de memória implícita para saber qual é o estado real do
projeto e separar formalmente:

- o que já está publicado no `main`
- o que existe apenas no working tree local

### Entregas

- `runtime_app sync`
- `runs/control/_repo/sync.json`
- `docs/canonical_state/published_main_baseline.json`
- `docs/canonical_state/workspace_manifest.json`
- documentação do pacote e do fluxo pós-SYNC-1

### Definition of Done

- existe baseline publicado congelado
- existe manifesto do workspace local congelado
- existe comparação automática entre workspace atual e baseline congelado
- o pacote documenta explicitamente a próxima fila de execução

---

## RCF-2 — Observer decomposition + shrink do boundary legado

### Objetivo

Fechar a refatoração do observer, reduzindo o volume de compatibilidade implícita
que ainda atravessa config tipada, overrides e runtime legado.

### Escopo

- quebrar `observe_signal_topk_perday.py` em módulos menores
- concentrar `CPREG` / `CP_ALPHA` / `SLOT2_MULT` em uma única fronteira
- reduzir dependência de bridges indiretas no observer
- manter paridade funcional com a suíte atual

### Done

- observer deixa de ser um monólito único
- não existe lógica de decisão duplicada entre observer e runtime
- knobs legados ficam isolados em um único boundary explícito
- testes e smokes passam sem regressão

---

## RCF-3 — Execution/Broker split

### Objetivo

Reduzir acoplamento entre adapter do broker, execução e reconciliação.

### Escopo

- quebrar `src/natbin/brokers/iqoption.py`
- quebrar `src/natbin/runtime/execution.py`
- quebrar `src/natbin/runtime/reconciliation.py`
- isolar submit policy, reconcile policy e broker guard

### Done

- live/paper continuam com paridade de comportamento
- execution/reconcile ficam menores e testáveis
- adapter do broker fica claramente separado das políticas

---

## INT-OPS-2 — Stabilize retrain + anti-overfit

### Objetivo

Fechar a semântica operacional do retrain, P22 e recoveries, removendo ruído da
surface de inteligência.

### Escopo

- simplificar `retrain_ops.py`
- consolidar artifacts de anti-overfit
- estabilizar promote / reject / cooldown / review
- melhorar legibilidade da `intelligence_surface`

### Done

- surface de inteligência sinaliza blockers reais
- retrain state fica determinístico e auditável
- P22 roda sem artefatos ambíguos

---

## READY-2 — Controlled Practice Green

### Objetivo

Fechar a primeira trilha operacional realmente pronta para prática controlada.

### Escopo

- limpar artefatos stale
- alinhar `doctor`, `practice` e `practice-round`
- consolidar soak + evidência operacional reproduzível
- fechar um runbook confiável para o stage practice

### Done

- `runtime_app security` = ok
- `runtime_app doctor` sem blocker real
- `runtime_app practice` com `ready_for_practice=true`
- `runtime_app practice-round` fecha verde
- o fluxo é reproduzível em checkout limpo

---

## HANDOFF — Próxima trilha fora da refatoração

A partir daqui, a base interna de refatoração/observabilidade/intelligence/practice
está fechada. O próximo passo deixa de ser estrutural e passa a ser o trilho de
produção final com foco em:

- anti-ban / hardening específico do broker
- execução real controlada
- escala multi-asset final
- produção / operação externa

Esse handoff é o ponto natural para voltar ao Grok com um baseline já canonizado
e com a trilha de PRACTICE auditável.
