# Package M7.2 — Controlled live validation

Este pacote adiciona um trilho separado para validação live controlada na
máquina do operador:

- baseline sem broker live
- practice com adapter live em conta `PRACTICE`
- preflight de conta `REAL` com `drain mode` ligado
- primeiro submit real mínimo com dupla confirmação

Arquivos principais:
- `scripts/tools/controlled_live_validation.py`
- `scripts/tools/run_controlled_live_validation.ps1`
- `src/natbin/ops/live_validation.py`
- `docs/CONTROLLED_LIVE_VALIDATION_M72.md`
- `config/live_controlled_practice.yaml.example`
- `config/live_controlled_real.yaml.example`
