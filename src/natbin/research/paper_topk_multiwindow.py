from __future__ import annotations
try:
    from ..config.env import env_bool, env_float, env_int, env_str
except Exception:  # pragma: no cover
    from ..config.env import env_float, env_int, env_bool, env_str

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
        raise RuntimeError("Nao achei bloco 'best' no config.yaml. Rode o tuner e congele o best.")
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


def eval_baseline(y: np.ndarray, proba: np.ndarray, mask: np.ndarray, thr: float) -> tuple[int, float, float]:
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


def eval_topk_per_session(
    df_block: pd.DataFrame,
    y: np.ndarray,
    proba: np.ndarray,
    mask: np.ndarray,
    thr: float,
    k: int,
) -> tuple[int, float, float]:
    """
    Top-K por sessão (session_id):
    - candidato só se passaria no threshold (confidence>=thr) e mask==True
    - dentre os candidatos da sessão, pega os K maiores por confidence
    """
    conf = np.maximum(proba, 1.0 - proba)
    cand = mask & (conf >= thr)

    if not np.any(cand):
        return 0, 0.0, float("nan")

    tmp = pd.DataFrame({
        "session_id": df_block["session_id"].to_numpy(),
        "proba": proba,
        "conf": conf,
        "y": y,
        "cand": cand.astype(int),
    })
    tmp = tmp[tmp["cand"] == 1].copy()
    if tmp.empty:
        return 0, 0.0, float("nan")

    # direção prevista (CALL se p>=0.5, PUT se p<0.5)
    tmp["pred"] = (tmp["proba"] >= 0.5).astype(int)

    # escolhe top-K por sessão
    tmp = tmp.sort_values(["session_id", "conf"], ascending=[True, False])
    tmp["rank"] = tmp.groupby("session_id").cumcount() + 1
    top = tmp[tmp["rank"] <= k].copy()

    taken_n = int(len(top))
    if taken_n == 0:
        return 0, 0.0, float("nan")

    correct = int((top["pred"].to_numpy() == top["y"].to_numpy()).sum())
    hit = float(correct) / float(taken_n)
    cov = float(taken_n) / float(len(y))
    return taken_n, cov, hit


def main():
    best = load_best_cfg()
    thr = float(best["threshold"])
    bounds = best["bounds"]

    # K vem do ambiente (passado pelo PowerShell)
    import os
    k = env_int("TOPK_K", "2")

    df = read_dataset_csv("data/dataset_phase2.csv", label_col="y_open_close")
    df = df[df["y_open_close"].notna()].copy()
    feat = [c for c in df.columns if c.startswith("f_")]

    # Multiwindow pseudo-futuro (expanding train, rolling test)
    N = 6
    min_train = max(20000, int(0.25 * len(df)))
    start_test = max(min_train, int(0.40 * len(df)))

    tail = df.iloc[start_test:].copy().reset_index(drop=True)
    block = max(2000, int(len(tail) / N))

    rows: list[WindowRow] = []

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

        base_taken, base_cov, base_hit = eval_baseline(y_test, proba, mask, thr)
        topk_taken, topk_cov, topk_hit = eval_topk_per_session(test_df, y_test, proba, mask, thr, k=k)

        rows.append(WindowRow(
            window=i + 1,
            train_rows=int(len(train_df)),
            test_rows=int(len(test_df)),
            base_taken=int(base_taken),
            base_cov=float(base_cov),
            base_hit=float(base_hit) if np.isfinite(base_hit) else float("nan"),
            topk_taken=int(topk_taken),
            topk_cov=float(topk_cov),
            topk_hit=float(topk_hit) if np.isfinite(topk_hit) else float("nan"),
        ))

        print(f"[win {i+1}] base_taken={base_taken} topk_taken={topk_taken}")

    out_dir = Path("runs") / f"topk2_multiwindow_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    df_out = pd.DataFrame([r.__dict__ for r in rows])
    df_out.to_csv(out_dir / "multiwindow_topk.csv", index=False)

    # agregados ponderados (por trades)
    def wavg(hit_col: str, taken_col: str) -> float | None:
        taken = df_out[taken_col].to_numpy()
        hit = df_out[hit_col].to_numpy()
        ok = np.isfinite(hit) & (taken > 0)
        if not np.any(ok):
            return None
        correct = int(np.round((hit[ok] * taken[ok]).sum()))
        total = int(taken[ok].sum())
        return float(correct) / float(total) if total else None

    summary = {
        "k": k,
        "threshold": thr,
        "bounds": bounds,
        "windows": int(len(df_out)),
        "base_taken_total": int(df_out["base_taken"].sum()),
        "base_test_total": int(df_out["test_rows"].sum()),
        "base_cov_total": float(df_out["base_taken"].sum() / df_out["test_rows"].sum()),
        "base_hit_weighted": wavg("base_hit", "base_taken"),
        "topk_taken_total": int(df_out["topk_taken"].sum()),
        "topk_test_total": int(df_out["test_rows"].sum()),
        "topk_cov_total": float(df_out["topk_taken"].sum() / df_out["test_rows"].sum()),
        "topk_hit_weighted": wavg("topk_hit", "topk_taken"),
        "path": str(out_dir).replace("\\", "/"),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== TOPK MULTIWINDOW (pseudo-futuro) ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
