# RETRAIN-OPS-1 — retrain auditável por scope/profile

O objetivo deste pacote é transformar o retrain em um fluxo operacional explícito, auditável e seguro para o profile atual.

## Problema que ele resolve

Antes deste pacote, o projeto possuía apenas:

- `retrain_plan.json`
- `retrain_status.json`
- trigger/recomendação dentro da inteligência

Mas ainda não havia uma operação canônica que:

1. executasse o retrain do scope atual
2. comparasse before/after
3. decidisse se o novo estado deveria ser aceito
4. restaurasse o estado anterior em caso de regressão

## Novos artifacts

Por scope:

- `pack.json`
- `latest_eval.json`
- `retrain_plan.json`
- `retrain_status.json`
- `retrain_review.json`
- `anti_overfit_summary.json`

Por profile/config atual:

- `portfolio_cycle_latest.json` scoped
- `portfolio_allocation_latest.json` scoped

## Fluxo de estados

Recomendação da inteligência:

- `idle`
- `watch`
- `queued`
- `cooldown`

Operação do retrain:

- `fitting`
- `evaluated`
- `promoted`
- `rejected`

## Comandos

### Status

```powershell
python -m natbin.runtime_app retrain status --repo-root . --config config/live_controlled_practice.yaml --json
```

Retorna plan/status/review e métricas atuais do scope.

### Run

```powershell
python -m natbin.runtime_app retrain run --repo-root . --config config/live_controlled_practice.yaml --json
```

Executa um ciclo de retrain/review.

Opções úteis:

```powershell
python -m natbin.runtime_app retrain run --repo-root . --config config/live_controlled_practice.yaml --asset EURUSD-OTC --interval-sec 300 --json
python -m natbin.runtime_app retrain run --repo-root . --config config/live_controlled_practice.yaml --force --json
```

## Política de promoção

O review compara before/after em dimensões como:

- `anti_overfit.accepted`
- `anti_overfit.robustness_score`
- `portfolio_score`
- `intelligence_score`
- `allow_trade`
- `stack.decision`
- `retrain_priority`
- seleção/supressão no allocation scoped

Se houver regressão material ou `hard_regression`, o resultado é `rejected` e o pacote restaura os artifacts anteriores.

## Resultado esperado

Depois deste pacote, o projeto consegue responder com clareza:

- o retrain foi executado?
- o resultado foi promovido ou rejeitado?
- o novo pack melhorou ou piorou o estado do scope?
- a decisão final mudou?
- os artifacts atuais pertencem ao profile correto?
