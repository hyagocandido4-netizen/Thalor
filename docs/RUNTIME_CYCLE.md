# Runtime cycle (Package M baseline)

The runtime cycle is now a **Python-authoritative plan**.

Main module:

- `src/natbin/runtime/cycle.py`

Compatibility shim:

- `src/natbin/runtime_cycle.py`

## What changed in Package M

The canonical plan no longer depends on PowerShell for the observer step.
The final step is now a Python module:

1. `collect_recent`
2. `make_dataset`
3. `refresh_daily_summary`
4. `refresh_market_context`
5. `auto_volume`
6. `auto_isoblend`
7. `auto_hourthr`
8. `observe_loop_once` -> `python -m natbin.runtime.observe_once`

PowerShell wrappers still exist, but only as thin bootstrap entrypoints for
operators / Task Scheduler.

## CLI

Inspect the plan:

```powershell
python -m natbin.runtime_cycle --repo-root . --topk 3 --lookback-candles 2000 --json
```

Run the plan sequentially:

```powershell
python -m natbin.runtime_cycle --repo-root . --topk 3 --lookback-candles 2000 --run --json
```

## Smoke

- `scripts/tools/runtime_cycle_smoke.py`
