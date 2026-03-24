# INT-RECOVERY-1 — Artifact isolation + intelligence recovery

## Problema atacado

Depois da rodada controlada em PRACTICE, o runtime ficou saudável, mas a surface de inteligência ainda podia ler `portfolio_cycle_latest` / `portfolio_allocation_latest` globais produzidos por outro config/profile.

Ao mesmo tempo, o `intelligence_pack` podia ficar subalimentado quando a base de sinais tinha poucos `CALL/PUT` explícitos e muitos `HOLD`, além de falhar no anti-overfit quando `summary.json` não estava presente.

## Solução

### 1. Latest artifacts por config/profile

Os arquivos latest de portfolio agora têm uma variante scoped em `runs/portfolio/profiles/<profile_key>/...` e carregam metadata de contexto:

- `profile_key`
- `runtime_profile`
- `config_path`

A escrita continua atualizando o legado para compatibilidade, mas a leitura prioriza o scoped atual e só usa o legado se ele realmente combinar com o contexto ativo.

### 2. Recovery de training rows

O fit de inteligência agora descobre múltiplos DBs de sinais por scope e pode recuperar training rows a partir de eventos `HOLD`, inferindo a direção com base em `proba_up` quando necessário.

Isso permite que o learned gate seja treinado mesmo quando o histórico explícito de `CALL/PUT` é pequeno, desde que exista histórico suficiente de sinais observados.

### 3. Anti-overfit com fallback

Na ausência de `summary.json` com `per_window`, o pack sintetiza uma visão multiwindow a partir de `daily_summary_*.json`, usando `trades_eval_total` e `win_rate_eval_total`.

## Impacto esperado

- `portfolio status` e `intelligence` passam a refletir o profile/config atual
- warnings residuais em PRACTICE passam a ser sobre regime/drift reais, não contaminação de artifacts
- o `intelligence_pack` consegue produzir mais contexto útil para retrain/recovery
