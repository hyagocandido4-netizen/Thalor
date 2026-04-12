# FINAL FIX Recovery — Restore Green Baseline

This recovery package restores the last known green baseline after the incompatible FINAL_FIX overlay regressed the repository by mixing an older code snapshot with a newer test suite.

## Symptoms fixed
- ImportError on `natbin.dashboard.analytics`
- ImportError on `practice_payload`, `intelligence_payload`, `portfolio_status_payload`
- Missing `retrain_plan_path`, `intelligence_ops_state_path`, `anti_overfit_*_path`
- Missing `resolved_to_legacy_env_map`
- Missing `write_repo_control_artifact`
- Missing `RegimeBoundsSettings`
- Dashboard table helpers missing

## Strategy
The cleanest reversible fix is to restore the last known green code baseline for the affected modules and tests.
