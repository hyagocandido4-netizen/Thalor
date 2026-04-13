# Thalor diagnostic runner v3

## Principais correções

- Corrige o falso FAIL do runner anterior usando `ExitCode` real e preservando `timed_out`.
- Adiciona classificação semântica para saídas JSON dos comandos `--json`.
- Distingue `process_status` de `semantic_status` e calcula um `status` final mais fiel.
- Faz isolamento entre fases limpando artefatos mutáveis antes de cada fase.
- Limpa variáveis de ambiente `THALOR*` entre fases para evitar contaminação.
- Não cria automaticamente `.env` nem `config/broker_secrets.yaml`.
- Gera bundle agregado com `SUMMARY.json` já incluso.
- Move os artefatos para `diag_zips/<session_id>/`, fora de `runs/`, para não serem apagados no reset.

## Como rodar

### Offline

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v3.ps1 -Mode Offline -StrictWarnings -IncludeIsolatedPytest -IncludeDocker
```

### Practice

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v3.ps1 -Mode Practice -StrictWarnings
```

### Real preflight

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v3.ps1 -Mode RealPreflight -StrictWarnings
```

### Tudo

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v3.ps1 -Mode All -StrictWarnings -IncludeIsolatedPytest -IncludeDocker
```

## Onde ficam os artefatos

- Sessão: `diag_zips/<session_id>/`
- Bundle agregado: `diag_zips/diag_bundle_<session_id>.zip`
- Ponteiro para a última sessão: `diag_zips/LATEST_SESSION.txt`

## O que me enviar depois

- `diag_zips/<session_id>/SUMMARY.json`
- `diag_zips/diag_bundle_<session_id>.zip`
- ou só os ZIPs dos steps com `status=fail` e `status=warn`
