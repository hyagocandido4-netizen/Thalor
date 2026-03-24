# RETRAIN-OPS-1A — recomputação de cooldown expirado

Este pacote é um hotfix do `RETRAIN-OPS-1`.

## Sintoma observado

O retrain ficava bloqueado com:

- `state = cooldown`
- `reason = cooldown_active`

mesmo quando `cooldown_until_utc` já estava no passado.

## Causa

A trilha operacional estava lendo `retrain_plan.json` / `retrain_status.json` como verdade final, sem recomputar a validade real do cooldown contra o relógio atual.

## Estratégia do fix

Antes de montar o status ou iniciar o run, o pacote:

1. lê `retrain_plan` e `retrain_status`
2. calcula se o cooldown realmente ainda está ativo
3. se o cooldown venceu, tenta um refresh leve da inteligência (`rebuild_pack=false`)
4. se o refresh ainda deixar o plan em cooldown stale, normaliza o estado para:
   - `queued`, se `queue_recommended=true`
   - `watch`, se `watch_recommended=true`
   - `idle`, caso contrário

## Novo bloco de auditoria

`retrain_review.json` e o payload do run agora incluem:

- `cooldown_refresh.refreshed`
- `cooldown_refresh.normalized`
- `cooldown_refresh.refresh_payload`

Isso deixa explícito se houve refresh/normalização antes do retrain real.

## Critério de sucesso

- cooldown vencido não pode mais bloquear `retrain run`
- `retrain status` precisa refletir o estado recomputado
- um run real deve preencher `after` e `comparison` no review
