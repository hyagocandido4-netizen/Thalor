# Parte 5 — intelligence / cp_meta / convergência operacional

## Objetivo
Fechar os blockers dominantes restantes ligados à superfície de inteligência e à dívida de `cp_meta`, sem mexer em `./secrets`, `secrets/transport_endpoint` ou no envelope conservador do canary.

## O que foi corrigido

### 1. Observer cache se auto-recupera quando o gate pedido exige CP e o cache não suporta
- `cache_supports_gate(...)` passa a validar compatibilidade real do cache com o gate solicitado.
- `observer.runner` agora detecta `cache_incompatible_gate:<gate>` e reconstrói o cache automaticamente.
- o payload salvo do cache passa a registrar:
  - `gate_mode_requested`
  - `cp_available`
  - `meta_iso_available`
  - `refresh_reason`

Isso reduz a chance de permanecer em `fail-closed` por cache antigo/incompleto quando o gate ativo mudou para um modo que exige `cp`.

### 2. `latest_eval.json` passa a convergir mesmo quando falta decision artifact
- `natbin.intelligence.refresh` agora escreve um placeholder explícito de `latest_eval.json` quando:
  - a decisão ainda não existe;
  - ou a inteligência está desligada.
- o placeholder registra status, razão, disponibilidade do pack e metadados úteis de treinamento.

Isso reduz o estado ambíguo de “pack existe, mas `latest_eval` sumiu / `pack_missing` / sem superfície legível”.

### 3. `portfolio_cp_meta_maintenance` vira manutenção real de before/after
- o tool agora faz ciclo completo de:
  - closure report antes;
  - signal artifact audit antes;
  - `asset prepare` quando necessário;
  - `asset candidate`;
  - `intelligence-refresh`;
  - signal artifact audit depois;
  - closure report depois.
- o payload final mostra:
  - `selected_scope_tags`
  - `repaired_scope_tags`
  - `unresolved_scope_tags`
  - `summary_delta`

Isso transforma a manutenção de `cp_meta` em operação auditável e repetível, em vez de tentativa opaca.

### 4. Mirror do root `runs/live_signals.sqlite3`
- `write_sqlite_signal(...)` volta a espelhar para o DB raiz quando o runtime estiver escrevendo em DB particionado por scope.

Isso reduz a inconsistência entre `runs/signals/<scope>/live_signals.sqlite3` e o DB agregado raiz.

### 5. Freshness de artifact fica menos frágil
- `signal_artifact_audit` agora usa o timestamp mais fresco entre payload e mtime do arquivo.

Isso evita classificar como stale um artifact recém-materializado cujo payload interno carrega timestamp antigo.

### 6. Compatibilidade de imports da suíte
- adicionados `scripts/__init__.py` e `scripts/tools/__init__.py`.
- `pytest.ini` passa a incluir `.` e `src` no `pythonpath`.

## Impacto / risco / benefício
- **Impacto:** alto
- **Risco:** baixo-médio
- **Benefício:** muito alto
- **Mexe em caminho crítico:** sim, mas de forma conservadora e observável

## Não foi tocado
- `./secrets`
- `secrets/transport_endpoint`
- Decodo
- envelope top-1 / single-position

## Validação sugerida após aplicar
```powershell
$env:PYTHONPATH = ".;src"
.\.venv\Scripts\python.exe -m pytest -q tests\test_part31_intelligence_convergence.py tests\test_part31_intelligence_maintenance.py
.\scripts\tools\portfolio_cp_meta_maintenance.cmd --config config\practice_portfolio_canary.yaml --json
```
