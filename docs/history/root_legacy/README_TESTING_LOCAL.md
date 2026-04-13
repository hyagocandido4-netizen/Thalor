# Thalor test-ready bundle

Este ZIP já vem com o código completo do projeto, a suíte `tests/`, os `smokes`
operacionais e um runner único para facilitar os testes locais.

Arquivos principais para começar:
- `requirements-dev.txt`
- `docs/LOCAL_TESTING_M71.md`
- `scripts/tools/local_test_suite.py`
- `scripts/tools/run_local_test_suite.ps1`

Primeiro comando recomendado:

```powershell
.\.venv\Scripts\python.exe scripts\tools\local_test_suite.py --repo-root . --preset quick
```

## Validação live controlada

Depois da suíte local, use `docs/CONTROLLED_LIVE_VALIDATION_M72.md` e o runner `scripts/tools/controlled_live_validation.py`.
