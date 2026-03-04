# Runtime quota / pacing (Package K)

O Package K extrai a política de quota/pacing do daemon Python para um módulo
explícito:

- `src/natbin/runtime_quota.py`

## Objetivo

Permitir que a orquestração Python conheça:

- quantos trades já foram emitidos no dia (`signals_v2` como fonte de verdade)
- quanto do budget está liberado **neste momento** (`allowed_now`)
- quando é a próxima janela relevante (`next_at` / `next_wake_utc`)

sem reimplementar a política em SQL ou em parsing frágil de logs.

## API principal

- `pacing_allowed(...)`
- `compute_quota_day_context(...)`
- `build_quota_snapshot(...)`

## Snapshot

`QuotaSnapshot` expõe:

- `kind`
  - `open`
  - `pacing_quota_reached`
  - `max_k_reached_today`
- `executed`
- `allowed_now`
- `allowed_total`
- `budget_left_now`
- `budget_left_total`
- `next_at`
- `next_wake_utc`
- `sleep_sec`

## Integração com o daemon Python

`natbin.runtime_daemon` agora suporta:

- `--quota-json`
- `--quota-aware-sleep`

O modo `--quota-aware-sleep` é **aditivo** e ainda não substitui o scheduler
operacional principal em PowerShell; ele apenas aproxima a fundação Python da
paridade de comportamento.
