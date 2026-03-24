# READY-2 — Controlled Practice Green

O READY-2 fecha a trilha operacional da prática controlada com um bootstrap
explícito e reproduzível.

Até o READY-1, o projeto já conseguia responder se o scope estava pronto ou
não. O problema é que, em checkout limpo, ainda faltava uma trilha única para
materializar os pré-requisitos antes de exigir `doctor` e `practice` verdes.

O READY-2 adiciona exatamente essa peça.

## Comando canônico

```powershell
python -m natbin.runtime_app practice-bootstrap --repo-root . --config config/live_controlled_practice.yaml --json
```

Artefato emitido por scope:

```text
runs/control/<scope_tag>/practice_bootstrap.json
```

Relatórios auxiliares:

```text
runs/tests/practice_bootstraps/practice_bootstrap_latest_<scope_tag>.json
runs/tests/practice_bootstraps/practice_bootstrap_<timestamp>_<scope_tag>.json
```

## O que o bootstrap faz

Na ordem:

1. lê o `practice.json` atual
2. bloqueia imediatamente se houver problema estrutural de profile
   - conta REAL em vez de PRACTICE
   - escopo não controlado
   - stake/limits fora do envelope
   - guard/failsafe incompatíveis
3. executa `asset_prepare` quando faltam `dataset` ou `market_context`
4. refresca intelligence / portfolio materialization
5. executa soak quando necessário
6. refresca intelligence novamente
7. reemite `doctor` e `practice`

## Campos principais do payload

- `ready_for_practice_green`: `doctor` + `practice` verdes ao final do bootstrap
- `round_eligible`: o scope já pode seguir para `practice-round`
- `blocked_reason`: motivo canônico do bloqueio, quando existir
- `asset_prepare`: resumo do preparo de dataset/market context
- `intelligence_refresh`: resumo dos refreshes antes/depois do soak
- `soak`: ação tomada (`ran`, `reused_fresh`, `disabled`)
- `pre_practice` / `post_practice`: estado antes e depois do bootstrap

## Runbook READY-2

Em checkout limpo, a sequência recomendada fica:

```powershell
python -m natbin.runtime_app practice-bootstrap --repo-root . --config config/live_controlled_practice.yaml --json
python -m natbin.runtime_app doctor --repo-root . --config config/live_controlled_practice.yaml --json
python -m natbin.runtime_app practice --repo-root . --config config/live_controlled_practice.yaml --json
python -m natbin.runtime_app practice-round --repo-root . --config config/live_controlled_practice.yaml --json
```

## Integração com o practice-round

O `practice-round` agora embute o bootstrap como trilha anterior à validação.
Isso garante que a rodada passe a carregar, dentro do payload final, a evidência
explícita de:

- como o scope foi preparado
- se o soak foi executado ou reaproveitado
- qual era o estado do `practice` antes e depois

## Definition of Done

O READY-2 é considerado fechado quando, num profile de PRACTICE controlado e com
credenciais válidas:

- `runtime_app security` = `ok`
- `runtime_app doctor` fica sem blockers reais de runtime
- `runtime_app practice` fica com `ready_for_practice=true`
- `runtime_app practice-round` fecha verde
- o fluxo acima é reproduzível em checkout limpo
