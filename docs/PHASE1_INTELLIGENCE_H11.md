# H11 — Phase 1 Intelligence Hardening

H11 hardens the H10 foundation for Grok Phase 1 with two production-facing layers:

1. calibrated learned gating / stacking
2. scope-aware decision policy overrides

## What changed

### P19 — Learned gating / stacking hardening
- learned gate payload upgraded to `phase1-learned-gate-v3`
- logistic probabilities may now be calibrated with isotonic regression
- pack stores:
  - `probability_source`
  - `calibrator`
  - `calibration_ece`
  - `calibration_max_gap`
  - `reliability_score`
  - `reliability_status`
- runtime can neutralize stacking when reliability is below threshold

### Scope policy
- new `intelligence.scope_policies[]`
- matching keys:
  - `scope_tag`
  - `asset`
  - `interval_sec`
- override knobs:
  - `learned_weight`
  - `promote_above`
  - `suppress_below`
  - `abstain_band`
  - `min_reliability`
  - `neutralize_low_reliability`
  - `stack_max_bonus`
  - `stack_max_penalty`
  - `learned_fail_closed`
  - `drift_fail_closed`

### P20 — Retrain trigger hardening
- retrain trigger upgraded to `phase1-retrain-trigger-v3`
- includes `priority`
- includes `learned_reliability`

## Expected effect
- reduce overreaction to poorly calibrated learned gate outputs
- allow scope-specific stacking posture without forking the runtime
- improve diagnostics for later H12/H13 work
