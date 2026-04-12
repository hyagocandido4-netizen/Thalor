# Parte 6 — Fechamento de suíte

Esta parte fecha as regressões integradas restantes que impediam a suíte completa de fechar verde.

## Blocos corrigidos

1. `config_provenance`
   - Mantém `broker.balance_mode` como campo canônico de YAML.
   - Trata override proibido por secret bundle como blocker apenas em contexto REAL.
   - Em contexto PRACTICE, continua ignorando o campo sem transformar o payload em erro.

2. `collect_recent` / `refresh_market_context`
   - Restauram compatibilidade com `resolve_repo_root` e `load_resolved_config`.
   - Recuperam fallback resiliente em falhas operacionais do broker.
   - Mantêm compatibilidade com testes que usam `IQClient.from_runtime_config` e com cenários que exigem construção direta do cliente.

3. `incident_status` / `production_doctor`
   - Passam a expor diagnóstico enriquecido de breaker.
   - Superfícies agora incluem causa primária e último erro de transporte.

4. `repo_sync` / `release_hygiene`
   - Reintroduz `noise_only_dirty` / `meaningful_dirty`.
   - Trata `test_battery`, `diag_zips`, `coverage.xml` e `diag_bundle_*.zip` como ruído seguro para prune.

5. `IQClient`
   - Endurecido para construção parcial em testes/hotfixes.
   - Helpers de transport/request-metrics passam a usar `getattr(..., None)` defensivo.

## Resultado

- Suíte completa: `366 passed`
