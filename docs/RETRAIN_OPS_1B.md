# RETRAIN-OPS-1B

## Problema corrigido
O `RETRAIN-OPS-1A` já destravava o cooldown vencido, mas ainda existiam dois problemas após um retrain rejeitado:
1. `portfolio_cycle_latest.json` e `portfolio_allocation_latest.json` podiam continuar com `retrain_state=queued`, mesmo depois do review final ter saído como `rejected`.
2. O retrain podia ser reenfileirado cedo demais, sem backoff explícito para rejeições por ausência de ganho material.

## Solução
O `RETRAIN-OPS-1B` introduz:
- **rejection backoff** configurável (`intelligence.retrain_rejection_backoff_hours`, default `6`);
- **post-review resync** scoped do portfolio;
- snapshot/restore em ordem correta, para que `final_metrics` não herdem `retrain_state=fitting`.

## Semântica final
### Quando o retrain é promovido
- `retrain_status.state = promoted`
- `retrain_plan.state = idle`
- portfolio scoped é rematerializado e marcado com o verdict final.

### Quando o retrain é rejeitado
- `retrain_status.state = rejected`
- `retrain_plan.state = cooldown`
- `retrain_plan.cooldown_active = true`
- `retrain_plan.cooldown_until_utc` é preenchido
- portfolio scoped é rematerializado e deixa de ficar preso em `queued/high`.

## Observabilidade
Os seguintes arquivos passam a convergir para o mesmo estado final:
- `runs/intelligence/<scope>/retrain_review.json`
- `runs/intelligence/<scope>/retrain_status.json`
- `runs/intelligence/<scope>/retrain_plan.json`
- `runs/portfolio/profiles/<profile_key>/portfolio_cycle_latest.json`
- `runs/portfolio/profiles/<profile_key>/portfolio_allocation_latest.json`
