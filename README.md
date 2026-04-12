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

# opcional: instalar entrypoints de console do pacote
.\.venv\Scripts\python.exe -m pip install -e .

# ambiente local / toggles
Copy-Item .env.example .env

# fluxo preferido de credenciais (Package M6)
Copy-Item .\config\broker_secrets.yaml.example .\config\broker_secrets.yaml
# ajuste THALOR_SECRETS_FILE no .env para apontar para config/broker_secrets.yaml
# ou use THALOR_BROKER_EMAIL_FILE / THALOR_BROKER_PASSWORD_FILE
```

> Dica: por padrão o bot usa `IQ_BALANCE_MODE=PRACTICE`.


## Execução sem ativar `.venv`

Você **não precisa ativar a `.venv` manualmente** para usar o control plane.
No Windows/PowerShell use o wrapper canônico abaixo, que resolve automaticamente
`\.venv\Scripts\python.exe` e injeta `src/` no `PYTHONPATH`:

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts	ools\invoke_runtime_app.ps1 status --json
pwsh -ExecutionPolicy Bypass -File .\scripts	ools\invoke_runtime_app.ps1 --config config\live_controlled_practice.yaml practice-preflight --json
```

Também existe uma rodada canônica do toolkit:

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts	oolsun_diagnostic_toolkit.ps1 -Config config\live_controlled_practice.yaml -DryRun
```

## Configuração

O runtime agora prefere `config/base.yaml` como configuração moderna do repo e,
se ele não existir, cai automaticamente para o legado `config.yaml`.
O observer legado passou a resolver a mesma config tipada; `config.yaml` só é
usado quando ele é o caminho selecionado ou quando `config/base.yaml` não existe.

Defaults principais do escopo:

- `asset` / `interval_sec` / `timezone` no primeiro item de `assets` (`config/base.yaml`)
- ou `data.asset` / `data.interval_sec` / `data.timezone` no legado `config.yaml`
- `data.db_path` (ex.: `data/market_otc.sqlite3`)

Compatibilidade e precedência:

- `.env` legado com `IQ_EMAIL` / `IQ_PASSWORD` / `IQ_BALANCE_MODE` continua suportado
- process env `THALOR__*` continua com precedência total sobre `IQ_*` e sobre o YAML
- o `.env` repo-local aplica por padrão apenas chaves modernas **seguras** (por exemplo `broker.*`, `security.*`, `notifications.*`, `production.*`)
- se você quiser que o `.env` local volte a sobrescrever comportamento (`execution.*`, `decision.*`, `quota.*`, `runtime.*`, `multi_asset.*`, `intelligence.*`), exporte `THALOR_DOTENV_ALLOW_BEHAVIOR=1` no processo antes de iniciar
- CLIs Python com `--repo-root` resolvem `config/base.yaml`, `config.yaml` e `.env` relativos à raiz informada
- perfis YAML modernos suportam `extends: base.yaml` com merge recursivo de mapas e substituição de listas/escalars pelo filho

## Compartilhar um ZIP limpo (Package M1)

Para gerar um pacote seguro de compartilhamento, sem `.env`, `.git`, `.venv`,
`data/`, `runs/` e caches locais:

```powershell
python -m natbin.release_hygiene --repo-root . --out exports/thalor_clean.zip --json
```

Alternativas equivalentes:

```powershell
python scripts/tools/release_bundle.py --repo-root . --out exports/thalor_clean.zip --json
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1 -Out exports\thalor_clean.zip -Json
```

Dry-run / auditoria sem gerar ZIP:

```powershell
python -m natbin.release_hygiene --repo-root . --dry-run --json
```

Docs: `docs/RELEASE_HYGIENE.md`

## Intelligence Layer (Package M5)

O runtime agora suporta um pack de inteligência por scope, usado para
enriquecer os candidatos antes da alocação no portfolio:

```powershell
python -m natbin.intelligence_pack --repo-root . --asset EURUSD-OTC --interval-sec 300 --json
```

Artefatos por scope:

- `runs/intelligence/<scope_tag>/pack.json`
- `runs/intelligence/<scope_tag>/latest_eval.json`
- `runs/intelligence/<scope_tag>/drift_state.json`
- `runs/intelligence/<scope_tag>/retrain_trigger.json` (quando houver trigger)
- `runs/intelligence/<scope_tag>/retrain_plan.json`
- `runs/intelligence/<scope_tag>/retrain_status.json`

A priorização do allocator agora usa `portfolio_score` quando disponível e o
painel local de inteligência passou a mostrar o estado de retrain/feedback do H12.

Docs: `docs/INTELLIGENCE_M5.md`, `docs/PHASE1_INTELLIGENCE_H11.md`, `docs/PHASE1_INTELLIGENCE_H12.md`

## Security hardening (Package M6)

O runtime agora tem uma trilha explícita para secrets/redaction/auditoria:

```powershell
python -m natbin.runtime_app security --repo-root . --json
```

Fluxo recomendado para live controlado:

- mantenha credenciais fora do YAML canônico
- use `config/broker_secrets.yaml` ou `secrets/broker.yaml` ignorado pelo git
- aponte `THALOR_SECRETS_FILE` para esse arquivo
- em live, prefira `security.live_require_external_credentials: true`

O dashboard local agora mostra um painel **Security (M6)** e os dumps de config
efetiva / artefatos de control plane passam a ser redigidos antes de serem
compartilhados. No INT-OPS-1, o portfolio status e o dashboard também passam a
exibir a superfície operacional da inteligência (`runtime_app intelligence`,
`runs/control/<scope>/intelligence.json` e o rollup multi-asset).

Docs: `docs/SECURITY_HARDENING_M6.md`

## Canonical state sync (SYNC-1)

O repo agora tem um comando explícito para congelar e comparar o estado real do
workspace local contra um baseline canônico. No hotfix SYNC-1A esse comando
passa a ter um entrypoint leve e roda mesmo antes do ambiente Python completo
estar instalado:

```powershell
python -m natbin.runtime_app sync --repo-root . --json
```

Regenerar os manifests canônicos quando um novo estado local for aceito:

```powershell
python -m natbin.runtime_app sync --repo-root . --freeze-docs --json
```

Esse fluxo grava:

- `runs/control/_repo/sync.json`
- `docs/canonical_state/published_main_baseline.json`
- `docs/canonical_state/workspace_manifest.json`

Docs: `docs/SYNC1_CANONICAL_STATE.md`, `docs/V2_PRODUCTION_NEXT_PACKAGES.md`

## Controlled practice readiness (READY-1)

O runtime agora tem um gate específico para o primeiro estágio live-controlado em
conta `PRACTICE`:

```powershell
python -m natbin.runtime_app practice --repo-root . --config config/live_controlled_practice.yaml --json
```

Esse comando consolida `doctor + intelligence + gates + soak recente` e grava
`runs/control/<scope_tag>/practice.json`. O dashboard local também passou a
mostrar um card **Practice (READY-1)**.

Docs: `docs/READY1_CONTROLLED_PRACTICE_READINESS.md`

## Controlled practice round (PRACTICE-OPS-1)

O próximo passo depois do READY-1 agora tem um runner canônico que compõe:

- validação READY-1
- soak automático quando necessário
- `controlled_live_validation --stage practice`
- report consolidado por scope em `runs/control/<scope_tag>/practice_round.json`

```powershell
python -m natbin.runtime_app practice-round --repo-root . --config config/live_controlled_practice.yaml --json
```

Wrapper direto:

```powershell
python scripts/tools/controlled_practice_round.py --repo-root . --config config/live_controlled_practice.yaml --json
```

Docs: `docs/PRACTICE_OPS_1_CONTROLLED_ROUND.md`


## Diagnostic toolkit canônico

O toolkit canônico agora também está integrado ao `runtime_app`:

```powershell
python -m natbin.runtime_app diag-suite --repo-root . --config config/live_controlled_practice.yaml --json --include-practice --include-provider-probe
python -m natbin.runtime_app transport-smoke --repo-root . --config config/live_controlled_practice.yaml --json
python -m natbin.runtime_app module-smoke --repo-root . --config config/live_controlled_practice.yaml --json
python -m natbin.runtime_app redaction-audit --repo-root . --config config/live_controlled_practice.yaml --json
python -m natbin.runtime_app practice-preflight --repo-root . --config config/live_controlled_practice.yaml --json
```

Wrappers equivalentes em `scripts/tools/`:

```powershell
python scripts/tools/diag_suite.py --repo-root . --config config/live_controlled_practice.yaml --json --include-practice --include-provider-probe
python scripts/tools/transport_smoke.py --repo-root . --config config/live_controlled_practice.yaml --json
python scripts/tools/module_smoke.py --repo-root . --config config/live_controlled_practice.yaml --json
python scripts/tools/redaction_audit.py --repo-root . --config config/live_controlled_practice.yaml --json
python scripts/tools/practice_preflight.py --repo-root . --config config/live_controlled_practice.yaml --json
```

Docs: `docs/DIAGNOSTIC_TOOLKIT_CANONICAL.md`

## Comandos principais

### 1) Control plane canônico (Package M)

O entrypoint operacional do runtime agora é o **control plane Python**:

```powershell
python -m natbin.runtime_app status --repo-root . --json
python -m natbin.runtime_app plan --repo-root . --json
python -m natbin.runtime_app quota --repo-root . --json
python -m natbin.runtime_app precheck --repo-root . --json
python -m natbin.runtime_app health --repo-root . --json
python -m natbin.runtime_app security --repo-root . --json
python -m natbin.runtime_app sync --repo-root . --json
python -m natbin.runtime_app intelligence --repo-root . --json
python -m natbin.runtime_app practice --repo-root . --config config/live_controlled_practice.yaml --json
python -m natbin.runtime_app practice-round --repo-root . --config config/live_controlled_practice.yaml --json
python -m natbin.runtime_app incidents status --repo-root . --json
python -m natbin.runtime_app incidents drill --repo-root . --scenario broker_down --json
python -m natbin.runtime_app portfolio status --repo-root . --json
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
