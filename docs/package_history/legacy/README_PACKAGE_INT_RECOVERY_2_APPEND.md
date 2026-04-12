# Package INT-RECOVERY-2

## Objetivo

Fechar a segunda perna da recuperação da inteligência:

- materializar `portfolio_cycle_latest.json` e `portfolio_allocation_latest.json` **por profile/config atual**
- alinhar `practice-round` com rebuild/refresh da inteligência do scope atual
- remover a dependência operacional de fallback legado incompatível
- atualizar `latest_eval.json` com o `pack.json` recém-reconstruído

## O que muda

- `practice-round` agora executa um refresh de inteligência/profile:
  - após o soak (`rebuild_pack=true`)
  - após a validação (`rebuild_pack=false`, só reavalia/materializa)
- o refresh reconstrói o `pack.json`, reescreve `latest_eval.json` via `enrich_candidate()` e materializa os artifacts de portfolio do profile atual
- `asset candidate` também passa a materializar artifacts scoped do portfolio
- novo comando operacional:

```powershell
python -m natbin.runtime_app intelligence-refresh --repo-root . --config config/live_controlled_practice.yaml --json
```

## Resultado esperado

Depois de aplicar o pacote e rerodar o `practice-round`:

- `portfolio_status.latest_cycle` deixa de ser `null`
- `portfolio_status.latest_allocation` deixa de ser `null`
- `latest_sources.*.source` passa para `scoped`
- `portfolio_artifact_scope` deixa de aparecer como warning
- `latest_eval.json` passa a refletir o pack atual do scope/profile

## Smoke recomendado

```powershell
PYTHONPATH=src python scripts/tools/intelligence_recovery_2_smoke.py
```
