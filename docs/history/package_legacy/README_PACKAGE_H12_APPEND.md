# Package H12 — finalizado

H12 consolida a próxima camada da Fase 1:

- retrain orchestration (`retrain_plan.json` + `retrain_status.json`)
- allocator feedback orientado por regime/coverage (`portfolio_feedback`)
- priorização por `portfolio_score`
- carry-over do `portfolio_score` para o `OrderIntent` quando a execução nasce do allocation latest

## Arquivos principais

- `src/natbin/intelligence/retrain.py`
- `src/natbin/intelligence/runtime.py`
- `src/natbin/intelligence/policy.py`
- `src/natbin/portfolio/allocator.py`
- `src/natbin/portfolio/models.py`
- `src/natbin/runtime/execution.py`
- `docs/PHASE1_INTELLIGENCE_H12.md`
- `scripts/tools/phase1_h12_retrain_allocator_smoke.py`

## Validado

```powershell
pytest -q
python scripts/ci/smoke_execution_layer.py
python scripts/ci/smoke_runtime_app.py
python scripts/tools/phase1_h11_stack_calibration_smoke.py
python scripts/tools/phase1_h12_retrain_allocator_smoke.py
python -m natbin.leak_check
python scripts/ci/selfcheck_repo.py
```
