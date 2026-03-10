# CI do Thalor

O CI existe para impedir que o projeto volte a um estado “quebradiço”.
A ideia é ter checks rápidos, determinísticos e sem rede.

## Workflows

### `.github/workflows/ci.yml`

Checks principais:

- **Install deps**
- **Python syntax** (`compileall`)
- **PowerShell syntax** (parse dos scripts operacionais)
- **Hidden unicode / bidi guard**
- **Repo selfcheck** (`scripts/tools/selfcheck_repo.py`)
- **Regression smoke** (`scripts/tools/regression_smoke.py`)
- **Runtime contracts smoke** (`scripts/tools/runtime_contract_smoke.py`)
- **Runtime repositories smoke** (`scripts/tools/runtime_repos_smoke.py`)

> Nota: os scripts de patches/migrações históricos foram removidos do branch `main` (permanecem no histórico do git).
> O CI foca apenas nos scripts **operacionais** (ex.: `scripts/scheduler/`, `scripts/ci/`, `scripts/tools/`).

### `.github/workflows/integrity.yml`

- roda `scripts/tools/leak_check.py` (garantir que não vazamos segredos/artefatos no repo)

## Rodando localmente

```powershell
# syntax python
.\.venv\Scripts\python.exe -m compileall -q src/natbin

# selfcheck
.\.venv\Scripts\python.exe scripts/tools/selfcheck_repo.py

# unicode guard
.\.venv\Scripts\python.exe scripts/tools/check_hidden_unicode.py

# regression smoke
.\.venv\Scripts\python.exe scripts/tools/regression_smoke.py

# runtime contracts smoke
.\.venv\Scripts\python.exe scripts/tools/runtime_contract_smoke.py

# runtime repositories smoke
.\.venv\Scripts\python.exe scripts/tools/runtime_repos_smoke.py
```

## Filosofia

- CI não deve tentar “rodar o bot” nem conectar na IQ.
- CI deve travar regressões como:
  - imports quebrados
  - scripts PS com sintaxe inválida
  - caracteres invisíveis/bidi maliciosos
  - `.env`/`runs/`/`data/` não ignorados
  - invariantes críticos de schema/persistência


## Smokes adicionais

- `scripts/tools/runtime_contract_smoke.py`
- `scripts/tools/runtime_repos_smoke.py`
- `scripts/tools/runtime_orchestration_smoke.py`


## Smoke do pacote F

O CI também roda `scripts/tools/autos_refactor_smoke.py` para validar a camada
de políticas dos autos após a refatoração.


## Smokes adicionais

- `scripts/tools/runtime_observability_smoke.py` valida a camada de snapshots/incidentes de runtime.


## Smoke adicional do Package H

O CI agora executa também:

```powershell
python scripts/tools/runtime_scope_smoke.py
```

Ele valida paths escopados e os helpers de cache/IO do pacote H.


## Smoke adicional do Package I

O CI agora executa também:

```powershell
python scripts/tools/runtime_cycle_smoke.py
```

Ele valida o plano e a CLI Python de ciclo do runtime (`natbin.runtime_cycle`).


O CI também valida `runtime_daemon_smoke.py`, cobrindo o daemon Python aditivo do Package J.


O CI também valida `runtime_quota_smoke.py`, cobrindo o snapshot Python de quota/pacing do Package K.
