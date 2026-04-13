# Package PRACTICE-OPS-1B — Hotfix de soak/reconcile live

Este hotfix fecha o problema encontrado na rodada controlada em PRACTICE onde o runtime podia ficar preso indefinidamente dentro do reconcile live da IQ Option antes mesmo de produzir o summary do soak.

## O que foi corrigido

- `reconcile_scope()` agora **não faz scan do broker quando não existem intents pendentes**.
  - Isso elimina o caso mais comum de travamento em bootstrap/practice quando ainda não houve nenhuma ordem live.
- `IQClient.get_recent_closed_options()` agora tem **timeout guardado** e **cooldown**.
  - Se `get_optioninfo_v2()` travar, o runtime não fica mais bloqueado indefinidamente.
  - O histórico fechado volta como vazio temporariamente e a execução segue.
- `runtime_soak` agora escreve `runs/soak/soak_latest_<scope>.json` **desde o início** e atualiza o artefato mesmo em interrupção manual.

## Novos knobs operacionais

### Env vars

- `IQ_EXEC_HISTORY_TIMEOUT_S`
  - timeout do `get_optioninfo_v2()`
  - default do hotfix: `8.0`
- `IQ_EXEC_HISTORY_COOLDOWN_S`
  - cooldown após timeout do histórico fechado
  - default do hotfix: `300`
- `THALOR_RECONCILE_SCAN_WITHOUT_PENDING`
  - `1` para forçar scan completo do broker mesmo sem intents pendentes
  - default do hotfix: `0`

### Config

- `execution.reconcile.scan_without_pending: false`

## Fluxo recomendado após aplicar

```powershell
.\.venv\Scripts\python.exe scripts/tools/runtime_soak.py --repo-root . --config config/live_controlled_practice.yaml --asset EURUSD-OTC --interval-sec 300 --max-cycles 1

.\.venv\Scripts\python.exe -m natbin.runtime_app practice --repo-root . --config config/live_controlled_practice.yaml --json

.\.venv\Scripts\python.exe -m natbin.runtime_app practice-round --repo-root . --config config/live_controlled_practice.yaml --soak-cycles 1 --json
```

## Interpretação esperada

- se não houver intents pendentes, o reconcile preflight deve retornar rápido
- `runs/soak/soak_latest_EURUSD-OTC_300s.json` deve existir mesmo se você interromper manualmente
- se a rodada continuar em `HOLD` / `regime_block`, o gargalo deixou de ser operacional e passa a ser de inteligência/regime
