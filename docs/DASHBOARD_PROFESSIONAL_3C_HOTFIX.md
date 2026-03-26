# Dashboard Professional 3C Hotfix

## Problem
The dashboard launched correctly, but stopping `python -m natbin.dashboard` with `Ctrl+C` propagated `KeyboardInterrupt` from the Streamlit subprocess and printed a traceback.

## Fix
The dashboard launcher now treats `KeyboardInterrupt` as the normal operator-controlled shutdown path:
- prints `Dashboard stopped by user.`
- exits with status code `0`
- preserves the existing runtime behavior while the server is running

## Validation
- `tests/test_dashboard_package_3c_hotfix.py`
- `scripts/tools/dashboard_package_3c_hotfix_smoke.py`
