# Logging & Artefatos (Package U)

O runtime escreve **artefatos JSON/CSV/SQLite** em `runs/` e também alguns logs em JSONL (um JSON por linha).

## Principais arquivos

### Portfolio / Multi-asset

- `runs/portfolio_cycle_latest.json`  
  Snapshot do último ciclo multi-asset (prepare -> candidate -> allocate -> execute -> persist).

- `runs/allocation_latest.json`  
  Última alocação do portfólio (selecionados/suprimidos).

- `runs/logs/portfolio_cycle.jsonl`  
  Linha por ciclo (evento `portfolio_cycle`).

- `runs/logs/portfolio_candidate.jsonl`  
  Linha por scope/candidate (evento `portfolio_candidate`).

### Execução

- `runs/runtime_execution.sqlite3`  
  Banco do execution layer (intents + events).

- `runs/logs/execution_events.jsonl`  
  (novo no Package U) espelha os eventos gravados no SQLite em um JSONL
  fácil de `tail`/`grep`.

  Cada linha contém campos como:
  - `event_id`
  - `created_at_utc`
  - `intent_id`
  - `event_type`
  - `payload`

## Por que JSONL?

- Fácil de consumir em ferramentas (jq, pandas, etc.)
- Append-only (bom para produção)
- “Logs estruturados” > logs textuais quando você quer debugar pipeline

## Dica rápida

```bash
# ver os últimos ciclos do portfolio
tail -n 20 runs/logs/portfolio_cycle.jsonl

# ver eventos de execução
tail -n 50 runs/logs/execution_events.jsonl
```
