# H7 / E2E Broker Dependency Closeout

## Goal

Allow the runtime pipeline to degrade gracefully when the optional `iqoptionapi`
package is not installed yet.

## What changed

- `src/natbin/adapters/iq_client.py`
  - lazy dependency resolution for `iqoptionapi`
  - explicit `IQDependencyUnavailable`
  - `iqoption_dependency_status()` helper
  - deterministic smoke toggle: `THALOR_FORCE_IQOPTIONAPI_MISSING=1`

- `src/natbin/usecases/collect_recent.py`
  - no more import-time crash when broker package is absent
  - falls back to local DB snapshot + market context cache
  - succeeds only when there is already local candle data for the current scope

- `src/natbin/usecases/refresh_market_context.py`
  - emits a conservative market-context payload from local DB/cache without the broker package

- `src/natbin/brokers/iqoption.py`
  - `healthcheck()` now reports `iqoption_dependency_missing` explicitly

## Validation

- `pytest -q tests/test_h7_broker_dependency.py`
- `python scripts/tools/h7_broker_dependency_closeout_smoke.py`

## Operational meaning

Without `iqoptionapi` installed:

- `collect_recent` no longer dies with `ModuleNotFoundError`
- `refresh_market_context` still writes a valid scoped payload
- runtime diagnostics can keep working
- the live bridge reports dependency-missing instead of a misleading connect failure

This is a dependency closeout, not a live broker implementation replacement.
Fresh remote candle collection still requires the IQ stack to be installed.
