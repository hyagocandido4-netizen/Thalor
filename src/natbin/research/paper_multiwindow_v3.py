from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from .dsio import read_dataset_csv
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

    df = read_dataset_csv("data/dataset_phase2.csv", label_col="y_open_close")
    df = df[df["y_open_close"].notna()].copy()

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
