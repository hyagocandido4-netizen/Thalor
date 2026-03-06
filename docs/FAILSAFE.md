# FAILSAFE (Package M4 + Package P)

This package introduces the first explicit failsafe kernel for Thalor v2.0.

## Components
- `runtime_failsafe.py`
- `runtime_control_repo.py`

## Features
- Global fail-closed gate
- Kill-switch via file or env
- Per-asset circuit breaker
- Market-context stale fail-closed
- Runtime control SQLite for breaker state

## Package P additions

### Drain mode

**Drain mode** bloqueia *novas submissões* (novos trades), mas deixa a
**reconciliação** rodar.

Ativação:

* arquivo: `runs/DRAIN_MODE`
* env var: `THALOR_DRAIN_MODE=1`

### Ops CLI (runtime_app ops)

Package P adiciona comandos para operar kill-switch e drain mode:

```bash
python -m natbin.control.app ops killswitch status
python -m natbin.control.app ops killswitch on --reason "maintenance"
python -m natbin.control.app ops killswitch off

python -m natbin.control.app ops drain status
python -m natbin.control.app ops drain on --reason "broker latency"
python -m natbin.control.app ops drain off
```

## Intent
This package is additive and prepares the cutover where `runtime_daemon`
consults a single failsafe kernel before running collection / autos / observe.
