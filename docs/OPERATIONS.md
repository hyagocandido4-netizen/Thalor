# Operação do Thalor (runbook)

Este documento é um guia prático para **rodar**, **monitorar** e **avaliar** o Thalor com o mínimo de risco operacional.

## Antes de rodar

- Confirme:
  - `config.yaml` (asset/interval/timezone)
  - `.env` preenchido (não commitado)
  - `.venv` com dependências instaladas

## Como iniciar

### Rodar uma vez (debug)

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once -TopK 3
```

### Rodar como daemon

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -TopK 3
```

> Dica: use `-TopK 3` apenas como exemplo. Ajuste conforme estratégia.

## Observabilidade

### Logs

O loop escreve transcripts diários em:

- `runs/logs/observe_loop_auto_YYYYMMDD.log`

Você pode controlar retenção via:

- `LOOP_LOG_RETENTION_DAYS` (ex.: `14`)

### Status heartbeat

Se habilitado, o loop escreve um status JSON (útil para monitoramento):

```powershell
$env:LOOP_STATUS_ENABLE = "1"
```

Arquivos típicos:

- `runs/observe_loop_auto_status_<ASSET>_<INTERVAL>s.json`
- (compat) `runs/observe_loop_auto_status.json`

Campos importantes:

- `phase/state/message` (em que etapa está)
- `quota` (executed/allowed/budget)
- `settle` (pendências de avaliação)
- `market_context` (payout/open + freshness/stale)

## Como parar com segurança

- No terminal do `pwsh`, use **Ctrl + C**.
- Evite “matar” o processo se possível — o loop tenta finalizar status como `stopped`.

## Como avaliar o funcionamento após rodar

### 1) Verificar persistência e consistência

- SQLite:
  - `runs/live_signals.sqlite3` → tabela `signals_v2`

Perguntas práticas:

- O bot está gravando uma linha por candle avaliado?
- Trades (`CALL/PUT`) não estão sendo sobrescritos por reprocessamento?
- As colunas `reason`, `blockers`, `market_context_*` fazem sentido?

### 2) Rodar o risk report

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000
```

Esse relatório é o canal “oficial” para:

- métricas de performance
- sizing conservador (com estatística defensiva)

### 3) Verificar daily summaries

Arquivos típicos:

- `runs/daily_summary_YYYYMMDD_<ASSET>_<INTERVAL>s.json`

O `observe_loop_auto.ps1` já tenta atualizar isso quando necessário.

## Fail-safe (recomendado)

Em produção, prefira ligar fail‑closed:

```powershell
$env:GATE_FAIL_CLOSED = "1"
$env:MARKET_CONTEXT_FAIL_CLOSED = "1"
```

Assim, se algum componente crítico falhar, o bot tende a **HOLD**.

## Problemas comuns

### “Sem sinais” / sempre HOLD

Pode ser normal (estratégia seletiva), mas valide:

- `market_context_stale=1`? (payout/open cache velho)
- `regime_block`?
- `cp_reject`?
- `below_ev_threshold`?
- `max_k_reached`? (quota/pacing)

### DB locked / sqlite busy

- Evite múltiplas instâncias do loop para o mesmo asset/interval.
- Se necessário, pare o loop, aguarde e reinicie.

### Erros ruidosos do iqoptionapi em threads

Algumas versões do `iqoptionapi` podem gerar exceções em threads internas.
O Thalor tenta neutralizar isso reduzindo dependência dessas rotas (ex.: digital open),
mas se ocorrer, priorize:

- rodar com `MARKET_CONTEXT_FAIL_CLOSED=1`
- usar fontes alternativas (candle freshness / turbo payout)

## Retenção e limpeza

- `RUNTIME_RETENTION_DAYS` controla prune de artefatos (CSV/summary/SQLite rows).
- O prune deve ser **idempotente** e seguro.

