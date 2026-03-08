Thalor - Package Q hotfix5 (2026-03-08)

Objetivo
- Portfolio runner respeita cfg.execution.enabled.
- Evita executar trades reais quando execution.enabled=false (segurança).

Mudanças
- src/natbin/portfolio/runner.py
  - execute_scope: não força mais execution_enabled=True via env.
  - run_portfolio_cycle: se houver seleção e cfg.execution.enabled=false, adiciona erro
    'execution_skipped:execution_disabled' e não executa.

Como aplicar
1) Pare qualquer loop em execução.
2) Extraia este ZIP na raiz do repo (mesmo nível do src/ e config/), sobrescrevendo arquivos.
3) Rode:
   python -m natbin.runtime_app portfolio observe --repo-root . --config config/multi_asset.yaml --once --topk 3 --lookback-candles 5000 --json

Notas
- Para habilitar execução real, defina execution.enabled: true no config (ou via env THALOR__EXECUTION__ENABLED=1).
- Kill switch / drain continuam funcionando como antes.
