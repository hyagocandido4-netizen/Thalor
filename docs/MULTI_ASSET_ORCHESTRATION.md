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
   - `portfolio_allocator` (aplica quotas e escolhe os melhores candidatos)
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
  portfolio_hard_max_positions: 1

  partition_data_paths: true
  data_db_template: data/market_{scope_tag}.sqlite3
  dataset_path_template: data/datasets/{scope_tag}/dataset.csv
```

## Observações

- O particionamento de **runtime DBs** (signals/state) é feito por `scope_tag` quando `multi_asset.enabled: true`.
- Mesmo com execução desabilitada (`execution.enabled: false`), o ciclo roda e grava:
  - `runs/portfolio_cycle_latest.json`
  - `runs/allocation_latest.json`
  - `runs/live_signals_v2_YYYYMMDD_<scope_tag>.csv`
