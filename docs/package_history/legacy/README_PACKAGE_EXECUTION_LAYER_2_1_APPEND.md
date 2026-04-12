# Package 2.1 — Execution Layer Completo

Inclui submit real via `iqoptionapi` (fail-closed e PRACTICE por padrão), reconciliação por ordem, logs estruturados e comandos operacionais:

- `runtime_app execute-order` / `runtime_app execute_order`
- `runtime_app check-order-status` / `runtime_app check_order_status`
- `runtime_app orders`
- `runtime_app reconcile`

Segurança adicional:

- `execution.account_mode` continua `PRACTICE` por padrão.
- Conta `REAL` nunca executa implicitamente; exige `THALOR_EXECUTION_ALLOW_REAL=1`.
