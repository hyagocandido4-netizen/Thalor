# SYNC-1A Bootstrap Hotfix

## Problema resolvido

No SYNC-1 original, `python -m natbin.runtime_app sync` seguia o mesmo caminho de
import do control-plane inteiro. Em um checkout recém-extraído, isso falhava cedo
com `ModuleNotFoundError: No module named 'pydantic'`.

## Ajuste

O entrypoint público `natbin.runtime_app` agora:

1. detecta o subcomando primário
2. quando ele é `sync`, usa um CLI leve (`natbin.ops.sync_cli`)
3. só importa a stack completa do control-plane para os demais comandos

Além disso, `natbin.ops.sync_state` passou a gravar `runs/control/_repo/sync.json`
diretamente, sem depender de módulos que puxam a config tipada.

## Comandos

Pode usar qualquer uma das formas abaixo:

```powershell
python -m natbin.runtime_app sync --repo-root . --strict --json
python -m natbin.runtime_app sync --repo-root . --base-ref origin/main --write-manifest --json
```

## Observação

Esse hotfix deixa o `sync` independente do bootstrap completo, mas `doctor`,
`practice`, `release`, `security` e os demais comandos continuam exigindo o
ambiente do projeto:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
