# Package PRACTICE-OPS-1C — Warn-Only Intelligence Gate Hotfix

## O que este hotfix corrige

Durante a rodada controlada em `PRACTICE`, a pilha operacional podia ficar totalmente saudável (`soak`, `health`, `market_context`, `broker probe`) e ainda assim o `practice-round` bloquear antes da validação por causa de avisos de intelligence, por exemplo:

- `portfolio_feedback` em `warn`
- `retrain_state=queued`
- `retrain_priority=high`
- `regime_block` / no-trade legítimo

Na prática, isso impedia a rodada de produzir evidência operacional justamente nos cenários em que a inteligência estava pedindo cautela e `observe --once` provavelmente acabaria em `HOLD` / `no_trade`, o que **não é erro** em controlled practice.

## Mudanças aplicadas

- `runtime_app practice` agora retorna exit code `0` quando o payload está operacionalmente saudável (`ok=true`), mesmo com `severity=warn`.
- `practice-round` deixa de bloquear automaticamente em `warn-only readiness`.
- o round continua exigindo:
  - ausência de blockers críticos estruturais
  - `practice.ok=true`
  - validação obrigatória bem-sucedida
- avisos de intelligence continuam expostos no payload final e rebaixam a severidade para `warn`, mas **não abortam a rodada por si só**.
- `practice_round.json` agora inclui `round_eligible` em `pre_practice` e `post_practice`.

## Resultado esperado

Quando o scope estiver saudável mas em no-trade por `regime_block` / `portfolio_feedback`, o `practice-round` deverá:

- concluir o soak
- executar a validação
- aceitar `no_trade_is_not_error=true`
- terminar com `round_ok=true` e `severity=warn`
- recomendar tratar retrain / regime antes de insistir em execução

## Comando para repetir

```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app practice-round --repo-root . --config config/live_controlled_practice.yaml --soak-cycles 1 --json
```
