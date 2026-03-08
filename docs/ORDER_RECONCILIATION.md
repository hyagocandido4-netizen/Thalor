# Package R — Order Reconciliation

## Princípio

O broker é a autoridade do resultado. O runtime local não deve inferir vitória
ou derrota como fonte canônica quando a informação do broker estiver
indisponível.

## Ordem de matching

1. `external_order_id`
2. `client_order_key`
3. fingerprint determinístico:
   - `asset`
   - `side`
   - `amount`
   - `expiry_ts`
   - janela temporal curta

## Resultados possíveis

### Match único

- atualiza `order_intents`
- atualiza `broker_orders`
- grava `reconcile_matched`
- transiciona para:
  - `accepted_open`
  - `settled`
  - `rejected`

### Nenhum match

- `planned` pode virar `expired_unsubmitted`
- `submitted_unknown` ou `accepted_open` podem virar `expired_unconfirmed`
  após grace window

### Match ambíguo

- intent vira `ambiguous`
- evento `reconcile_ambiguous`
- é um sinal de risco operacional real

### Ordem órfã

Quando o broker devolve uma ordem sem intent local correspondente:

- snapshot é persistido com `intent_id = NULL`
- evento `reconcile_orphan`
- isso deve ser tratado como incidente operacional

## Fake broker e CI

O fake broker persiste seu próprio estado em `runs/fake_broker_state.json`, o
que permite validar submit + reconcile mesmo quando o fluxo passa por
subprocessos.

## Comandos

- `python -m natbin.runtime_app orders --repo-root . --json`
- `python -m natbin.runtime_app reconcile --repo-root . --json`

