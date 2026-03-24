# Package INT-HARDEN-1 — Anti-Overfit Enablement + Practice Semantics Polish

## O que este pacote fecha

1. **Anti-overfit canônico mesmo sem `summary.json/per_window`**
   - o fit da inteligência agora tenta a ordem:
     - `summary.json` real do tune dir
     - fallback por `daily_summary_*.json`
     - fallback por **training rows recuperados** dos DBs de sinais
   - quando cair em fallback sintético, o pacote materializa:
     - `runs/intelligence/<scope>/anti_overfit_summary.json`

2. **`practice-round` não trata mais reconcile no-op como falso negativo**
   - reconciliação com `reason=no_pending_intents` agora conta como `reconcile_ok=true`

3. **Incidents/release warn não rebaixa a rodada de PRACTICE à toa**
   - no contexto `stage=practice`, o incident report não promove `release_readiness_warn` genérico a issue operacional
   - isso reduz warns residuais de plumbing e deixa o foco em mercado/intelligence real

## Critério de done

- `anti_overfit.available=true` mesmo quando não existir `summary.json/per_window`, desde que haja histórico recuperável suficiente
- `practice-round` continua completando com `round_ok=true`
- `reconcile_ok` não sai `false` em rodada sem intents pendentes
- warn residual da rodada passa a refletir mercado/intelligence real, não semântica errada de practice/reconcile
