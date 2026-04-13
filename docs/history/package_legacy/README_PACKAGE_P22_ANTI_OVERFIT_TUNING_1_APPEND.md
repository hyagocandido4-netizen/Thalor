# Package P22-ANTI-OVERFIT-TUNING-1

Este pacote adiciona um sweep controlado de tuning para a camada de
anti-overfitting do pack de inteligência.

## O que muda

- gera `runs/intelligence/<scope>/anti_overfit_tuning.json`
- testa variantes seguras de `min_robustness`, `min_windows` e `gap_penalty`
- considera também uma variante `recent_training_rows` quando houver suporte
- mantém a baseline como candidata e só troca para a variante tuned quando o
  ganho de objetivo for material
- injeta o resultado do tuning em `pack.json`, `latest_eval.json` e na surface
  de inteligência/retrain

## Resultado esperado

Após rebuild do pack ou `retrain run`, o scope passa a expor:

- `anti_overfit_tuning.selected_variant`
- `anti_overfit_tuning.improved`
- `anti_overfit_tuning.selection_reason`

O objetivo é deixar o retrain menos binário e mais explicável quando o gargalo
está em robustez/anti-overfit, sem relaxar demais os guardrails.
