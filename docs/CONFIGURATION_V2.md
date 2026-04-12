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

Base settings resolution now follows:

1. process `THALOR__*` / init overrides
2. process compatibility keys (`IQ_*`, `ASSET`, `INTERVAL_SEC`, `TIMEZONE`)
3. repo-local `.env` `THALOR__*` **safe keys only** (for example `broker.*`,
   `security.*`, `notifications.*`, `production.*`)
4. YAML (`config/base.yaml` preferred, `config.yaml` fallback, including explicit profile files)
5. repo-local `.env` compatibility keys (`IQ_*`, `ASSET`, `INTERVAL_SEC`, `TIMEZONE`)

Trading-behaviour sections such as `execution.*`, `decision.*`, `quota.*`,
`runtime.*`, `multi_asset.*`, `intelligence.*` and `runtime_overrides.*` are
filtered out of the repo-local `.env` by default. This keeps profile YAMLs such
as `config/live_controlled_practice.yaml` authoritative for behaviour, while the
`.env` remains useful for secrets, account mode and deployment posture.

If you intentionally want the historical behaviour where repo-local `.env`
`THALOR__*` can override any field, export this flag in the **process** before
starting the app:

```powershell
$env:THALOR_DOTENV_ALLOW_BEHAVIOR="1"
```

## Profile inheritance (`extends`)

Modern YAML profiles can now inherit from one or more parent files:

```yaml
extends: base.yaml
runtime:
  profile: multi
execution:
  enabled: true
  mode: paper
```

Merge semantics are recursive for nested mappings. Lists and scalar values are
replaced by the child config. The effective `source_trace` keeps the full YAML
chain (base -> child), which improves auditability in control-plane payloads.

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


## Account protection section (Package PROTECTION-1)

Package PROTECTION-1 adds a second typed safety layer under `security:`:

```yaml
security:
  protection:
    enabled: true
    live_submit_only: true
    state_path: runs/security/account_protection_state.json
    decision_log_path: runs/logs/account_protection.jsonl
    sessions:
      enabled: true
      inherit_guard_window: true
      blocked_weekdays_local: []
      windows: []
    cadence:
      enabled: true
      apply_delay_before_submit: true
      min_delay_sec: 0.5
      max_delay_sec: 2.5
      early_morning_extra_sec: 0.5
      midday_extra_sec: 0.0
      evening_extra_sec: 0.25
      overnight_extra_sec: 0.75
      volatility_extra_sec: 0.75
      recent_submit_weight_sec: 0.15
      jitter_max_sec: 0.5
    pacing:
      enabled: true
      min_spacing_global_sec: 20
      min_spacing_asset_sec: 60
      max_submit_15m_global: 3
      max_submit_15m_asset: 1
      max_submit_60m_global: 6
      max_submit_60m_asset: 2
      max_submit_day_global: 6
      max_submit_day_asset: 2
    correlation:
      enabled: true
      block_same_cluster_active: true
      max_active_per_cluster: 1
      max_pending_per_cluster: 1
```

Main intents:

- keep submit cadence sustainable
- centralize session-window evaluation for execution
- block simultaneous exposure inside the same configured `cluster_key`
- emit explicit protection artifacts/logs for audit and debugging

The control plane now exposes:

- `runtime_app protection`
- `runs/control/<scope>/protection.json`
- `runs/logs/account_protection.jsonl`

## Multi-asset complete (Package MULTI-ASSET-2)

The typed config now carries explicit knobs for the complete multi-asset layer:

```yaml
multi_asset:
  enabled: true
  max_parallel_assets: 3
  stagger_sec: 1.0
  execution_stagger_sec: 2.0
  portfolio_topk_total: 3
  portfolio_hard_max_positions: 2
  portfolio_hard_max_trades_per_day: 4
  portfolio_hard_max_pending_unknown_total: 2
  asset_quota_default_trades_per_day: 2
  asset_quota_default_max_open_positions: 1
  asset_quota_default_max_pending_unknown: 1
  portfolio_hard_max_positions_per_asset: 1
  portfolio_hard_max_positions_per_cluster: 1
  correlation_filter_enable: true
  max_trades_per_cluster_per_cycle: 1
```

`execution_stagger_sec` is used only for broker submit ordering in portfolio mode.
When it is zero, the runtime falls back to `stagger_sec`.

Automatic correlation behavior is intentionally conservative:

- explicit `cluster_key` wins
- missing / `default` `cluster_key` falls back to an inferred correlation group
- the inferred group is currently derived from the asset symbol, e.g. `EURUSD-OTC -> pair_quote:USD`

This keeps the runtime deterministic while still allowing per-asset overrides.


## Dashboard (Package 3)

A new optional top-level `dashboard:` section controls the professional local dashboard.

```yaml
dashboard:
  enabled: true
  title: Thalor
  theme: cyber_dragon
  default_refresh_sec: 3.0
  default_equity_start: 1000.0
  max_alerts: 50
  max_equity_points: 500
  report:
    output_dir: runs/reports/dashboard
    export_json: true
```

Notes:

- `default_equity_start` is used as the baseline for the equity curve visualization.
- `max_alerts` and `max_equity_points` keep the dashboard responsive for long-running repos.
- `report.output_dir` is used by `python -m natbin.dashboard.report` and by the export button inside the dashboard.


## monte_carlo

Package 4 introduz a surface `monte_carlo` para projeção realista baseada no
ledger histórico do projeto.

Exemplo:

```yaml
monte_carlo:
  enabled: true
  initial_capital_brl: 1000.0
  horizon_days: 60
  trials: 1000
  rng_seed: 42
  min_realized_trades: 20
  max_stake_fraction_cap: 0.10
  conservative:
    label: Conservador
    trade_count_scale: 0.85
    return_scale: 0.90
    stake_scale: 0.90
  medium:
    label: Médio
    trade_count_scale: 1.00
    return_scale: 1.00
    stake_scale: 1.00
  aggressive:
    label: Agressivo
    trade_count_scale: 1.15
    return_scale: 1.10
    stake_scale: 1.10
  report:
    output_dir: runs/reports/monte_carlo
    export_json: true
    export_html: true
    export_pdf: true
```

Comando principal:

```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app monte-carlo --repo-root . --config config/multi_asset.yaml --json
```

## Production (Package 5)

O bloco `production` formaliza backup e healthcheck para Docker/VPS.

```yaml
production:
  enabled: false
  profile: local
  backup:
    enabled: true
    output_dir: runs/backups
    archive_prefix: thalor_backup
    format: tar.gz
    interval_minutes: 60
    retention_days: 14
    max_archives: 48
  healthcheck:
    enabled: true
    require_loop_status: false
    max_loop_status_age_sec: 1800
    check_kill_switch: true
    check_drain_mode: false
    require_execution_repo: false
    scope_sample_limit: 6
```

Os comandos associados são `runtime_app backup` e `runtime_app healthcheck`.


## execution.real_guard

Package 3.2 adds `execution.real_guard` for live/REAL hardening.

Key fields:
- `require_env_allow_real`: requires `THALOR_EXECUTION_ALLOW_REAL=1` before REAL submits
- `allow_multi_asset_live`: explicit opt-in for REAL + `multi_asset.enabled=true`
- `serialize_submits`: serializes REAL submits with a cross-process file lock
- `min_submit_spacing_sec`: global spacing between REAL submits
- `max_pending_unknown_total` / `max_open_positions_total`: global limits before new submit
- `recent_failure_window_sec` + `max_recent_transport_failures`: cooldown on recent transport failures
- `post_submit_verify_*`: short verification poll after ACK
