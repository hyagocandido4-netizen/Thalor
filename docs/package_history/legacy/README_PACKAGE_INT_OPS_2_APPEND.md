# Package INT-OPS-2 — finalizado

INT-OPS-2 estabiliza a semântica operacional de retrain + anti-overfit.

## Entregas

- novo artifact por scope: `runs/intelligence/<scope>/intelligence_ops_state.json`
- nova camada compartilhada: `src/natbin/intelligence/ops_state.py`
- `retrain_ops` agora materializa um state efetivo consistente para:
  - `queued / cooldown / fitting / promoted / rejected`
  - `anti_overfit_tuning` live vs review-only
  - rollback com restore de artifacts
- `intelligence_surface` passa a consumir o state efetivo e reduz warn falso
  quando o review-only tuning é esperado por rollback/cooldown consistente
- novos testes de regressão para consistência semântica

## Resultado prático

- `retrain_review = rejected` deixa de gerar ruído automático quando o par
  `status=rejected + plan=cooldown` está consistente
- `anti_overfit_tuning_review.json` sozinho deixa de ser tratado como problema
  quando ele é consequência esperada do restore pós-rejeição
- `build_retrain_status_payload()` passa a expor `ops_state` por scope

## Validado

```powershell
pytest -q
python scripts/tools/selfcheck_repo.py
```
