# Alerting (M7)

O M7 fecha a trilha de productization com um subsistema operacional de alertas.

## O que existe agora

- `runtime_app alerts status`
- `runtime_app alerts test`
- `runtime_app alerts release`
- `runtime_app alerts flush`
- outbox append-only em `runs/alerts/telegram_outbox.jsonl`
- estado de entrega em `runs/alerts/telegram_state.json`
- painel **Alerts (M7)** no dashboard

## Fluxo

1. O control plane resolve a configuração e as credenciais do Telegram.
2. Um alerta é serializado e persistido no outbox.
3. Se `notifications.telegram.send_enabled=true` e houver credenciais válidas,
   o envio é tentado imediatamente.
4. Se o envio não estiver ativo, o alerta fica em `queued`.
5. `runtime_app alerts flush` tenta reenviar itens `queued/failed`.

## Configuração

Exemplo em YAML:

```yaml
notifications:
  enabled: true
  history_limit: 200
  telegram:
    enabled: true
    send_enabled: false
    parse_mode: HTML
    outbox_path: runs/alerts/telegram_outbox.jsonl
    state_path: runs/alerts/telegram_state.json
```

## Credenciais suportadas

Ordem prática de resolução:

1. `THALOR_TELEGRAM_BOT_TOKEN_FILE` / `THALOR_TELEGRAM_CHAT_ID_FILE`
2. `THALOR_TELEGRAM_BOT_TOKEN` / `THALOR_TELEGRAM_CHAT_ID`
3. `THALOR_SECRETS_FILE` (`telegram.bot_token`, `telegram.chat_id`)
4. valores embutidos em `notifications.telegram.bot_token/chat_id`

Para live, prefira sempre `THALOR_SECRETS_FILE` ou arquivos externos.

## Operação rápida

### Ver status

```bash
python -m natbin.runtime_app alerts status --repo-root . --config config/multi_asset.yaml --json
```

### Gerar alerta de teste

```bash
python -m natbin.runtime_app alerts test --repo-root . --config config/multi_asset.yaml --json
```

### Gerar alerta de readiness

```bash
python -m natbin.runtime_app alerts release --repo-root . --config config/multi_asset.yaml --json
```

### Reenviar fila pendente

```bash
python -m natbin.runtime_app alerts flush --repo-root . --config config/multi_asset.yaml --limit 20 --json
```

## Observação importante

O outbox existe mesmo quando o envio está desligado. Isso é intencional:
permite auditar o que *teria* sido enviado sem depender de rede na CI/lab.
