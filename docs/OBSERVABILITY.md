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

## Métricas Prometheus-style (/metrics)

**Package P** adiciona um servidor HTTP opcional com:

* métricas no formato Prometheus
* endpoints de saúde (liveness/readiness)

### Como habilitar

No `config/base.yaml` (ou no seu override), ajuste:

```yaml
observability:
  metrics_enable: true
  metrics_bind: 127.0.0.1:9108
```

### Endpoints

* `GET /metrics` — texto Prometheus.
* `GET /livez` — liveness probe.
* `GET /readyz` — readiness probe (503 quando o loop está bloqueado por precheck).
* `GET /healthz` — snapshot JSON (último ciclo + gates globais).

Exemplos:

```bash
curl -s http://127.0.0.1:9108/metrics | head
curl -s http://127.0.0.1:9108/readyz
```

## Structured logs (JSONL)

Além de logs de transcript, **Package P** cria um canal de logs estruturados em
JSONL (um JSON por linha), ideal para ingestão.

Config:

```yaml
observability:
  structured_logs_enable: true
  structured_logs_path: runs/logs/runtime_structured.jsonl
```

Eventos típicos:

* `runtime_daemon_cycle`
* `portfolio_cycle`
