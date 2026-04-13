# Package 3.2 – Hardening do execution real multi-asset

Este pacote endurece a camada de execução para a transição controlada para modo REAL em cenário multi-asset.

## Entregas
- `execution.real_guard` tipado no config
- bloqueio explícito de live multi-asset até o operador armar `allow_multi_asset_live=true`
- checagem antecipada de `THALOR_EXECUTION_ALLOW_REAL=1`
- serialização cross-process de submits reais com lock de arquivo
- limites globais de `open_positions` e `pending_unknown`
- cooldown por falhas recentes de transporte
- verificação pós-submit (`post_submit_verification`) sem alterar a lógica de reconciliação já existente
- comando novo `runtime_app execution-hardening`
- artefato novo `runs/control/<scope>/execution_hardening.json`
- smoke test dedicado

## Comandos principais
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app execution-hardening --repo-root . --config config/live_controlled_real.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app execute-order --repo-root . --config config/live_controlled_real.yaml --json
```

## Validação
```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m pytest -q tests/test_phase3_execution_hardening_2.py
.\.venv\Scripts\python.exe scripts/tools/phase3_execution_hardening_2_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
