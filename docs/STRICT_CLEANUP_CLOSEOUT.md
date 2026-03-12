# Strict Cleanup Closeout

Data: **2026-03-11**

Este documento fecha os pontos da Fase 0 que ainda estavam marcados como **parciais** sob critério de cleanup estrito.

## Fechado neste pacote

### 1) Patches históricos deixaram de existir no branch de trabalho

Arquivos removidos:
- `scripts/setup/phase2_1_patch.ps1`
- `scripts/setup/phase2_1_patch_v2.ps1`
- `scripts/setup/phase2_1_fix_main.ps1`
- `scripts/tools/package_w_cleanup.ps1`

O runtime canônico não depende mais de scripts de patch. O histórico permanece no git.

### 2) Organização final de `src/natbin/`

Código canônico foi consolidado em subpastas:
- `config/`: `env.py`, `legacy.py`, `settings.py`
- `state/`: `db.py`
- `runtime/`: `perf.py`, `observability.py`
- `usecases/`: `backfill_candles.py`, `collect_candles.py`, `refresh_daily_summary.py`, `validate_gaps.py`
- `research/`: `dsio.py`, `train_walkforward.py`, `paper_*`, `risk_report.py`, `sweep_thresholds.py`, `tune_multiwindow_topk.py`

Os módulos na raiz passaram a ser **shims de compatibilidade explícitos**. O código interno foi atualizado para importar os módulos canônicos nas subpastas, não os wrappers da raiz.

### 3) `effective_config` virou contrato nativo por ciclo

A emissão do effective config saiu do fluxo ad-hoc em `control.plan` e foi centralizada em:
- `src/natbin/control/effective_config.py`

Agora cada `build_context()` emite:
- `runs/config/effective_config_latest_<scope>.json`
- snapshot diário com `cycle_id`
- `runs/control/<scope>/effective_config.json`

O artefato de controle agora registra:
- `generated_at_utc`
- `cycle_id`
- `latest_path`
- `snapshot_path`
- `resolved_config`
- `source_trace`

### 4) Lock definitivo do scheduler no caminho canônico

`run_once()` e `run_daemon()` agora usam o **mesmo lock escopado** em `runtime.daemon`.

Contratos garantidos:
- lock exclusivo por scope
- owner metadata (`asset`, `interval_sec`, `scope_tag`, `repo_root`, `mode`)
- heartbeat refresh no loop do daemon
- `run_once()` também bloqueia corretamente quando o lock já existe

## Validação

Rodar no repo:

```bash
pytest -q
python scripts/tools/strict_cleanup_smoke.py
python scripts/tools/selfcheck_repo.py
python scripts/ci/smoke_runtime_app.py
python scripts/ci/smoke_execution_layer.py
```

## Critério de aceite

Sob critério de cleanup estrito, estes itens passam a ficar **fechados**:
- patches -> código nativo
- organização em subpastas -> cleanup final
- `effective_config_dump` -> cleanup final
- lock definitivo no scheduler -> cleanup final
