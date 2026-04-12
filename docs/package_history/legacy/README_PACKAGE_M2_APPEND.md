# Package M2 — Live Execution Bridge

Data: **2026-03-10**

Este pacote fecha o adapter live do broker no contrato novo de execução.
O foco é tirar o `iqoption` do estado de placeholder e colocá-lo em um estado
operacional minimamente seguro dentro do runtime v2.

## Entregas

- `src/natbin/brokers/iqoption.py`
  - submit live real para binária/turbo (`CALL` / `PUT`)
  - `healthcheck` real
  - `fetch_order`, `fetch_open_orders`, `fetch_closed_orders`
  - bridge-state local em `runs/iqoption_bridge_state.json`
  - fail-closed quando `execution.mode != live`

- `src/natbin/adapters/iq_client.py`
  - helpers canônicos para submit/reconcile live (`submit_binary_option`, `get_betinfo_safe`, `get_recent_closed_options`, streams de ordem)

- `src/natbin/runtime/execution.py`
  - `adapter_from_context()` agora injeta `repo_root`, `broker_config`, modo de execução e grace window do reconcile

- testes / smoke:
  - `tests/test_iqoption_adapter.py`
  - `scripts/tools/broker_adapter_contract_smoke.py`

## Regra operacional importante

`provider: iqoption` só deve submeter ordem real quando:

- `execution.enabled: true`
- `execution.mode: live`

Se `provider: iqoption` for combinado com `mode: paper`, o adapter fica em
**fail-closed** e rejeita submits por segurança.

## Limites atuais

- escopo atual: opções binárias/turbo
- descoberta de órfãs fora da sessão atual é best-effort
- hardening 24/7, soak e restart-recovery profundo continuam sendo trabalho do M3
