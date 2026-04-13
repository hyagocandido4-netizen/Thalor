# Package SYNC-1 — Canonicalizar o estado atual

Entrega principal do pacote:

- novo comando `runtime_app sync`
- novo artefato de repo `runs/control/_repo/sync.json`
- congelamento explícito do baseline público em `docs/canonical_state/published_main_baseline.json`
- congelamento explícito do working tree local em `docs/canonical_state/workspace_manifest.json`
- comparação automática entre o workspace atual e o manifesto congelado
- registro da próxima trilha de packages em `docs/V2_PRODUCTION_NEXT_PACKAGES.md`

Comandos principais:

```powershell
python -m natbin.runtime_app sync --repo-root . --json
python -m natbin.runtime_app sync --repo-root . --freeze-docs --json
python -m natbin.runtime_app sync --repo-root . --strict --json
```

Semântica do pacote:

- **dirty workspace não é erro por si só**
- o estado é considerado canônico quando o working tree atual bate **exatamente** com `docs/canonical_state/workspace_manifest.json`
- os próprios arquivos de sync em `docs/canonical_state/` são ignorados na comparação para não gerar drift circular

Próximo pacote da fila:

- **RCF-2 — Observer decomposition + shrink do boundary legado**
