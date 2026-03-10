# Thalor v2.0 — Roadmap de Packages (2026)

Este documento existe para evitar que a gente “perca o fio” entre patches/hotfixes e para deixar explícito:

- qual é a ordem dos próximos pacotes
- o que entra em cada pacote (escopo)
- critérios mínimos de conclusão (Definition of Done)

> **Regra de ouro:** não avançar para um pacote novo se ainda existir **hotfix pendente** ou **regressão** no pacote anterior.

---

## Status atual (2026-03-09)

- **Package Q**: done
- **Package R**: done
- **Package S**: done
- **Package T**: done
- **Package U**: done (inclui hotfix2)
- **Package V**: done
- **Próximo**: **Package W** (Phase 0 Closeout)

---

## Package Q — Decision Pipeline v2 + Multi-Asset Runtime Paths + Gates

### Objetivo
Consolidar o pipeline de decisão/observação v2, com suporte multi-asset básico, e garantir que os paths (data/runtime) sejam segregados por `scope_tag` para evitar vazamento/colisão de estado.

### Entregas (escopo)
- `portfolio status/observe` funcionando com `config/base.yaml` e `config/multi_asset.yaml`
- Paths por `scope_tag`:
  - **market db** por asset/scope
  - **dataset** por asset/scope
  - **runtime dbs** (signals/state) por asset/scope
- Correções de gates/fail-closed e robustez do observe

### Definition of Done (DoD)
- `python -m natbin.runtime_app portfolio status --config config/multi_asset.yaml --json` retorna `ok:true`
- `python -m natbin.runtime_app portfolio observe --config config/multi_asset.yaml --once ... --json` retorna `ok:true`
- Em multi-asset, nenhum scope entra em `gate_fail_closed_missing_cp_meta` (a não ser que **realmente falte** artefato)
- `precheck` e `health` retornam estáveis (sem bloqueios indevidos)
- Sem regressão em `base.yaml`

---

## Package R — Execution Layer v2 (paper/live) + Order Lifecycle

### Objetivo
Criar/solidificar a camada de execução (contrato + implementação), com tracking de ordens e proteção forte de failsafe/quota.

### Entregas (escopo)
- Interface clara de `ExecutionProvider`/adapter
- `runtime_cycle`/`runtime_daemon` capazes de:
  - ler decisão
  - aplicar gating/quota/failsafe
  - **executar** (ou simular/paper) e persistir resultado
- Persistência de estado de execução (open/pending/unknown) e reconciliação

### DoD
- Modo **paper** (execução desabilitada) funciona e registra “skipped” de forma explícita
- Modo **live** (quando habilitado) executa com rastreio completo e rollback seguro
- Teste smoke automatizado no CI (pelo menos 1 cenário)

---

## Package S — Multi-Asset Orchestration (run_multi + stagger + portfolio quota)

### Objetivo
Escalar do “multi-asset observe” para um runtime multi-asset de verdade com orquestração e proteção por portfólio.

### Entregas (escopo)
- `natbin.runtime_app portfolio observe` / `natbin.portfolio.runner` com **stagger** (evitar picos simultâneos)
- Portfolio quota global (hard limit) + quotas por asset
- Bases separadas por asset e higiene de cache/artefatos

> Status (implementado): suporte a `multi_asset.stagger_sec` + paralelismo protegido por `partition_data_paths`.

### DoD
- Execução/observe multi-asset estável por horas sem colisão de DB/arquivos
- `max_parallel_assets` respeitado
- Logs deixam claro seleção e supressões por quota

---

## Package T — Hardening Operacional e Segurança

### Objetivo
Melhorar robustez operacional: sessões, credenciais, rate limits e observabilidade de falhas.

> Nota: qualquer mecanismo que viole termos de uso do broker/plataforma deve ser evitado.

### Entregas (escopo)
- Credential management seguro
- Rate limiting e backoff
- Time filters e “human-like throttling” **para estabilidade**, não para evasão

