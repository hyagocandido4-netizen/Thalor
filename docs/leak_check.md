# Leak check

O projeto usa avaliação temporal (walk-forward / pseudo-futuro) e ONLINE Top‑K por dia.
Pequenos bugs de *leakage* podem inflar paper/tune e quebrar no LIVE.

Este utilitário é um *guardrail* (não é prova formal de ausência de leakage).

## Code mode (CI-safe)

```powershell
python src/natbin/leak_check.py --mode code
```

- Escaneia `src/natbin/*.py` em busca de padrões comuns de lookahead (shift/roll negativos em contexto de features, etc.)
- Só usa stdlib, então roda em CI mesmo sem dataset.

## Data mode (local)

```powershell
python src/natbin/leak_check.py --mode data --csv data/dataset_phase2.csv --label y_open_close
```

- `data/` é ignorado pelo git, então isso é para rodar localmente.
- Se `pandas` existir, roda heurísticas extras (ex.: correlação extrema com o label).

## Severidade

- `ERROR`: provável leakage (corrigir)
- `WARN`: suspeito (revisar)
