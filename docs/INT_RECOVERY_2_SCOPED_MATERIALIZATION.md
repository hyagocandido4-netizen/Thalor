# INT-RECOVERY-2 — Scoped Portfolio Materialization + Intelligence Rebuild

## Problema atacado

Mesmo após o INT-RECOVERY-1, o profile de `controlled practice` podia ficar nesta situação:

- `portfolio_cycle_latest.json` / `portfolio_allocation_latest.json` globais existiam, mas pertenciam a outro profile/config
- o profile atual ignorava corretamente esse fallback legado (`legacy_mismatch`)
- porém ainda não existia um artifact scoped equivalente para o profile atual
- `latest_eval.json` podia continuar antigo em relação ao `pack.json` novo

Resultado: a operação estava saudável, mas `portfolio status` e `intelligence surface` ainda mostravam warning estrutural de source/mismatch.

## Solução implementada

### 1) Materialização scoped do portfolio

Foi adicionada uma camada de materialização que transforma o estado atual do scope em:

- `runs/portfolio/profiles/<profile_key>/portfolio_cycle_latest.json`
- `runs/portfolio/profiles/<profile_key>/portfolio_allocation_latest.json`

Essa materialização é feita sem sobrescrever o fallback legado global por padrão.

### 2) Refresh canônico da inteligência

Foi adicionada a rotina `refresh_config_intelligence()` que:

1. reconstrói o `pack.json` do scope atual
2. relê `decision_latest_<scope>.json`
3. reexecuta `enrich_candidate()` com o pack novo
4. regrava `latest_eval.json`, `retrain_*` e `drift_state`
5. materializa os latest payloads scoped do portfolio

### 3) Integração com o practice-round

O `practice-round` agora faz refresh:

- depois do soak, com `rebuild_pack=true`
- depois da validação, com `rebuild_pack=false`

Isso garante que os artifacts finais reflitam o estado real da rodada atual.

## Novo comando operacional

```powershell
python -m natbin.runtime_app intelligence-refresh --repo-root . --config config/live_controlled_practice.yaml --json
```

Parâmetros úteis:

- `--asset EURUSD-OTC`
- `--interval-sec 300`
- `--no-rebuild-pack`
- `--no-materialize-portfolio`

## Critério de sucesso

O pacote é considerado validado quando, após um `practice-round`:

- `portfolio_status.latest_cycle` existe
- `portfolio_status.latest_allocation` existe
- `latest_sources.cycle.source == "scoped"`
- `latest_sources.allocation.source == "scoped"`
- `portfolio_artifact_scope` deixa de aparecer como warning
- `latest_eval.json` reflete o pack atual do scope/profile

Se ainda houver warning, ele deve ser de mercado/intelligence real (por exemplo `portfolio_feedback` / `regime_block`), e não mais de contaminação de artifacts.
