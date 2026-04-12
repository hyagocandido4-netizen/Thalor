## Package M3 (série M pós-Phase 0) — Runtime Soak & Scheduler Hardening

Este pacote fecha o hardening operacional do runtime:

- lock escopado E2E também no `observe --once`
- heartbeat/owner metadata no lockfile
- invalidação de artefatos stale no startup
- `guard.json` e `lifecycle.json` por scope
- smoke e testes para restart limpo / lock contention

Validação principal:

```powershell
python scripts/tools/runtime_hardening_smoke.py
pytest -q
```
