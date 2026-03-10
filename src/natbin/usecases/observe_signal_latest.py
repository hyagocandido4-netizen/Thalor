from __future__ import annotations

import csv
import os
import sqlite3
import time
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


def write_sqlite(row: dict, db_path: str = "runs/live_signals.sqlite3") -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
              dt_local TEXT NOT NULL,
              ts INTEGER NOT NULL,
              proba_up REAL NOT NULL,
              regime_ok INTEGER NOT NULL,
              threshold REAL NOT NULL,
              action TEXT NOT NULL,
              close REAL NOT NULL
            )
            """
        )
        con.execute(
            "INSERT INTO signals(dt_local, ts, proba_up, regime_ok, threshold, action, close) VALUES(?,?,?,?,?,?,?)",
            (row["dt_local"], row["ts"], row["proba_up"], row["regime_ok"], row["threshold"], row["action"], row["close"]),
        )
        con.commit()
    finally:
        con.close()


def append_csv_with_retry(path: Path, row: dict, retries: int = 8) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(retries):
        try:
            write_header = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(row.keys()))
                if write_header:
                    w.writeheader()
                w.writerow(row)
            return True
        except PermissionError:
            # arquivo trancado (Excel / outra instância). Espera e tenta de novo.
            time.sleep(0.25 * (2 ** i))
    return False


def main():
    best = load_best_cfg()
    thr = float(best["threshold"])
    b = best["bounds"]

    df = pd.read_csv("data/dataset_phase2.csv").sort_values("ts").reset_index(drop=True)
    feat = [c for c in df.columns if c.startswith("f_")]

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

    row = {
        "dt_local": datetime.now().isoformat(timespec="seconds"),
        "ts": int(last["ts"]),
        "proba_up": p_up,
        "regime_ok": int(ok),
        "threshold": thr,
        "action": action,
        "close": float(last["close"]),
    }

    # (1) Sempre grava em SQLite (robusto)
    write_sqlite(row)

    # (2) CSV best-effort (com path configurável)
    csv_path = os.getenv("LIVE_SIGNALS_PATH", "runs/live_signals.csv")
    csv_path = Path(csv_path)

    ok_csv = append_csv_with_retry(csv_path, row, retries=8)
    if not ok_csv:
        # fallback: não quebra o loop; salva em arquivo alternativo
        fallback = Path("runs") / f"live_signals_fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.csv"
        append_csv_with_retry(fallback, row, retries=1)
        print(f"[WARN] CSV locked: {csv_path}. Wrote fallback: {fallback}")

    print("\n=== OBSERVE (latest) ===")
    print(row)
    if ok_csv:
        print(f"csv_ok: {csv_path}")
    print("sqlite_ok: runs/live_signals.sqlite3")


if __name__ == "__main__":
    main()