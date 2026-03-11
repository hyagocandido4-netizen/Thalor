# Package R / M2: Execution Layer v2

This repo includes an execution module (`natbin.runtime.execution`) that can turn
an approved trade signal into a broker order (or explicitly skip it), while
persisting all intent/attempt/history into a local SQLite repository.

Key goals:

- **Auditable**: every trade decision is represented as an *intent* with events and (optional) broker orders.
- **Safe by default**: multiple gates exist (kill switch, drain mode, broker health, entry deadline).
- **Works in CI**: a fake broker adapter provides deterministic behaviour without credentials.
- **Live-capable**: Package M2 closes the real IQ Option adapter on the new execution contract for binary/turbo orders.

## Modes

### Disabled (`execution.enabled: false`)

- No broker adapter is instantiated.
- The latest trade signal still produces an **intent** and a corresponding **intent_blocked** event with:
  - `reason = execution_disabled`
- This ensures portfolio runs remain auditable even when execution is turned off.

### Paper (`execution.mode: paper`)

- Recommended provider: `fake`.
- The pipeline remains fully auditable without placing real trades.
- If `provider: iqoption` is used together with `mode: paper`, the adapter stays **fail-closed** and rejects submit attempts by design.

### Live (`execution.mode: live`)

- `provider: fake` — deterministic local/CI path.
- `provider: iqoption` — real binary/turbo bridge implemented in Package M2.

## IQ Option live bridge (Package M2)

The adapter `natbin.brokers.iqoption.IQOptionAdapter` now supports:

- real `healthcheck()` against a live IQ client/session
- real `submit_order()` for binary/turbo options (`CALL` / `PUT`)
- `fetch_order()` with layered reconciliation:
  1. async/socket order state from the current session
  2. `get_betinfo()` terminal result
  3. `get_optioninfo_v2()` recent closed history
  4. local grace-window fallback until `expiry + settle_grace_sec`
- `fetch_open_orders()` / `fetch_closed_orders()` for reconcile sweeps
- local bridge-state persistence in `runs/iqoption_bridge_state.json`

This state file is intentionally simple and append-safe enough for operational
restarts: it stores the external order id plus deterministic request metadata so
reconciliation remains possible after a process restart.

## CLI

The execution module is typically invoked as a subprocess from the portfolio runtime:

- `python -m natbin.runtime.execution process --config config/base.yaml`
- `python -m natbin.runtime.execution orders --config config/base.yaml`
- `python -m natbin.runtime.execution reconcile --config config/base.yaml`

## Persistence

Execution state is stored in:

- `runs/runtime_execution.sqlite3`
- `runs/iqoption_bridge_state.json` (live IQ bridge only)

Tables:

- `order_intents`: planned/blocked/submitted/settled intent state
- `order_submit_attempts`: submission transport attempts
- `broker_orders`: broker-side order snapshots
- `order_events`: append-only event history

## Caveats / current boundaries

- Current live bridge targets **binary/turbo option flow** (the same family used by the existing 1m/5m OTC runtime).
- Broker-side orphan discovery is **best-effort**. Orders seen in the current session and recent closed history are surfaced; deeper historical backfill remains an ops-hardening topic for later packages.
- The bridge keeps accepted orders OPEN until `expiry + settle_grace_sec` when broker telemetry is temporarily unavailable, avoiding premature `not_found` transitions during normal settlement lag.

## CI

A lightweight smoke test validates the disabled + live (fake broker) paths:

- `scripts/ci/smoke_execution_layer.py`
- `scripts/tools/broker_adapter_contract_smoke.py`
- `tests/test_iqoption_adapter.py`
