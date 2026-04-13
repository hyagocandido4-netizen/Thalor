# PACKAGE DASHBOARD-3C — Graceful Streamlit shutdown

This hotfix makes `python -m natbin.dashboard` exit cleanly when the operator stops the dashboard with `Ctrl+C`.

## Included
- Catch `KeyboardInterrupt` in `src/natbin/dashboard/__main__.py`
- Return exit code `0` on normal interactive shutdown
- Friendly stderr message instead of Python traceback
- Regression test + smoke
