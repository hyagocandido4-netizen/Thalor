# Catálogo de variáveis de ambiente (ENV)

> As env vars são **strings**. Use `""`/unset para “vazio”.

## Credenciais / Conta

### Fluxo preferido (Package M6)

- `THALOR_SECRETS_FILE` — aponta para bundle YAML / JSON / KEY=VALUE com `broker.email`, `broker.password` e opcionalmente `broker.balance_mode`
- `THALOR_BROKER_EMAIL_FILE` — arquivo contendo apenas o email
- `THALOR_BROKER_PASSWORD_FILE` — arquivo contendo apenas a senha

Observação importante: os secret files são aplicados **depois** do merge normal
de config/env e, para credenciais do broker, passam a valer como override final.
Se `THALOR_BROKER_EMAIL_FILE` / `THALOR_BROKER_PASSWORD_FILE` e
`THALOR_SECRETS_FILE` coexistirem, os arquivos separados vencem o bundle.

### Compatibilidade legado

- `IQ_EMAIL`
- `IQ_PASSWORD`
- `IQ_BALANCE_MODE` (`PRACTICE` | `REAL`) — recomendado `PRACTICE`

## Security posture / guard (Package M6)

- `THALOR__SECURITY__DEPLOYMENT_PROFILE` (`local` | `ci` | `live`)
- `THALOR__SECURITY__LIVE_REQUIRE_EXTERNAL_CREDENTIALS` (`1/0`)
- `THALOR__SECURITY__ALLOW_EMBEDDED_CREDENTIALS` (`1/0`)
- `THALOR__SECURITY__AUDIT_ON_CONTEXT_BUILD` (`1/0`)
- `THALOR__SECURITY__GUARD__ENABLED` (`1/0`)
- `THALOR__SECURITY__GUARD__LIVE_ONLY` (`1/0`)
- `THALOR__SECURITY__GUARD__MIN_SUBMIT_SPACING_SEC`
- `THALOR__SECURITY__GUARD__MAX_SUBMIT_PER_MINUTE`
- `THALOR__SECURITY__GUARD__TIME_FILTER_ENABLE` (`1/0`)
- `THALOR__SECURITY__GUARD__ALLOWED_START_LOCAL` (`HH:MM`)
- `THALOR__SECURITY__GUARD__ALLOWED_END_LOCAL` (`HH:MM`)
- `THALOR__SECURITY__GUARD__BLOCKED_WEEKDAYS_LOCAL` (lista; `0=segunda`, `6=domingo`)

## Broker pacing / throttling

Preferencialmente use config tipada:

- `broker.api_throttle_min_interval_s`
- `broker.api_throttle_jitter_s`

Compatibilidade / fallback legado ainda aceitos pelo IQ client:

- `IQ_THROTTLE_MIN_INTERVAL_S`
- `IQ_THROTTLE_JITTER_S`
- `IQ_CONNECT_RETRIES`
- `IQ_CONNECT_SLEEP_S`
- `IQ_CONNECT_SLEEP_MAX_S`
- `IQ_CALL_RETRIES`
- `IQ_CALL_SLEEP_S`

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

## IQ execution bridge (Package M2+)

- `IQ_EMAIL` / `IQ_PASSWORD` — compatibilidade para credenciais do broker live
- `IQ_BALANCE_MODE` (`PRACTICE` | `REAL`) — fallback de conta quando o config não preencher
- `IQ_EXEC_BUY_RETRIES`, `IQ_EXEC_BUY_SLEEP_S`, `IQ_EXEC_BUY_SLEEP_MAX_S` — retry/backoff de submit live
- `IQ_EXEC_BETINFO_RETRIES`, `IQ_EXEC_HISTORY_RETRIES` — retries do reconcile live
- `IQ_EXEC_HISTORY_TIMEOUT_S`, `IQ_EXEC_HISTORY_COOLDOWN_S` — guard rail para `get_optioninfo_v2()` não travar o runtime indefinidamente
- `IQ_EXEC_ASYNC_RETRIES` — leitura do stream assíncrono local do `iqoptionapi`
- `THALOR_RECONCILE_SCAN_WITHOUT_PENDING` (`1/0`) — força scan completo do broker mesmo sem intents pendentes

Observação: o caminho live usa também o arquivo local `runs/iqoption_bridge_state.json`
para sobreviver a restart curto do processo e manter a reconciliação determinística.

---

> Nota: o catálogo acima cobre as variáveis mais comuns.
> O projeto possui outras env vars internas; quando uma nova variável virar “contrato”,
> ela deve ser documentada aqui.


## Alerting (M7)

- `THALOR_TELEGRAM_BOT_TOKEN`
- `THALOR_TELEGRAM_CHAT_ID`
- `THALOR_TELEGRAM_BOT_TOKEN_FILE`
- `THALOR_TELEGRAM_CHAT_ID_FILE`
- `THALOR__NOTIFICATIONS__TELEGRAM__ENABLED`
- `THALOR__NOTIFICATIONS__TELEGRAM__SEND_ENABLED`
