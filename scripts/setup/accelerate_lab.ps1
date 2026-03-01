param(
  [int]$Years = 3,
  [int]$IntervalSec = 300,
  [int]$SleepMs = 200,
  [switch]$SkipBackfill,
  [switch]$SkipTune,
  [switch]$SkipMultiwindow
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param([string]$Path, [string]$Content)
  $dir = Split-Path -Parent $Path
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

$root = (Get-Location).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
Require-Path $py "Nao encontrei .venv. Rode init.ps1."
Require-Path "config.yaml" "Nao encontrei config.yaml."
Require-Path "src\natbin\iq_client.py" "Nao encontrei src\natbin\iq_client.py (fase 1)."
Require-Path "src\natbin\db.py" "Nao encontrei src\natbin\db.py (fase 1)."
Require-Path "src\natbin\make_dataset.py" "Nao encontrei src\natbin\make_dataset.py (fase 2)."
Require-Path "data" "Nao encontrei pasta data/."

# ---------------------------------------------------------------------
# (A) Backfill Python module (cria/atualiza)
# ---------------------------------------------------------------------
Write-Utf8NoBomFile "src\natbin\backfill_candles.py" @'
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from natbin.settings import load_settings
from natbin.iq_client import IQClient, IQConfig
from natbin.db import open_db, upsert_candles


def dt(ts: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz=tz).isoformat(timespec="seconds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--asset", type=str, default=None)
    ap.add_argument("--interval", type=int, default=None)
    ap.add_argument("--sleep_ms", type=int, default=200)
    ap.add_argument("--max_batch", type=int, default=None)
    args = ap.parse_args()

    s = load_settings()
    tz = ZoneInfo(s.data.timezone)

    asset = args.asset or s.data.asset
    interval_sec = int(args.interval or s.data.interval_sec)
    max_batch = int(args.max_batch or s.data.max_batch)
    db_path = s.data.db_path

    end_dt = datetime.now(tz=tz)
    start_dt = end_dt - timedelta(days=int(args.days))
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    con = open_db(db_path)
    client = IQClient(IQConfig(
        email=s.iq.email,
        password=s.iq.password,
        balance_mode=s.iq.balance_mode,
    ))
    client.connect()

    cursor_end = end_ts
    loops = 0
    total_rows_seen = 0

    print(f"[backfill] asset={asset} interval={interval_sec}s days={args.days}")
    print(f"[backfill] window: {start_dt.isoformat(timespec='seconds')} -> {end_dt.isoformat(timespec='seconds')}")

    while cursor_end > start_ts:
        loops += 1
        candles = client.get_candles(asset, interval_sec, max_batch, cursor_end)
        if not candles:
            print("[backfill] sem retorno; parando.")
            break

        total_rows_seen += len(candles)
        upsert_candles(con, asset, interval_sec, candles)

        min_ts = min(int(c.get("from", 0)) for c in candles if c.get("from") is not None)
        cursor_end = min_ts - interval_sec

        if loops % 20 == 0:
            print(f"[backfill] loops={loops} last_cursor={dt(cursor_end, tz)} rows_seen~{total_rows_seen}")

        time.sleep(max(0.0, float(args.sleep_ms) / 1000.0))

    con.close()
    print(f"[backfill] done. loops={loops} rows_seen~{total_rows_seen} last_cursor={dt(cursor_end, tz)}")


if __name__ == "__main__":
    main()
'@

# ---------------------------------------------------------------------
# (B) Multiwindow evaluator (cria/atualiza)
# ---------------------------------------------------------------------
Write-Utf8NoBomFile "src\natbin\paper_multiwindow_v3.py" @'
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split


@dataclass
class WindowResult:
    window: int
    train_rows: int
    test_rows: int
    taken: int
    coverage: float
    hit_rate: float


def load_best_cfg() -> dict:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")) or {}
    best = cfg.get("best")
    if not best:
        raise RuntimeError("Nao achei bloco 'best' em config.yaml. Rode o tuner e congele o best.")
    return best


def train_calibrated_hgb(X_train: np.ndarray, y_train: np.ndarray) -> CalibratedClassifierCV:
    X_sub, X_cal, y_sub, y_cal = train_test_split(X_train, y_train, test_size=0.2, shuffle=False)
    base = HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.05,
        max_iter=600,
        l2_regularization=1.0,
        random_state=42,
    )
    base.fit(X_sub, y_sub)
    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(X_cal, y_cal)
    return cal


def make_mask(df: pd.DataFrame, b: dict) -> np.ndarray:
    vol = df["f_vol48"].to_numpy()
    bb  = df["f_bb_width20"].to_numpy()
    atr = df["f_atr14"].to_numpy()
    ok = (
        (vol >= b["vol_lo"]) & (vol <= b["vol_hi"]) &
        (bb  >= b["bb_lo"])  & (bb  <= b["bb_hi"]) &
        (atr >= b["atr_lo"]) & (atr <= b["atr_hi"])
    )
    ok = np.where(np.isfinite(vol) & np.isfinite(bb) & np.isfinite(atr), ok, False)
    return ok


