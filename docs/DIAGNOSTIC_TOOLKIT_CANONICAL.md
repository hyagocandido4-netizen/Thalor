# Diagnostic Toolkit Canonical Flow

O Thalor agora expõe um toolkit canônico diretamente no `runtime_app`, sem exigir ativação manual da `.venv`.

## Entrada recomendada no Windows

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\invoke_runtime_app.ps1 status --json
```

## Toolkit individual

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\invoke_runtime_app.ps1 --config config\live_controlled_practice.yaml diag-suite --json --include-practice --include-provider-probe
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\invoke_runtime_app.ps1 --config config\live_controlled_practice.yaml transport-smoke --json
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\invoke_runtime_app.ps1 --config config\live_controlled_practice.yaml module-smoke --json
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\invoke_runtime_app.ps1 --config config\live_controlled_practice.yaml redaction-audit --json
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\invoke_runtime_app.ps1 --config config\live_controlled_practice.yaml practice-preflight --json
```

## Rodada canônica do toolkit

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\run_diagnostic_toolkit.ps1 -Config config\live_controlled_practice.yaml -DryRun
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\run_diagnostic_toolkit.ps1 -Config config\live_controlled_practice.yaml -ProbeBroker -ProbeProvider
```

## Observações

- `invoke_runtime_app.ps1` localiza automaticamente `\.venv\Scripts\python.exe` ou `.venv/bin/python`.
- `run_diagnostic_toolkit.ps1` executa a sequência `diag-suite -> transport-smoke -> module-smoke -> redaction-audit -> practice-preflight`.
- Para uma sessão longa em PRACTICE, a política atual é `zero-warning`: qualquer retorno diferente de `OK` deve ser tratado como bloqueio operacional.
