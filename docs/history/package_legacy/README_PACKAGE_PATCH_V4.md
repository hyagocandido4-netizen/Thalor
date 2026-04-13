# Thalor patch v4

## Corrigido

- `incidents status`: corrigido conflito de keyword `message` em `natbin.incidents.reporting._issue(...)`.
- `security audit`: ausência de credenciais do broker fora do modo live não bloqueia mais baseline/local por default; continua bloqueando quando a execução está habilitada em live.
- `hidden unicode`: `diag_zips/` agora é ignorado pelo checker.
- `portfolio latest`: payloads legados sem metadata de contexto voltam a ser aceitos pelo rollup de intelligence.
- `practice bootstrap/round`: subprocessos com timeout agora retornam `SubprocessOutcome(returncode=124)` em vez de derrubar o fluxo por traceback; `collect_recent` ficou com timeout default mais conservador no `prepare_scope`.
- `controlled live validation baseline`: baseline agora usa `runtime_doctor` como check obrigatório e trata `runtime_release` como snapshot informacional/opcional.
- smoke scripts atualizados para refletir o contrato atual do sistema e evitar falsos vermelhos.

## Testes recomendados

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_incident_ops.py tests\test_intelligence_surface.py tests\test_live_validation_plan.py tests\test_production_doctor.py tests\test_intelligence_harden_1.py -W error

.\.venv\Scripts\python.exe scripts\tools\h7_broker_dependency_closeout_smoke.py
.\.venv\Scripts\python.exe scripts\tools\h9_production_hardening_smoke.py
.\.venv\Scripts\python.exe scripts\tools\int_harden_1_smoke.py
.\.venv\Scripts\python.exe scripts\tools\intelligence_ops_smoke.py
```

## Validação executada

- `pytest -q` -> `194 passed`
- `h7_broker_dependency_closeout_smoke.py` -> PASS
- `h9_production_hardening_smoke.py` -> PASS
- `int_harden_1_smoke.py` -> PASS
- `intelligence_ops_smoke.py` -> PASS
