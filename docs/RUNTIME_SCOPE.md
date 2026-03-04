# Runtime scope & performance helpers

Package H introduces two low-risk runtime utilities:

- `src/natbin/runtime_scope.py`
- `src/natbin/runtime_perf.py`

## Objetivo

Reduzir duplicação de naming/path de artefatos escopados por:

- `asset`
- `interval_sec`

E evitar recomputações/IO desnecessários dentro do mesmo processo.

## `runtime_scope.py`

Define a camada canônica de resolução de paths para artefatos escopados:

- `effective_env_<asset>_<interval>.json`
- `market_context_<asset>_<interval>.json`
- `observe_loop_auto_status_<asset>_<interval>.json`
- `live_signals_v2_<day>_<asset>_<interval>.csv`
- `observe_loop_auto_<asset>_<interval>_<day>.log`
- snapshots/incidents de observabilidade

## `runtime_perf.py`

Implementa helpers conservadores:

- `apply_runtime_sqlite_pragmas()`
- `load_json_cached()`
- `write_text_if_changed()`

### Notas

- `load_json_cached()` invalida por `mtime_ns + size`
- retorna cópia profunda para evitar mutação acidental do cache
- `write_text_if_changed()` evita rewrites idênticos em sidecars/snapshots
- os pragmas SQLite são intencionalmente conservadores (`WAL`, `NORMAL`, `busy_timeout`)

## Escopo do pacote

Este pacote **não** muda a policy do bot.
Ele só consolida paths escopados e reduz custo de IO/reparse.
