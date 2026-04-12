# Package RCF-3 — Execution/Broker split

Este package fecha a próxima etapa da refatoração estrutural do núcleo live.

## Objetivo

Reduzir acoplamento entre:
- `runtime.execution`
- `runtime.reconciliation`
- construção de adapter do broker
- política de submit / reconciliação / escrita de artefatos

## O que entrou

- `src/natbin/runtime/broker_surface.py`
- `src/natbin/runtime/execution_signal.py`
- `src/natbin/runtime/execution_submit.py`
- `src/natbin/runtime/execution_artifacts.py`
- `src/natbin/runtime/execution_process.py`
- `src/natbin/runtime/reconciliation_core.py`
- `src/natbin/runtime/reconciliation_flow.py`
- `src/natbin/runtime/execution.py` virou façade compatível
- `src/natbin/runtime/reconciliation.py` virou façade compatível
- `src/natbin/ops/production_doctor.py` agora consome `runtime.broker_surface`

## Resultado prático

- `runtime.execution` deixa de concentrar construção de adapter, leitura de sinal, submit, gating, reconciliação e CLI no mesmo arquivo.
- `runtime.reconciliation` deixa de concentrar matching + batch flow no mesmo módulo público.
- a superfície do broker fica isolada e reaproveitável fora da trilha de execução.
- imports históricos continuam funcionando.

## Compatibilidade

Não muda contrato externo do CLI:

- `python -m natbin.runtime.execution process`
- `python -m natbin.runtime.execution orders`
- `python -m natbin.runtime.execution reconcile`

## Próximo package

- `INT-OPS-2 — Stabilize retrain + anti-overfit`
