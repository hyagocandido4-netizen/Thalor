# Package R: Execution Layer v2

This repo includes an execution module (`natbin.runtime.execution`) that can turn an approved trade signal into a broker order (or explicitly skip it), while persisting all intent/attempt/history into a local SQLite repository.

Key goals:

- **Auditable**: every trade decision is represented as an *intent* with events and (optional) broker orders.
- **Safe by default**: multiple gates exist (kill switch, drain mode, broker health, entry deadline).
- **Works in CI**: a fake broker adapter provides deterministic behavior without credentials.

## Modes

### Disabled (execution.enabled: false)

- No broker adapter is instantiated.
- The latest trade signal still produces an **intent** and a corresponding **intent_blocked** event with:
  - `reason = execution_disabled`
- This ensures portfolio runs remain auditable even when execution is turned off.

### Paper (execution.mode: paper)

- Uses the broker adapter but submits with `simulate=True`.
- Intended for end-to-end verification of the execution pipeline without placing real trades.

### Live (execution.mode: live)

- Submits real orders via the configured broker adapter.
- Broker integration is currently:
  - `fake` adapter (CI/local)
  - `iqoption` adapter stub (placeholder for real integration)

## CLI

The execution module is typically invoked as a subprocess from the portfolio runtime:

- `python -m natbin.runtime.execution process --config config/base.yaml`
- `python -m natbin.runtime.execution orders --config config/base.yaml`
- `python -m natbin.runtime.execution reconcile --config config/base.yaml`

## Persistence

Execution state is stored in:

- `runs/runtime_execution.sqlite3`

Tables:

- `order_intents`: planned/blocked/submitted/settled intent state
- `order_submit_attempts`: submission transport attempts
- `broker_orders`: broker-side order snapshots
- `order_events`: append-only event history

## CI

A lightweight smoke test validates the disabled + live (fake broker) paths:

- `scripts/ci/smoke_execution_layer.py`

It is executed by `.github/workflows/integrity.yml`.
