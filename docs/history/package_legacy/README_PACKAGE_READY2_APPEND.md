# Package READY-2 — Controlled Practice Green

## Entregas

- novo comando `runtime_app practice-bootstrap`
- novo módulo `src/natbin/ops/practice_bootstrap.py`
- alias `src/natbin/practice_bootstrap.py`
- `practice-round` passa a carregar o bootstrap como trilha canônica antes da rodada
- novo artefato por scope: `runs/control/<scope>/practice_bootstrap.json`
- reports em `runs/tests/practice_bootstraps/`
- documentação do runbook READY-2

## Objetivo

Transformar o stage de PRACTICE em um fluxo reproduzível a partir de checkout limpo:

1. preparar dataset + market context quando necessário
2. refrescar intelligence/portfolio
3. executar soak controlado
4. revalidar `doctor` + `practice`
5. seguir para `practice-round`

## Comando novo

```powershell
python -m natbin.runtime_app practice-bootstrap --repo-root . --config config/live_controlled_practice.yaml --json
```

## Resultado esperado

Depois do bootstrap saudável:

- `runtime_app doctor` tende a sair sem blocker real de runtime
- `runtime_app practice` tende a sair com `ready_for_practice=true`
- `runtime_app practice-round` passa a ter um bootstrap explícito e auditável antes da validação
