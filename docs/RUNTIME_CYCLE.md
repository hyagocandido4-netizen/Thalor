# Runtime cycle (Package I)

O Package I introduz uma camada Python **additiva** para representar um ciclo do
runtime do Thalor sem substituir de imediato os loops PowerShell.

Arquivo principal:

- `src/natbin/runtime_cycle.py`

## Objetivo

Criar uma descrição canônica, serializável e testável de um ciclo `AUTO PREPARE + OBSERVE`
para reduzir dependência futura da orquestração em shell.

## O que a camada fornece

- `StepCommand` — comando/timeout/cwd de cada etapa
- `StepOutcome` — resultado classificado da etapa
- `build_auto_cycle_plan(...)` — plano canônico do ciclo
- `run_step(...)` / `run_plan(...)` — execução opcional em Python
- CLI: `python -m natbin.runtime_cycle`

## Etapas do plano atual

1. `collect_recent`
2. `make_dataset`
3. `refresh_daily_summary`
4. `refresh_market_context`
5. `auto_volume`
6. `auto_isoblend`
7. `auto_hourthr`
8. `observe_loop_once`

A última etapa ainda chama `scripts/scheduler/observe_loop.ps1 -Once -SkipCollect -SkipDataset`,
ou seja: este pacote é **fundação**, não substituição total do scheduler.

## Exemplos

### Inspecionar o plano

```powershell
python -m natbin.runtime_cycle --repo-root . --topk 3 --lookback-candles 2000
```

### Emitir plano em JSON

```powershell
python -m natbin.runtime_cycle --repo-root . --topk 3 --lookback-candles 2000 --json
```

### Executar o plano sequencialmente (experimental)

```powershell
python -m natbin.runtime_cycle --repo-root . --topk 3 --lookback-candles 2000 --run --json
```

## Smoke

- `scripts/tools/runtime_cycle_smoke.py`

Valida:
- shape do plano
- argumentos do `observe_loop_once`
- classificador de resultados
- CLI `--json`

## Status

Esta camada ainda é **opt-in** e serve para preparar o pacote seguinte, onde a
orquestração vai migrando do shell para Python de forma controlada.
