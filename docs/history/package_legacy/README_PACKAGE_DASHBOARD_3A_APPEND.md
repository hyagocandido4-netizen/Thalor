# Package DASHBOARD-3A — Streamlit Direct-Run Import Hotfix

## Objetivo
Corrigir a abertura do dashboard via `streamlit run src/natbin/dashboard/app.py` sem quebrar o launcher `python -m natbin.dashboard`.

## Escopo
- bootstrap de imports absolutos quando `app.py` ou `report.py` são executados como script
- `app.py` passa a chamar `run()` quando executado como `__main__`
- smoke e teste de regressão para import standalone

## Resultado esperado
O dashboard volta a abrir normalmente em `localhost:8501` sem `ImportError: attempted relative import with no known parent package`.
