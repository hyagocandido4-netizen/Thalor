# RCF-3A — Windows path normalization + SYNC legacy compatibility

Este hotfix corrige duas regressões observadas após o RCF-3 em ambiente Windows:

1. serialização de `Path` tipado em formato com `\` dentro das surfaces/metadata do observer e da inteligência
2. perda de compatibilidade do `control.app sync` com os flags legados `--base-ref`, `--write-manifest`, `--strict-clean` e `--strict-base-ref`

O comportamento atual fica híbrido:

- `sync --freeze-docs` / `sync --strict` continua usando `sync_state`
- chamadas legacy centradas em `--base-ref` passam a usar `repo_sync` novamente dentro do control-plane

Isso preserva o fluxo novo do SYNC-1 e mantém compatibilidade com a trilha de testes/artefatos herdada.
