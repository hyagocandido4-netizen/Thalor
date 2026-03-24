# RCF-2 — Observer decomposition + legacy boundary shrink

## Objetivo

Fechar a próxima etapa da refatoração do Thalor v2.0 reduzindo o tamanho do observer TOPK, eliminando duplicação de bridge entre runtime e observer, e isolando melhor o contrato legado baseado em env vars.

## O que mudou

### 1. Superfície compartilhada runtime/observer

Foi criado `src/natbin/runtime/observer_surface.py`.

Ele concentra:
- resolução do `repo_root`
- resolução do `config_path`
- carregamento do config tipado
- flatten do observer para `(cfg, best)`
- geração do mapa legado de env
- montagem do ambiente escopado usado pelo `runtime.observe_once`

Com isso, `load_cfg()` do observer e `prepare_observer_environment()` do runtime deixam de reimplementar a mesma lógica por caminhos diferentes.

### 2. Decomposição do observer TOPK

O antigo `src/natbin/usecases/observe_signal_topk_perday.py` virou uma façade compatível.
A lógica foi quebrada em:

- `observer/config.py`
- `observer/model_cache.py`
- `observer/signal_store.py`
- `observer/summary.py`
- `observer/selection.py`
- `observer/runner.py`

Benefícios:
- menor blast radius para mudanças futuras
- mais fácil testar por responsabilidade
- menos chance de regressão ao mexer em persistência, cache ou decisão

### 3. Encolhimento do boundary legado

A antiga `config/compat_runtime.py` concentrava:
- helpers genéricos
- mapeamento do resolved config para payload legado
- mapeamento do payload legado para env vars
- fallback env-only
- regras específicas de CP/CPREG/META_ISO_BLEND/REGIME_MODE/MARKET_OPEN

Agora isso foi separado em:

- `config/compat_helpers.py` — helpers utilitários
- `config/legacy_surface.py` — payload/env map legado
- `config/compat_runtime.py` — wrapper fino da compat layer

Isso reduz a dispersão de knobs legados como:
- `CP_ALPHA`
- `CPREG_ENABLE`
- `CPREG_ALPHA_START`
- `CPREG_ALPHA_END`
- `CPREG_SLOT2_MULT`
- `META_ISO_BLEND`
- `REGIME_MODE`
- `MARKET_OPEN`

## Compatibilidade preservada

Os import paths históricos continuam válidos:
- `natbin.usecases.observe_signal_topk_perday`
- `natbin.observe_signal_topk_perday`

Scripts antigos e smoke tests continuam funcionando porque a façade reexporta os helpers públicos esperados.

## Correção embutida no RCF-2

O caminho CP/CPREG do observer foi endurecido para não depender de variável intermediária inexistente durante a aplicação dinâmica do `CP_ALPHA`.

## Validação executada

- `PYTHONPATH=src pytest -q`
- `PYTHONPATH=src python scripts/tools/selfcheck_repo.py`

Status local do package:
- testes verdes
- surface compartilhada validada por teste dedicado
- fluxo CP/CPREG do runner validado por teste dedicado

## Próximo passo

Depois deste package, a sequência natural continua em:

- RCF-3 — Execution/Broker split
