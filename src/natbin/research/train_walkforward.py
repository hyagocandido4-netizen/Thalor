from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from .dsio import read_dataset_csv

from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..config.legacy import load_config
from ..ml_compat import build_binary_logreg


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

        st = results[float(t)]
        st.total += int(y_true.shape[0])
        st.taken += int(taken.sum())
        st.correct += int((pd_ == yt).sum())

    return results


def main():
    cfg = load_config()
    dataset_path = cfg.phase2.dataset_path
    runs_dir = Path(cfg.phase2.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)

    df = read_dataset_csv(dataset_path, label_col="y_open_close")
    df = df[df["y_open_close"].notna()].copy()

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

        # 80/20 interno para calibrar
        cut = int(0.8 * len(X_train))
        X_sub, y_sub = X_train[:cut], y_train[:cut]
        X_cal, y_cal = X_train[cut:], y_train[cut:]

        base = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", build_binary_logreg(max_iter=3000)),
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
        rows.append({
            "threshold": float(t),
            "coverage": st.coverage(),
            "taken": st.taken,
            "accuracy": st.accuracy(),
        })

    thr_df = pd.DataFrame(rows).sort_values(["accuracy", "coverage"], ascending=[False, True])

    # -------- Phase 2.1 selection --------
    COV_MIN = 0.001   # 0.10%
    COV_MAX = 0.02    # 2.00%
    MIN_TRADES = 300

    candidates = thr_df[
        (thr_df["coverage"] >= COV_MIN) &
        (thr_df["coverage"] <= COV_MAX) &
        (thr_df["taken"] >= MIN_TRADES)
    ].copy()

    if candidates.empty:
        candidates2 = thr_df[thr_df["taken"] >= 50].copy()
        if candidates2.empty:
            best = thr_df.iloc[0]
            rule = "fallback_best_overall"
        else:
            best = candidates2.iloc[0]
            rule = "fallback_best_accuracy_min50trades"
    else:
        best = candidates.iloc[0]
        rule = "best_accuracy_cov0.1-2%_min300trades"
    # -----------------------------------

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
