# Thalor v2.0 — Roadmap de Packages (2026)

Este documento existe para evitar que a gente “perca o fio” entre patches/hotfixes e para deixar explícito:

- qual é a ordem dos próximos pacotes
- o que entra em cada pacote (escopo)
- critérios mínimos de conclusão (Definition of Done)

> **Regra de ouro:** não avançar para um pacote novo se ainda existir **hotfix pendente** ou **regressão** no pacote anterior.

---

## Status atual

- **Package Q**: _done (funcional) / closing (hotfix final pendente, se aplicável)_
- **Próximo**: **Package R**

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
- `run_multi.py`/runner com **stagger inteligente** (evitar picos simultâneos)
- Portfolio quota global (hard limit) + quotas por asset
- Bases separadas por asset e higiene de cache/artefatos

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

