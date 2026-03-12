from __future__ import annotations

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
class Row:
    threshold: float
    vol_lo: float
    vol_hi: float
    bb_lo: float
    bb_hi: float
    atr_lo: float
    atr_hi: float
    taken: int
    coverage: float
    accuracy: float


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


def make_regime_mask(df: pd.DataFrame, bounds: dict) -> np.ndarray:
    vol = df["f_vol48"].to_numpy()
    bb = df["f_bb_width20"].to_numpy()
    atr = df["f_atr14"].to_numpy()

    ok = (
        (vol >= bounds["vol_lo"]) & (vol <= bounds["vol_hi"]) &
        (bb  >= bounds["bb_lo"])  & (bb  <= bounds["bb_hi"]) &
        (atr >= bounds["atr_lo"]) & (atr <= bounds["atr_hi"])
    )
    ok = np.where(np.isfinite(vol) & np.isfinite(bb) & np.isfinite(atr), ok, False)
    return ok


def eval_one(y_true: np.ndarray, proba: np.ndarray, mask: np.ndarray, thr: float) -> tuple[int, float, float]:
    take_call = (proba >= thr) & mask
    take_put  = (proba <= (1.0 - thr)) & mask
    taken = take_call | take_put

    pred = np.where(take_call, 1, 0)
    taken_n = int(taken.sum())
    if taken_n == 0:
        return 0, 0.0, float("nan")

    correct = int((pred[taken] == y_true[taken]).sum())
    acc = float(correct) / float(taken_n)
    cov = float(taken_n) / float(len(y_true))
    return taken_n, cov, acc


def main():
    df = read_dataset_csv("data/dataset_phase2.csv", label_col="y_open_close")
    df = df[df["y_open_close"].notna()].copy()

    # split sequencial: últimos 20% = teste (paper)
    split = int(0.8 * len(df))
    df_train = df.iloc[:split].copy().reset_index(drop=True)
    df_test  = df.iloc[split:].copy().reset_index(drop=True)

    feature_cols = [c for c in df.columns if c.startswith("f_")]
    X_train = df_train[feature_cols].astype("float64").values
    y_train = df_train["y_open_close"].astype("int64").values
    X_test  = df_test[feature_cols].astype("float64").values
    y_test  = df_test["y_open_close"].astype("int64").values

    model = train_calibrated_hgb(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]

    # bounds calculados NO TREINO (sem olhar o teste)
    vol_tr = df_train["f_vol48"].to_numpy()
    bb_tr  = df_train["f_bb_width20"].to_numpy()
    atr_tr = df_train["f_atr14"].to_numpy()

    # grids (pequenos e úteis)
    vol_lo_q = [0.10, 0.20, 0.30]
    vol_hi_q = [0.85, 0.90, 0.95]
    bb_lo_q  = [0.10, 0.20]
    bb_hi_q  = [0.90, 0.95]
    atr_lo_q = [0.10, 0.20]
    atr_hi_q = [0.90, 0.95]

    thresholds = np.round(np.arange(0.52, 0.76, 0.01), 4)

    rows: list[Row] = []
    for vlo in vol_lo_q:
        for vhi in vol_hi_q:
            if vhi <= vlo:
                continue
            for blo in bb_lo_q:
                for bhi in bb_hi_q:
                    if bhi <= blo:
                        continue
                    for alo in atr_lo_q:
                        for ahi in atr_hi_q:
                            if ahi <= alo:
                                continue

                            bounds = {
                                "vol_lo": float(np.nanquantile(vol_tr, vlo)),
                                "vol_hi": float(np.nanquantile(vol_tr, vhi)),
                                "bb_lo":  float(np.nanquantile(bb_tr,  blo)),
                                "bb_hi":  float(np.nanquantile(bb_tr,  bhi)),
                                "atr_lo": float(np.nanquantile(atr_tr, alo)),
                                "atr_hi": float(np.nanquantile(atr_tr, ahi)),
                            }
                            mask = make_regime_mask(df_test, bounds)

                            for thr in thresholds:
                                taken, cov, acc = eval_one(y_test, proba, mask, float(thr))
                                rows.append(Row(
                                    threshold=float(thr),
                                    vol_lo=bounds["vol_lo"], vol_hi=bounds["vol_hi"],
                                    bb_lo=bounds["bb_lo"],   bb_hi=bounds["bb_hi"],
                                    atr_lo=bounds["atr_lo"], atr_hi=bounds["atr_hi"],
                                    taken=int(taken),
                                    coverage=float(cov),
                                    accuracy=float(acc) if np.isfinite(acc) else float("nan"),
                                ))

    out = pd.DataFrame([r.__dict__ for r in rows])

    # regras: poucos sinais + amostra mínima
    MIN_TRADES = 80
    COV_MIN = 0.002   # 0,2%
    COV_MAX = 0.015   # 1,5%

    cand = out[
        (out["taken"] >= MIN_TRADES) &
        (out["coverage"] >= COV_MIN) &
        (out["coverage"] <= COV_MAX) &
        (out["accuracy"].notna())
    ].copy()

    if cand.empty:
        # fallback: pega melhor acurácia com >=50 trades
        cand = out[(out["taken"] >= 50) & (out["accuracy"].notna())].copy()
        rule = "fallback_best_accuracy_min50trades"
    else:
        rule = "best_accuracy_cov0.2-1.5%_min80trades"

    cand = cand.sort_values(["accuracy", "coverage"], ascending=[False, True])
    best = cand.iloc[0].to_dict()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path("runs") / f"tune_v2_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    out.to_csv(run_dir / "tune_grid.csv", index=False)
    cand.head(100).to_csv(run_dir / "tune_top100.csv", index=False)

    summary = {
        "rule": rule,
        "best": best,
        "test_rows": int(len(y_test)),
        "notes": "bounds computed on TRAIN quantiles; applied on TEST; HGB calibrated(sigmoid)",
    }
    (run_dir / "tune_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== TUNER V2 (paper) ===")
    print(f"Rule: {rule}")
    print(f"Best threshold: {best['threshold']}")
    print(f"Hit rate: {best['accuracy']:.4f}")
    print(f"Coverage: {best['coverage']:.4%}")
    print(f"Trades: {int(best['taken'])} / {len(y_test)}")
    print(f"Saved: {run_dir}")

if __name__ == "__main__":
    main()
