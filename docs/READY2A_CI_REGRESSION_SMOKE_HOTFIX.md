# READY-2A — CI regression smoke hotfix

This hotfix restores the legacy private helper export `_resolve_live_signals_csv_path` on the
compatibility observer modules after the RCF-2/RCF-3 decomposition.

Why it exists:
- GitHub Actions regression smoke still imports `from natbin.observe_signal_topk_perday import _resolve_live_signals_csv_path`
- the public helper `resolve_live_signals_csv_path` remained available
- the private compatibility alias was dropped during facade decomposition, which caused CI to fail

Behavior after the hotfix:
- `natbin.observe_signal_topk_perday._resolve_live_signals_csv_path` works again
- `natbin.usecases.observe_signal_topk_perday._resolve_live_signals_csv_path` works as well
- existing operational scripts do not need to change
