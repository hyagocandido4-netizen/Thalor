# Package RETRAIN-OPS-1

Este pacote operacionaliza o retrain do scope atual com trilha auditável.

## O que entra

- comando novo `runtime_app retrain status`
- comando novo `runtime_app retrain run`
- artefato novo `runs/intelligence/<scope>/retrain_review.json`
- estado operacional de retrain com fases explícitas:
  - `cooldown`
  - `queued`
  - `fitting`
  - `evaluated`
  - `promoted`
  - `rejected`
- comparação before/after com restore automático dos artifacts anteriores quando o retrain é rejeitado
- materialização e leitura dos artifacts scoped do profile atual

## Comandos

```powershell
python -m natbin.runtime_app retrain status --repo-root . --config config/live_controlled_practice.yaml --json
python -m natbin.runtime_app retrain run --repo-root . --config config/live_controlled_practice.yaml --json
```

## Critério de leitura do review

O `retrain_review.json` grava:

- métricas `before`
- métricas `after`
- métricas `final`
- `comparison`
- `verdict`
- `reason`
- se houve `restored_previous_artifacts`

Quando o verdict é `rejected`, o pacote restaura os artifacts anteriores do scope/profile atual.
