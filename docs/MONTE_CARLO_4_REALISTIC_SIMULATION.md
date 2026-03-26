# Monte Carlo 4 — Simulação realista baseada no histórico do Thalor

## Objetivo

Adicionar uma trilha reproduzível de projeção de capital usando os trades
realizados já registrados no ledger de execução do projeto.

## Fonte de dados

A simulação lê o banco:

- `runs/runtime_execution.sqlite3`

Tabela usada:

- `broker_orders`

Somente trades realizados são considerados (`win`, `loss`, `refund`,
`cancelled`, além de snapshots fechados/settled inferidos pelo runtime).

## Modelo de simulação

O engine usa bootstrap empírico dos componentes observados no histórico:

- retorno por trade (`net_pnl / amount`)
- stake por trade (`amount`)
- frequência diária observada

Cada cenário aplica fatores explícitos e configuráveis sobre a base empírica:

- **Conservador** → menos frequência, retorno e stake menores
- **Médio** → baseline histórico
- **Agressivo** → mais frequência, retorno e stake maiores

A intenção é produzir projeções claras sem abandonar a distribuição real já
observada no projeto.

## Comando

```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app monte-carlo --repo-root . --config config/multi_asset.yaml --json
```

Ou diretamente:

```powershell
.\.venv\Scripts\python.exe -m natbin.monte_carlo --repo-root . --config config/multi_asset.yaml --json
```

## Artefatos gerados

Por padrão:

- `runs/reports/monte_carlo/monte_carlo_latest.json`
- `runs/reports/monte_carlo/monte_carlo_latest.html`
- `runs/reports/monte_carlo/monte_carlo_latest.pdf`

Também são gerados arquivos timestamped para histórico dos relatórios.

## Critérios de uso seguro

- não executa ordens
- não escreve em broker
- opera somente em cima do ledger histórico local
- falha com `insufficient_history` quando não há amostra mínima suficiente

## Smoke recomendado

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_monte_carlo_package_4.py

$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/monte_carlo_package_4_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
