# Controlled live validation (M7.2)

Este pacote adiciona um trilho **seguro e auditável** para validar o Thalor na
sua máquina antes de abrir a operação live real.

A ideia é separar a validação em 4 estágios:

1. `baseline` — sem tocar no broker live
2. `practice` — usa o adapter live, mas na conta `PRACTICE`
3. `real_preflight` — usa a conta `REAL`, porém com **drain mode ligado**
4. `real_submit` — uma única janela de submit real, com dupla confirmação

## Arquivos principais

- `scripts/tools/controlled_live_validation.py`
- `scripts/tools/run_controlled_live_validation.ps1`
- `src/natbin/ops/live_validation.py`
- `config/live_controlled_practice.yaml.example`
- `config/live_controlled_real.yaml.example`

## Ordem recomendada

## 0) Preparar credenciais externas

Antes do primeiro estágio que você quiser ver **verde**, configure as credenciais
fora do YAML canônico:

```powershell
Copy-Item .\config\broker_secrets.yaml.example .\config\broker_secrets.yaml
$env:THALOR_SECRETS_FILE = (Resolve-Path .\config\broker_secrets.yaml)
```

Sem isso, `runtime_app security` e `runtime_app release` vão acusar corretamente
que faltam credenciais do broker.

### 1) Baseline

```powershell
.\.venv\Scripts\python.exe scripts\tools\controlled_live_validation.py --repo-root . --stage baseline
```

Esse estágio roda:
- `selfcheck_repo.py`
- `pytest -q`
- smokes principais
- `runtime_app security`
- `runtime_app health`
- `runtime_app release`
- `runtime_app incidents status`
- `runtime_app incidents drill`

### 2) Practice live

Copie o config exemplo:

```powershell
Copy-Item .\config\live_controlled_practice.yaml.example .\config\live_controlled_practice.yaml
```

Ajuste o asset e confirme que as credenciais do broker estão fora do YAML
canônico, usando por exemplo `THALOR_SECRETS_FILE`.

Rode:

```powershell
.\.venv\Scripts\python.exe scripts\tools\controlled_live_validation.py --repo-root . --stage practice --config config/live_controlled_practice.yaml
```

Esse estágio:
- valida `security/health/release`
- prepara dataset / market context
- roda `precheck` com `--enforce-market-context`
- executa `observe --once` no adapter live **em PRACTICE**
- inspeciona `orders`
- roda `reconcile`
- consulta `incidents status`

Observação importante: se não surgir candidato naquele candle, o relatório vai
mostrar que o ciclo rodou, mas pode não existir submit. Isso não é bug por si
só.

### 3) REAL preflight sem submit

Copie o config exemplo:

```powershell
Copy-Item .\config\live_controlled_real.yaml.example .\config\live_controlled_real.yaml
```

Rode:

```powershell
.\.venv\Scripts\python.exe scripts\tools\controlled_live_validation.py --repo-root . --stage real_preflight --config config/live_controlled_real.yaml
```

Esse estágio liga **drain mode** no início, roda um ciclo com conta `REAL`, mas
sem permitir submit novo, e fecha com `orders/reconcile/incidents`.

Esse é o estágio mais importante antes do primeiro dinheiro real.

### 4) Primeiro submit real mínimo

Só faça isso depois de `practice` e `real_preflight` limpos.

O runner exige duas proteções explícitas:
- `--allow-live-submit`
- `--ack-live I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT`

Comando:

```powershell
.\.venv\Scripts\python.exe scripts\tools\controlled_live_validation.py --repo-root . --stage real_submit --config config/live_controlled_real.yaml --allow-live-submit --ack-live I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT
```

Esse estágio:
- desliga `drain` para uma janela controlada
- confere `killswitch`
- roda `security/health/release/precheck`
- executa um único `observe --once`
- coleta `orders`
- roda `reconcile`
- gera `incidents report`
- religa `drain` no fim

## Relatórios

Cada execução gera um JSON em:

```text
runs/tests/controlled_live_validation_<stage>_YYYYMMDDTHHMMSSZ.json
```

Use esse arquivo como evidência operacional do que foi testado.

## Wrapper PowerShell

Exemplo:

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\run_controlled_live_validation.ps1 -Stage practice -Config config/live_controlled_practice.yaml
```

## Regras operacionais

- manter `multi_asset.enabled: false` no primeiro live real
- manter stake mínima
- usar uma única scope/asset
- preferir `PRACTICE` até ver `orders + reconcile + incidents` estáveis
- após o primeiro ciclo real, voltar para `drain on` e revisar o relatório

## Interpretação

- falha em `baseline` => não tocar em broker live
- falha em `practice` => não abrir `REAL`
- falha em `real_preflight` => não autorizar submit real
- `real_submit` só serve para **um primeiro ciclo controlado**, não para abrir a
  operação contínua ainda
