# Catálogo de variáveis de ambiente (ENV)

> As env vars são **strings**. Use `""`/unset para “vazio”.

## Credenciais / Conta

- `IQ_EMAIL` (obrigatório)
- `IQ_PASSWORD` (obrigatório)
- `IQ_BALANCE_MODE` (`PRACTICE` | `REAL`) — recomendado `PRACTICE`

## Scheduler / Loop

- `LOOP_STATUS_ENABLE` (`1/0`) — grava status JSON do loop
- `LOOP_LOG_RETENTION_DAYS` (ex.: `14`) — rotação/limpeza de logs do loop
- `RUNTIME_RETENTION_DAYS` (ex.: `30`) — prune de artefatos em `runs/` e linhas em SQLite
- `STATE_RECONCILE_DAYS` (ex.: `7`) — reconciliação de estado com fonte de verdade
- `COLLECT_RECENT_TIMEOUT_SEC` (ex.: `120`) — timeout duro do `collect_recent` no auto-loop
- `MAKE_DATASET_TIMEOUT_SEC` (ex.: `120`) — timeout duro do `make_dataset` no auto-loop
- `REFRESH_DAILY_SUMMARY_TIMEOUT_SEC` (ex.: `90`) — timeout do refresh de summaries
- `REFRESH_MARKET_CONTEXT_TIMEOUT_SEC` (ex.: `60`) — timeout do refresh de payout/open
- `AUTO_VOLUME_TIMEOUT_SEC` / `AUTO_ISOBLEND_TIMEOUT_SEC` / `AUTO_HOURTHR_TIMEOUT_SEC` — timeout dos autos
- `OBSERVE_LOOP_TIMEOUT_SEC` (ex.: `180`) — timeout do observe subprocess no auto-loop

## Top‑K

- `TOPK_ROLLING_MINUTES` (ex.: `360`) — janela rolling para ranking
- `TOPK_MIN_GAP_MINUTES` (ex.: `30`) — cooldown mínimo entre trades
- `TOPK_PACING_ENABLE` (`1/0`) — habilita quota/pacing diário

## Volume / Auto‑tune

- `VOL_TARGET_TRADES_PER_DAY` (ex.: `3`) — alvo aproximado de trades/dia

> Observação: o auto‑tune pode ajustar threshold/alpha/blend com base em métricas recentes.

## Gates e fail-safe

- `GATE_FAIL_CLOSED` (`1/0`) — se gate falhar ou estiver inconsistente, força `HOLD`
- `MARKET_CONTEXT_FAIL_CLOSED` (`1/0`) — se payout/open não puder ser confiável, força `HOLD`

## Market context (payout/open)

- `PAYOUT` — override manual (normalmente não precisa)
- `MARKET_OPEN` — override manual (normalmente não precisa)

O loop tenta capturar payout/open automaticamente e mantém cache com informações:

- `MARKET_CONTEXT_FRESH`
- `MARKET_CONTEXT_AGE_SEC`
- `MARKET_CONTEXT_STALE`
- `MARKET_CONTEXT_SOURCE`

## Dataset / Observer

- `LOOKBACK_CANDLES` — candles para observar/coletar
- `RETRAIN_EVERY_CANDLES` — re-treino periódico do modelo (cache)
- `LIVE_SIGNALS_PATH` — override do CSV de saída

## Daily summary

- `SUMMARY_LEGACY_FALLBACK` (`1/0`) — permite fallback para summaries legados (menos seguro)
- `SUMMARY_REQUIRE_TIMEZONE` (`1/0`) — exige timezone coerente no summary

## IQ client (opcional)

- `IQ_MARKET_OPEN_USE_API` (`1/0`) — evita rotas ruidosas do `iqoptionapi` por padrão

---

> Nota: o catálogo acima cobre as variáveis mais comuns.
> O projeto possui outras env vars internas; quando uma nova variável virar “contrato”,
> ela deve ser documentada aqui.
