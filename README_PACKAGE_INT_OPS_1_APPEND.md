# Package INT-OPS-1 — finalizado

INT-OPS-1 integra a inteligência na camada operacional do Thalor:

- novo `runtime_app intelligence`
- novo artifact `runs/control/<scope>/intelligence.json`
- `runtime_app status` e `portfolio status` passam a expor a surface de inteligência
- `release_readiness`, `production_doctor` e `incidents` consomem score/retrain/feedback/traceabilidade
- `order_intents` agora persistem `intelligence_score`, `retrain_state`, `retrain_priority`, `allocation_reason`, `allocation_rank` e `portfolio_feedback_json`
- dashboard local ganha o quadro **Portfolio intelligence ops**

## Validado

```powershell
pytest -q
python scripts/ci/smoke_execution_layer.py
python scripts/ci/smoke_runtime_app.py
python scripts/tools/phase1_h11_stack_calibration_smoke.py
python scripts/tools/phase1_h12_retrain_allocator_smoke.py
python scripts/tools/intelligence_ops_smoke.py
python -m natbin.leak_check
python scripts/tools/selfcheck_repo.py
```
