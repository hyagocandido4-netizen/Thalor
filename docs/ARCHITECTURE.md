# Arquitetura do Thalor

> Este documento descreve o **fluxo principal** e as **fontes de verdade** do sistema.
> Ele foi escrito para ser útil tanto para operação quanto para refatoração futura.

## Visão geral (fluxo)

1) **Config/credenciais**
   - `config.yaml` define `asset`, `interval_sec`, `timezone` e paths.
   - `.env` (não versionado) fornece `IQ_EMAIL`/`IQ_PASSWORD`/`IQ_BALANCE_MODE`.

2) **Coleta de candles**
   - Módulo: `src/natbin/collect_recent.py`
   - DB: `data/market_otc.sqlite3` (tabela `candles`)
   - Estratégia: coletar **somente candles fechados** e fazer upsert (idempotente).

3) **Dataset / features**
   - Módulo: `src/natbin/make_dataset.py`
   - Output típico: `data/dataset_phase2.csv`
   - A base do dataset vem do DB de candles + engenharia de features.

4) **Daily summary (métricas por dia)**
   - Módulo: `src/natbin/refresh_daily_summary.py`
   - Artefatos em `runs/` (scoped por dia/asset/interval quando possível):
     - `runs/daily_summary_YYYYMMDD_<ASSET>_<INTERVAL>s.json`
   - Observação: summaries **scoped por interval** evitam “misturar timeframe”.

5) **Observer / decisão (Top‑K)**
   - Módulo: `src/natbin/observe_signal_topk_perday.py`
   - Saída: uma linha por candle avaliado com `action` + explicação (`reason`, blockers, etc.)
   - Persistência (fonte de verdade):
     - SQLite: `runs/live_signals.sqlite3` (tabela `signals_v2`)
     - CSV diário/scoped: `runs/live_signals_v2_<DAY>_<ASSET>_<INTERVAL>s.csv`

6) **Estado do Top‑K / quota / pacing**
   - DB de estado: `runs/live_topk_state.sqlite3`
   - Objetivo: garantir consistência após reinício e impedir “overtrading”.

7) **Scheduler / orquestração**
   - Script: `scripts/scheduler/observe_loop_auto.ps1`
   - Responsável por:
     - preparar contexto (coleta/dataset/summary/market context)
     - rodar o observer
     - aplicar regras de pacing/quota
     - escrever status heartbeat e logs

---

## Fontes de verdade

- **Candles**: `data/market_otc.sqlite3:candles`
- **Sinais**: `runs/live_signals.sqlite3:signals_v2`
- **Estado operacional Top‑K**: `runs/live_topk_state.sqlite3`
- **Evidência de performance**: `docs/risk_report.md` + `scripts/tools/risk_report.ps1`

---

## Princípios do design (práticos)

- **Idempotência**: rodar o mesmo passo duas vezes não pode corromper estado.
- **Fail‑closed**: falha em gate/contexto crítico resulta em `HOLD`, não em trade.
- **Imutabilidade de trade**: uma vez emitido `CALL/PUT` para um candle, não deve ser sobrescrito por reprocessamento.
- **Escopo por (day, asset, interval_sec)** sempre que aplicável.

---

## Onde refatorar primeiro (guias)

Para uma refatoração grande, é útil preservar essas fronteiras:

- `market data` (candles) separado de `decision` (observer)
- persistência de sinais com schema/versionamento explícito
- scheduler como “thin orchestrator” chamando CLIs idempotentes

Documento de plano: `docs/REFRACTOR_PLAN.md`
