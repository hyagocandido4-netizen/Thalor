# Part 4 — Portfolio cycle headroom and multi-asset runtime budget

## Goal
Reduce per-cycle pressure in the long canary/soak loop without weakening the conservative execution envelope.

This part attacks the dominant runtime pressure found in the last run:
- heavy prepare phases on `AUDUSD-OTC_300s` and `USDCAD-OTC_300s`
- cycle time drifting too close to the 300s candle budget
- full candidate fan-out on every cycle even when the provider governor was already signaling a smaller safe budget

## What changed

### 1) Adaptive prepare in `portfolio.runner`
The runtime no longer blindly does a full:
- `collect_recent`
- `make_dataset`
- `refresh_market_context`

for every scope on every cycle.

New behavior:
- if local candle DB + market context are fresh, the scope is **skipped** in prepare
- if candle DB is fresh but market context is stale, the runtime does **refresh-only** via `refresh_market_context_safe`
- if a local DB exists but is stale, the runtime uses an **incremental prepare lookback** instead of the full decision lookback
- only when the scope has no usable local DB does it fall back to **full prepare**

### 2) Candidate budget rotation in the real portfolio cycle
The portfolio cycle now respects the governor candidate budget instead of always scanning every scope.

Behavior:
- keep the best/recently-strongest scope first
- rotate the remaining scopes with a persisted cursor
- mark skipped scopes explicitly as `candidate_budget_skip`
- persist budget metadata into the cycle payload

### 3) Shared scope-budget helper
A shared helper now backs the rotation logic so the runtime and the canary signal proof follow the same governed budget semantics.

### 4) Governor prepare fallback cap tightened in degraded mode
When the provider is degraded/noisy, the governor now caps expensive prepare fallbacks more conservatively.

### 5) Config surface for long-run headroom
New multi-asset settings:
- `adaptive_prepare_enable`
- `prepare_incremental_lookback_candles`
- `candidate_budget_rotation_enable`

These are enabled in the main practice/live profiles shipped in this overlay.

## Operational effect
This part is designed to:
- reduce unnecessary repeated full prepares
- reduce cycle-time overruns near the 300s boundary
- preserve multi-asset observation while keeping top-1 execution conservative
- make long soak runs more likely to stay alive for hours without accumulating timing debt

## Risk
Low to medium.

Why not zero:
- this changes how often a scope is fully refreshed
- some scopes will now be rotated instead of scanned every single cycle when the governor budget is smaller than the full scope count

Why acceptable:
- the conservative top-1 envelope remains unchanged
- skipped scopes are explicit and traceable
- heavy fallbacks are reduced, not hidden
- the behavior is backed by tests
