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

O runtime agora prefere `config/base.yaml` como configuração moderna do repo e,
se ele não existir, cai automaticamente para o legado `config.yaml`.

Defaults principais do escopo:

- `asset` / `interval_sec` / `timezone` no primeiro item de `assets` (`config/base.yaml`)
- ou `data.asset` / `data.interval_sec` / `data.timezone` no legado `config.yaml`
- `data.db_path` (ex.: `data/market_otc.sqlite3`)

Compatibilidade e precedência:

- `.env` legado com `IQ_EMAIL` / `IQ_PASSWORD` / `IQ_BALANCE_MODE` continua suportado
- overrides modernos `THALOR__*` têm precedência sobre `IQ_*` e sobre o YAML
- CLIs Python com `--repo-root` resolvem `config/base.yaml`, `config.yaml` e `.env` relativos à raiz informada

## Comandos principais

### 1) Control plane canônico (Package M)

O entrypoint operacional do runtime agora é o **control plane Python**:

```powershell
python -m natbin.runtime_app status --repo-root . --json
python -m natbin.runtime_app plan --repo-root . --json
python -m natbin.runtime_app quota --repo-root . --json
python -m natbin.runtime_app precheck --repo-root . --json
python -m natbin.runtime_app health --repo-root . --json
```

Rodar **um ciclo**:

```powershell
python -m natbin.runtime_app observe --repo-root . --once --topk 3
```

Rodar em **loop**:

```powershell
python -m natbin.runtime_app observe --repo-root . --topk 3
```

### 2) Wrapper fino para o Task Scheduler

O `scripts/scheduler/observe_loop_auto.ps1` continua existindo, mas agora só
resolve Python + `PYTHONPATH` e chama `runtime_app observe`.

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once -TopK 3
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

> Runtime hardening: o auto-loop agora aplica timeouts explícitos para `collect_recent`, `make_dataset`, `refresh_daily_summary`, autos e `observe_loop.ps1`.

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

## Contratos de runtime

A refatoração agora tem um módulo explícito de contratos/migrações:

- `src/natbin/runtime_contracts.py` — schemas/versionamento dos artefatos duráveis
- `src/natbin/runtime_migrations.py` — migrações explícitas de `signals_v2` e `executed`

Smokes específicos dos pacotes:

```powershell
python scripts/tools/runtime_contract_smoke.py
python scripts/tools/runtime_repos_smoke.py
python scripts/tools/runtime_orchestration_smoke.py
python scripts/tools/autos_refactor_smoke.py
python scripts/tools/runtime_observability_smoke.py
python scripts/tools/runtime_scope_smoke.py
python scripts/tools/runtime_cycle_smoke.py
```

## Documentação

- `docs/ARCHITECTURE.md` — visão arquitetural (fluxo, DBs, estados)
- `docs/RUNTIME_REPOSITORIES.md` — camada de repositórios/ledger do runtime
- `docs/AUTOS_POLICY_LAYER.md` — camada de políticas/refatoração dos autos
- `docs/OBSERVABILITY.md` — snapshots estruturados, incidentes e health report
- `docs/RUNTIME_SCOPE.md` — camada canônica de paths escopados + helpers de performance
- `docs/RUNTIME_CYCLE.md` — plano/CLI Python de um ciclo do runtime (fundação para afinar o shell)
- `docs/RUNTIME_QUOTA.md` — camada Python de quota/pacing para o daemon
- `docs/OPERATIONS.md` — runbook operacional
- `docs/ENV_VARS.md` — catálogo de env vars
- `docs/risk_report.md` — avaliação e stake sizing
- `docs/BACKLOG_BRAIN.md` — backlog do “cérebro” (ML + decisão)

## Nota sobre licença

Este repositório não inclui um arquivo de licença ainda.
Até uma licença ser adicionada, aplica-se o copyright padrão.


## Runtime daemon / cycle / state

Package M estabelece o split base:

- `runtime_app` = control plane
- `runtime.daemon` = execution engine
- `runtime.cycle` = plano canônico Python
- `state.*` = contratos duráveis / repositórios / artefatos do control plane

O CLI direto do daemon ainda existe para compatibilidade e debug:

```powershell
python -m natbin.runtime_daemon --plan-json
python -m natbin.runtime_daemon --repo-root . --quota-json
```
