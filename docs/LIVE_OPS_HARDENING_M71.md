# Live ops hardening (M7.1)

O M7.1 fecha o polimento operacional pós-M7 com foco em **incident posture**,
runbooks e sinalização rápida para operação live.

## Entregas

- `runtime_app incidents status|report|alert|drill`
- artefato canônico `runs/control/<scope>/incidents.json`
- relatórios persistidos em `runs/incidents/reports/`
- painel **Incident Ops (M7.1)** no dashboard
- smoke `scripts/tools/incident_ops_smoke.py`
- checks de release agora exigem docs de incident runbooks

## O que a surface de incidentes avalia

- release/security severity
- kill-switch / drain mode
- stale artifacts do runtime
- alert queue `queued/failed`
- incidentes warning recentes do scope
- health/loop status problemáticos

## Filosofia

O M7.1 não adiciona automação perigosa. Ele concentra contexto operacional e
entrega **ações sugeridas**. A decisão de ativar/desativar gates continua
explícita pelo operador.
