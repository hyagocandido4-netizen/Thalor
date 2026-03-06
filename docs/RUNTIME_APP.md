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
- exposing quota / precheck / health snapshots
- exposing execution / orders / reconciliation snapshots
- calling the Python runtime daemon / cycle
- writing control-plane artifacts under `runs/control/<scope>/`

## Public commands

- `status`
- `plan`
- `quota`
- `precheck`
- `health`
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
- `runs/control/<scope>/loop_status.json`
- `runs/control/<scope>/effective_config.json`
- `runs/control/<scope>/execution.json`
- `runs/control/<scope>/orders.json`
- `runs/control/<scope>/reconcile.json`

## Important compatibility note

The legacy observer step (`observe_signal_topk_perday.py`) still consumes
`config.yaml` for model/tuning fields that have not been migrated yet.
Therefore Package M makes `config/base.yaml` the preferred control-plane config
while keeping `config.yaml` present as a compatibility input for the legacy
observer path.
