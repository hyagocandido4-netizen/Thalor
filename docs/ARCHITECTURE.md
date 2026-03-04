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


## Contratos explícitos (Package B)

A partir do Package B, os schemas duráveis deixam de viver só como detalhes espalhados no runtime:

- `natbin.runtime_contracts`
  - define versões e contratos de `signals_v2` e `executed`
- `natbin.runtime_migrations`
  - concentra as migrações explícitas desses artefatos

Objetivo: preparar a extração futura de repositórios/serviços sem mudar o comportamento do bot.

## Camada de repositórios (Package C)

A partir do Package C, o acesso a persistência/estado começa a sair do observer e a entrar em uma camada própria:

- `natbin.runtime_repos.SignalsRepository`
- `natbin.runtime_repos.ExecutedStateRepository`
- `natbin.runtime_repos.RuntimeTradeLedger`

Objetivo: preservar o comportamento atual enquanto reduz o acoplamento do `observe_signal_topk_perday.py` com SQLite/state internamente.


## Autos policy layer (Package F)

A lógica dos controladores automáticos foi extraída para `src/natbin/autos/`,
com `summary_loader.py`, `common.py` e políticas puras para volume, isoblend e
hour-threshold. Os CLIs originais continuam existindo apenas como wrappers.


## Observability / incident response (Package G)

O runtime agora grava artefatos estruturados de observabilidade em `runs/`:

- `runs/decisions/decision_latest_<asset>_<interval>.json` — último snapshot de decisão
- `runs/decisions/decision_<day>_<asset>_<interval>_<ts>.json` — snapshots detalhados de decisões relevantes (ex.: trade emitido, bloqueio sério)
- `runs/incidents/incidents_<day>_<asset>_<interval>.jsonl` — stream JSONL de incidentes/ações relevantes

Essa camada é deliberadamente **não-bloqueante**: falhas de escrita de observabilidade não interrompem o observer.


## Runtime scope / performance

Pacote H introduz:

- `runtime_scope.py` para naming canônico de artefatos escopados
- `runtime_perf.py` para cache JSON mtime-based e IO idempotente (`write_text_if_changed`)

Isso reduz duplicação e custo de IO sem alterar a policy do Thalor.


## Runtime cycle foundation (Package I)

O pacote I adiciona `runtime_cycle.py`, uma camada Python que descreve e pode
executar um ciclo completo do runtime em etapas canônicas. Neste momento ela é
aditiva: o loop PowerShell segue sendo a rota principal, mas a orquestração
passa a ter um equivalente serializável/testável no lado Python.


## Runtime daemon (Package J)

Além do loop PowerShell principal, o projeto agora possui `natbin.runtime_daemon`
como fundação Python para orquestração futura do ciclo completo.

O Package K adiciona `natbin.runtime_quota`, que calcula snapshot explícito de
quota/pacing a partir da fonte de verdade durável (`signals_v2`). Isso permite
ao daemon Python razonar sobre `max_k_reached_today` e
`pacing_quota_reached` sem depender de lógica embutida no shell.
