# PRACTICE-OPS-1 — Controlled practice round

Este pacote fecha o próximo passo lógico depois do READY-1.

O objetivo é ter **um comando único** que consolida a rodada operacional em
conta `PRACTICE` com evidência auditável por scope.

## O que ele faz

O runner `practice-round` executa, nesta ordem:

1. `runtime_app practice` (pré-gate READY-1)
2. `runtime_soak` automático quando o soak estiver ausente/stale ou quando o
   scope ainda não estiver ready
3. `runtime_app practice` novamente depois do soak
4. `controlled_live_validation` do stage `practice`
5. `incident_report` pós-rodada
6. grava o resumo canônico em `runs/control/<scope_tag>/practice_round.json`

## Comando canônico

```powershell
python -m natbin.runtime_app practice-round --repo-root . --config config/live_controlled_practice.yaml --json
```

Wrapper direto:

```powershell
python scripts/tools/controlled_practice_round.py --repo-root . --config config/live_controlled_practice.yaml --json
```

Wrapper PowerShell:

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\run_controlled_practice_round.ps1 -Config config/live_controlled_practice.yaml
```

## Artefatos

Latest por scope:

```text
runs/control/<scope_tag>/practice_round.json
```

Relatórios timestamped:

```text
runs/tests/practice_rounds/practice_round_<timestamp>_<scope_tag>.json
runs/tests/practice_rounds/practice_round_latest_<scope_tag>.json
```

O stage de validação interna continua gerando também o report original em
`runs/tests/controlled_live_validation_practice_<timestamp>.json`.

## Critérios de sucesso do round

O round só sai `round_ok=true` quando:

- o READY-1 fica `ready_for_practice=true` após o soak
- todos os passos obrigatórios do stage `practice` passam
- o `incident_report` pós-rodada não volta com severidade `warn/error`

Observação importante: **não gerar trade naquele candle não é erro por si só**.
A ausência de sinal pode continuar sendo um resultado válido do round.

## Blockers críticos antes do soak

O runner para antes mesmo do soak se encontrar problemas críticos de posture,
como:

- `execution.mode/provider` incompatíveis com o trilho de PRACTICE
- `execution.account_mode != PRACTICE`
- `broker.balance_mode != PRACTICE`
- `multi_asset`/scope não-controlado
- stake fora do envelope seguro
- limites de execução fora do modo controlado
- `kill_switch` ativo
- `drain_mode` ativo
- broker guard/time filter inconsistentes

## Leitura rápida do payload

Campos úteis do `practice_round.json`:

- `pre_practice`: READY-1 antes do soak
- `soak.action`: `ran`, `reused_fresh`, `disabled` ou `skipped`
- `post_practice`: READY-1 depois do soak
- `validation.summary`: total/passed/failed_required
- `validation.observe`: resumo do intento/submit/reconcile
- `incident_report`: severidade e artefatos pós-rodada
- `recommended_next_steps`: próximo passo recomendado
