# Package PHASE3-COLLECT-FIX-1

## Objetivo

Corrigir a coleta multi-asset via IQ Option para ativos que não existem
nativamente no mapa interno de consts do `iqoptionapi`, sem hardcodes por asset.

## Entregas

- refresh dinâmico do catálogo de ativos no `IQClient`
- resolução genérica de aliases de asset por normalização de nome
- reconnect robusto quando o client novo ainda não tem `api`
- `collect_recent` com logging explícito de `requested_asset` e `broker_asset`
- smoke test cobrindo os 6 assets do `multi_asset.yaml`

## Compatibilidade

- sem mudar o contrato público do `collect_recent`
- sem alterar schema de banco
- sem ativar execução real
- sem branchs de config específicas por profile
