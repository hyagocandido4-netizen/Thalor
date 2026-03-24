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

The observer runtime now resolves the typed config directly, using the same
loader as the control plane. This closes the old split where `config/base.yaml`
and `config.yaml` could disagree on `threshold`, `tune_dir` or regime bounds.

`config.yaml` is still supported, but only in two cases:

- it is passed explicitly as the selected config path
- `config/base.yaml` does not exist and repo resolution falls back to the legacy file

So the compatibility file remains available, but it is no longer a second hidden
source read separately by the observer.

## Legacy observer bridge (RCF-1)

RCF-1 formalizes the remaining observer knobs inside the typed config and exports
them back into the legacy env contract only at runtime.

Representative keys:

```yaml
decision:
  threshold: 0.02
  cp_alpha: null
  tune_dir: runs/tune_mw_topk_20260223_222559
  bounds:
    vol_lo: 0.001960
    vol_hi: 0.006510
    bb_lo: 0.008000
    bb_hi: 0.039000
    atr_lo: 0.000800
    atr_hi: 0.003100
  cpreg:
    enabled: false
    alpha_start: 0.06
    alpha_end: 0.09
    warmup_frac: 0.50
    ramp_end_frac: 0.90
    slot2_mult: 0.85

runtime_overrides:
  threshold: null
  cp_alpha: null
  cpreg_enable: null
  cpreg_alpha_start: null
  cpreg_alpha_end: null
  cpreg_slot2_mult: null
  meta_iso_blend: null
  regime_mode: null
  payout: null
  market_open: null
```

At runtime, `prepare_observer_environment()` maps the resolved config back to
legacy env variables such as `THRESHOLD`, `CP_ALPHA`, `CPREG_*`,
`META_ISO_BLEND`, `REGIME_MODE`, `PAYOUT` and `MARKET_OPEN`. This keeps the
legacy observer path working without reintroducing `config.yaml` as a second
implicit source of truth.


## Intelligence section (H11 / H12)

The typed config also carries the Phase 1 intelligence policy surface used by
pack build, runtime enrichment and allocator scoring.

Representative keys:

```yaml
intelligence:
  learned_stacking_enable: true
  learned_promote_above: 0.62
  learned_suppress_below: 0.42
  learned_abstain_band: 0.03
  learned_calibration_enable: true
  learned_min_reliability: 0.50
  stack_max_bonus: 0.05
  stack_max_penalty: 0.05
  portfolio_weight: 1.0
  allocator_block_regime: true
  allocator_warn_penalty: 0.04
  allocator_block_penalty: 0.12
  allocator_under_target_bonus: 0.03
  allocator_over_target_penalty: 0.04
  allocator_retrain_penalty: 0.05
  allocator_reliability_penalty: 0.03
  retrain_plan_cooldown_hours: 24
  retrain_watch_reliability_below: 0.55
  retrain_queue_on_regime_block: true
  retrain_queue_on_anti_overfit_reject: true
  anti_overfit_enable: true
  anti_overfit_min_robustness: 0.50
  anti_overfit_min_windows: 3
  anti_overfit_gap_penalty_weight: 0.10
  anti_overfit_tuning_enable: true
  anti_overfit_tuning_min_robustness_floor: 0.45
  anti_overfit_tuning_window_flex: 1
  anti_overfit_tuning_gap_penalty_flex: 0.03
  anti_overfit_tuning_recent_rows_min: 48
  anti_overfit_tuning_objective_min_delta: 0.015
  scope_policies: []
```

The new H12 artifacts written from this section are:

- `runs/intelligence/<scope_tag>/retrain_plan.json`
- `runs/intelligence/<scope_tag>/retrain_status.json`

And the allocator now prefers `portfolio_score` over `intelligence_score` when
that field is available on the candidate.

P22 adds a constrained anti-overfit tuning sweep during `fit_intelligence_pack`.
The sweep materializes `runs/intelligence/<scope_tag>/anti_overfit_tuning.json`,
keeps the baseline as a candidate, and only switches to a tuned variant when the
objective improvement is large enough or the tuned variant flips the guard from
rejected to accepted.

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
