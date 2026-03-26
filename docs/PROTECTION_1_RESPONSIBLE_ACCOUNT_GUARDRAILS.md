# Package PROTECTION-1 — Account protection and responsible pacing

Package PROTECTION-1 introduces a dedicated pre-submit layer that sits between the execution guardrails and the broker submit step. Its job is to keep the execution cadence sustainable and auditable.

## Scope

The protection layer evaluates: 

- session windows (`security.protection.sessions`)
- cadence-aware delays (`security.protection.cadence`)
- global and per-asset pacing (`security.protection.pacing`)
- same-cluster exposure (`security.protection.correlation`)

When execution reaches the submit stage, the runtime now does:

1. broker guard
2. broker health
3. account protection
4. optional recommended delay
5. broker submit

## Commands

### Protection surface

```powershell
python -m natbin.runtime_app protection --repo-root . --config config/live_controlled_practice.yaml --json
```

This emits the current decision plus behavior metrics and also writes:

- `runs/control/<scope>/protection.json`
- `runs/logs/account_protection.jsonl`

### Execution payload

`execute-order` now includes an `account_protection` block so each submit decision is observable from the control plane.

## Config example

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

## Behavior metrics

Each decision captures a small behavior bundle:

- `cadence_pressure`
- `volatility_score`
- `volatility_source`
- `delay_components.*`

These fields are intentionally descriptive and deterministic enough for audit/debug work.

## Persistence

State is kept in `runs/security/account_protection_state.json`.

This file tracks recent submit timestamps at three levels:

- global
- scope / asset
- cluster

It is updated only after a submit attempt is recorded, so dry evaluations remain side-effect free.

## Smoke test

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/protection_package_1_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
