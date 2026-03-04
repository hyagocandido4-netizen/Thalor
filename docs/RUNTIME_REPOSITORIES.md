# Runtime Repositories

Este documento resume a camada de repositórios introduzida no **Package C** da refatoração.

## Objetivo

Separar o acesso a persistência/estado do código de decisão, sem alterar o comportamento do bot.

## Módulo

- `src/natbin/runtime_repos.py`

## Repositórios

### `SignalsRepository`
Responsável por:
- garantir schema de `signals_v2`
- gravar linhas duráveis no SQLite
- preservar a imutabilidade do primeiro `CALL/PUT` por candle
- buscar trades duráveis por `(asset, interval_sec, day[, ts])`
- listar dias recentes e trades por múltiplos dias

### `ExecutedStateRepository`
Responsável por:
- garantir schema do `executed`
- consultar contagem/último ts/exists por dia
- fazer `upsert` do cache operacional
- inserir linhas ignorando duplicatas (healing/reconcile)

### `RuntimeTradeLedger`
Fachada de alto nível para:
- `executed_today_count`
- `last_executed_ts`
- `already_executed`
- `mark_executed`
- `heal_state_from_signals`

## Princípio-chave

- `signals_v2` continua sendo a **fonte de verdade durável**
- `executed` continua sendo o **cache operacional**
- o ledger cura o state a partir do histórico durável quando necessário

## Smoke

```powershell
python scripts/tools/runtime_repos_smoke.py
```

Esse smoke valida que:
- o repositório de sinais preserva a imutabilidade do trade
- o ledger prefere `signals_v2` e cura o state
- o repositório de state aceita upsert/inserção ignorando duplicatas
