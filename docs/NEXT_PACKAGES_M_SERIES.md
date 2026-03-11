# Próximos Packages (série M pós-Phase 0)

Este documento registra a trilha definida após o fechamento do Package W.
A ideia é evitar perda de contexto entre o roadmap Q→W e a nova sequência
operacional M1→M7.

## Status

- M1 — concluído
- M2 — concluído
- M3 — concluído
- M4 — concluído
- M5 — concluído
- M6 — concluído
- M7 — concluído
- M7.1 — concluído
- Série M — fechada com hardening fino

## Ordem de execução

### M1 — Release Hygiene & Runtime Sanitation ✅
Fechar empacotamento limpo, exclusão canônica de segredos/artefatos, smoke
de bundle e checklist de compartilhamento.

### M2 — Live Execution Bridge ✅
Fechar o adapter live no contrato novo (submit/status/reconcile) com fail-safe
e paridade paper/live.

### M3 — Runtime Soak & Scheduler Hardening ✅
Soak 24h/72h, lock E2E, invalidação de artefatos stale, restart limpo e dumps
de config efetiva por ciclo.

### M4 — Portfolio Risk Engine ✅
Correlation filter, quota global forte, exposure caps e política agregada por
portfólio multi-asset.

### M5 — Intelligence Layer (P18–P22) ✅
Slot-aware tuning, learned gating/stacking, drift/regime monitor, retrain
trigger, coverage regulator 2.0 e anti-overfitting harness.

Entregas principais:
- `runs/intelligence/<scope_tag>/pack.json`
- `latest_eval.json`, `drift_state.json`, `retrain_trigger.json`
- enriquecimento dos candidatos no portfolio runner
- dashboard com painel de inteligência
- smoke + testes do pack builder / runtime intelligence

### M6 — Security & Secrets Hardening ✅
External secret files, redaction em artefatos, auditoria de postura de
segurança, `runtime_app security`, guard de submit live (spacing/rate/time
filter) e knobs formais de throttling do adapter IQ.

Entregas principais:
- `src/natbin/security/`
- artefato `runs/control/<scope>/security.json`
- secret bundle / secret files (`THALOR_SECRETS_FILE`, `THALOR_BROKER_*_FILE`)
- dashboard com painel **Security (M6)**
- smoke + testes de redaction / audit / guard

### M7 — Productization Final ✅
Telegram, dashboard operacional final, runbooks, docs/diagramas, perfis
Docker e checklist formal de release.

Entregas principais:
- `runtime_app release`
- `runtime_app alerts status|test|release|flush`
- `runs/control/<scope>/release.json`
- `runs/alerts/telegram_outbox.jsonl` e `telegram_state.json`
- `docker-compose.prod.yml`
- docs `ALERTING_M7`, `PRODUCTION_CHECKLIST_M7`, `DIAGRAMS_M7`

### M7.1 — Live Ops Hardening & Incident Runbooks ✅
Hardening fino de operação live com surface de incidentes, relatórios
operacionais e drills sem side effects.

Entregas principais:
- `runtime_app incidents status|report|alert|drill`
- `runs/control/<scope>/incidents.json`
- `runs/incidents/reports/incident_report_*.json`
- dashboard com painel **Incident Ops (M7.1)**
- docs `INCIDENT_RUNBOOKS_M71`, `LIVE_OPS_HARDENING_M71`

## Regra de avanço

Não abrir M(N+1) com regressão aberta em M(N).

## Fechamento

A série M agora cobre M1→M7.1 sem regressão aberta conhecida na trilha local.
