# Intelligence Layer (M5)

O M5 fecha a primeira versão do layer de inteligência pós-refatoração.

## Objetivo

Melhorar a priorização dos candidatos sem reespalhar lógica no observer legado.
A ideia é que o `portfolio.runner` continue sendo o ponto de montagem do ciclo,
mas agora com um enriquecimento explícito e auditável antes do allocator.

## Componentes do M5

### P18 — Slot-aware tuning

Arquivos:
- `src/natbin/intelligence/slot_profile.py`

Entrega:
- perfil por hora (`slot_profile`)
- shrinkage em direção ao baseline global
- `slot_multiplier` por hora do dia

Saída no pack:
- `runs/intelligence/<scope_tag>/pack.json -> slot_profile`

### P19 — Learned gating / stacking

Arquivos:
- `src/natbin/intelligence/learned_gate.py`

Entrega:
- geração de features derivadas do sinal base
- treino de um gate leve (`LogisticRegression`)
- `learned_gate_prob` por candidato

Saída no pack:
- `pack.json -> learned_gate`

### P20 — Drift / regime monitor + retrain trigger

Arquivos:
- `src/natbin/intelligence/drift.py`

Entrega:
- baseline de distribuição (`score`, `conf`, `ev`)
- PSI por campo
- `drift_state.json`
- `retrain_trigger.json` quando streak de warn/block estoura

Artefatos:
- `runs/intelligence/<scope_tag>/drift_state.json`
- `runs/intelligence/<scope_tag>/retrain_trigger.json`

### P21 — Coverage regulator 2.0

Arquivos:
- `src/natbin/intelligence/coverage.py`

Entrega:
- perfil cumulativo por hora
- viés leve positivo/negativo conforme o ciclo esteja abaixo/acima da meta
- `coverage_bias` incorporado ao score final

### P22 — Anti-overfitting tuning

Arquivos:
- `src/natbin/intelligence/anti_overfit.py`

Entrega:
- leitura do `summary.json` multi-window
- robustez agregada por janela
- `penalty` e, opcionalmente, fail-closed

## Artefatos novos

Por scope:

- `runs/intelligence/<scope_tag>/pack.json`
- `runs/intelligence/<scope_tag>/latest_eval.json`
- `runs/intelligence/<scope_tag>/drift_state.json`
- `runs/intelligence/<scope_tag>/retrain_trigger.json` (opcional)

## Integração com o runtime

O enrichment do M5 acontece no `portfolio.runner`:

1. o candidato base é gerado a partir do decision snapshot
2. o M5 carrega o `pack.json` do scope
3. calcula `slot_multiplier`, `learned_gate_prob`, `coverage_bias`, drift e anti-overfit
4. escreve `latest_eval.json`
5. devolve um `CandidateDecision` enriquecido

O `CandidateDecision.rank_value()` agora prioriza `intelligence_score` quando ele
está disponível. Assim o allocator continua simples, mas passa a operar sobre
um score já ajustado pelo layer de inteligência.

## Build do pack

CLI canônico:

```powershell
python -m natbin.intelligence_pack --repo-root . --asset EURUSD-OTC --interval-sec 300 --json
```

Parâmetros úteis:

- `--signals-db`
- `--dataset-path`
- `--multiwindow-summary`
- `--lookback-days`
- `--out`

## Dashboard

O dashboard local passa a exibir um painel `Intelligence (M5)` com:

- resumo do `latest_eval.json`
- metadados do `pack.json`
- `retrain_trigger.json` quando presente

## Testes e smoke

- `tests/test_intelligence_slot_profile.py`
- `tests/test_intelligence_drift.py`
- `tests/test_intelligence_learned_gate.py`
- `tests/test_intelligence_runtime.py`
- `tests/test_intelligence_fit.py`
- `scripts/tools/intelligence_pack_smoke.py`
