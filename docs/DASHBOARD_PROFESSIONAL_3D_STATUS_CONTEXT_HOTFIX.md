# Dashboard Professional 3D — Status Context Hotfix

## Problema
No cockpit do dashboard, `PRACTICE` e `DOCTOR` apareciam como `ERROR` sem contexto suficiente quando o usuário abria o dashboard com um profile multi-asset como `config/multi_asset.yaml`.

Isso causava confusão porque:
- `practice_readiness` é uma surface específica de controlled practice;
- profiles multi-asset frequentemente têm `execution.enabled=false` e múltiplos assets por design;
- `production_doctor` pode acusar blockers de hidratação (`dataset_ready`, `market_context`) antes do primeiro prepare/observe do scope.

## Solução
O hotfix adiciona uma camada de apresentação chamada `control_display`:
- `PRACTICE -> N/A` quando o profile não é aplicável a controlled practice;
- `DOCTOR -> WAIT DATA` quando os blockers são apenas de hidratação/frescura do scope;
- nova seção `Why these statuses?` com blockers, warnings e checks relevantes.

## Importante
Os payloads operacionais originais não foram alterados. A mudança é apenas de UX/observabilidade do dashboard.
