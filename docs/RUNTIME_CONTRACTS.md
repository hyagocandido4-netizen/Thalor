# Runtime Contracts

Este documento resume os contratos duráveis introduzidos no **Package B** da refatoração.

## Objetivo

Tornar explícito o que antes estava espalhado:
- schema de `signals_v2`
- schema de `executed`
- versões dos contratos
- helpers de migração

## Módulos

- `src/natbin/runtime_contracts.py`
- `src/natbin/runtime_migrations.py`

## Tabelas duráveis

### `signals_v2`
Fonte de verdade dos sinais duráveis.

Chave primária:
- `(day, asset, interval_sec, ts)`

### `executed`
Cache/estado operacional do Top-K.

Chave primária:
- `(asset, interval_sec, day, ts)`

## Smoke

```powershell
python scripts/tools/runtime_contract_smoke.py
```

Esse smoke valida que:
- os contratos existem
- as migrações criam tabelas compatíveis
- PK e colunas mínimas batem com o esperado


## Relação com o Package C

No Package C, esses contratos passam a ser consumidos por uma camada explícita de repositórios (`runtime_repos.py`).
Ou seja: o schema continua centralizado em contratos/migrações, e o acesso operacional passa a usar repositórios/ledger.
