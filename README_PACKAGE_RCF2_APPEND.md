# Package RCF-2 — Observer decomposition + legacy boundary shrink

Este package fecha a próxima etapa da refatoração estrutural do Thalor v2.0.

## Entregas principais

- decomposição do observer TOPK em módulos menores sob `src/natbin/usecases/observer/`
- criação de `src/natbin/runtime/observer_surface.py` como superfície compartilhada entre runtime e observer
- redução de acoplamento entre `runtime.observe_once` e `observe_signal_topk_perday`
- redução de `src/natbin/config/compat_runtime.py` via extração do boundary legado para:
  - `src/natbin/config/compat_helpers.py`
  - `src/natbin/config/legacy_surface.py`
- preservação total do import path histórico:
  - `natbin.usecases.observe_signal_topk_perday`
  - `natbin.observe_signal_topk_perday`
- fix do caminho CP/CPREG no observer para não depender de variável intermediária inexistente

## Estrutura nova do observer

- `observer/config.py` — load_cfg / surface bridge
- `observer/model_cache.py` — cache / feat hash / retrain gate
- `observer/signal_store.py` — sqlite/csv/state/ledger helpers
- `observer/summary.py` — resumo diário
- `observer/selection.py` — regime mask + knobs do observer
- `observer/runner.py` — orquestração do loop latest/topk

## Resultado

O observer deixou de concentrar config bridge, cache, storage, summary e execução em um único arquivo grande.
A fronteira legada também ficou mais explícita e centralizada.
