param(
  [switch]$NoRun  # se você passar -NoRun, ele só cria arquivos e ajusta config, sem rodar o treino
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Content
  )
  $dir = Split-Path -Parent $Path
  if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Run {
  param(
    [Parameter(Mandatory=$true)][string]$Exe,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$CmdArgs
  )
  if ($null -eq $CmdArgs) { $CmdArgs = @() }

  $pretty = (@($Exe) + $CmdArgs | ForEach-Object {
    if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
  }) -join ' '

  Write-Host ">> $pretty"
  & $Exe @CmdArgs
  if ($LASTEXITCODE -ne 0) { throw "Falhou: $pretty" }
}

$ProjectRoot = (Get-Location).Path
$VenvPython  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
  throw "Nao encontrei o python da venv em: $VenvPython`nRode primeiro o init.ps1 que cria a .venv."
}

if (-not (Test-Path "config.yaml")) {
  throw "Nao encontrei config.yaml na pasta atual. Entre na raiz do projeto (onde esta config.yaml) e rode novamente."
}

# garante pasta e __init__ (para imports funcionarem sempre)
New-Item -ItemType Directory -Force -Path "src\natbin" | Out-Null
if (-not (Test-Path "src\natbin\__init__.py")) {
  Write-Utf8NoBomFile "src\natbin\__init__.py" "__all__ = []`n"
}

# (A) Garantir deps (idempotente)
if (Test-Path ".\requirements.txt") {
  Run $VenvPython @("-m","pip","install","-r",".\requirements.txt")
}

# -------------------------
# (B) Criar arquivos da Fase 2
# -------------------------
Write-Utf8NoBomFile "src\natbin\config2.py" @"
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass(frozen=True)
class DataConfig:
    asset: str
    interval_sec: int
    db_path: str
    timezone: str
    max_batch: int = 1000


@dataclass(frozen=True)
class Phase2Config:
    dataset_path: str = "data/dataset_phase2.csv"
    runs_dir: str = "runs"
    n_splits: int = 6
    threshold_min: float = 0.60
    threshold_max: float = 0.80
    threshold_step: float = 0.01


@dataclass(frozen=True)
class Config:
    data: DataConfig
    phase2: Phase2Config


def load_config(path: str = "config.yaml") -> Config:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    d = cfg["data"]

    data = DataConfig(
        asset=str(d["asset"]).strip(),
        interval_sec=int(d["interval_sec"]),
        db_path=str(d["db_path"]).strip(),
        timezone=str(d.get("timezone", "America/Sao_Paulo")).strip(),
        max_batch=int(d.get("max_batch", 1000)),
    )

    p = cfg.get("phase2", {}) or {}
    phase2 = Phase2Config(
        dataset_path=str(p.get("dataset_path", "data/dataset_phase2.csv")),
        runs_dir=str(p.get("runs_dir", "runs")),
        n_splits=int(p.get("n_splits", 6)),
        threshold_min=float(p.get("threshold_min", 0.60)),
        threshold_max=float(p.get("threshold_max", 0.80)),
        threshold_step=float(p.get("threshold_step", 0.01)),
    )

    return Config(data=data, phase2=phase2)
"@

Write-Utf8NoBomFile "src\natbin\dataset2.py" @"
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DatasetBuildResult:
    path: str
    n_rows: int
    feature_cols: list[str]


def _load_candles(db_path: str, asset: str, interval_sec: int) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            '''
            SELECT ts, open, high, low, close, volume
            FROM candles
            WHERE asset = ? AND interval_sec = ?
            ORDER BY ts ASC
            ''',
            con,
            params=(asset, interval_sec),
        )
    finally:
        con.close()

    if df.empty:
        raise RuntimeError("Nenhum candle encontrado no SQLite para esse asset/interval.")

    df["ts"] = df["ts"].astype("int64")
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype("float64")
    df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce")

    return df


