# Release Hygiene (Package M1)

O objetivo desta camada é produzir um **ZIP limpo, reproduzível e seguro**
para compartilhar o Thalor sem vazar credenciais, runtime artifacts ou lixo
local do desenvolvedor.

## Problema que o M1 resolve

Um checkout de desenvolvimento normalmente contém:

- `.env` com credenciais
- `secrets/` e bundles locais de credenciais (`config/*secret*.yaml`)
- `.venv/`
- `.git/`
- `data/` e `runs/` com bancos/logs/datasets
- caches (`.pytest_cache/`, `__pycache__/`)
- restos de editor/build (`*.swp`, `*.tmp`, `*.egg-info`)

O Package M1 centraliza a lógica de exclusão em um único módulo:
`src/natbin/ops/release_hygiene.py`.

## Comandos

### Dry-run

- `python -m natbin.release_hygiene --repo-root . --dry-run --json`
- `python scripts/tools/release_bundle.py --repo-root . --dry-run --json`

### Gerar ZIP limpo

- `python -m natbin.release_hygiene --repo-root . --out exports/thalor_clean.zip --json`
- `python scripts/tools/release_bundle.py --repo-root . --out exports/thalor_clean.zip --json`
- `pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1 -Out exports\thalor_clean.zip`

O ZIP sai em modo **rootless** por padrão, pronto para extração direta na
raiz de outro checkout.

## O que entra / o que fica fora

**Inclui** código-fonte, configs versionadas, documentação e scripts do repo.

**Exclui**:

- `.env`, `.env.*` (exceto `.env.example`)
- `secrets/` e `config/*secret*.y*ml`
- `.git/`, `.venv/`, `venv/`
- `data/`, `runs/`, `exports/`, `backups/`
- `configs/variants/`
- `*.sqlite3`, `*.db`, `*.joblib`, `*.pkl`, `*.npy`, `*.npz`
- `*.log`, `*.swp`, `*.tmp`, `*.bak_*`, `*.orig`, `*.rej`
- `*.egg-info`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`

## CI

O CI passa a validar:

- `scripts/tools/release_hygiene_smoke.py`
- `python -m natbin.release_hygiene --repo-root . --dry-run --json`

## Checklist rápido antes de compartilhar

1. `python scripts/tools/selfcheck_repo.py`
2. `python scripts/tools/release_hygiene_smoke.py`
3. `python -m natbin.release_hygiene --repo-root . --out exports/thalor_clean.zip --json`
4. Compartilhar **somente** o ZIP gerado
