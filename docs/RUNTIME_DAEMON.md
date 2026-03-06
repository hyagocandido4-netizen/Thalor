# Runtime daemon (Package M baseline)

The daemon is now the **Python execution engine** behind the control plane.

Implementation module:

- `src/natbin/runtime/daemon.py`

Compatibility shim:

- `src/natbin/runtime_daemon.py`

## Role

The daemon is responsible for:

- loading runtime context from `runtime_app` / control-plane state
- running the canonical Python cycle plan
- evaluating precheck + failsafe before each cycle
- writing status + health + control-plane artifacts
- handling scoped daemon lock + sleep

## Entry strategy

Operationally, the preferred route is now:

```powershell
python -m natbin.runtime_app observe --repo-root . --topk 3
```

The direct daemon CLI still exists for compatibility and diagnostics:

```powershell
python -m natbin.runtime_daemon --plan-json
python -m natbin.runtime_daemon --once --repo-root . --topk 3
python -m natbin.runtime_daemon --quota-json --repo-root .
```

## Control-plane relation

`runtime_app` is the control plane.
`runtime_daemon` is the execution engine.

That is the Package M baseline split.
