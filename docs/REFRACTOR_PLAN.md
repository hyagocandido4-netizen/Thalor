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

## Pacote C — Repositórios de runtime / persistência / state

- extrair acesso a `signals_v2` e `executed` para repositórios explícitos
- criar um ledger de runtime reutilizável pelo observer e comandos auxiliares
- reduzir acoplamento do observer com SQLite/state sem mudar comportamento

## Pacote D — Orquestração / runtime hardening

- garantir `daily_summary` do dia atual antes dos autos
- timeouts explícitos para subprocessos críticos do loop
- classificar falhas do runtime (`timeout`, `nonzero_exit`, `interrupted`)
- registrar fase/etapa com mais fidelidade no status do loop

## Pacote E — Decision engine

Este pacote isolou a decisão determinística do candle atual em `src/natbin/decision_engine.py`, com smoke e documentação dedicados.

## Pacote F — Camada de políticas dos autos

- extrair `auto_volume`, `auto_isoblend` e `auto_hourthr` para uma camada compartilhada
- centralizar loader/scan de summaries
- manter CLIs atuais como wrappers finos
- preparar a futura engine única dos autos sem mudar comportamento

## Pacote G — Observabilidade e incident response

Este pacote introduziu:

- snapshots estruturados de decisões (`runs/decisions/*.json`)
- trilha JSONL de incidentes (`runs/incidents/*.jsonl`)
- `runtime_health_report.py` para leitura rápida do último status/decisão
- smoke dedicado de observabilidade

## Pacote H — Performance e escalabilidade

Este pacote introduz:

- `runtime_scope.py` como camada canônica de naming/path escopado
- `runtime_perf.py` com cache JSON mtime-based
- `write_text_if_changed()` para reduzir writes redundantes
- pragmas SQLite conservadores para runtime repos

---

> Regra de ouro: refatoração só “conta” quando deixa o sistema mais fácil de evoluir
> **sem** piorar segurança operacional.



## Pacote I — Runtime cycle / thin shell foundation

Este pacote introduz `runtime_cycle.py`, que descreve e opcionalmente executa
um ciclo completo do runtime em Python, sem substituir ainda o loop PowerShell.
A intenção é migrar a orquestração para uma camada mais testável em pacotes
seguintes.

## Status da execução

- Package A: docs + harness base
- Package B: contratos/migrações explícitas de runtime
- Package C: repositórios/ledger de runtime
- Package D: orchestration hardening (summary stub + subprocess timeouts + status de etapa)
- Package E: decision engine puro (`src/natbin/decision_engine.py`) + smoke dedicado
- Package F: camada de políticas dos autos (`src/natbin/autos/`) + smoke dedicado
- Package G: observabilidade/incident response (snapshots + incidents + health report)
- Package H: performance e escalabilidade (`runtime_scope.py` + `runtime_perf.py`)
- Package I: runtime cycle Python / fundação para afinar o shell
- Package J: daemon Python aditivo + wrapper shell fino
- Package K: quota/pacing Python explícitos para o daemon (`runtime_quota.py`)


## Pacote J — Python daemon / thin scheduler bridge

Este pacote adiciona `runtime_daemon.py` e um wrapper PowerShell fino
(`observe_loop_auto_py.ps1`) para preparar a migração gradual da orquestração do
Thalor para Python, sem substituir o loop operacional principal de forma brusca.


## Pacote K — runtime quota / pacing Python

Este pacote adiciona `runtime_quota.py` e suporte opcional de quota-aware sleep
no daemon Python, preparando a próxima etapa de convergência entre o ciclo
Python e a política hoje estabilizada no scheduler PowerShell.
