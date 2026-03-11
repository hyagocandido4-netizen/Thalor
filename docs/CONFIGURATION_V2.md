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

Base settings resolution still follows:

1. process `THALOR__*` / init overrides
2. `.env` `THALOR__*`
3. compatibility keys (`IQ_*`, `ASSET`, `INTERVAL_SEC`, `TIMEZONE`)
4. YAML (`config/base.yaml` preferred, `config.yaml` fallback)

### External secrets overlay (Package M6)

After the normal merge above, the loader may optionally overlay broker
credentials from external files:

- `THALOR_BROKER_EMAIL_FILE` / `THALOR_BROKER_PASSWORD_FILE`
- `THALOR_SECRETS_FILE` or `security.secrets_file`

Within this overlay phase, the separate email/password files win over the
secret bundle, and both win over values already loaded from env/YAML.

## Important compatibility note

The legacy observer still uses `config.yaml` for model/tuning fields that are
not yet represented in the typed schema. Therefore Package M keeps both files:

- `config/base.yaml` for the control plane
- `config.yaml` for legacy observer compatibility

This is an intentional transitional baseline and not yet the final cleanup.

## Execution layer (Package N / M2 baseline)

The typed config includes an optional execution section. The control plane
remains compatible when execution is disabled, but when enabled the runtime now
also resolves:

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

## Security section (Package M6)

Package M6 adds a typed `security:` block to the canonic config:

```yaml
security:
  deployment_profile: local
  allow_embedded_credentials: false
  live_require_credentials: true
  live_require_external_credentials: false
  secrets_file: null
  audit_on_context_build: true
  guard:
    enabled: true
    live_only: true
    min_submit_spacing_sec: 10
    max_submit_per_minute: 4
    time_filter_enable: false
    allowed_start_local: "00:00"
    allowed_end_local: "23:59"
    blocked_weekdays_local: []
```

Main intents:

- formalize deployment profile (`local` / `ci` / `live`)
- discourage embedded broker credentials in YAML
- support external secret files as the preferred live path
- produce a control-plane `security.json` artifact
- block bursty / mistimed live submits through the broker guard

## Broker throttling knobs (Package M6)

The broker section also gains typed pacing knobs for the IQ adapter:

```yaml
broker:
  api_throttle_min_interval_s: 0.10
  api_throttle_jitter_s: 0.05
```

They are mapped into the compatibility env expected by the existing IQ client
only while the adapter is performing its connect/call flow.