def eval_block(model: CalibratedClassifierCV, df_block: pd.DataFrame, thr: float, b: dict) -> tuple[int, float, float]:
    feat = [c for c in df_block.columns if c.startswith("f_")]
    X = df_block[feat].astype("float64").values
    y = df_block["y_open_close"].astype("int64").values

    proba = model.predict_proba(X)[:, 1]
    mask = make_mask(df_block, b)

    take_call = (proba >= thr) & mask
    take_put  = (proba <= (1.0 - thr)) & mask
    taken = take_call | take_put

    taken_n = int(taken.sum())
    if taken_n == 0:
        return 0, 0.0, float("nan")

    pred = np.where(take_call, 1, 0)
    correct = int((pred[taken] == y[taken]).sum())
    hit = float(correct) / float(taken_n)
    cov = float(taken_n) / float(len(y))
    return taken_n, cov, hit


def main():
    best = load_best_cfg()
    thr = float(best["threshold"])
    b = best["bounds"]

    df = pd.read_csv("data/dataset_phase2.csv").sort_values("ts").reset_index(drop=True)

    # Definição de janelas: usa os últimos 60% do dataset e divide em N blocos sequenciais.
    N = 6
    min_train = max(20000, int(0.25 * len(df)))  # garante treino mínimo
    start_test = max(min_train, int(0.40 * len(df)))  # começa a avaliar a partir de ~40%

    tail = df.iloc[start_test:].copy().reset_index(drop=True)
    block = max(2000, int(len(tail) / N))

    results: list[WindowResult] = []
    for i in range(N):
        a = i * block
        bidx = min(len(tail), (i + 1) * block)
        if bidx - a < 1000:
            break

        # treino é tudo antes do bloco (expanding window)
        train_df = df.iloc[:(start_test + a)].copy()
        test_df = tail.iloc[a:bidx].copy()

        feat = [c for c in df.columns if c.startswith("f_")]
        X_train = train_df[feat].astype("float64").values
        y_train = train_df["y_open_close"].astype("int64").values

        model = train_calibrated_hgb(X_train, y_train)
        taken, cov, hit = eval_block(model, test_df, thr, best["bounds"])

        results.append(WindowResult(
            window=i + 1,
            train_rows=int(len(train_df)),
            test_rows=int(len(test_df)),
            taken=int(taken),
            coverage=float(cov),
            hit_rate=float(hit) if np.isfinite(hit) else float("nan"),
        ))

    out_dir = Path("runs") / f"multiwindow_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = [r.__dict__ for r in results]
    pd.DataFrame(rows).to_csv(out_dir / "multiwindow.csv", index=False)

    # agrega
    taken_sum = int(sum(r.taken for r in results))
    test_sum = int(sum(r.test_rows for r in results))
    correct_sum = 0
    for r in results:
        if np.isfinite(r.hit_rate):
            correct_sum += int(round(r.hit_rate * r.taken))

    summary = {
        "threshold": thr,
        "bounds": best["bounds"],
        "windows": len(results),
        "taken_total": taken_sum,
        "test_total": test_sum,
        "coverage_total": (taken_sum / test_sum) if test_sum else 0.0,
        "hit_rate_weighted": (correct_sum / taken_sum) if taken_sum else None,
        "path": str(out_dir).replace("\\", "/"),
    }
    (out_dir / "multiwindow_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== MULTIWINDOW (pseudo-futuro) ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
'@

# preflight
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

# ---------------------------------------------------------------------
# (C) Rodar: backfill -> dataset -> tuner -> freeze best -> multiwindow
# ---------------------------------------------------------------------
$days = $Years * 365

if (-not $SkipBackfill) {
  Write-Host "`n[1/5] Backfill ($Years anos)..." -ForegroundColor Cyan
  & $py -m natbin.backfill_candles --days $days --interval $IntervalSec --sleep_ms $SleepMs
  if ($LASTEXITCODE -ne 0) { throw "backfill_candles falhou" }
} else {
  Write-Host "`n[1/5] Backfill: skip" -ForegroundColor DarkGray
}

Write-Host "`n[2/5] Rebuild dataset..." -ForegroundColor Cyan
& $py -m natbin.make_dataset
if ($LASTEXITCODE -ne 0) { throw "make_dataset falhou" }

if (-not $SkipTune) {
  Require-Path "src\natbin\paper_tune_v2.py" "Nao achei paper_tune_v2.py. Rode o tuner (phase3_2_tune.ps1) ou me peça para gerar."
  Write-Host "`n[3/5] TUNER V2 (paper)..." -ForegroundColor Cyan
  & $py -m natbin.paper_tune_v2
  if ($LASTEXITCODE -ne 0) { throw "paper_tune_v2 falhou" }

  # congela best no config.yaml (pega o último tune_v2_*)
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
  "notes": "Frozen from latest tune_v2."
}
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("Frozen best into config.yaml:", cfg["best"])
'@
  Write-Host "`n[4/5] Freeze BEST into config.yaml..." -ForegroundColor Cyan
  & $py -c $freeze
  if ($LASTEXITCODE -ne 0) { throw "freeze best falhou" }
} else {
  Write-Host "`n[3/5] Tuner+Freeze: skip" -ForegroundColor DarkGray
}

if (-not $SkipMultiwindow) {
  Write-Host "`n[5/5] Multiwindow evaluation..." -ForegroundColor Cyan
  & $py -m natbin.paper_multiwindow_v3
  if ($LASTEXITCODE -ne 0) { throw "paper_multiwindow_v3 falhou" }
} else {
  Write-Host "`n[5/5] Multiwindow: skip" -ForegroundColor DarkGray
}

Write-Host "`nOK. accelerate_lab concluído." -ForegroundColor Green