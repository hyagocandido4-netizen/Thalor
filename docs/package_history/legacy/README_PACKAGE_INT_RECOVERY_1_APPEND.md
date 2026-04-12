# Package INT-RECOVERY-1 — Intelligence Recovery & Artifact Isolation

Este pacote fecha a próxima etapa lógica após a validação operacional em PRACTICE.

## O que entra

- isolamento dos artifacts `portfolio_cycle_latest.json` e `portfolio_allocation_latest.json` por `config/profile`, evitando que o controlled practice leia cycles antigos de outro profile
- leitura contextual desses artifacts em `portfolio status`, `intelligence surface` e `execution`
- recuperação de `training_rows` da inteligência a partir de múltiplos DBs de sinais, com fallback para inferência de direção em rows `HOLD`
- fallback de anti-overfit a partir de `daily_summary_*.json` quando `summary.json/per_window` não existir
- testes cobrindo recuperação da inteligência e isolamento dos artifacts de portfolio

## Critérios de done deste pacote

- `portfolio status` deixa de reaproveitar artifacts globais incompatíveis com o `config/profile` atual
- `intelligence_pack` consegue treinar com base recuperada quando houver histórico suficiente em sinais `HOLD`
- `anti_overfit` deixa de falhar apenas por ausência de `summary.json` se existirem daily summaries com informação suficiente

## Observações

- o pacote mantém escrita legado+scoped para compatibilidade, mas a leitura agora prioriza o escopo atual e ignora fallback legado incompatível
- quando o scope continuar em `regime_block`, isso passa a refletir um estado real de inteligência/regime, e não mais contaminação de artifacts antigos
