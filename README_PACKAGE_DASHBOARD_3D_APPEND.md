# Package DASHBOARD 3D — Status Context Hotfix

Este hotfix melhora o cockpit do dashboard para evitar cards vermelhos opacos quando o profile carregado não é um profile de controlled practice.

## O que muda
- `PRACTICE` passa a aparecer como `N/A` quando o profile atual não se aplica a controlled practice.
- `DOCTOR` passa a aparecer como `WAIT DATA` quando os blockers são apenas artefatos frescos do scope (`dataset_ready`, `market_context`, etc.).
- Nova seção `Why these statuses?` explica o motivo de cada estado diretamente no dashboard.

## Semântica preservada
- Os payloads brutos de `runtime_app practice` e `runtime_app doctor` continuam inalterados.
- A normalização acontece apenas na camada de apresentação do dashboard.
