# Local testing guide (M7.1 test-ready bundle)

Este bundle já contém o projeto inteiro **mais** um runner local para executar a
suíte recomendada na sua máquina sem precisar sair procurando cada comando.

## 1) Setup mínimo

Windows 10/11, PowerShell 7 e Python 3.12.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## 2) Arquivos locais de ambiente

```powershell
Copy-Item .env.example .env
Copy-Item .\config\broker_secrets.yaml.example .\config\broker_secrets.yaml
```

Mantenha o ambiente em **PRACTICE** no primeiro teste.

## 3) Teste rápido

```powershell
.\.venv\Scripts\python.exe scripts\tools\local_test_suite.py --repo-root . --preset quick
```

Esse preset roda:
- `selfcheck_repo.py`
- `pytest -q`
- `smoke_runtime_app.py`
- `smoke_execution_layer.py`

## 4) Teste completo recomendado

```powershell
.\.venv\Scripts\python.exe scripts\tools\local_test_suite.py --repo-root . --preset full
```

Ou pelo wrapper PowerShell:

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\run_local_test_suite.ps1 -Preset full
```

O preset completo roda também:
- `release_hygiene_smoke.py`
- `broker_adapter_contract_smoke.py`
- `runtime_execution_integration_smoke.py`
- `runtime_hardening_smoke.py`
- `portfolio_risk_smoke.py`
- `intelligence_pack_smoke.py`
- `security_hardening_smoke.py`
- `productization_smoke.py`
- `incident_ops_smoke.py`

## 5) Soak opcional

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\run_local_test_suite.ps1 -Preset full -IncludeSoak
```

ou

```powershell
.\.venv\Scripts\python.exe scripts\tools\local_test_suite.py --repo-root . --preset full --include-soak
```

## 6) Onde sai o relatório

Cada execução gera um relatório em:

```text
runs/tests/local_test_suite_YYYYMMDDTHHMMSSZ.json
```

## 7) Ordem prática que eu recomendo

1. `quick`
2. `full`
3. dashboard local
4. practice/paper validation
5. só depois live controlado

## 8) Subir o dashboard

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\run_dashboard.ps1
```

## 9) Geração de ZIP limpo depois dos testes

```powershell
.\.venv\Scripts\python.exe -m natbin.release_hygiene --repo-root . --out exports\thalor_clean.zip --json
```


## 10) Validação live controlada

Depois do `full`, use o runner `scripts/tools/controlled_live_validation.py`.
Guia: `docs/CONTROLLED_LIVE_VALIDATION_M72.md`.
