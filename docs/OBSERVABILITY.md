# Observability / incident response

O Pacote G adiciona artefatos estruturados para auditoria e resposta a incidentes sem depender apenas do log textual.

## Artefatos

### Último snapshot de decisão
- `runs/decisions/decision_latest_<asset>_<interval>.json`

Contém o último candle avaliado com:
- ação
- reason
- blockers
- gate
- orçamento (`executed_today`, `budget_left`)
- score/conf/proba/ev/payout

### Snapshot detalhado por candle
- `runs/decisions/decision_<day>_<asset>_<interval>_<ts>.json`

É escrito apenas para decisões relevantes, por exemplo:
- trade emitido (`CALL/PUT`)
- bloqueio sério (`market_closed`, `market_context_stale`, `gate_fail_closed`)

### Incidentes JSONL
- `runs/incidents/incidents_<day>_<asset>_<interval>.jsonl`

Cada linha é um evento JSON independente. Isso facilita ingestão por ferramentas externas sem precisar parsear transcript.

## Ferramenta de health

```powershell
python scripts/tools/runtime_health_report.py
```

Ela combina:
- último `observe_loop_auto_status*.json`
- último `decision_latest_*.json`

## Propriedades importantes

- **não bloqueante**: falha de escrita de observabilidade não derruba o bot
- **escopado por asset + interval**
- **compatível com auditoria pós-rodada**
