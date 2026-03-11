# Package M3 — Runtime Soak & Scheduler Hardening

O M3 fecha a parte operacional que ainda faltava entre o control plane já
modularizado e um runtime realmente robusto para rodar por muito tempo sem
misturar artefatos velhos, locks órfãos e execuções concorrentes.

## O que entrou

### 1. Lock E2E para `observe --once` e daemon

Antes do M3, o daemon tinha lock escopado por `(asset, interval_sec)`, mas o
modo `--once` podia passar por fora e colidir com o scheduler principal.

Agora:

- `run_once()` usa o mesmo `runtime_daemon_<scope>.lock`
- lock ganha metadata de owner (`repo_root`, `scope_tag`, `mode`, etc.)
- o processo atual renova `heartbeat_at_utc` durante a execução e antes do
  sleep entre candles

Isso fecha o buraco de overlap entre:

- scheduler principal
- execução manual via CLI
- debug local / scripts de smoke

### 2. Guard de startup

Na subida do runtime, o módulo `natbin.runtime.hardening`:

- inspeciona o lock atual
- avalia o frescor dos artefatos `latest`
- invalida snapshots stale (`state=stale`) antes do primeiro ciclo
- grava o diagnóstico em `runs/control/<scope>/guard.json`

Artefatos rastreados:

- `loop_status`
- `health`
- `precheck`
- `execution`
- `orders`
- `reconcile`
- sidecars legacy `observe_loop_auto_status_*` e `health_latest_*`

### 3. Lifecycle artifact

O runtime agora grava o último evento operacional em:

- `runs/control/<scope>/lifecycle.json`

Eventos atuais:

- `startup`
- `shutdown`

Campos úteis:

- scope
- mode (`once` / `daemon`)
- lock path
- total de ciclos
- último phase / último ok / exit code

### 4. Configuração

Novos campos em `runtime`:

```yaml
runtime:
  stale_artifact_after_sec: 900
  startup_invalidate_stale_artifacts: true
  startup_lifecycle_artifacts: true
  lock_refresh_enable: true
```

Se `stale_artifact_after_sec` não for definido, o runtime deriva um valor
conservador a partir do `interval_sec`.

## Operação recomendada

### Rodar um one-shot seguro

```powershell
python -m natbin.runtime_app observe --repo-root . --once --topk 3 --json
```

Se o daemon do mesmo scope já estiver rodando, o comando falha fechado com
`lock_exists:<arquivo>`.

### Rodar soak curto / debug operacional

```powershell
python scripts/tools/runtime_hardening_smoke.py
```

### Inspecionar status completo

```powershell
python scripts/tools/runtime_health_report.py
```

Esse relatório agora inclui `guard`, `lifecycle` e o snapshot de frescor.

## Objetivo do pacote

O M3 não tenta melhorar edge, win rate ou seleção de sinais.
Ele endurece o runtime para que os próximos packages (M4+) avancem sobre uma
base mais previsível e auditável.