def _add_sessions(df: pd.DataFrame, step: int) -> pd.DataFrame:
    gap = df["ts"].diff().fillna(step).astype("int64")
    new_sess = (gap != step).astype("int64")
    df["session_id"] = new_sess.cumsum().astype("int64")
    return df


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / (avg_loss.replace(0, np.nan))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _build_features_one_session(g: pd.DataFrame) -> pd.DataFrame:
    g = g.copy()

    g["f_ret1"] = np.log(g["close"] / g["close"].shift(1))
    g["f_ret3"] = np.log(g["close"] / g["close"].shift(3))
    g["f_ret6"] = np.log(g["close"] / g["close"].shift(6))
    g["f_ret12"] = np.log(g["close"] / g["close"].shift(12))

    g["f_range"] = (g["high"] - g["low"]) / g["close"]
    g["f_body"] = (g["close"] - g["open"]) / g["close"]

    oc_max = np.maximum(g["open"], g["close"])
    oc_min = np.minimum(g["open"], g["close"])
    g["f_wick_up"] = (g["high"] - oc_max) / g["close"]
    g["f_wick_dn"] = (oc_min - g["low"]) / g["close"]

    g["f_vol12"] = g["f_ret1"].rolling(12, min_periods=12).std()
    g["f_vol48"] = g["f_ret1"].rolling(48, min_periods=48).std()
    g["f_mom12"] = g["f_ret1"].rolling(12, min_periods=12).mean()

    prev_close = g["close"].shift(1)
    tr = np.maximum(
        g["high"] - g["low"],
        np.maximum((g["high"] - prev_close).abs(), (g["low"] - prev_close).abs()),
    )
    g["f_atr14"] = tr.rolling(14, min_periods=14).mean() / g["close"]

    g["f_rsi14"] = _rsi(g["close"], period=14)

    if g["volume"].notna().any():
        vmean = g["volume"].rolling(20, min_periods=20).mean()
        g["f_volratio20"] = g["volume"] / vmean

    return g


def build_dataset(db_path: str, asset: str, interval_sec: int, out_csv: str) -> DatasetBuildResult:
    step = int(interval_sec)

    df = _load_candles(db_path, asset, step)
    df = _add_sessions(df, step)

    # Label delay-aware: entrada no OPEN do próximo candle, expiração no CLOSE do próximo candle
    entry_open = df["open"].shift(-1)
    expiry_close = df["close"].shift(-1)
    same_sess_next = (df["session_id"].shift(-1) == df["session_id"])

    y = (expiry_close > entry_open).astype("float64")
    y[~same_sess_next] = np.nan
    df["y_open_close"] = y

    df = df.groupby("session_id", group_keys=False).apply(_build_features_one_session)

    feature_cols = [c for c in df.columns if c.startswith("f_")]
    keep_cols = ["ts","open","high","low","close","volume","session_id","y_open_close"] + feature_cols

    out = df[keep_cols].copy()
    out = out.dropna(subset=["y_open_close"] + feature_cols).reset_index(drop=True)

    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)

    return DatasetBuildResult(path=out_csv, n_rows=int(out.shape[0]), feature_cols=feature_cols)
"@

Write-Utf8NoBomFile "src\natbin\make_dataset.py" @"
from __future__ import annotations

from natbin.config2 import load_config
from natbin.dataset2 import build_dataset


def main():
    cfg = load_config()
    res = build_dataset(
        db_path=cfg.data.db_path,
        asset=cfg.data.asset,
        interval_sec=cfg.data.interval_sec,
        out_csv=cfg.phase2.dataset_path,
    )
    print("Dataset pronto:")
    print(f"  path: {res.path}")
    print(f"  rows: {res.n_rows}")
    print(f"  features: {len(res.feature_cols)}")


if __name__ == "__main__":
    main()
"@

Write-Utf8NoBomFile "src\natbin\train_walkforward.py" @"
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from natbin.config2 import load_config


@dataclass
class AggStats:
    correct: int = 0
    taken: int = 0
    total: int = 0

    def accuracy(self) -> float:
        return float(self.correct) / float(self.taken) if self.taken else float("nan")

    def coverage(self) -> float:
        return float(self.taken) / float(self.total) if self.total else 0.0


def evaluate_thresholds(y_true: np.ndarray, proba: np.ndarray, thresholds: np.ndarray):
    results = {float(t): AggStats() for t in thresholds}

    for t in thresholds:
        take_call = proba >= t
        take_put = proba <= (1.0 - t)
        taken = take_call | take_put

        pred_dir = np.where(take_call, 1, 0)  # 1=CALL, 0=PUT
        yt = y_true[taken]
        pd_ = pred_dir[taken]

        stats = results[float(t)]
        stats.total += int(y_true.shape[0])
        stats.taken += int(taken.sum())
        stats.correct += int((pd_ == yt).sum())

    return results


