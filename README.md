# Thalor (iq-bot)

> ⚠️ **ALTO RISCO**: este projeto lida com **opções binárias** e automação de trading.
> Nada aqui é promessa de ganho, nem recomendação de investimento.
> O objetivo é **engenharia + evidência + controle de risco**.

**Thalor** (nome interno antigo: *iq-bot*) é um pipeline Windows-first para:

- coletar candles fechados da IQ Option (OTC ou não) em SQLite
- gerar dataset/feature store
- treinar/selecionar modelos com validação temporal (walk-forward / pseudo-futuro)
- produzir sinais LIVE **ultra-seletivos** (Top‑K por dia + gates de regime/EV + fail-closed)
- persistir e auditar tudo (CSV/SQLite + logs + status heartbeat)

## Requisitos

- Windows 10/11
- PowerShell 7 (`pwsh`)
- Python 3.12

## Setup

```powershell
# clone
git clone https://github.com/hyagocandido4-netizen/Thalor.git
cd Thalor

# venv
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# credenciais (NUNCA commitar)
Copy-Item .env.example .env
# edite .env com IQ_EMAIL / IQ_PASSWORD
```

> Dica: por padrão o bot usa `IQ_BALANCE_MODE=PRACTICE`.

## Configuração

O arquivo `config.yaml` define defaults do repo:

- `data.asset` (ex.: `EURUSD-OTC`)
- `data.interval_sec` (ex.: `300` para 5 min)
- `data.db_path` (ex.: `data/market_otc.sqlite3`)
- `data.timezone` (ex.: `America/Sao_Paulo`)

## Comandos principais

### 1) Rodar o loop de observação (recomendado)

O **orquestrador principal** é `scripts/scheduler/observe_loop_auto.ps1`.
Ele pode (dependendo das flags) preparar o contexto e então observar:

- coletar candles recentes
- atualizar dataset
- atualizar daily summary
- capturar *market context* (payout/open) com cache e *freshness guard*
- calcular decisão Top‑K (com gates)
- persistir sinal em `runs/live_signals.sqlite3` (tabela `signals_v2`)

Rodar **uma vez** (bom para debug):

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once -TopK 3
```

Rodar como **daemon** (loop infinito):

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -TopK 3
```

### 2) Relatório de risco (stake sizing conservador)

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000
```

Docs: `docs/risk_report.md`

## Variáveis de ambiente (as mais usadas)

Você pode setar por sessão (PowerShell) antes de iniciar o loop:

```powershell
$env:TOPK_ROLLING_MINUTES = "360"   # janela de ranking (rolling)
$env:TOPK_MIN_GAP_MINUTES = "30"    # cooldown entre trades
$env:TOPK_PACING_ENABLE   = "1"     # quota/pacing por dia
$env:VOL_TARGET_TRADES_PER_DAY = "3"

$env:GATE_FAIL_CLOSED = "1"                 # se gate falhar, HOLD
$env:MARKET_CONTEXT_FAIL_CLOSED = "1"       # se ctx/payout falhar, HOLD

# observabilidade
$env:LOOP_STATUS_ENABLE = "1"               # escreve status json (heartbeat)
$env:LOOP_LOG_RETENTION_DAYS = "14"         # rotação de logs
```

Lista completa e explicação: `docs/ENV_VARS.md`

## Artefatos de runtime

- `data/` → DBs locais (ignorado no git)
- `runs/` → logs/artefatos/caches (ignorado no git)
  - `runs/live_signals.sqlite3` (`signals_v2`) = fonte de verdade de sinais
  - `runs/logs/observe_loop_auto_YYYYMMDD.log` = transcript do loop
  - `runs/observe_loop_auto_status*.json` = status heartbeat (opcional)

Docs: `docs/OPERATIONS.md`

## CI

O CI faz *guardrails* rápidos e objetivos (sem rede):

- `compileall` (sintaxe Python)
- parse do PowerShell (scripts operacionais)
- detector de unicode/bidi invisível
- `selfcheck_repo.py` (imports + gitignore hygiene)
- `leak_check.py` (integrity workflow)

Docs: `docs/CI.md`

## Documentação

- `docs/ARCHITECTURE.md` — visão arquitetural (fluxo, DBs, estados)
- `docs/OPERATIONS.md` — runbook operacional
- `docs/ENV_VARS.md` — catálogo de env vars
- `docs/risk_report.md` — avaliação e stake sizing
- `docs/BACKLOG_BRAIN.md` — backlog do “cérebro” (ML + decisão)

## Nota sobre licença

Este repositório não inclui um arquivo de licença ainda.
Até uma licença ser adicionada, aplica-se o copyright padrão.
