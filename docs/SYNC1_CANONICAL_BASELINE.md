# SYNC-1 — Canonical baseline / workspace source-of-truth

O objetivo do SYNC-1 é fechar o maior risco operacional identificado no estado
atual do Thalor: **o código local já avançou além do histórico público**, mas esse
estado ainda não estava materializado de forma canônica e reproduzível.

Este pacote adiciona uma superfície explícita para capturar esse contexto antes
que a refatoração continue.

## Comando canônico

```powershell
python -m natbin.runtime_app sync --repo-root . --base-ref origin/main --write-manifest --json
```

O mesmo fluxo está disponível diretamente em:

```powershell
python scripts/tools/repo_sync_snapshot.py --repo-root . --base-ref origin/main --write-manifest --json
```

## O que o SYNC-1 gera

- snapshot offline do workspace git atual
- `branch`, `HEAD`, `base_ref`, `ahead/behind`
- working tree (`tracked_modified`, `untracked`, `staged`, conflitos)
- agrupamento das mudanças por área (`src`, `tests`, `docs`, `scripts`, `config` etc.)
- inventário dos `README_PACKAGE_*_APPEND.md`
- delta commitado contra a baseline (`base_ref...HEAD`)
- snapshot de `release_hygiene`
- fingerprint determinístico do baseline

Quando `--write-manifest` é usado, o pacote grava:

- `docs/REPO_SYNC_MANIFEST_SYNC1.json`
- `docs/REPO_SYNC_MANIFEST_SYNC1.md`

## Por que isso importa

O workflow correto agora fica:

1. extrair/aplicar o package sobre a árvore local
2. rodar `runtime_app sync --write-manifest`
3. congelar esse estado com commit/tag/branch local
4. só então seguir para o próximo package de refatoração

Isso evita repetir a perda de contexto entre:

- `origin/main` público
- working tree local do projeto
- packages já materializados somente em arquivos soltos

## Integração com release readiness

O `runtime_app release` agora passa a incluir uma surface `repo_sync`.

Ela **não bloqueia** bundles sanitizados sem `.git`, mas em um checkout git real
ela marca `warn` quando o workspace estiver:

- dirty
- diverged do `base_ref`
- em conflito

Assim o release/readiness volta a refletir não só runtime/config/docs, mas também
se o repositório local está ou não congelado de forma segura.

## Gate recomendado para fechar o SYNC-1

```powershell
python scripts/tools/repo_sync_snapshot.py --repo-root . --base-ref origin/main --write-manifest --json
python scripts/tools/selfcheck_repo.py
python -m pytest -q
python scripts/tools/release_bundle.py --repo-root . --dry-run --json
```

## Próximo package técnico sugerido

Depois do SYNC-1, o próximo passo natural é o **RCF-2**:

- quebrar o observer principal
- reduzir a fronteira de compatibilidade legada
- concentrar knobs antigos em um único boundary de compat
