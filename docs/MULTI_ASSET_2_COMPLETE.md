# Package MULTI-ASSET-2 — Multi-asset completo

Package MULTI-ASSET-2 formalizes the current portfolio runtime into a complete six-asset orchestration surface.

## What changed

### 1. Execution stagger

The runtime already supported stagger for prepare/candidate phases. This package adds a dedicated execution stagger:

- `multi_asset.execution_stagger_sec`
- `latest_cycle.execution_plan`
- dashboard visibility for the submit schedule

Execution remains sequential and explicit. The stagger only inserts a configurable delay between selected assets.

### 2. Shared quota + per-asset quota

`portfolio_status` now exposes:

- `asset_quotas`
- `portfolio_quota`
- `asset_board`

Per-asset quotas can now be configured centrally for assets that do not have their own override:

```yaml
multi_asset:
  asset_quota_default_trades_per_day: 2
  asset_quota_default_max_open_positions: 1
  asset_quota_default_max_pending_unknown: 1
```

### 3. Automatic correlation group

When an asset does not define an explicit `cluster_key`, the portfolio layer now infers a deterministic correlation group from the asset symbol.

Current rule set:

- explicit `cluster_key` wins
- otherwise `EURUSD-OTC` → `pair_quote:USD`
- otherwise fallback to `asset_family:<prefix>`

This keeps the behavior conservative and predictable, while still allowing a user to override the grouping explicitly.

### 4. Unified asset board

`portfolio_status` now emits `asset_board`, a row-oriented payload designed for the Streamlit dashboard and for JSON inspection.

Each row includes:

- `scope_tag`
- `asset`
- `correlation_group`
- quota state
- selected/suppressed status
- latest execution status
- execution stagger metadata

## Example

```powershell
python -m natbin.runtime_app portfolio status --repo-root . --config config/multi_asset.yaml --json
```

## Example config

`config/multi_asset.yaml` now ships as a six-asset demo profile with:

- 6 assets
- prepare/candidate stagger
- execution stagger
- shared quota defaults
- explicit crypto/metal groups
- automatic grouping for default FX pairs

## Smoke test

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/multi_asset_package_2_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
