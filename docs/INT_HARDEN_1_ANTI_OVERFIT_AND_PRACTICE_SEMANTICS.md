# INT-HARDEN-1 — Anti-Overfit Enablement + Practice Semantics Polish

## Motivação

Após o INT-RECOVERY-2, o runtime e a materialização scoped do portfolio ficaram corretos, mas ainda restavam dois ruídos:

- `anti_overfit.available=false` quando o repo não tinha `summary.json/per_window` utilizável
- `practice-round` marcando warning residual por semântica de plumbing:
  - reconcile no-op sendo interpretado como `false`
  - incident report em PRACTICE herdando `release_readiness_warn` genérico de live

## Mudanças principais

### 1) Fallback canônico de anti-overfit

O fit do pack (`fit_intelligence_pack`) agora resolve o payload de anti-overfit nesta ordem:

1. `summary.json` do tune dir, se tiver `per_window`
2. resumo sintético por `daily_summary_*.json`
3. resumo sintético por **training rows recuperados** dos DBs de sinais

Quando o fallback sintético é usado, ele é materializado em:

`runs/intelligence/<scope>/anti_overfit_summary.json`

Isso ajuda auditoria e debugging do source efetivo usado pelo pack.

### 2) Reconcile no-op tratado como sucesso operacional

Na rodada de PRACTICE, o resumo de observação agora entende que:

- `detail.reason = no_pending_intents`
- `pending_before = 0`
- `errors = []`

é um **reconcile ok**, não um falso negativo.

### 3) Incident report stage-aware para PRACTICE

O incident report/status agora aceita `stage='practice'`.

Nesse modo, `release_readiness_warn` genérico deixa de ser tratado como issue operacional da rodada, evitando warning residual de release/live checklist dentro de uma evidência de PRACTICE que já terminou saudável.

## Arquivos principais

- `src/natbin/intelligence/fit.py`
- `src/natbin/intelligence/recovery.py`
- `src/natbin/intelligence/paths.py`
- `src/natbin/ops/practice_round.py`
- `src/natbin/incidents/reporting.py`
- `tests/test_intelligence_harden_1.py`
- `scripts/tools/int_harden_1_smoke.py`

## Validação esperada após aplicar

Rerodando o `practice-round` no profile de controlled practice:

- o runtime continua saudável
- `round_ok=true`
- `reconcile_ok=true` em no-op
- `anti_overfit.available=true` quando houver histórico recuperável suficiente
- warnings residuais ficam concentrados em mercado/intelligence real
