# READY-1 — Controlled Practice Readiness

O READY-1 fecha a ponte entre a refatoração/hardening do runtime e a primeira
operação **live-controlada em conta PRACTICE**.

A ideia não é “ligar em produção”, e sim responder com um payload canônico se o
scope atual está pronto para um ciclo live **controlado** com risco reduzido.

## Comando canônico

```powershell
python -m natbin.runtime_app practice --repo-root . --config config/live_controlled_practice.yaml --json
```

Artefato emitido por scope:

```text
runs/control/<scope_tag>/practice.json
```

## O que o READY-1 valida

O payload consolida, no mínimo:

- `execution.mode=live`, `execution.provider=iqoption`, `execution.account_mode=PRACTICE`
- `broker.balance_mode=PRACTICE`
- escopo controlado (`multi_asset=false`, uma scope, `portfolio_topk_total=1`)
- stake dentro do envelope recomendado para practice (default: `<= 5.0`)
- limites de execução controlados (`max_pending_unknown=1`, `max_open_positions=1`)
- broker guard / time filter
- gates operacionais (`kill_switch`, `drain_mode`)
- `production_doctor`
- `intelligence_surface`
- `alerts_status`
- soak recente (`runs/soak/soak_latest_<scope>.json`)

## Campos principais

- `ready_for_practice`: pronto de forma **estrita** para o stage practice
- `severity`: `ok`, `warn` ou `error`
- `checks`: lista detalhada dos gates/validações
- `doctor`: resumo do production doctor
- `intelligence`: resumo da surface de inteligência
- `soak`: resumo do último soak por scope
- `validation`: plano recomendado do `controlled_live_validation.py --stage practice`

## Diferença entre release / doctor / practice

- `runtime_app doctor` = saúde operacional do runtime / broker / artifacts
- `runtime_app release` = checklist de release / bundle / docs / posture geral
- `runtime_app practice` = gate objetivo para **live-controlado em PRACTICE**

Ou seja: READY-1 não substitui `doctor` nem `release`; ele usa essas superfícies
como insumo e fecha a decisão operacional específica do stage `practice`.

## Config propagation fix

Neste pacote, `runtime_app observe --config ...` e `runtime_daemon` passam a
propagar o config selecionado até o auto-cycle inteiro:

- lock/scope são resolvidos pelo config realmente escolhido
- `build_auto_cycle_plan()` injeta `THALOR_CONFIG_PATH`, `ASSET` e `INTERVAL_SEC`
  nos subprocessos
- `runtime_soak.py` também aceita `--config`, `--asset` e `--interval-sec`

Isso remove o risco de rodar o profile de practice e o daemon cair silenciosamente
no `config/base.yaml`.

## Soak recomendado antes do practice

```powershell
python scripts/tools/runtime_soak.py --repo-root . --config config/live_controlled_practice.yaml --max-cycles 3
python -m natbin.runtime_app practice --repo-root . --config config/live_controlled_practice.yaml --json
```

Se o soak estiver ausente ou stale, o READY-1 marca isso explicitamente.

## Próximo passo lógico

Depois que o READY-1 estiver verde, a rodada operacional completa em PRACTICE
pode ser executada com:

```powershell
python -m natbin.runtime_app practice-round --repo-root . --config config/live_controlled_practice.yaml --json
```

Esse comando usa o READY-1 como gate, renova o soak quando necessário e dispara
o trilho `controlled_live_validation --stage practice`, consolidando tudo em
`runs/control/<scope_tag>/practice_round.json`.
