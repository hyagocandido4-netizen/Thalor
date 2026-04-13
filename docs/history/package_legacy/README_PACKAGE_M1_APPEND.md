# Package M1 — Release Hygiene & Runtime Sanitation

Data: **2026-03-10**

Este pacote fecha a parte operacional de **higiene de entrega** do Thalor.
O foco não é mudar o runtime de trading; o foco é impedir que a pasta do
projeto seja compartilhada com:

- segredos (`.env`, `.env.*`)
- metadados git (`.git/`)
- virtualenv local (`.venv/`, `venv/`)
- bancos/logs/artefatos de execução (`data/`, `runs/`, `exports/`, `backups/`)
- caches e sobras de editor/build (`__pycache__/`, `.pytest_cache/`, `*.swp`, `*.tmp`, `*.egg-info`)

## Entregas

- **Bundle canônico cross-platform**:
  - `python -m natbin.release_hygiene --repo-root . --out exports/thalor_m1_clean.zip --json`
  - `python scripts/tools/release_bundle.py --repo-root . --out exports/thalor_m1_clean.zip --json`
  - `pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1 -Out exports\thalor_m1_clean.zip`

- **Dry-run / auditoria**:
  - `python -m natbin.release_hygiene --repo-root . --dry-run --json`

- **Smoke de CI**:
  - `python scripts/tools/release_hygiene_smoke.py`

- **Testes unitários**:
  - `pytest -q tests/test_release_hygiene.py`

## Regras do bundle

O ZIP é gerado em modo **rootless** por padrão.
Exemplo: ele contém `README.md` e `src/natbin/...` diretamente, para permitir
extração direta na raiz de outro checkout do projeto.

Itens **sempre incluídos** (sanity check mínimo):

- `README.md`
- `.env.example`
- `requirements.txt`
- `pyproject.toml`
- `setup.cfg`
- `src/natbin/runtime_app.py`
- `scripts/tools/release_bundle.py`

Itens **sempre excluídos**:

- `.env`, `.env.*` (exceto `.env.example`)
- `.git/`, `.venv/`, `venv/`
- `data/`, `runs/`, `exports/`, `backups/`
- `configs/variants/`
- `*.sqlite3`, `*.db`, `*.joblib`, `*.pkl`, `*.npy`, `*.npz`
- `*.log`, `*.swp`, `*.tmp`, `*.bak_*`, `*.orig`, `*.rej`
- `*.egg-info`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`

## Uso recomendado

1. Rode o dry-run:
   - `python -m natbin.release_hygiene --repo-root . --dry-run --json`

2. Revise os warnings:
   - `.env` local presente
   - `.venv` local presente
   - `runs/` ou `data/` presentes
   - `src/natbin.egg-info/` presente

3. Gere o ZIP limpo:
   - `python -m natbin.release_hygiene --repo-root . --out exports/thalor_m1_clean.zip --json`

4. Compartilhe **somente** o ZIP limpo.

## Observação importante

Este pacote **não apaga** automaticamente `.git`, `.venv`, `.env`, `data/` ou
`runs/` do checkout local. Eles continuam válidos para o desenvolvedor.
O objetivo é impedir vazamento no **bundle compartilhado**, não destruir o
ambiente de trabalho local.
