# P22-ANTI-OVERFIT-TUNING-1

## Problema

Depois do `INT-HARDEN-1` e da trilha `RETRAIN-OPS`, a camada de anti-overfit já
estava materializada, mas o retrain continuava frequentemente terminando em:

- `comparison.score = 0.0`
- `anti_overfit.accepted = false`
- `reason = no_material_improvement`

Isso mostrava que a trilha operacional estava correta, mas a avaliação ainda
ficava excessivamente dependente de uma única configuração fixa da política de
anti-overfit.

## O que este pacote faz

O pacote adiciona um sweep pequeno e controlado de variantes, sem transformar o
runtime em um tuner aberto:

- baseline (config atual)
- relaxed robustness floor
- relaxed robustness + gap relief
- relaxed window requirement
- variantes equivalentes usando `recent_training_rows`, quando houver suporte

Cada variante gera um `report` de anti-overfit usando o mesmo payload-base, e
um `objective` que balanceia:

- robustness score
- accepted bonus
- relief cost (quanto a variante relaxou a política)
- source cost para variantes “recent”

A variante só substitui a baseline quando:

- muda `accepted` de `false` para `true`, ou
- melhora o objetivo acima do delta mínimo configurado, ou
- melhora a robustez de forma material

## Artefato novo

`runs/intelligence/<scope>/anti_overfit_tuning.json`

Campos principais:

- `baseline_variant`
- `selected_variant`
- `selection_reason`
- `improved`
- `baseline`
- `selected`
- `variants[]`

## Config nova

```yaml
intelligence:
  anti_overfit_tuning_enable: true
  anti_overfit_tuning_min_robustness_floor: 0.45
  anti_overfit_tuning_window_flex: 1
  anti_overfit_tuning_gap_penalty_flex: 0.03
  anti_overfit_tuning_recent_rows_min: 48
  anti_overfit_tuning_objective_min_delta: 0.015
```

## Integração

O tuning foi integrado em:

- `fit_intelligence_pack`
- `refresh_config_intelligence`
- `retrain review/comparison`
- `intelligence surface`

## Critério de done

- o pack passa a materializar `anti_overfit_tuning.json`
- a surface mostra a variante selecionada
- o retrain review passa a capturar ganho de objetivo da camada de anti-overfit
- quando não houver ganho material, a baseline é preservada
