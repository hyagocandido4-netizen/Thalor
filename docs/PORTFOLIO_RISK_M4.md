# Portfolio Risk Engine (M4)

O M4 fecha a primeira versão de risco agregado do modo multi-asset.

## Objetivo

Evitar que o portfolio runner tome decisões boas **individualmente** mas ruins
**no agregado** por excesso de correlação, duplicação do mesmo ativo em vários
timeframes ou acúmulo de submits pendentes.

## O que entrou

- `compute_portfolio_quota` agora calcula:
  - breakdown por `asset`
  - breakdown por `cluster_key`
  - headroom global de `pending_unknown`
- `allocator.allocate` agora considera:
  - `portfolio_hard_max_pending_unknown_total`
  - `portfolio_hard_max_positions_per_asset`
  - `portfolio_hard_max_positions_per_cluster`
  - `correlation_filter_enable`
  - `max_trades_per_cluster_per_cycle`
- o artefato `portfolio_allocation_latest.json` passa a incluir:
  - `portfolio_quota`
  - `asset_quotas`
  - `risk_summary`

## Semântica

### `portfolio_hard_max_pending_unknown_total`

Quota global forte. Se houver submits ainda no estado `submitted_unknown`,
o portfolio pode:
- bloquear o ciclo inteiro quando o total já estourou;
- reduzir `max_allowed` do allocator pelo headroom restante.

### `portfolio_hard_max_positions_per_asset`

Cap cross-timeframe. Exemplo:
- `EURUSD-OTC@300s` já está aberto
- `EURUSD-OTC@60s` surge como novo candidato

Com cap = `1`, o segundo candidato é suprimido com:
`asset_exposure_cap:EURUSD-OTC`

### `portfolio_hard_max_positions_per_cluster`

Cap de correlação usando `cluster_key` como grupo.

Exemplo:
- `EURUSD-OTC` e `USDJPY-OTC` ambos no cluster `fx`
- já existe 1 exposição `fx` ativa
- novo candidato `fx` é suprimido com:
`correlation_cluster_cap:fx`

### `max_trades_per_cluster_per_cycle`

Mesmo sem posição aberta prévia, o ciclo ainda evita abrir mais de N seleções
novas no mesmo cluster.

## Config recomendada (baseline conservador)

```yaml
multi_asset:
  enabled: true
  portfolio_topk_total: 3
  portfolio_hard_max_positions: 2
  portfolio_hard_max_pending_unknown_total: 1
  portfolio_hard_max_positions_per_asset: 1
  portfolio_hard_max_positions_per_cluster: 1
  correlation_filter_enable: true
  max_trades_per_cluster_per_cycle: 1
```

## Observabilidade

No dashboard / status do portfólio, procure:

- `portfolio_quota.open_positions_by_asset`
- `portfolio_quota.pending_unknown_by_asset`
- `portfolio_quota.open_positions_by_cluster`
- `portfolio_quota.pending_unknown_by_cluster`
- `risk_summary.selected_by_asset`
- `risk_summary.selected_by_cluster`

## Testes

- `tests/test_portfolio_risk_m4.py`
- `scripts/tools/portfolio_risk_smoke.py`

