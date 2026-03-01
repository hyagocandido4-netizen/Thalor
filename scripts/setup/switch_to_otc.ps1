param(
  [string]$Asset = "EURUSD-OTC",
  [int]$BackfillDays = 120,
  [int]$LookbackCandles = 8000,
  [int]$IntervalSec = 300,
  [int]$SleepMs = 200,
  [switch]$Retune
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

$root = (Get-Location).Path
$py = Join-Path $root ".venv\Scripts\python.exe"

Require-Path $py "Nao encontrei .venv. Rode init.ps1."
Require-Path "config.yaml" "Nao encontrei config.yaml."
Require-Path ".env" "Nao encontrei .env (credenciais)."
Require-Path "src\natbin\settings.py" "Projeto incompleto (natbin.settings)."
Require-Path "src\natbin\iq_client.py" "Projeto incompleto (natbin.iq_client)."
Require-Path "src\natbin\db.py" "Projeto incompleto (natbin.db)."
Require-Path "src\natbin\make_dataset.py" "Nao achei make_dataset.py (fase 2)."

$ts = (Get-Date -Format "yyyyMMdd_HHmmss")

Write-Host "== SWITCH TO OTC ==" -ForegroundColor Cyan
Write-Host "Asset: $Asset | BackfillDays: $BackfillDays | Lookback: $LookbackCandles" -ForegroundColor Cyan

# 1) Backup config.yaml
Copy-Item -Force "config.yaml" "config_backup_$ts.yaml"
Write-Host "Backup: config_backup_$ts.yaml" -ForegroundColor Green

# 2) Backup runs (para não misturar logs/estado)
if (Test-Path "runs") {
  Rename-Item -Force "runs" "runs_fx_backup_$ts"
  Write-Host "Runs movido -> runs_fx_backup_$ts" -ForegroundColor Green
}
New-Item -ItemType Directory -Force -Path "runs" | Out-Null

# 3) Atualiza config.yaml: asset OTC + db_path separado
$pyCfg = @"
import yaml
from pathlib import Path

p = Path("config.yaml")
cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
cfg.setdefault("data", {})
cfg["data"]["asset"] = "$Asset"
cfg["data"]["interval_sec"] = $IntervalSec
cfg["data"]["db_path"] = "data/market_otc.sqlite3"

# zera best antigo (era calibrado no EURUSD normal)
cfg.pop("best", None)

p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("config.yaml atualizado:", cfg["data"])
"@
& $py -c $pyCfg
if ($LASTEXITCODE -ne 0) { throw "Falhou ao atualizar config.yaml" }

# 4) (Opcional) Checagem se o OTC está aberto (get_all_open_time é pesado, mas ok rodar 1x) :contentReference[oaicite:1]{index=1}
$pyOpen = @"
from natbin.settings import load_settings
from natbin.iq_client import IQClient, IQConfig

s = load_settings()
client = IQClient(IQConfig(email=s.iq.email, password=s.iq.password, balance_mode=s.iq.balance_mode))
client.connect()

asset = s.data.asset
all_open = client.iq.get_all_open_time()  # pesado (rede)

def show(cat):
    try:
        v = all_open[cat][asset]["open"]
        print(f"{cat}: {asset} open={v}")
    except Exception as e:
        print(f"{cat}: {asset} (nao achei)")

for cat in ["turbo","binary","digital","forex","crypto","cfd"]:
    show(cat)
"@
Write-Host "`nChecando se o ativo está aberto (pode demorar alguns segundos)..." -ForegroundColor Yellow
& $py -c $pyOpen
# não falha o script se isso der ruim; só informa
Write-Host "(Se turbo/binary/digital estiver open=False, OTC pode estar fechado agora.)" -ForegroundColor DarkGray

# 5) Garante backfill_candles.py (se não existir, cria)
if (-not (Test-Path "src\natbin\backfill_candles.py")) {
@'
from __future__ import annotations
import argparse, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from natbin.settings import load_settings
from natbin.iq_client import IQClient, IQConfig
from natbin.db import open_db, upsert_candles

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--sleep_ms", type=int, default=200)
    args = ap.parse_args()

    s = load_settings()
    tz = ZoneInfo(s.data.timezone)
    asset = s.data.asset
    interval_sec = int(s.data.interval_sec)
    max_batch = int(s.data.max_batch)
    db_path = s.data.db_path

    end_dt = datetime.now(tz=tz)
    start_dt = end_dt - timedelta(days=int(args.days))
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    con = open_db(db_path)
    client = IQClient(IQConfig(email=s.iq.email, password=s.iq.password, balance_mode=s.iq.balance_mode))
    client.connect()

    cursor_end = end_ts
    loops = 0
    seen = 0

    print(f"[backfill] {asset} {interval_sec}s days={args.days}")
    while cursor_end > start_ts:
        loops += 1
        candles = client.get_candles(asset, interval_sec, max_batch, cursor_end)
        if not candles:
            print("[backfill] sem retorno; parando.")
            break
        seen += len(candles)
        upsert_candles(con, asset, interval_sec, candles)
        min_ts = min(int(c.get("from", 0)) for c in candles if c.get("from") is not None)
        cursor_end = min_ts - interval_sec
        if loops % 20 == 0:
            print(f"[backfill] loops={loops} seen~{seen}")
        time.sleep(max(0.0, float(args.sleep_ms)/1000.0))

    con.close()
    print(f"[backfill] done. loops={loops} seen~{seen}")

if __name__ == "__main__":
    main()
'@ | Set-Content -Encoding UTF8 "src\natbin\backfill_candles.py"
}

# 6) Backfill + seed recent
Write-Host "`nBackfill OTC ($BackfillDays dias)..." -ForegroundColor Cyan
& $py -m natbin.backfill_candles --days $BackfillDays --sleep_ms $SleepMs

Write-Host "`nSeed recent (overlap)..." -ForegroundColor Cyan
$env:LOOKBACK_CANDLES = "$LookbackCandles"
& $py -m natbin.collect_recent

Write-Host "`nRebuild dataset..." -ForegroundColor Cyan
& $py -m natbin.make_dataset

# 7) (Opcional) Retune completo + freeze best + paper v3
if ($Retune) {
  Require-Path "src\natbin\paper_tune_v2.py" "Nao achei paper_tune_v2.py (tuner). Se quiser, eu te mando o script que gera."
  Require-Path "src\natbin\paper_backtest_v3.py" "Nao achei paper_backtest_v3.py."

  Write-Host "`nTUNER (paper)..." -ForegroundColor Cyan
  & $py -m natbin.paper_tune_v2

  $freeze = @'
import json, yaml
from pathlib import Path
runs = Path("runs")
tunes = sorted(runs.glob("tune_v2_*"), key=lambda p: p.name, reverse=True)
if not tunes:
    raise SystemExit("Nao achei runs/tune_v2_*.")
t = tunes[0]
best = json.loads((t/"tune_summary.json").read_text(encoding="utf-8"))["best"]
p = Path("config.yaml")
cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
cfg["best"] = {
  "tune_dir": str(t).replace("\\","/"),
  "threshold": float(best["threshold"]),
  "bounds": {"vol_lo": float(best["vol_lo"]), "vol_hi": float(best["vol_hi"]),
             "bb_lo": float(best["bb_lo"]), "bb_hi": float(best["bb_hi"]),
             "atr_lo": float(best["atr_lo"]), "atr_hi": float(best["atr_hi"])},
  "notes": "Frozen from latest tune_v2 (OTC)."
}
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("Frozen best into config.yaml.")
'@
  Write-Host "`nFreeze BEST..." -ForegroundColor Cyan
  & $py -c $freeze

  Write-Host "`nPAPER V3 (sanity)..." -ForegroundColor Cyan
  & $py -m natbin.paper_backtest_v3
}

Write-Host "`nOK. OTC configurado. Agora reabilite o Scheduler." -ForegroundColor Green