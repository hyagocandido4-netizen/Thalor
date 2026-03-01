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

Require-Path $py "Nao encontrei .venv. Rode init.ps1."
Require-Path "data/dataset_phase2.csv" "Nao achei data/dataset_phase2.csv. Rode: python -m natbin.make_dataset"
Require-Path "runs" "Nao achei pasta runs. Rode tuner antes."

Write-Host "== Phase 3.3 Bootstrap (freeze best + paper v3 + observe) ==" -ForegroundColor Cyan

# (1) Atualiza config.yaml com o BEST do último tune_v2_*
$pyCfg = @'
import json, yaml
from pathlib import Path

runs = Path("runs")
tunes = sorted(runs.glob("tune_v2_*"), key=lambda p: p.name, reverse=True)
if not tunes:
    raise SystemExit("Nao achei runs/tune_v2_* (rode paper_tune_v2).")

tune_dir = tunes[0]
summary_path = tune_dir / "tune_summary.json"
if not summary_path.exists():
    raise SystemExit(f"Nao achei {summary_path}")

summary = json.loads(summary_path.read_text(encoding="utf-8"))
best = summary["best"]

p = Path("config.yaml")
cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
cfg.setdefault("best", {})
cfg["best"] = {
    "tune_dir": str(tune_dir).replace("\\", "/"),
    "threshold": float(best["threshold"]),
    "bounds": {
        "vol_lo": float(best["vol_lo"]), "vol_hi": float(best["vol_hi"]),
        "bb_lo": float(best["bb_lo"]),   "bb_hi": float(best["bb_hi"]),
        "atr_lo": float(best["atr_lo"]), "atr_hi": float(best["atr_hi"]),
    },
    "notes": "Frozen from latest tune_v2 summary (paper)."
}
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("config.yaml atualizado com best config:", cfg["best"])
'@
& $py -c $pyCfg
if ($LASTEXITCODE -ne 0) { throw "Falhou ao atualizar config.yaml com o BEST." }

# (2) Cria Paper V3 (usa bounds fixos do tune e threshold fixo)
Write-Utf8NoBomFile "src\natbin\paper_backtest_v3.py" @'
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
class PaperResult:
    threshold: float
    test_rows: int
    taken: int
    accuracy: float
    coverage: float


def load_best_cfg() -> dict:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")) or {}
    best = cfg.get("best")
    if not best:
        raise RuntimeError("Nao achei bloco 'best' em config.yaml. Rode phase3_3_bootstrap.ps1.")
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


def main():
    best = load_best_cfg()
    thr = float(best["threshold"])
    b = best["bounds"]

    df = pd.read_csv("data/dataset_phase2.csv").sort_values("ts").reset_index(drop=True)
    feat = [c for c in df.columns if c.startswith("f_")]

    split = int(0.8 * len(df))
    df_train = df.iloc[:split].copy().reset_index(drop=True)
    df_test  = df.iloc[split:].copy().reset_index(drop=True)

    X_train = df_train[feat].astype("float64").values
    y_train = df_train["y_open_close"].astype("int64").values
    X_test  = df_test[feat].astype("float64").values
    y_test  = df_test["y_open_close"].astype("int64").values

    model = train_calibrated_hgb(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]

    mask = make_mask(df_test, b)
    take_call = (proba >= thr) & mask
    take_put  = (proba <= (1.0 - thr)) & mask
    taken = take_call | take_put

    pred = np.where(take_call, 1, 0)
    taken_n = int(taken.sum())
    acc = float((pred[taken] == y_test[taken]).sum()) / float(taken_n) if taken_n else float("nan")
    cov = float(taken_n) / float(len(y_test)) if len(y_test) else 0.0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("runs") / f"paper_v3_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    log = df_test[["ts","open","high","low","close","session_id","y_open_close"]].copy()
    log["proba_up"] = proba
    log["regime_ok"] = mask.astype(int)
    log["taken"] = taken.astype(int)
    log["pred_dir"] = pred.astype(int)
    log["correct"] = ((pred == y_test) & taken).astype(int)
    log.to_csv(out_dir / "paper_v3_test_log.csv", index=False)

    summary = {
        "best_from": best.get("tune_dir"),
        "threshold": thr,
        "bounds": b,
        "test_rows": int(len(y_test)),
        "taken": taken_n,
        "coverage": cov,
        "hit_rate": acc,
        "notes": "Paper V3 = frozen tune bounds + HGB calibrated(sigmoid)."
    }
    (out_dir / "paper_v3_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== PAPER V3 (frozen tune config) ===")
    print(f"Threshold: {thr}")
    print(f"Trades tomados: {taken_n} / {len(y_test)} (coverage={cov:.4%})")
    print(f"Hit rate (somente tomados): {acc:.4f}")
    print(f"Logs: {out_dir}")