def main():
    cfg = load_config()
    dataset_path = cfg.phase2.dataset_path
    runs_dir = Path(cfg.phase2.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(dataset_path).sort_values("ts").reset_index(drop=True)

    feature_cols = [c for c in df.columns if c.startswith("f_")]
    X = df[feature_cols].astype("float64").values
    y = df["y_open_close"].astype("int64").values

    thresholds = np.round(
        np.arange(cfg.phase2.threshold_min, cfg.phase2.threshold_max + 1e-9, cfg.phase2.threshold_step),
        4,
    )

    tscv = TimeSeriesSplit(n_splits=cfg.phase2.n_splits)
    agg = {float(t): AggStats() for t in thresholds}

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X), start=1):
        X_train, y_train = X[train_idx], y[train_idx]
        X_test, y_test = X[test_idx], y[test_idx]

        cut = int(0.8 * len(X_train))
        X_sub, y_sub = X_train[:cut], y_train[:cut]
        X_cal, y_cal = X_train[cut:], y_train[cut:]

        base = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, solver="lbfgs")),
        ])
        base.fit(X_sub, y_sub)

        cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
        cal.fit(X_cal, y_cal)

        proba = cal.predict_proba(X_test)[:, 1]
        fold_res = evaluate_thresholds(y_test, proba, thresholds)

        for t in thresholds:
            a = agg[float(t)]
            r = fold_res[float(t)]
            a.correct += r.correct
            a.taken += r.taken
            a.total += r.total

        print(f"[fold {fold}] ok | test={len(test_idx)}")

    rows = []
    for t in thresholds:
        st = agg[float(t)]
        rows.append({"threshold": float(t), "coverage": st.coverage(), "taken": st.taken, "accuracy": st.accuracy()})

    thr_df = pd.DataFrame(rows).sort_values(["accuracy", "coverage"], ascending=[False, True])

    candidates = thr_df[(thr_df["coverage"] >= 0.002) & (thr_df["coverage"] <= 0.03)].copy()
    if candidates.empty:
        best = thr_df.iloc[0]
        rule = "fallback_best_overall"
    else:
        best = candidates.iloc[0]
        rule = "best_accuracy_with_cov_0.2%-3%"

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = runs_dir / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    thr_df.to_csv(run_dir / "thresholds.csv", index=False)

    summary = {
        "dataset_path": dataset_path,
        "n_rows": int(df.shape[0]),
        "n_features": int(len(feature_cols)),
        "n_splits": int(cfg.phase2.n_splits),
        "threshold_rule": rule,
        "best_threshold": float(best["threshold"]),
        "best_accuracy": float(best["accuracy"]) if np.isfinite(best["accuracy"]) else None,
        "best_coverage": float(best["coverage"]),
        "best_taken": int(best["taken"]),
        "top5": thr_df.head(5).to_dict(orient="records"),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== RESULTADO (walk-forward, calibrado) ===")
    print(f"Best threshold: {summary['best_threshold']} ({summary['threshold_rule']})")
    print(f"Hit rate (nos trades tomados): {summary['best_accuracy']:.4f}")
    print(f"Coverage (quantos candles viram trade): {summary['best_coverage']:.4%}")
    print(f"Trades tomados (somando folds): {summary['best_taken']}")
    print(f"\nRun salvo em: {run_dir}")


if __name__ == "__main__":
    main()
"@

# -------------------------
# (C) Ajustar config.yaml (phase2) via Python (idempotente)
# -------------------------
$py = @'
import yaml
from pathlib import Path

p = Path("config.yaml")
cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
cfg.setdefault("phase2", {})
cfg["phase2"].update({
    "dataset_path": "data/dataset_phase2.csv",
    "runs_dir": "runs",
    "n_splits": 6,
    "threshold_min": 0.60,
    "threshold_max": 0.80,
    "threshold_step": 0.01,
})
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("config.yaml atualizado (phase2).")
'@

Run $VenvPython @("-c", $py)

# -------------------------
# (D) Preflight
# -------------------------
Run $VenvPython @("-c","import yaml, numpy, pandas, sklearn; print('deps OK')")
Run $VenvPython @("-m","compileall",".\src\natbin") | Out-Null
Write-Host "Phase2 files OK." -ForegroundColor Green

# -------------------------
# (E) Run phase2 (default)
# -------------------------
if (-not $NoRun) {
  Run $VenvPython @("-m","natbin.make_dataset")
  Run $VenvPython @("-m","natbin.train_walkforward")
  Write-Host "`nFase 2 concluida." -ForegroundColor Green
} else {
  Write-Host "Fase 2 gerada. Para rodar:" -ForegroundColor Yellow
  Write-Host "  .\.venv\Scripts\python.exe -m natbin.make_dataset"
  Write-Host "  .\.venv\Scripts\python.exe -m natbin.train_walkforward"
}