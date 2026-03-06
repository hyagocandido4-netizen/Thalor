# Configuration v2 (Package M)

Package M closes the typed configuration foundation for the control plane.

## Preferred path

The control plane now prefers:

- `config/base.yaml`

Legacy fallback:

- `config.yaml`

## Resolution rules

When `repo_root` is provided, config resolution is anchored to that root for:

- `config/base.yaml`
- `config.yaml`
- `.env`
- `runs/`

## Precedence

1. CLI / init overrides
2. process `THALOR__*`
3. `.env` `THALOR__*`
4. compatibility keys (`IQ_*`, `ASSET`, `INTERVAL_SEC`, `TIMEZONE`)
5. YAML (`config/base.yaml` preferred, `config.yaml` fallback)

## Important compatibility note

The legacy observer still uses `config.yaml` for model/tuning fields that are
not yet represented in the typed schema. Therefore Package M keeps both files:

- `config/base.yaml` for the control plane
- `config.yaml` for legacy observer compatibility

This is an intentional transitional baseline and not yet the final cleanup.


## Execution layer (Package N)

Package N adds an optional execution section to the typed config. The control
plane remains compatible when execution is disabled, but when enabled the
runtime now also resolves:

- `execution.enabled`
- `execution.mode`
- `execution.provider`
- `execution.account_mode`
- `execution.stake.*`
- `execution.submit.*`
- `execution.reconcile.*`
- `execution.limits.*`
- `execution.fake.*`

When `execution.enabled: true`, quota and precheck use the execution ledger
(`runs/runtime_execution.sqlite3`) instead of the legacy executed cache as the
authoritative source for open positions, pending unknown submits and quota
consumption.
