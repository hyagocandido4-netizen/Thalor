# Plano de refatoração (macro)

Este plano existe para guiar uma refatoração “grande” sem quebrar o bot.
A estratégia é evoluir por **pacotes pequenos**, sempre preservando:

- idempotência
- auditabilidade
- fail‑closed
- CI verde

## Pacote A — Base / estabilidade / contrato

Objetivos:

- documentação atualizada (README + docs)
- guardrails no CI
- harness de regressão (smoke tests) cobrindo invariantes críticas

Status: **em andamento / incremental**.

## Pacote B — Contratos de dados (schemas) e versionamento

- formalizar schemas (candles/dataset/signals/summary/state)
- versionar “contracts” e migrações
- impedir drift silencioso (ex.: coluna muda de semântica)

## Pacote C — Separação “core decision engine” vs “I/O”

- isolar decisão (features → score → decisão) em módulo puro
- isolar I/O (SQLite/CSV/IQ API) em adapters
- permitir simulação 100% offline

## Pacote D — Observabilidade e incident response

- métricas/alertas (stale ctx, drift, quota, timeouts)
- runbook de incidentes
- snapshots estruturados de decisões (JSON) para auditoria

## Pacote E — Performance e escalabilidade

- reduzir recomputações no loop
- caching seguro por timestamp
- otimização de IO (SQLite pragmas, batched writes)

---

> Regra de ouro: refatoração só “conta” quando deixa o sistema mais fácil de evoluir
> **sem** piorar segurança operacional.
