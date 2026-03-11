# Multi-Asset Orchestration (Package S)

Este documento descreve como o loop multi-asset funciona no Thalor e como controlar **paralelismo** e **stagger**.

## Visão geral do ciclo

Quando você roda:

```bash
python -m natbin.runtime_app portfolio observe --repo-root . --config config/multi_asset.yaml
```

O ciclo faz, por *scope*:

1. **prepare**
   - `collect_recent` (atualiza o market DB)
   - `make_dataset` (gera/atualiza o dataset)
   - `refresh_market_context` (payout/market_open + last_candle_ts)
2. **candidate**
   - `observe_once` (gera decisão/candidato por scope)
3. **allocate**
   - `portfolio_allocator` (aplica quotas, correlation filter, exposure caps e escolhe os melhores candidatos)
4. **execute**
   - se `execution.enabled: true`, o ciclo cria intent/ordem/reconcile via camada de execução (Package R)

## Paralelismo (max_parallel_assets)

O `multi_asset.max_parallel_assets` define quantos *scopes* podem rodar em paralelo durante as fases **prepare** e **candidate**.

### Regras de segurança

- A fase **prepare** escreve em arquivos (DB/dataset). Por isso:
  - se `multi_asset.partition_data_paths: true` → pode paralelizar
  - se `multi_asset.partition_data_paths: false` → o prepare é automaticamente forçado para **1 worker** (sequencial)

- A fase **candidate** só paraleliza quando:
  - `multi_asset.enabled: true`
  - `multi_asset.partition_data_paths: true`
  - `max_parallel_assets > 1`

Isso evita corrupção/colisão de arquivos quando os paths são compartilhados.

## Stagger (stagger_sec)

O `multi_asset.stagger_sec` adiciona um atraso por scope para evitar picos simultâneos (I/O e chamadas ao broker/API).

- **Modo paralelo** (`workers > 1`):
  - scope `i` inicia após `i * stagger_sec`
- **Modo sequencial** (`workers == 1`):
  - scope `i>0` adiciona um atraso constante de `stagger_sec` antes de iniciar

Recomendação prática:
- comece com `stagger_sec: 0.0`
- se notar burst/rate-limit, teste `0.2` a `1.0`

## Exemplo de configuração

```yaml
multi_asset:
  enabled: true
  max_parallel_assets: 2
  stagger_sec: 0.5

  portfolio_topk_total: 2
  portfolio_hard_max_positions: 2
  portfolio_hard_max_pending_unknown_total: 1
  portfolio_hard_max_positions_per_asset: 1
  portfolio_hard_max_positions_per_cluster: 1
  correlation_filter_enable: true
  max_trades_per_cluster_per_cycle: 1

  partition_data_paths: true
  data_db_template: data/market_{scope_tag}.sqlite3
  dataset_path_template: data/datasets/{scope_tag}/dataset.csv
```



## Risk engine do portfólio (Package M4)

O M4 adiciona uma camada de risco agregada por portfólio, acima das quotas
por *scope*:

- **quota global forte**: `portfolio_hard_max_pending_unknown_total` pode
  bloquear novas seleções enquanto houver submits ainda ambíguos;
- **exposure cap por asset**: `portfolio_hard_max_positions_per_asset` evita
  duplicar exposição no mesmo ativo em múltiplos timeframes;
- **exposure / correlation cap por cluster**:
  `portfolio_hard_max_positions_per_cluster` usa `cluster_key` como grupo de
  correlação e impede empilhar posições altamente correlacionadas;
- **cluster cap por ciclo**: `max_trades_per_cluster_per_cycle` continua
  limitando quantas novas seleções podem sair no mesmo ciclo.

Os artefatos do allocator agora incluem:

- `portfolio_quota` com breakdown por `asset` e `cluster`
- `risk_summary` com exposições abertas/pending e seleções do ciclo
- motivos explícitos de supressão como:
  - `asset_exposure_cap:<asset>`
  - `correlation_cluster_cap:<cluster>`
  - `portfolio_capacity_reached`

## Observações

- O particionamento de **runtime DBs** (signals/state) é feito por `scope_tag` quando `multi_asset.enabled: true`.
- Mesmo com execução desabilitada (`execution.enabled: false`), o ciclo roda e grava:
  - `runs/portfolio_cycle_latest.json`
  - `runs/allocation_latest.json`
  - `runs/live_signals_v2_YYYYMMDD_<scope_tag>.csv`
## Pacing de requisições (opcional)

Além do `multi_asset.stagger_sec`, existe um **throttle cross-process** no `IQClient` (Package T) para reduzir bursts de chamadas quando você roda múltiplos subprocessos em paralelo.

Configuração via env vars:

- `IQ_THROTTLE_MIN_INTERVAL_S` (float; default `0.0`) — intervalo mínimo entre *inícios* de chamadas (entre processos).
- `IQ_THROTTLE_JITTER_S` (float; default `0.0`) — jitter aleatório adicional (0..jitter) para espalhar chamadas.
- `IQ_THROTTLE_STATE_FILE` (path; default `runs/iq_throttle_state.json`) — arquivo de estado compartilhado.

> Importante: isto é **para estabilidade** (suavizar carga / reduzir risco de rate-limit), não para evasão.
