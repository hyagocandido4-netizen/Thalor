# Thalor Diagnostic Runner v4.1 Hotfix

## O que corrige

Corrige um bug do runner v4 sob `Set-StrictMode -Version Latest` em PowerShell 7.5.x:

- o objeto retornado por `New-SemanticAssessment` não expunha a propriedade `parse_error`
- `Invoke-NativeStep` sempre tentava ler `$semantic.parse_error`
- isso quebrava no primeiro step nativo (`02_python_version`) mesmo quando o comando tinha sucesso

## Mudanças

1. `New-SemanticAssessment` agora sempre retorna:
   - `status`
   - `reasons`
   - `detected`
   - `parse_error`

2. `Finalize-Step` passa a receber `SemanticParseError` via helper defensivo:
   - `Get-SemanticParseErrorValue`

## Como usar

Substitua o arquivo anterior por `run_thalor_diagnostics_v4_1.ps1` e rode:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v4_1.ps1 -Mode Offline -StrictWarnings -IncludeIsolatedPytest -IncludeDocker
```

## Hotfix rápido manual

Se quiser corrigir o arquivo atual manualmente, altere a função `New-SemanticAssessment` para incluir:

```powershell
[string]$ParseError = ''
```

e retorne também:

```powershell
parse_error = $ParseError
```

Além disso, troque a chamada:

```powershell
-SemanticParseError $semantic.parse_error
```

por:

```powershell
-SemanticParseError (Get-SemanticParseErrorValue $semantic)
```
