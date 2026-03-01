# iq-bot

WARNING: high risk

This project deals with signals/automation for **binary options** (high risk). Nothing here is a promise of profit.
The goal is engineering + statistical validation + risk control.

## What is this?

A Windows-first pipeline to:
- collect closed candles into SQLite
- generate features/datasets
- train/select models with walk-forward / pseudo-future validation
- run a very selective LIVE observer (Top-K per day + regime gate + EV gating)
- audit LIVE performance and risk via a risk report (stake sizing with conservative statistics)

Default target (can be changed in `config.yaml`):
- Asset: `EURUSD-OTC`
- Interval: `300s` (5 minutes)
- Timezone: `America/Sao_Paulo`

## Requirements

- Windows 10/11
- PowerShell 7 (`pwsh`)
- Python 3.12

## Setup (quick)

```powershell
git clone https://github.com/hyagocandido4-netizen/iq-bot.git
cd iq-bot

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env
# Edit .env with your credentials (DO NOT COMMIT)
```

## Key commands

### Observe loop (LIVE)

Run once (debug):

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once
```

### Risk report (P3)

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000
```

## Repository layout

- `src/natbin/` - Python modules
- `scripts/` - PowerShell automation (setup/scheduler/tools/patches)
- `data/` - local SQLite DBs (ignored by git)
- `runs/` - live logs / model caches / run artifacts (ignored by git)

## Notes

- This repo does not ship a license file yet. Until a license is added, default copyright applies.
- Do not commit DBs, WAL/SHM files, or credentials.