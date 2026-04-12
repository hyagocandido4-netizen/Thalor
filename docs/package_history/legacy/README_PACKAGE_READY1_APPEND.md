# Package READY-1 — Controlled Practice Readiness

Entrega principal do pacote:

- novo comando `runtime_app practice`
- novo artefato `runs/control/<scope>/practice.json`
- integração do estado de practice em `status`, `health` e dashboard
- `runtime_soak.py` enriquecido para servir como evidência operacional do stage practice
- correção de propagação de `--config` / scope no `runtime_daemon` e no auto-cycle

Smoke dedicado:

```powershell
python scripts/tools/ready1_practice_readiness_smoke.py
```