> Status (implementado): throttling cross-process no `IQClient` via env `IQ_THROTTLE_*` + docs (`.env.example` + guia operacional).

### DoD
- Falhas de rede/autenticação não derrubam o runtime (retries + circuit breaker)
- Não há credenciais expostas em logs

---

## Package U — Produção (testes, CI, Docker, logging estruturado)

### Objetivo
Deixar o projeto pronto para rodar “como produto” com confiança.

### Entregas (escopo)
- Testes automatizados (mínimo: unit + smoke runtime)
- Logging estruturado + decision log
- Docker + docker-compose
- Documentação + diagramas

> Status (implementado): CI com `pytest` + smoke runtime, Dockerfile/docker-compose (paper), e JSONL de eventos de execução (`runs/logs/execution_events.jsonl`).

### DoD
- CI verde
- `docker compose up` sobe runtime (paper) sem intervenção

---

## Package V — Interface Gráfica / Dashboard Local

### Objetivo
Construir UI local (Python) para monitoramento e controle do runtime.

### Entregas (escopo)
- Dashboard local lendo `runs/signals/*/live_signals.sqlite3` e `runs/state/*/live_topk_state.sqlite3`
- Painel de status (health/precheck), incident feed e gráficos de sinais

### DoD
- UI roda local e reflete dados em “quase tempo real”
- Sem travar o runtime (processos separados / leitura segura)

---

## Como usar este documento
- Atualize o **Status atual** ao concluir um pacote.
- Crie hotfixes numerados dentro do pacote, mas sempre finalize o pacote com DoD batido.



---

## Package T — Operational Hardening (throttle + hygiene)

### Objetivo
Endurecer o runtime para uso contínuo: backoff/throttle em erros, higiene de logs/artefatos e limites operacionais para evitar colapsos.

### Status
✅ Done

---

## Package U — Production CI + Docker + Logging

### Objetivo
Consolidar CI em produção (incluindo Docker) e padronizar logging/JSONL para observabilidade e auditoria.

### Status
✅ Done (inclui hotfix1 e hotfix2)

---

## Package V — Local Dashboard (Streamlit)

### Objetivo
Dashboard local para inspeção rápida de status/decisões/execuções, sem depender de infra externa.

### Status
✅ Done

---

## Package W — Phase 0 Closeout (código morto + patches + testes determinísticos + organização final)

### Objetivo
Fechar a Fase 0 com um baseline limpo e determinístico: remover duplicações/código morto,
arquivar/remover scripts de patches históricos e adicionar testes que garantam os contratos
mais críticos (ex.: “sem 1-candle lag” no dataset).

### Entregas (escopo)
- `dataset2.py`: remover duplicação de `build_dataset`, mantendo `build_dataset` (incremental P11) + `_full_build_dataset` (full rebuild).
- Teste determinístico garantindo que o dataset **inclui** a última vela (label NaN) e que o incremental atualiza corretamente.
- Centralização de CPREG em `natbin.runtime.gates.cpreg` (menos código “patchy” espalhado).
- Remoção de `scripts/patches/` do branch `main` (histórico permanece no git).
- `leak_check` sem warnings ruidosos por termos “future …”.

### Definition of Done (DoD)
- `pytest -q` passa
- `python -m natbin.leak_check` passa sem warnings relevantes
- `python scripts/ci/smoke_execution_layer.py` e `python scripts/ci/smoke_runtime_app.py` passam
- CI do GitHub Actions fica **verde**


### Package W — fechamento integral
- `src/natbin/domain/` agora é o caminho canônico para dataset/gate/decision.
- `src/natbin/adapters/` agora é o caminho canônico para integração de broker/client.
- `src/natbin/usecases/` agora é o caminho canônico para collect/dataset/observe/refresh.
- Módulos raiz permanecem como *compatibility shims* para não quebrar scripts/imports legados.
- `scripts/patches/` foi removido do branch principal.
- `pytest.ini` fixa `pythonpath=src` para testes determinísticos.
