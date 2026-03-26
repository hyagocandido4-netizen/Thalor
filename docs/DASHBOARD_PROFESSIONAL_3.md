# Dashboard Profissional Thalor — Package 3

O Package 3 transforma o dashboard local em um **control deck profissional** para operação e análise.

## Entregas principais

- visual dark-mode com identidade "cyber-dragon"
- KPIs de performance a partir do ledger de execução
- equity curve e drawdown em tempo real
- board unificado por asset
- feed de alertas recentes
- export de relatório em **HTML + JSON**

## Fontes de dados usadas

O dashboard lê, de forma read-only quando possível:

- `runs/runtime_execution.sqlite3`
- `runs/logs/execution_events.jsonl`
- `runs/logs/account_protection.jsonl`
- `runs/portfolio_*_latest.json`
- payloads do control plane (`health`, `release`, `practice`, `doctor`, `portfolio`, `alerts`, `incidents`)

## KPIs calculados

A partir das ordens reconciliadas / snapshots do broker:

- `current_equity`
- `pnl_total`
- `win_rate`
- `ev_brl`
- `expectancy_r`
- `max_drawdown_brl`
- `max_drawdown_pct`
- `sharpe_per_trade`
- `profit_factor`
- `avg_latency_ms`

## Export de relatório

O export gera:

- um arquivo HTML pronto para abrir no navegador
- um JSON com o snapshot usado para gerar o relatório

Comando:

```powershell
python -m natbin.dashboard.report --repo-root . --config config/multi_asset.yaml --json
```

Por padrão o output vai para:

```text
runs/reports/dashboard/
```

## Observações de segurança

- o dashboard **não envia ordens**
- o export é apenas leitura + materialização de relatório
- não há promoção automática para `REAL`

## Próximo package recomendado

Depois do dashboard profissional, a próxima etapa da Fase 2 é:

- **Pacote 4 – Simulação Monte Carlo Realista**
