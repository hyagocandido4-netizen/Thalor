# Package PRACTICE-OPS-1A — hotfix de bootstrap para a rodada controlada em PRACTICE

## O que corrige

- corrige o crash de `runtime_app asset prepare` causado por `NameError: resolve_scope_runtime_paths`
- faz `runtime_app asset candidate` repassar a config carregada para o `candidate_scope`, permitindo enriquecimento de intelligence na helper single-asset sem executar ordens

## Por que isso importa

No bootstrap da rodada controlada em PRACTICE, o caminho recomendado é:

1. `asset prepare` para regenerar `market_context`
2. `intelligence_pack` para materializar `pack.json`
3. `asset candidate` para gerar/atualizar `latest_eval.json`, `retrain_plan.json` e `retrain_status.json` sem submit
4. `practice`
5. `practice-round`

Sem este hotfix, o passo 1 quebrava antes de atualizar o `market_context`, e o passo 3 não recebia `cfg`, então a trilha de intelligence podia ficar incompleta.

## Runbook recomendado após aplicar o pacote

Use sempre a `.venv`:

- `./.venv/Scripts/python.exe -m natbin.runtime_app asset prepare --repo-root . --config config/live_controlled_practice.yaml --asset EURUSD-OTC --interval-sec 300 --json`
- `./.venv/Scripts/python.exe -m natbin.intelligence_pack --repo-root . --config config/live_controlled_practice.yaml --asset EURUSD-OTC --interval-sec 300 --json`
- `./.venv/Scripts/python.exe -m natbin.runtime_app asset candidate --repo-root . --config config/live_controlled_practice.yaml --asset EURUSD-OTC --interval-sec 300 --topk 1 --json`
- `./.venv/Scripts/python.exe -m natbin.runtime_app practice --repo-root . --config config/live_controlled_practice.yaml --json`
- `./.venv/Scripts/python.exe -m natbin.runtime_app practice-round --repo-root . --config config/live_controlled_practice.yaml --soak-cycles 1 --json`

## Validação local do pacote

- `PYTHONPATH=src pytest -q` → 92 passed
- `python scripts/ci/smoke_runtime_app.py` → OK
- `PYTHONPATH=src python scripts/tools/selfcheck_repo.py` → ALL OK
