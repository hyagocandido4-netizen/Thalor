# Package SYNC-1A — Lightweight sync bootstrap hotfix

Este hotfix fecha uma falha prática do SYNC-1 no Windows local:
`python -m natbin.runtime_app sync ...` não podia ser executado antes do ambiente
Python completo estar instalado, porque o entrypoint importava a stack inteira
(do config tipado até `pydantic`) antes de chegar ao subcomando `sync`.

## O que muda

- `natbin.runtime_app` agora detecta `sync` cedo e usa um entrypoint leve
- `natbin.ops.sync_state` não depende mais de `state.control_repo`
- `runtime_app` passa a mostrar erro de bootstrap amigável quando faltar
  `pydantic` / `pydantic_settings` nos demais comandos
- compatibilidade preservada para:
  - `--freeze-docs`
  - `--write-manifest`
  - `--base-ref origin/main`

## Resultado esperado

Agora o comando abaixo roda mesmo antes do `pip install -r requirements.txt`:

```powershell
python -m natbin.runtime_app sync --repo-root . --strict --json
```

Os demais comandos do control-plane continuam exigindo o ambiente Python do
projeto instalado.
