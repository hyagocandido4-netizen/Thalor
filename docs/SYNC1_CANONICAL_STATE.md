# SYNC-1 — Canonicalização do estado atual

Data: **2026-03-23**

## Problema que o pacote fecha

O Thalor já não está mais no ponto em que o `main` público do GitHub conta toda a
história. O workspace local passou a ter arquivos novos e modificações não
publicadas que representam o estado real do projeto.

Sem um baseline explícito, três coisas ficam frágeis:

1. continuidade entre sessões/conversas
2. comparação honesta entre “o que está no GitHub” e “o que está no ZIP real”
3. avanço seguro para os próximos packages de refatoração

## O que vira fonte de verdade agora

O pacote cria **duas fontes de verdade locais e explícitas**:

- `docs/canonical_state/published_main_baseline.json`
- `docs/canonical_state/workspace_manifest.json`

### `published_main_baseline.json`

Congela o **ref publicado** que serve como baseline público do repo:

- URL do `origin`
- ref usado como baseline publicado (`origin/main` quando disponível; `HEAD` como fallback)
- commit SHA/subject/date
- relação entre `HEAD` e esse ref publicado

### `workspace_manifest.json`

Congela o **estado local real** do working tree:

- commit base do workspace
- contagem de arquivos modificados / adicionados / deletados / untracked
- listas completas por categoria
- agrupamento por área (`docs/`, `config/`, `src/`, `tests/`, etc.)
- inventário dos `README_PACKAGE_*_APPEND.md`

## Regra operacional do SYNC-1

No hotfix SYNC-1A, `runtime_app sync` ganhou um caminho leve e deixou de
depender da stack completa do control-plane. Isso permite congelar/comparar o
estado canônico mesmo antes do `pip install -r requirements.txt`.


O estado local **não precisa estar clean** para ser considerado correto.

A regra canônica passa a ser:

> o workspace atual está sincronizado quando bate exatamente com o manifesto
> congelado em `docs/canonical_state/workspace_manifest.json`

Isso permite continuar trabalhando em cima de um working tree intencionalmente
“dirty”, sem perder a trilha do que já foi aceito como baseline local.

## Comando novo

```powershell
python -m natbin.runtime_app sync --repo-root . --json
```

Esse comando:

- lê o estado atual do repo via `git`
- compara com os manifests congelados
- escreve um artefato operacional em `runs/control/_repo/sync.json`
- retorna um payload com checks, drift e ações recomendadas

### Opções úteis

Congelar/regerar o baseline local:

```powershell
python -m natbin.runtime_app sync --repo-root . --freeze-docs --json
```

Falhar quando houver drift:

```powershell
python -m natbin.runtime_app sync --repo-root . --strict --json
```

## Importante: comparação sem drift circular

Os próprios arquivos gerados pelo pacote SYNC-1 em `docs/canonical_state/` são
**ignorados** na comparação do working tree. Isso evita que regenerar os
manifests crie um falso positivo infinito.

## Artefatos novos

### Repo-level

- `runs/control/_repo/sync.json`

### Tracked docs

- `docs/canonical_state/published_main_baseline.json`
- `docs/canonical_state/workspace_manifest.json`
- `docs/V2_PRODUCTION_NEXT_PACKAGES.md`

## Como usar daqui para frente

### 1) Antes de começar um package novo

```powershell
python -m natbin.runtime_app sync --repo-root . --json
```

### 2) Depois de aceitar conscientemente um novo estado local

```powershell
python -m natbin.runtime_app sync --repo-root . --freeze-docs --json
```

### 3) Se quiser usar como gate duro

```powershell
python -m natbin.runtime_app sync --repo-root . --strict --json
```

## Resultado prático

Depois do SYNC-1, o projeto deixa de depender apenas de memória, conversa
anterior ou leitura manual do `git status` para saber “qual é o estado real”.

O baseline passa a ser:

- **ref publicado congelado**
- **manifesto local congelado**
- **comparação automática via `runtime_app sync`**
