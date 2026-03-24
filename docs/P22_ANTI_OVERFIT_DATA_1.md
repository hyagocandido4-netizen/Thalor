# P22-ANTI-OVERFIT-DATA-1

## Objetivo

O package anterior validou o sweep multi-variant do anti-overfit, mas a tentativa ainda podia ficar limitada por duas coisas:

1. a fonte de `per_window` podia degradar para `training_rows_fallback`
2. o `anti_overfit_tuning.json` podia desaparecer depois de um retrain rejeitado, porque os artifacts anteriores eram restaurados

Este package resolve as duas lacunas.

## Mudanças principais

### 1. Fonte `per_window` mais real

A construção do pack agora tenta, na ordem:

1. `summary.json` explícito do tune dir
2. `daily_summary.by_hour` materializado em `anti_overfit_data_summary.json`
3. `daily_summary` diário agregado
4. `signals_eval_fallback` a partir de `signals_v2` + labels do dataset
5. `training_rows_fallback` como último recurso

O artifact padrão continua:

`runs/intelligence/<scope>/anti_overfit_summary.json`

E agora existe também:

`runs/intelligence/<scope>/anti_overfit_data_summary.json`

que guarda a fonte de dados materializada quando a origem vem de summaries/sinais reais.

### 2. Persistência do tuning mesmo com review rejeitado

Agora o retrain escreve:

`runs/intelligence/<scope>/anti_overfit_tuning_review.json`

Esse artifact preserva o tuning da tentativa (`selected_variant`, baseline, objective, selection reason, verdict) mesmo quando o retrain termina em `rejected` e o snapshot antigo é restaurado.

### 3. Métricas e review leem o tuning do último attempt

Quando `anti_overfit_tuning.json` não existe mais por causa do restore, as métricas internas passam a usar o conteúdo de `anti_overfit_tuning_review.json`.

Isso mantém auditáveis:
- `selected_variant`
- `baseline_variant`
- `improved`
- objective selected/baseline
- verdict do último tuning/review

## Artifacts novos

- `runs/intelligence/<scope>/anti_overfit_data_summary.json`
- `runs/intelligence/<scope>/anti_overfit_tuning_review.json`

## Smoke

```powershell
PYTHONPATH=src python scripts/tools/p22_anti_overfit_data_1_smoke.py
```

## Critério de done

- `fit_intelligence_pack` prefere fonte real (`daily_hourly_summary_fallback` ou `signals_eval_fallback`) quando disponível
- `anti_overfit_data_summary.json` é materializado
- `anti_overfit_tuning_review.json` persiste o tuning de attempts rejeitados
- métricas de retrain conseguem continuar auditando o tuning mesmo após restore
