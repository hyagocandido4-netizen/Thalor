# Thalor diagnostics runner v4

## O que mudou na v4

- parsing JSON compatível com **Windows PowerShell 5.1** e **PowerShell 7**
- escrita de artifacts em **UTF-8 sem BOM**, evitando contaminar o `check_hidden_unicode`
- `meta.json` agora inclui:
  - `process_status`
  - `semantic_status`
  - `semantic_parse_error`
- quando um comando `--json` falha no parse, o ZIP passa a conter:
  - `context/json_parse_error.txt`
  - `context/json_parse_candidate.txt` quando houver payload útil
- isolamento de fases preservado:
  - offline
  - practice_baseline
  - practice_live
  - real_preflight
  - real_submit
- bundle agregado continua sendo gerado com `SUMMARY.json`

## Uso

### Offline

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v4.ps1 -Mode Offline -StrictWarnings -IncludeIsolatedPytest -IncludeDocker
```

### Practice

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v4.ps1 -Mode Practice -StrictWarnings
```

### Real preflight

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v4.ps1 -Mode RealPreflight -StrictWarnings
```

### Tudo

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v4.ps1 -Mode All -StrictWarnings -IncludeIsolatedPytest -IncludeDocker
```

## Artefatos

Cada sessão cria:

- `diag_zips/<session_id>/SUMMARY.json`
- `diag_zips/<session_id>/*.zip`
- `diag_zips/diag_bundle_<session_id>.zip`

## Observação importante

A v4 foi revisada estruturalmente para corrigir os problemas observados nos bundles da v3.
Neste ambiente eu não consegui executar o `.ps1` ponta a ponta porque o container não tem PowerShell instalado.
A validação executada aqui foi feita no **patch do Thalor** via `pytest` e smoke scripts Python.
