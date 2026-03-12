# Package H7 — E2E Broker Dependency Closeout

This package closes the gap where `collect_recent` / `refresh_market_context`
would crash immediately on machines that had not installed `iqoptionapi` yet.

It adds:
- lazy broker dependency resolution
- explicit dependency-missing diagnostics
- local DB / cache fallback for `collect_recent` and `refresh_market_context`
- smoke + tests for the fallback path
