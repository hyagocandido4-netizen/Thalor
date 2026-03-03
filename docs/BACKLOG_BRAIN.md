# BACKLOG_BRAIN — Roadmap do “cérebro” (ML + decisão) do Thalor

**Última atualização:** 2026-03-02 (BRT)

**Objetivo central:** poucos sinais, alta convicção, decisões auditáveis, evolução incremental **sem autoengano**.

Este documento registra hipóteses, melhorias e experimentos do “cérebro” do bot (features, modelo, calibração, tuning, gating, sinais). Tudo aqui deve virar **commit rastreável** quando entrar no core.

---

## Princípios de evolução

1) **Evidência > intuição**
   - mudança só entra se passar nos testes (paper / multiwindow / live) com critérios claros.

2) **Uma variável por vez (por padrão)**
   - evitar pacotes grandes que impedem atribuir causa.
   - exceção: quando mudanças são *acopladas por design* (ex.: schema + writer + reader) — nesse caso, documentar como “bundle”.

3) **Auditabilidade total**
   - todo sinal precisa ter explicação (`reason`, `gate`, `regime_ok`, `ev`, `rank`, etc.).

4) **Fail-closed sempre que possível**
   - falhou dado crítico? **HOLD**.

5) **Sem promessas**
   - métricas do projeto = hit rate / coverage / estabilidade.
   - não existe promessa de lucro.

---

## Métricas oficiais

### Primárias

- **Hit rate (apenas trades emitidos)**: acerto em `CALL/PUT` quando `reason=topk_emit`.
- **Coverage**: % de candles que viram trade.
- **Trades/dia**: alvo típico **≤ 3 por dia** (Top‑K/pacing), com testes controlados acima/abaixo.

### Secundárias

- **Estabilidade multiwindow**: performance ponderada por trades em janelas pseudo-futuras.
- **Drift de calibração**: distribuição de `proba_up`/`conf` no tempo.
- **Robustez do loop**: não travar, não exceder tempo aceitável por ciclo.

---

## Escada de testes (gating para entrar no core)

1) **Paper holdout (rápido)**
   - split temporal 80/20
   - baseline

2) **Multiwindow (pseudo‑futuro)**
   - janelas sequenciais (expanding train)
   - critério de robustez (não só média)

3) **Live observe (sem execução)**
   - loop rodando com persistência + logs
   - validar estabilidade dos scores, gates, drift

4) **Live execution (PRACTICE primeiro)**
   - só avaliar `CALL/PUT` com `reason=topk_emit`
   - mínimo de trades antes de qualquer conclusão

---

## Definition of Done (para entrar no core)

Uma mudança só entra como “core” se:

- [ ] não piora o CI (lint/sintaxe/guardrails)
- [ ] não cria regressão operacional (loop, persistência, status, logs)
- [ ] melhora métrica definida **ou** reduz risco (fail‑closed, consistência) com evidência
- [ ] adiciona/atualiza testes de regressão quando aplicável
- [ ] adiciona/atualiza docs (README/OPERATIONS/ENV)

---

## Estado atual (resumo)

- Pipeline Windows-first com `observe_loop_auto.ps1` como orquestrador.
- Sinais persistidos em `runs/live_signals.sqlite3` (`signals_v2`) e CSV diário/scoped.
- Modelo/gate padrão atual tende a operar em modo **seletivo** (Top‑K) com múltiplos bloqueios (regime, CP, EV, market context, pacing).

---

## Backlog priorizado (alto nível)

### A) Qualidade e consistência (base)

- [ ] Contratos estáveis de schema (signals/summary/state) + migrações explícitas
- [ ] Testes de regressão (smoke) cobrindo invariantes críticas
- [ ] Uma “fonte de verdade” por conceito (execução vs observação vs status)

### B) Features / modelagem

- [ ] Catálogo de features versionado (hash + documentação)
- [ ] Feature ablation automatizada (impacto em EV e cobertura)
- [ ] Monitor de drift (alerta quando `proba_up/conf` muda de regime)

### C) Calibração e gating

- [ ] Calibração por regime (volatilidade / horário / payout)
- [ ] Gate adaptativo com fail-closed e explicações claras
- [ ] Explorar “top‑k com pacing” vs “threshold fixo” com critérios formais

### D) Operação e segurança

- [ ] Runbook de incidentes (erro na API, payout inconsistente, DB lock)
- [ ] Guardas contra execução indevida (market fechado, payout ruim, staleness)
- [ ] Rotação e retenção de artefatos com auditoria