if __name__ == "__main__":
    main()
'@

# (3) Cria Observe: sinal do ÚLTIMO candle do dataset (HOLD/CALL/PUT), logando em runs/live_signals.csv
Write-Utf8NoBomFile "src\natbin\observe_signal_latest.py" @'
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split


def load_best_cfg() -> dict:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")) or {}
    best = cfg.get("best")
    if not best:
        raise RuntimeError("Nao achei bloco 'best' em config.yaml. Rode phase3_3_bootstrap.ps1.")
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


def regime_ok(row: pd.Series, b: dict) -> bool:
    return (
        (row["f_vol48"] >= b["vol_lo"]) and (row["f_vol48"] <= b["vol_hi"]) and
        (row["f_bb_width20"] >= b["bb_lo"]) and (row["f_bb_width20"] <= b["bb_hi"]) and
        (row["f_atr14"] >= b["atr_lo"]) and (row["f_atr14"] <= b["atr_hi"])
    )


def main():
    best = load_best_cfg()
    thr = float(best["threshold"])
    b = best["bounds"]

    df = pd.read_csv("data/dataset_phase2.csv").sort_values("ts").reset_index(drop=True)
    feat = [c for c in df.columns if c.startswith("f_")]

    # treina em tudo menos os últimos 200 registros (evita "peeking" no final)
    cut = max(1000, len(df) - 200)
    train = df.iloc[:cut].copy()
    X_train = train[feat].astype("float64").values
    y_train = train["y_open_close"].astype("int64").values

    model = train_calibrated_hgb(X_train, y_train)

    last = df.iloc[-1].copy()
    X_last = last[feat].astype("float64").values.reshape(1, -1)
    p_up = float(model.predict_proba(X_last)[0, 1])

    ok = regime_ok(last, b)
    action = "HOLD"
    if ok and p_up >= thr:
        action = "CALL"
    elif ok and p_up <= (1.0 - thr):
        action = "PUT"

    out_path = Path("runs") / "live_signals.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "dt_local": datetime.now().isoformat(timespec="seconds"),
        "ts": int(last["ts"]),
        "proba_up": p_up,
        "regime_ok": int(ok),
        "threshold": thr,
        "action": action,
        "close": float(last["close"]),
    }

    write_header = not out_path.exists()
    with out_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            w.writeheader()
        w.writerow(row)

    print("\n=== OBSERVE (latest) ===")
    print(row)

if __name__ == "__main__":
    main()
'@

# preflight
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou (algum .py invalido)" }

if (-not $NoRun) {
  Write-Host "`nRodando PAPER V3..." -ForegroundColor Cyan
  & $py -m natbin.paper_backtest_v3
  if ($LASTEXITCODE -ne 0) { throw "paper_backtest_v3 falhou" }

  Write-Host "`nRodando OBSERVE (latest signal)..." -ForegroundColor Cyan
  & $py -m natbin.observe_signal_latest
  if ($LASTEXITCODE -ne 0) { throw "observe_signal_latest falhou" }

  Write-Host "`nPhase 3.3 concluida." -ForegroundColor Green
} else {
  Write-Host "`nGerado. Para rodar:" -ForegroundColor Yellow
  Write-Host "  .\.venv\Scripts\python.exe -m natbin.paper_backtest_v3"
  Write-Host "  .\.venv\Scripts\python.exe -m natbin.observe_signal_latest"
}