# Part 21 — provider shield entrypoint fix

Este patch corrige uma regressão de integração do `Provider Session Shield` sem tocar na lógica de transporte/proxy ou na execução de ordens.

## O que corrige

- `invoke_runtime_app.cmd ... provider-stability-report ...`
- `invoke_runtime_app.cmd ... portfolio-canary-signal-scan ...`
- `capture_provider_stability_bundle.cmd` agora usa wrappers diretos e não depende do dispatch incompleto do `runtime_app`.

## Comandos após aplicar

```powershell
.\scripts\tools\invoke_runtime_app.cmd --config config\practice_portfolio_canary.yaml provider-stability-report --all-scopes --active-provider-probe --json
.\scripts\tools\invoke_runtime_app.cmd --config config\practice_portfolio_canary.yaml portfolio-canary-signal-scan --all-scopes --json
.\scripts\tools\capture_provider_stability_bundle.cmd --config config\practice_portfolio_canary.yaml
```
