param(
  [int]$K = 2
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
Require-Path "config.yaml" "Nao achei config.yaml."
Require-Path "data/dataset_phase2.csv" "Nao achei data/dataset_phase2.csv. Rode: python -m natbin.make_dataset"

Write-Host "== Phase 3.5 (Top-K por DIA) ==" -ForegroundColor Cyan
Write-Host "K = $K" -ForegroundColor Cyan

Write-Utf8NoBomFile "src\natbin\paper_topk_perday_multiwindow.py" @'
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split


@dataclass
class WindowRow:
    window: int
    train_rows: int
    test_rows: int

    base_taken: int
    base_cov: float
    base_hit: float

    topk_taken: int
    topk_cov: float
    topk_hit: float


def load_best_cfg() -> dict:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")) or {}
    best = cfg.get("best")
    if not best:
        raise RuntimeError("Nao achei bloco 'best' no config.yaml. Rode tuner+freeze (phase3_3_bootstrap/accelerate_lab).")
    tz = (cfg.get("data", {}) or {}).get("timezone", "America/Sao_Paulo")
    best["_timezone"] = tz
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


def eval_baseline(y: np.ndarray, proba: np.ndarray, mask: np.ndarray, thr: float) -> tuple[int, float, int]:
    take_call = (proba >= thr) & mask
    take_put  = (proba <= (1.0 - thr)) & mask
    taken = take_call | take_put
    taken_n = int(taken.sum())
    if taken_n == 0:
        return 0, float("nan"), 0
    pred = np.where(take_call, 1, 0)
    correct_n = int((pred[taken] == y[taken]).sum())
    hit = float(correct_n) / float(taken_n)
    return taken_n, hit, correct_n


def eval_topk_per_day(
    df_block: pd.DataFrame,
    y: np.ndarray,
    proba: np.ndarray,
    mask: np.ndarray,
    thr: float,
    k: int,
    tzname: str,
) -> tuple[int, float, int]:
    """
    Top-K por DIA:
    - candidatos = os que PASSARIAM no baseline (CALL/PUT) e mask==True
    - escolhe até K por dia (mais confiantes)
    """
    take_call = (proba >= thr) & mask
    take_put  = (proba <= (1.0 - thr)) & mask
    taken = take_call | take_put

    if not np.any(taken):
        return 0, float("nan"), 0

    conf = np.maximum(proba, 1.0 - proba)

    tz = ZoneInfo(tzname)
    dt = pd.to_datetime(df_block["ts"], unit="s", utc=True).dt.tz_convert(tz)
    day_id = dt.dt.strftime("%Y-%m-%d")

    tmp = pd.DataFrame({
        "day": day_id.to_numpy(),
        "proba": proba,
        "conf": conf,
        "y": y,
        "take_call": take_call.astype(int),
        "take_put": take_put.astype(int),
        "taken": taken.astype(int),
    })
    tmp = tmp[tmp["taken"] == 1].copy()
    if tmp.empty:
        return 0, float("nan"), 0

    tmp["pred"] = np.where(tmp["take_call"].to_numpy() == 1, 1, 0)

    tmp = tmp.sort_values(["day", "conf"], ascending=[True, False])
    tmp["rank"] = tmp.groupby("day").cumcount() + 1
    top = tmp[tmp["rank"] <= k].copy()

    taken_n = int(len(top))
    if taken_n == 0:
        return 0, float("nan"), 0

    correct_n = int((top["pred"].to_numpy() == top["y"].to_numpy()).sum())
    hit = float(correct_n) / float(taken_n)
    return taken_n, hit, correct_n


def main():
    import os
    k = int(os.getenv("TOPK_K", "2"))

    best = load_best_cfg()
    thr = float(best["threshold"])
    bounds = best["bounds"]
    tzname = str(best.get("_timezone", "America/Sao_Paulo"))

    df = pd.read_csv("data/dataset_phase2.csv").sort_values("ts").reset_index(drop=True)
    feat = [c for c in df.columns if c.startswith("f_")]

    # Multiwindow pseudo-futuro (expanding train, rolling test)
    N = 6
    min_train = max(20000, int(0.25 * len(df)))
    start_test = max(min_train, int(0.40 * len(df)))

    tail = df.iloc[start_test:].copy().reset_index(drop=True)
    block = max(2000, int(len(tail) / N))

    rows: list[WindowRow] = []

    base_taken_total = 0
    base_correct_total = 0
    topk_taken_total = 0
    topk_correct_total = 0
    test_total = 0

    for i in range(N):
        a = i * block
        bidx = min(len(tail), (i + 1) * block)
        if bidx - a < 1000:
            break

        train_df = df.iloc[:(start_test + a)].copy()
        test_df = tail.iloc[a:bidx].copy().reset_index(drop=True)

        X_train = train_df[feat].astype("float64").values
        y_train = train_df["y_open_close"].astype("int64").values

        X_test = test_df[feat].astype("float64").values
        y_test = test_df["y_open_close"].astype("int64").values

        model = train_calibrated_hgb(X_train, y_train)
        proba = model.predict_proba(X_test)[:, 1]
        mask = make_mask(test_df, bounds)

        base_taken, base_hit, base_correct = eval_baseline(y_test, proba, mask, thr)
        topk_taken, topk_hit, topk_correct = eval_topk_per_day(test_df, y_test, proba, mask, thr, k=k, tzname=tzname)

        test_n = int(len(y_test))
        base_cov = float(base_taken) / float(test_n) if test_n else 0.0
        topk_cov = float(topk_taken) / float(test_n) if test_n else 0.0

        rows.append(WindowRow(
            window=i + 1,
            train_rows=int(len(train_df)),
            test_rows=test_n,
            base_taken=int(base_taken),
            base_cov=base_cov,
            base_hit=float(base_hit) if np.isfinite(base_hit) else float("nan"),
            topk_taken=int(topk_taken),
            topk_cov=topk_cov,
            topk_hit=float(topk_hit) if np.isfinite(topk_hit) else float("nan"),
        ))

        base_taken_total += base_taken
        base_correct_total += base_correct
        topk_taken_total += topk_taken
        topk_correct_total += topk_correct
        test_total += test_n

        print(f"[win {i+1}] base_taken={base_taken} topk_taken={topk_taken}")

    out_dir = Path("runs") / f"topk_perday_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    df_out = pd.DataFrame([r.__dict__ for r in rows])
    df_out.to_csv(out_dir / "multiwindow_topk_perday.csv", index=False)

    base_hit_weighted = (base_correct_total / base_taken_total) if base_taken_total else None
    topk_hit_weighted = (topk_correct_total / topk_taken_total) if topk_taken_total else None

    summary = {
        "k": k,
        "timezone": tzname,
        "threshold": thr,
        "bounds": bounds,
        "windows": int(len(df_out)),
        "test_total": int(test_total),

        "base_taken_total": int(base_taken_total),
        "base_cov_total": float(base_taken_total / test_total) if test_total else 0.0,
        "base_hit_weighted": base_hit_weighted,

        "topk_taken_total": int(topk_taken_total),
        "topk_cov_total": float(topk_taken_total / test_total) if test_total else 0.0,
        "topk_hit_weighted": topk_hit_weighted,

        "path": str(out_dir).replace("\\", "/"),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== TOPK PER-DAY MULTIWINDOW (pseudo-futuro) ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
'@

# preflight
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

# roda
$env:TOPK_K = "$K"
& $py -m natbin.paper_topk_perday_multiwindow
if ($LASTEXITCODE -ne 0) { throw "paper_topk_perday_multiwindow falhou" }

Write-Host "`nOK. Veja runs/topk_perday_*/summary.json" -ForegroundColor Green