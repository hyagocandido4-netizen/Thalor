param(
  [switch]$NoRun
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

Require-Path $py "Nao encontrei $py. Rode primeiro o init.ps1."
Require-Path "src\natbin\make_dataset.py" "Nao encontrei make_dataset.py (rode phase2_bootstrap.ps1)."
Require-Path "src\natbin\dataset2.py" "Nao encontrei dataset2.py (rode phase2_bootstrap.ps1)."
Require-Path "src\natbin\train_walkforward.py" "Nao encontrei train_walkforward.py (fase 2)."
Require-Path "runs" "Nao encontrei pasta runs. Rode a fase 2 pelo menos 1 vez."

Write-Host "== Phase 3: Paper Backtest Bootstrap ==" -ForegroundColor Cyan

# (1) cria paper_backtest.py
Write-Utf8NoBomFile "src\natbin\paper_backtest.py" @'
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass
class PaperResult:
    threshold: float
    test_rows: int
    taken: int
    accuracy: float
    coverage: float


def load_latest_best_threshold(runs_dir: str = "runs") -> tuple[str, float]:
    runs = sorted(Path(runs_dir).glob("run_*"), key=lambda p: p.name, reverse=True)
    if not runs:
        raise RuntimeError("Nenhum run_* encontrado em runs/. Rode a fase 2 primeiro.")
    latest = runs[0]
    summary_path = latest / "summary.json"
    if not summary_path.exists():
        raise RuntimeError(f"Nao achei summary.json em {latest}.")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    thr = float(summary["best_threshold"])
    return str(latest), thr


def train_calibrated(X_train: np.ndarray, y_train: np.ndarray) -> CalibratedClassifierCV:
    # 80/20 interno para calibrar (evita probas mentirosas)
    cut = int(0.8 * len(X_train))
    X_sub, y_sub = X_train[:cut], y_train[:cut]
    X_cal, y_cal = X_train[cut:], y_train[cut:]

    base = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=3000, solver="lbfgs")),
    ])
    base.fit(X_sub, y_sub)

    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(X_cal, y_cal)
    return cal


def simulate(df: pd.DataFrame, threshold: float) -> PaperResult:
    feature_cols = [c for c in df.columns if c.startswith("f_")]
    X = df[feature_cols].astype("float64").values
    y = df["y_open_close"].astype("int64").values

    # holdout: últimos 20% como teste (paper)
    split = int(0.8 * len(df))
    X_train, y_train = X[:split], y[:split]
    X_test, y_test = X[split:], y[split:]

    model = train_calibrated(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]

    take_call = proba >= threshold
    take_put = proba <= (1.0 - threshold)
    taken = take_call | take_put

    pred = np.where(take_call, 1, 0)
    correct = (pred[taken] == y_test[taken]).sum()

    taken_n = int(taken.sum())
    test_n = int(len(y_test))

    acc = float(correct) / float(taken_n) if taken_n else float("nan")
    cov = float(taken_n) / float(test_n) if test_n else 0.0

    return PaperResult(threshold=threshold, test_rows=test_n, taken=taken_n, accuracy=acc, coverage=cov), proba, taken, pred, y_test, df.iloc[split:].copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--runs_dir", type=str, default="runs")
    ap.add_argument("--out_dir", type=str, default="runs")
    args = ap.parse_args()

    latest_run_dir, thr_auto = load_latest_best_threshold(args.runs_dir)
    threshold = float(args.threshold) if args.threshold is not None else float(thr_auto)

    # dataset sempre do arquivo padrão da fase 2
    dataset_path = Path("data/dataset_phase2.csv")
    if not dataset_path.exists():
        raise RuntimeError("Nao achei data/dataset_phase2.csv. Rode: python -m natbin.make_dataset")

    df = pd.read_csv(dataset_path).sort_values("ts").reset_index(drop=True)

    res, proba, taken, pred, y_test, df_test = simulate(df, threshold)

    # salva logs em uma pasta paper_YYYYMMDD_HHMMSS
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) / f"paper_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # log de cada candle do período de teste
    out = df_test[["ts", "open", "high", "low", "close", "session_id", "y_open_close"]].copy()
    out["proba_up"] = proba
    out["taken"] = taken.astype(int)
    out["pred_dir"] = pred.astype(int)            # 1=CALL, 0=PUT (quando taken=1)
    out["correct"] = ((pred == y_test) & taken).astype(int)

    out.to_csv(out_dir / "paper_test_log.csv", index=False)

    summary = {
        "latest_phase2_run": latest_run_dir,
        "threshold_used": res.threshold,
        "test_rows": res.test_rows,
        "taken": res.taken,
        "hit_rate": res.accuracy,
        "coverage": res.coverage,
    }
    (out_dir / "paper_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== PAPER (holdout 20%, sequencial) ===")
    print(f"Threshold: {summary['threshold_used']}")
    print(f"Trades tomados: {summary['taken']} / {summary['test_rows']} (coverage={summary['coverage']:.4%})")
    print(f"Hit rate (somente tomados): {summary['hit_rate']:.4f}")
    print(f"Logs: {out_dir}")

if __name__ == "__main__":
    main()
'@

Write-Host "paper_backtest.py criado." -ForegroundColor Green

# (2) preflight rápido
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou (tem algum .py inválido)" }

if (-not $NoRun) {
  # garante dataset atualizado
  Write-Host "`nRodando make_dataset..." -ForegroundColor Cyan
  & $py -m natbin.make_dataset
  if ($LASTEXITCODE -ne 0) { throw "make_dataset falhou" }

  Write-Host "`nRodando PAPER backtest..." -ForegroundColor Cyan
  & $py -m natbin.paper_backtest
  if ($LASTEXITCODE -ne 0) { throw "paper_backtest falhou" }

  Write-Host "`nPhase 3 concluida (paper)." -ForegroundColor Green
} else {
  Write-Host "`nGerado. Para rodar:" -ForegroundColor Yellow
  Write-Host "  .\.venv\Scripts\python.exe -m natbin.make_dataset"
  Write-Host "  .\.venv\Scripts\python.exe -m natbin.paper_backtest"
}