## Package RCF-1 — Refactor Closeout Final

Este package fecha a dívida estrutural restante da refatoração em torno do
observer e da config.

Inclui:

- remoção do hardcode/fallback interno de `config.yaml` nos observers
- `decision.bounds` tipado e validado
- formalização de `cp_alpha` / `cpreg` no schema moderno
- export de `runtime_overrides` para o env legado do observer
- freeze do inventário atual de root shims
- docs e testes de closeout

Validação local do pacote:

- `pytest -q` → **83 passed**
- `python scripts/ci/smoke_execution_layer.py` → **OK**
- `python scripts/ci/smoke_runtime_app.py` → **OK**
- `PYTHONPATH=src python scripts/tools/phase1_h11_stack_calibration_smoke.py` → **OK**
- `PYTHONPATH=src python scripts/tools/phase1_h12_retrain_allocator_smoke.py` → **OK**
- `PYTHONPATH=src python -m natbin.leak_check` → **OK**
- `PYTHONPATH=src python scripts/tools/selfcheck_repo.py` → **OK**
