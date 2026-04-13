## Package M5 (série M) — Intelligence Layer

Entrega:
- `pack.json` por scope com slot profile, learned gate, drift baseline, coverage e anti-overfit
- enriquecimento dos candidatos no `portfolio.runner`
- `intelligence_score` como prioridade de ranking
- `latest_eval.json`, `drift_state.json`, `retrain_trigger.json`
- painel de inteligência no dashboard
- smoke + testes do pack builder / runtime intelligence

Arquivos principais:
- `src/natbin/intelligence/fit.py`
- `src/natbin/intelligence/runtime.py`
- `src/natbin/intelligence/slot_profile.py`
- `src/natbin/intelligence/learned_gate.py`
- `src/natbin/intelligence/drift.py`
- `src/natbin/intelligence/coverage.py`
- `src/natbin/intelligence/anti_overfit.py`
- `docs/INTELLIGENCE_M5.md`
