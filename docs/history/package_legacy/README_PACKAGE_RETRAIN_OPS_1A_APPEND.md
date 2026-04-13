# Package RETRAIN-OPS-1A

Hotfix de cooldown vencido no retrain operacional.

## O que este pacote corrige

- `retrain status` agora recompõe o estado quando o `cooldown_until_utc` já expirou
- `retrain run` não fica mais preso em `cooldown_active` quando o cooldown já venceu
- quando necessário, o pacote força um refresh leve (`rebuild_pack=false`) para atualizar plan/status antes do retrain real
- se mesmo após o refresh o plan continuar stale, o pacote normaliza manualmente o estado para `queued/watch/idle`
- o `retrain_review.json` agora registra o bloco `cooldown_refresh`

## Resultado esperado

Quando o cooldown está vencido:

- `retrain status` deve deixar de mostrar `cooldown_active`
- `retrain run` deve prosseguir para `fitting -> evaluated -> promoted/rejected`
- `retrain_review.json` deve vir com `executed=true`, `after` e `comparison` preenchidos

## Smoke

```powershell
PYTHONPATH=src python scripts/tools/retrain_ops_1a_smoke.py
```
