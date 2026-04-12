# Thalor — Runbook final do envelope Canary

## Estado-alvo
O canary é considerado **fechado e saudável** quando o comando abaixo retorna `decision=GO_WAITING_SIGNAL` ou `decision=GO_ACTIONABLE`:

```powershell
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
```

## Envelope congelado
- profile: `config/practice_portfolio_canary.yaml`
- provider: pode permanecer `degraded` enquanto continuar `provider_ready_scopes > 0`
- paralelismo: **não aumentar**
- topologia: multi-asset na observação, top-1/single-position na execução
- maintenance: dívida secundária de `cp_meta` é manutenção, não blocker do top-1

## Sequência operacional mínima
### 1. GO/NO-GO do canary
```powershell
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
```

### 2. Se houver dívida secundária de cp_meta
```powershell
.\scripts\tools\portfolio_cp_meta_maintenance.cmd --config config\practice_portfolio_canary.yaml --json
```

### 3. Bundle finalista do canary
```powershell
.\scripts\tools\capture_canary_closure_bundle.cmd --config config\practice_portfolio_canary.yaml
```

## Interpretação rápida
### `GO_WAITING_SIGNAL`
O envelope está saudável. O melhor scope está em no-trade legítimo e a operação pode permanecer rodando aguardando o próximo candle elegível.

### `GO_ACTIONABLE`
O envelope está saudável e há scope acionável.

### `NO_GO_REPAIR`
Ainda existe reparo realmente bloqueante.

### `NO_GO_PROVIDER_UNSTABLE`
Provider instável para o envelope atual. Não expandir regime operacional.

## O que não fazer
- não aumentar `max_parallel_assets`
- não abrir execução concorrente só porque `provider_ready_scopes=6`
- não usar canary como prova de prontidão para REAL/live

## O que é manutenção legítima
- rodar `portfolio_cp_meta_maintenance`
- regenerar bundle de fechamento
- acompanhar dívida secundária de `cp_meta`

## Critério de encerramento desta fase
Esta fase termina quando:
- `canary_go_no_go` retorna `GO_WAITING_SIGNAL` ou `GO_ACTIONABLE`
- `capture_canary_closure_bundle` sai com `ok=true`
- não há `blocking_cp_meta_missing_scopes`
- não há `blocking_gate_fail_closed_scopes`
