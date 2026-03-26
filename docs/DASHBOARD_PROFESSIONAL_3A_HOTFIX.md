# DASHBOARD-3A — Streamlit Direct-Run Import Hotfix

## Problema
O pacote Dashboard 3 funcionava bem pelo launcher `python -m natbin.dashboard`, mas falhava quando o operador iniciava diretamente com Streamlit:

```powershell
streamlit run src/natbin/dashboard/app.py -- --repo-root . --config config/multi_asset.yaml
```

A causa era o uso de imports relativos no topo de `app.py`, enquanto o Streamlit executa esse arquivo como script.

## Correção
- bootstrap do `src/` no `sys.path` quando o arquivo é executado sem contexto de pacote
- troca para imports absolutos `natbin.dashboard.*`
- `run()` é chamado automaticamente quando `app.py` roda como `__main__`
- `report.py` recebe o mesmo bootstrap para manter consistência operacional

## Modos suportados
- `python -m natbin.dashboard --repo-root . --config config/multi_asset.yaml`
- `streamlit run src/natbin/dashboard/app.py -- --repo-root . --config config/multi_asset.yaml`

## Compatibilidade
Sem mudanças de schema, contratos ou config. O hotfix é apenas de entrypoint/import.
