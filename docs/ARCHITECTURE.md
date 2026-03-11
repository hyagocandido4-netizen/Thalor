# Arquitetura do Thalor

> Este documento descreve o fluxo principal e as fontes de verdade do sistema.

## Visão geral (Package M + Package N)

```text
Task Scheduler / operador
    ->
scripts/scheduler/observe_loop*.ps1   (bootstrap fino)
    ->
python -m natbin.runtime_app observe --repo-root .
    ->
natbin.control.*                      (control plane)
    ->
natbin.runtime.*                      (cycle + precheck + execution orchestration)
    ->
natbin.state.*                        (repos + control artifacts + ledgers)
    ->
módulos legados de coleta / dataset / observer
```

## Regras centrais do baseline atual

1. `runtime_app` é o entrypoint canônico do runtime.
2. `observe_loop*.ps1` são wrappers finos.
3. `config/base.yaml` é o config preferido do control plane.
4. `config.yaml` continua existindo como compatibilidade para o observer legado.
5. `repo_root` ancora config, `.env`, `runs/` e `config/base.yaml`.
6. O plano canônico do ciclo é Python, não PowerShell.
7. Package N adiciona execução reconciliada sem recolocar lógica no PowerShell.

## Fluxo principal

1. **Control plane / config**
   - `runtime_app` resolve `repo_root`
   - prefere `config/base.yaml`
   - faz fallback para `config.yaml`
   - escreve effective config

2. **Coleta de candles**
   - `src/natbin/collect_recent.py`
   - DB: `data/market_otc.sqlite3`

3. **Dataset / features**
   - `src/natbin/make_dataset.py`
   - output legado: `data/dataset_phase2.csv`

4. **Daily summary**
   - `src/natbin/refresh_daily_summary.py`
   - artefatos em `runs/`

5. **Observer / decisão**
   - `src/natbin/observe_signal_topk_perday.py`
   - `runtime.observe_once` chama esse observer a partir do plano Python

6. **Quota / precheck / failsafe**
   - `natbin.runtime.quota`
   - `natbin.runtime.precheck`
   - `natbin.runtime.failsafe`

7. **Execution & reconciliation**
   - `natbin.runtime.execution`
   - `natbin.runtime.reconciliation`
   - `natbin.state.execution_repo`
   - `natbin.brokers.*`

8. **Loop / daemon**
   - `natbin.runtime.daemon`
   - lock escopado por `(asset, interval_sec)`
   - status / health / control snapshots

## Fontes de verdade atuais

- **Candles**: `data/market_otc.sqlite3:candles`
- **Sinais**: `runs/live_signals.sqlite3:signals_v2`
- **Estado Top-K legado**: `runs/live_topk_state.sqlite3`
- **Execution ledger**: `runs/runtime_execution.sqlite3`
- **Control-plane artifacts**: `runs/control/<scope>/...`
- **Control DB**: `runs/runtime_control.sqlite3`

## Artefatos do control plane

Por scope:

- `runs/control/<scope>/plan.json`
- `runs/control/<scope>/quota.json`
- `runs/control/<scope>/precheck.json`
- `runs/control/<scope>/health.json`
- `runs/control/<scope>/loop_status.json`
- `runs/control/<scope>/effective_config.json`
- `runs/control/<scope>/execution.json`
- `runs/control/<scope>/orders.json`
- `runs/control/<scope>/reconcile.json`
- `runs/control/<scope>/guard.json`
- `runs/control/<scope>/lifecycle.json`

## Package M3 — runtime guard

O runtime mantém agora duas peças adicionais de controle por scope:

- `guard.json`: diagnóstico de frescor dos artefatos `latest`, incluindo lock
  ativo e ações de invalidação de snapshots stale.
- `lifecycle.json`: último evento operacional (`startup` / `shutdown`) do
  runtime, útil para restart recovery e soak tests.

Além disso, o modo `observe --once` usa o mesmo lock do daemon para fechar o
buraco de overlap entre execução manual, scheduler e debug local.

## Notas de compatibilidade

O observer legado ainda depende de campos do `config.yaml` que ainda não foram
migrados para o modelo tipado. Por isso o Package M fecha o control plane e o
ciclo Python sem ainda matar o `config.yaml` como insumo do observer legado.
Essa limpeza total fica para os packages seguintes.
