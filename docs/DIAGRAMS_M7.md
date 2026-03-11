# Diagramas operacionais (M7)

## Visão de alto nível

```text
+---------------------+
| runtime_app         |
| (control plane)     |
+----------+----------+
           |
           +---------------------------+
           |                           |
           v                           v
+---------------------+      +----------------------+
| portfolio/runtime   |      | release / alerts     |
| observe / execute   |      | readiness / telegram |
+----------+----------+      +----------+-----------+
           |                            |
           v                            v
+---------------------+      +----------------------+
| runs/control/*      |      | runs/alerts/*        |
| health/precheck/... |      | outbox/state         |
+----------+----------+      +----------+-----------+
           |                            |
           +-------------+--------------+
                         |
                         v
                +----------------+
                | dashboard       |
                | local read-only |
                +----------------+
```

## Fluxo do alerting

```text
runtime_app alerts release
        |
        v
build release payload
        |
        v
serialize alert -> telegram_outbox.jsonl
        |
        +--> send_enabled=false  -> queued
        |
        +--> send_enabled=true   -> Telegram API
                                   |
                                   +--> sent
                                   +--> failed
```

## Fluxo do checklist de produção

```text
build_context
   + security audit
   + gate status
   + docker/docs presence
   + release hygiene report
   + telegram readiness
   + execution mode
        |
        v
release.json
```
