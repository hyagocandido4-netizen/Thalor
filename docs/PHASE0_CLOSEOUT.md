# Phase 0 — Closeout (Package W)

Data: **2026-03-09**

Este documento registra o fechamento formal da **Fase 0** (fundação/infra do monorepo).

## O que foi fechado

### 1) Dataset “sem 1-candle lag” ficou determinístico

- O pipeline do `dataset2.py` **mantém a última vela** (mesmo com `y_open_close = NaN`) para permitir inferência online na vela mais recente.
- Foi adicionado teste determinístico que:
  - cria um SQLite mínimo com candles;
  - roda `build_dataset` (P11 incremental) em modo full build;
  - valida que a última `ts` do dataset é a última `ts` do banco;
  - valida que existe **apenas 1** `NaN` no label (a última vela);
  - adiciona mais 1 candle e valida que o incremental atualiza corretamente.

### 2) CPREG formalizado como módulo “nativo”

- A lógica de CPREG (schedule de `CP_ALPHA` + slot-aware) foi centralizada em:
  - `src/natbin/runtime/gates/cpreg.py`
- O observer passou a chamar o helper único, reduzindo duplicação e código “patchy”.

### 3) Remoção de `scripts/patches/` do branch principal

- `scripts/patches/` era **histórico** e não deveria impactar runtime.
- A remoção reduz ruído e evita falhas de parse/linters em CI.
- **Histórico continua no git** (commits antigos preservam os arquivos).

### 4) Leak-check sem warnings ruidosos

- Removidos textos que acionavam warnings (ex.: “future …”) sem valor operacional.

## Checklist de validação

Rodar no repo:

```bash
pytest -q
python -m natbin.leak_check
python scripts/ci/smoke_execution_layer.py
python scripts/ci/smoke_runtime_app.py
```

## Observação

Este closeout não muda o roadmap de features (Fase 1+). Ele garante um baseline limpo, testável e consistente.


### 4) Organização final de subpastas
- Módulos canônicos movidos para `domain/`, `adapters/` e `usecases/`.
- Os módulos na raiz viraram *shims* compatíveis (`import *` + `main()` quando aplicável).

### 5) Lock definitivo do scheduler
- `natbin.ops.lockfile` agora é coberto por testes (`tests/test_lockfile.py`) para aquisição/liberação e remoção de lock stale.
