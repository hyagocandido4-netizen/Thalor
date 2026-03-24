# Runtime App (Package M)

Package M turns `natbin.runtime_app` into the **canonical control plane** for the
single-asset runtime baseline.

Main entrypoint:

```powershell
python -m natbin.runtime_app status --repo-root . --json
```

Operational command:

```powershell
python -m natbin.runtime_app observe --repo-root . --topk 3
```

## Role in the architecture

`runtime_app` is now responsible for:

- resolving `repo_root`
- resolving `config/base.yaml` with fallback to `config.yaml`
- writing effective config dumps
- exposing the canonical cycle plan
- exposing quota / precheck / health / security / sync / intelligence snapshots
- exposing execution / orders / reconciliation snapshots
- calling the Python runtime daemon / cycle
- writing control-plane artifacts under `runs/control/<scope>/`

## Public commands

- `status`
- `plan`
- `quota`
- `precheck`
- `health`
- `security`
- `sync`
- `intelligence`
- `observe`
- `orders`
- `reconcile`

The legacy invocation still works:

```powershell
python -m natbin.runtime_app --repo-root . --json
```

That is treated as `status --json` for compatibility.

## Control-plane artifacts

For each runtime scope, Package M writes:

- `runs/control/<scope>/plan.json`
- `runs/control/<scope>/quota.json`
- `runs/control/<scope>/precheck.json`
- `runs/control/<scope>/health.json`
- `runs/control/<scope>/security.json`
- `runs/control/_repo/sync.json`
- `runs/control/<scope>/intelligence.json`
- `runs/control/<scope>/loop_status.json`
- `runs/control/<scope>/effective_config.json`
- `runs/control/<scope>/execution.json`
- `runs/control/<scope>/orders.json`
- `runs/control/<scope>/reconcile.json`
- `runs/control/<scope>/guard.json`
- `runs/control/<scope>/lifecycle.json`

## Package M3 hardening note

- `observe --once` agora usa o mesmo lock escopado do daemon, evitando overlap
  com o scheduler principal.
- No startup do runtime, o guard inspecciona os artefatos `latest` e invalida
  snapshots stale (`state=stale`) antes do primeiro ciclo.
- `lifecycle.json` registra os eventos mais recentes de `startup` e `shutdown`
  do runtime por scope.

## Important compatibility note

The legacy observer step (`observe_signal_topk_perday.py`) now resolves the
selected typed config directly. `config/base.yaml` remains the preferred input,
while `config.yaml` stays supported only as an explicit selected config path or
as the automatic repo fallback when `config/base.yaml` is absent.


## Package M6 security note

- `runtime_app security` executa a auditoria de postura do scope atual.
- O payload inclui origem das credenciais, checks de embed/redaction e o estado
  atual do broker guard.
- O dashboard local usa esse snapshot para o painel **Security (M6)**.

## INT-OPS-1 note

- `runtime_app intelligence` gera a surface operacional por scope e persiste `runs/control/<scope>/intelligence.json`.
- `runtime_app status` agora inclui esse snapshot em `control.intelligence`.
- `runtime_app portfolio status` agrega um rollup multi-asset com severidade, score, retrain state e traceabilidade de execution/alocação.

## SYNC-1 note

- `runtime_app sync` compara o workspace atual com os manifests congelados em
  `docs/canonical_state/`.
- O comando escreve um artefato repo-level em `runs/control/_repo/sync.json`.
- Os próprios manifests de `docs/canonical_state/` são ignorados na comparação
  para evitar drift circular.
