# Risk report v2 (P3)

> ⚠️ Alto risco: este projeto lida com sinais/automação para opções binárias.
> Este relatório NÃO é promessa de ganho — é engenharia + evidência + controle de risco.

## Objetivo

Transformar o LIVE em algo **controlável** via estatística:

1) O bot está acima do break-even nos trades tomados?
2) Quão confiante estamos nisso? (intervalo de confiança)
3) Qual stake faz sentido sem overbet? (Kelly fracionado + cap)

## Fonte de dados (atual)

- `runs/live_signals.sqlite3` — tabela `signals_v2`
  - filtramos apenas trades tomados: `action in ('CALL','PUT')`
- `data/market_otc.sqlite3` — tabela `candles`
  - por padrão (`outcome=open_close`) avaliamos o trade como:
    - **entrada** no `open` do candle `ts_next = ts + interval_sec`
    - **expiração** no `close` do candle `ts_next`
  - win (open_close):
    - CALL: close_next > open_next
    - PUT:  close_next < open_next
  - opção alternativa (`outcome=close_close`) para aproximar entrada por close:
    - CALL: close_next > close_now
    - PUT:  close_next < close_now
  - tie:
    - default: loss
    - opcional: push (`--tie push`)

## Como rodar (PowerShell)

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000
```

Exemplo (modo close_close):

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000 -Outcome close_close
```

Com salvar artefatos:

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000 -Save
# outputs:
#   runs/risk_report_YYYYMMDD_HHMMSS.json
#   runs/risk_trades_YYYYMMDD_HHMMSS.csv
```

## Como rodar (Python)

```powershell
.\.venv\Scripts\python.exe -m natbin.risk_report --signals-db runs/live_signals.sqlite3
```

## Métricas

Para cada janela (overall + rolling 30/60/120 dias):

- `p_hat`: win rate observado
- `Wilson CI`: intervalo conservador para win rate (usamos p_low)
- `payout_ref`: por default usamos **p10** do payout observado (conservador)
- `EV_low`: `p_low * payout_ref - (1 - p_low)`
- `stake_suggested`:
  - Kelly para payoff binário: `f* = p - (1-p)/b`
  - substitui `p` por `p_low`
  - aplica `kelly_frac` (default 0.25)
  - aplica cap duro `cap_frac` (default 0.02)
  - só sugere se `n_trades >= min_trades` e `EV_low > 0`

## Notas

- Se o bot é ultra-seletivo (Top‑K), o n de trades é pequeno: o CI vai ser largo.
- A recomendação de stake é propositalmente conservadora.