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

> Importante: scripts antigos em `scripts/patches/` podem conter trechos não‑PS1 válidos.
> O CI deve focar nos scripts **operacionais** (ex.: `scripts/scheduler/`).

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
```

## Filosofia

- CI não deve tentar “rodar o bot” nem conectar na IQ.
- CI deve travar regressões como:
  - imports quebrados
  - scripts PS com sintaxe inválida
  - caracteres invisíveis/bidi maliciosos
  - `.env`/`runs/`/`data/` não ignorados
  - invariantes críticos de schema/persistência
