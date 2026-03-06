# Package N — Execution Layer

O Package N adiciona uma camada explícita de execução reconciliada ao baseline
Python-first do Package M.

## Objetivo

Separar claramente:

- **sinal** (`signals_v2`)
- **ordem lógica** (`order_intents`)
- **tentativa física de submit** (`order_submit_attempts`)
- **estado visto no broker** (`broker_orders`)
- **trilha de eventos** (`order_events`)

## Fonte de verdade

DB canônico:

- `runs/runtime_execution.sqlite3`

Tabelas:

- `order_intents`
- `order_submit_attempts`
- `broker_orders`
- `order_events`
- `reconcile_cursors`

## Fluxo do Package N

```text
signals_v2 (CALL/PUT emitido)
    ->
OrderIntent (idempotente por candle)
    ->
submit attempt journal
    ->
broker order snapshot
    ->
reconciliation
    ->
intent_state terminal / aberto / pending_unknown
```

## Estados principais

### `intent_state`

- `planned`
- `submitted_unknown`
- `accepted_open`
- `rejected`
- `expired_unsubmitted`
- `expired_unconfirmed`
- `settled`
- `orphaned`
- `ambiguous`

### `broker_status`

- `unknown`
- `open`
- `closed_win`
- `closed_loss`
- `closed_refund`
- `rejected`
- `cancelled`
- `not_found`

## Adapters de broker

- `natbin.brokers.fake.FakeBrokerAdapter`
- `natbin.brokers.iqoption.IQOptionAdapter`

O adapter fake é o contrato validado em CI. O adapter IQ Option fica protegido
por lazy import para não quebrar ambientes sem a SDK.

## Integração com o runtime

- `runtime.observe_once` roda o observer legado
- em seguida chama `natbin.runtime.execution.process_latest_signal()`
- essa função:
  - reconcilia pendências
  - cria intent idempotente
  - submete (quando `execution.enabled=true`)
  - reconcilia novamente
  - escreve artefatos em `runs/control/<scope>/`

## Artefatos de controle novos

- `runs/control/<scope>/execution.json`
- `runs/control/<scope>/orders.json`
- `runs/control/<scope>/reconcile.json`

## Quota

Quando `execution.enabled=true`, a quota deixa de ler somente `executed` e passa
a preferir o execution ledger (`runtime_execution.sqlite3`).

Bloqueios novos de quota:

- `execution_pending_unknown`
- `execution_open_position`

