from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from .dsio import read_dataset_csv

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


def train_calibrated_hgb(X_train: np.ndarray, y_train: np.ndarray) -> CalibratedClassifierCV:
    # split interno para calibração
    X_sub, X_cal, y_sub, y_cal = train_test_split(
        X_train, y_train, test_size=0.2, shuffle=False
    )

    base = HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.05,
        max_iter=400,
        l2_regularization=1.0,
        random_state=42,
    )
    base.fit(X_sub, y_sub)

    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(X_cal, y_cal)
    return cal


def regime_filter(df: pd.DataFrame) -> np.ndarray:
    """
    Filtro simples e efetivo para 5m:
    - remove extremos de volatilidade (muito baixo = ruído; muito alto = spike)
    Usa features já existentes: f_vol48, f_bb_width20, f_atr14
    """
    vol = df["f_vol48"].to_numpy()
    bb = df["f_bb_width20"].to_numpy()
    atr = df["f_atr14"].to_numpy()

    # quantis (calculados no próprio período de teste) — simples e robusto
    v_lo, v_hi = np.nanquantile(vol, [0.20, 0.90])
    b_lo, b_hi = np.nanquantile(bb,  [0.20, 0.95])
    a_lo, a_hi = np.nanquantile(atr, [0.20, 0.95])

    ok = (
        (vol >= v_lo) & (vol <= v_hi) &
        (bb  >= b_lo) & (bb  <= b_hi) &
        (atr >= a_lo) & (atr <= a_hi)
    )
    ok = np.where(np.isfinite(vol) & np.isfinite(bb) & np.isfinite(atr), ok, False)
    return ok


def simulate(df: pd.DataFrame, threshold: float) -> PaperResult:
    feature_cols = [c for c in df.columns if c.startswith("f_")]
    X = df[feature_cols].astype("float64").values
    y = df["y_open_close"].astype("int64").values

    # holdout: últimos 20%
    split = int(0.8 * len(df))
    X_train, y_train = X[:split], y[:split]
    X_test, y_test = X[split:], y[split:]
    df_test = df.iloc[split:].copy().reset_index(drop=True)

    model = train_calibrated_hgb(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]

    # filtro de regime (antes da decisão)
    ok_regime = regime_filter(df_test)

    take_call = (proba >= threshold) & ok_regime
    take_put  = (proba <= (1.0 - threshold)) & ok_regime
    taken = take_call | take_put

    pred = np.where(take_call, 1, 0)
    correct = (pred[taken] == y_test[taken]).sum()

    taken_n = int(taken.sum())
    test_n = int(len(y_test))

    acc = float(correct) / float(taken_n) if taken_n else float("nan")
    cov = float(taken_n) / float(test_n) if test_n else 0.0

    return PaperResult(threshold=threshold, test_rows=test_n, taken=taken_n, accuracy=acc, coverage=cov), proba, taken, pred, y_test, df_test, ok_regime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--runs_dir", type=str, default="runs")
    ap.add_argument("--out_dir", type=str, default="runs")
    args = ap.parse_args()

    latest_run_dir, thr_auto = load_latest_best_threshold(args.runs_dir)
    threshold = float(args.threshold) if args.threshold is not None else float(thr_auto)

    dataset_path = Path("data/dataset_phase2.csv")
    if not dataset_path.exists():
        raise RuntimeError("Nao achei data/dataset_phase2.csv. Rode: python -m natbin.make_dataset")

    df = read_dataset_csv(dataset_path, label_col="y_open_close")
    df = df[df["y_open_close"].notna()].copy()

    res, proba, taken, pred, y_test, df_test, ok_regime = simulate(df, threshold)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) / f"paper_v2_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    out = df_test[["ts", "open", "high", "low", "close", "session_id", "y_open_close"]].copy()
    out["proba_up"] = proba
    out["regime_ok"] = ok_regime.astype(int)
    out["taken"] = taken.astype(int)
    out["pred_dir"] = pred.astype(int)
    out["correct"] = ((pred == y_test) & taken).astype(int)

    out.to_csv(out_dir / "paper_v2_test_log.csv", index=False)

    summary = {
        "latest_phase2_run": latest_run_dir,
        "model": "HistGradientBoosting + sigmoid calibration",
        "regime_filter": "quantile bands on f_vol48/f_bb_width20/f_atr14",
        "threshold_used": res.threshold,
        "test_rows": res.test_rows,
        "taken": res.taken,
        "hit_rate": res.accuracy,
        "coverage": res.coverage,
    }
    (out_dir / "paper_v2_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== PAPER V2 (HGB + regime filter) ===")
    print(f"Threshold: {summary['threshold_used']}")
    print(f"Trades tomados: {summary['taken']} / {summary['test_rows']} (coverage={summary['coverage']:.4%})")
    print(f"Hit rate (somente tomados): {summary['hit_rate']:.4f}")
    print(f"Logs: {out_dir}")

if __name__ == "__main__":
    main()
