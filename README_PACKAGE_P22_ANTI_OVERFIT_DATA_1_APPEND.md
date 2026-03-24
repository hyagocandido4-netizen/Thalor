# Package P22-ANTI-OVERFIT-DATA-1

Este package fecha a trilha de **data quality** do P22.

## O que muda
- materializa uma fonte `per_window` mais fiel para anti-overfit usando dados reais de `daily_summary.by_hour` e, quando necessário, `signals_v2` + labels do dataset
- persiste `anti_overfit_data_summary.json` por scope
- persiste `anti_overfit_tuning_review.json` para auditoria mesmo quando o review do retrain rejeita a tentativa e restaura os artifacts anteriores
- faz `retrain_review` / `status` / métricas internas conseguirem ler o tuning do último attempt via artifact de review

## Resultado esperado
- `anti_overfit_source.kind` deixa de cair em `training_rows_fallback` quando houver dados reais suficientes
- o sweep do P22 continua auditável mesmo em review rejeitado
- a próxima rodada de retrain consegue comparar tuning com base em artifact persistido
