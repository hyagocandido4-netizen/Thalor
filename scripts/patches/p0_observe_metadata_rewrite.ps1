$ErrorActionPreference="Stop"
Set-StrictMode -Version Latest

$path = ".\src\natbin\observe_signal_topk_perday.py"
if(-not (Test-Path $path)){ throw "Nao achei $path" }

@'
from __future__ import annotations

import csv
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split


BASE_FIELDS = [
    "dt_local",
    "day",
    "ts",
    "proba_up",
    "conf",
    "regime_ok",
    "threshold",
    "rank_in_day",
    "executed_today",
    "action",
    "reason",
    "close",
]

META_FIELDS = [
    "asset",
    "model_version",
    "train_rows",
    "train_end_ts",
    "best_source",
]

ALL_FIELDS = BASE_FIELDS + META_FIELDS


def load_cfg() -> tuple[dict, dict]:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")) or {}
    best = cfg.get("best")
    if not best:
        raise RuntimeError("Nao achei bloco 'best' em config.yaml (rode tuner+freeze).")
    data = cfg.get("data", {}) or {}
    return best, data


def _tz(tzname: str):
    try:
        return ZoneInfo(tzname)
    except Exception:
        return timezone(timedelta(hours=-3))


def get_model_version() -> str:
    v = (os.getenv("MODEL_VERSION") or os.getenv("GIT_COMMIT") or "").strip()
    if v:
        return v
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        s = out.decode("utf-8", errors="ignore").strip()
        return s or "unknown"
    except Exception:
        return "unknown"


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
    bb = df["f_bb_width20"].to_numpy()
    atr = df["f_atr14"].to_numpy()

    ok = (
        (vol >= b["vol_lo"]) & (vol <= b["vol_hi"]) &
        (bb >= b["bb_lo"]) & (bb <= b["bb_hi"]) &
        (atr >= b["atr_lo"]) & (atr <= b["atr_hi"])
    )
    ok = np.where(np.isfinite(vol) & np.isfinite(bb) & np.isfinite(atr), ok, False)
    return ok


def ensure_signals_v2(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL;")
    # cria com base + meta (pra DB novo já nascer completo)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS signals_v2 (
          dt_local TEXT NOT NULL,
          day TEXT NOT NULL,
          ts INTEGER NOT NULL,
          proba_up REAL NOT NULL,
          conf REAL NOT NULL,
          regime_ok INTEGER NOT NULL,
          threshold REAL NOT NULL,
          rank_in_day INTEGER NOT NULL,
          executed_today INTEGER NOT NULL,
          action TEXT NOT NULL,
          reason TEXT NOT NULL,
          close REAL NOT NULL,
          asset TEXT,
          model_version TEXT,
          train_rows INTEGER,
          train_end_ts INTEGER,
          best_source TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_signals_v2_day_ts ON signals_v2(day, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_signals_v2_ts ON signals_v2(ts)")

    # migração (DB existente)
    existing = {r[1] for r in con.execute("PRAGMA table_info(signals_v2)").fetchall()}
    add_cols = {
        "asset": "asset TEXT",
        "model_version": "model_version TEXT",
        "train_rows": "train_rows INTEGER",
        "train_end_ts": "train_end_ts INTEGER",
        "best_source": "best_source TEXT",
    }
    for name, ddl in add_cols.items():
        if name not in existing:
            con.execute(f"ALTER TABLE signals_v2 ADD COLUMN {ddl}")


def write_sqlite_signal(row: dict, db_path: str = "runs/live_signals.sqlite3") -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    try:
        ensure_signals_v2(con)

        cols = list(row.keys())
        collist = ",".join(cols)
        placeholders = ",".join(["?"] * len(cols))

        con.execute(
            f"INSERT INTO signals_v2({collist}) VALUES({placeholders})",
            tuple(row[c] for c in cols),
        )
        con.commit()
    finally:
        con.close()


def _read_csv_header(path: Path) -> list[str] | None:
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            line = f.readline().strip()
        if not line:
            return None
        return line.split(",")
    except Exception:
        return None


def append_csv_with_retry(path: Path, row: dict, retries: int = 8) -> tuple[bool, Path]:
    path.parent.mkdir(parents=True, exist_ok=True)

    # Se existir e a header não bater com a nova estrutura, escreve em arquivo paralelo
    if path.exists():
        hdr = _read_csv_header(path)
        if hdr and hdr != ALL_FIELDS:
            path = path.with_name(path.stem + "_meta" + path.suffix)

    for i in range(retries):
        try:
            write_header = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=ALL_FIELDS)
                if write_header:
                    w.writeheader()
                w.writerow(row)
            return True, path
        except PermissionError:
            time.sleep(0.25 * (2**i))

    return False, path


def state_db() -> Path:
    return Path("runs") / "live_topk_state.sqlite3"


def ensure_state(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS executed (
          day TEXT NOT NULL,
          ts INTEGER NOT NULL,
          action TEXT NOT NULL,
          conf REAL NOT NULL,
          PRIMARY KEY(day, ts)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_executed_day ON executed(day)")


def executed_today_count(day: str) -> int:
    db = state_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db, timeout=30)
    try:
        ensure_state(con)
        cur = con.execute("SELECT COUNT(*) FROM executed WHERE day=?", (day,))
        return int(cur.fetchone()[0])
    finally:
        con.close()


def already_executed(day: str, ts: int) -> bool:
    con = sqlite3.connect(state_db(), timeout=30)
    try:
        ensure_state(con)
        cur = con.execute("SELECT 1 FROM executed WHERE day=? AND ts=? LIMIT 1", (day, ts))
        return cur.fetchone() is not None
    finally:
        con.close()


def mark_executed(day: str, ts: int, action: str, conf: float) -> None:
    con = sqlite3.connect(state_db(), timeout=30)
    try:
        ensure_state(con)
        con.execute(
            "INSERT OR IGNORE INTO executed(day, ts, action, conf) VALUES(?,?,?,?)",
            (day, int(ts), str(action), float(conf)),
        )
        con.commit()
    finally:
        con.close()


def main():
    k = int(os.getenv("TOPK_K", "2"))
    min_train_rows = int(os.getenv("MIN_TRAIN_ROWS", "5000"))

    best, data = load_cfg()
    thr = float(best["threshold"])
    bounds = best["bounds"]

    asset = str(data.get("asset", "")).strip()
    tzname = str(data.get("timezone", "America/Sao_Paulo")).strip()
    tz = _tz(tzname)

    df = pd.read_csv("data/dataset_phase2.csv").sort_values("ts").reset_index(drop=True)
    feat = [c for c in df.columns if c.startswith("f_")]

    last = df.iloc[-1].copy()
    last_ts = int(last["ts"])
    last_day = datetime.fromtimestamp(last_ts, tz=tz).strftime("%Y-%m-%d")

    model_version = get_model_version()

    # P0: evita treinar em dataset muito pequeno
    if len(df) < (min_train_rows + 300):
        row = {
            "dt_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "day": str(last_day),
            "ts": int(last_ts),
            "proba_up": 0.5,
            "conf": 0.5,
            "regime_ok": 0,
            "threshold": float(thr),
            "rank_in_day": -1,
            "executed_today": int(executed_today_count(last_day)),
            "action": "HOLD",
            "reason": "insufficient_data",
            "close": float(last["close"]),
            "asset": asset,
            "model_version": model_version,
            "train_rows": int(len(df)),
            "train_end_ts": int(df["ts"].iloc[-1]),
            "best_source": str(best.get("tune_dir", "")),
        }
        write_sqlite_signal(row)
        csv_path = os.getenv("LIVE_SIGNALS_PATH", f"runs/live_signals_v2_{datetime.now().strftime('%Y%m%d')}.csv")
        ok, used = append_csv_with_retry(Path(csv_path), row, retries=8)
        print("\n=== OBSERVE TOPK-PERDAY (latest) ===")
        print(row)
        print(f"csv_ok: {ok} | csv_path_used: {used}")
        print("sqlite_ok: runs/live_signals.sqlite3 (signals_v2)")
        return

    # Treina sem “espiar” no final
    cut = max(min_train_rows, len(df) - 200)
    train = df.iloc[:cut].copy()
    X_train = train[feat].astype("float64").values
    y_train = train["y_open_close"].astype("int64").values

    model = train_calibrated_hgb(X_train, y_train)

    # Probas do dia (até agora)
    dts_local = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df_day = df[(dts_local.dt.strftime("%Y-%m-%d") == last_day) & (df["ts"] <= last_ts)].copy().reset_index(drop=True)

    X_day = df_day[feat].astype("float64").values
    proba_day = model.predict_proba(X_day)[:, 1]
    mask_day = make_mask(df_day, bounds)
    conf_day = np.maximum(proba_day, 1.0 - proba_day)

    cand = mask_day & (conf_day >= thr)
    idx = np.where(cand)[0]

    last_local_idx = int(len(df_day) - 1)
    proba_now = float(proba_day[last_local_idx])
    conf_now = float(conf_day[last_local_idx])
    regime_now = bool(mask_day[last_local_idx])

    rank_in_day = -1
    is_topk_now = False
    if idx.size > 0 and cand[last_local_idx]:
        order = idx[np.argsort(conf_day[idx])[::-1]]
        rank_in_day = int(np.where(order == last_local_idx)[0][0]) + 1
        is_topk_now = (last_local_idx in set(order[:k]))

    executed_n = executed_today_count(last_day)

    action = "HOLD"
    reason = "neutral"
    if executed_n >= k:
        reason = "max_k_reached"
    elif not regime_now:
        reason = "regime_block"
    elif conf_now < thr:
        reason = "below_conf_threshold"
    elif not is_topk_now:
        reason = "not_in_topk_today"
    else:
        if already_executed(last_day, last_ts):
            reason = "already_emitted_for_ts"
        else:
            action = "CALL" if proba_now >= 0.5 else "PUT"
            reason = "topk_emit"
            mark_executed(last_day, last_ts, action, conf_now)
            executed_n = executed_today_count(last_day)

    row = {
        "dt_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "day": str(last_day),
        "ts": int(last_ts),
        "proba_up": float(proba_now),
        "conf": float(conf_now),
        "regime_ok": int(regime_now),
        "threshold": float(thr),
        "rank_in_day": int(rank_in_day),
        "executed_today": int(executed_n),
        "action": str(action),
        "reason": str(reason),
        "close": float(last["close"]),
        "asset": asset,
        "model_version": model_version,
        "train_rows": int(len(train)),
        "train_end_ts": int(train["ts"].iloc[-1]),
        "best_source": str(best.get("tune_dir", "")),
    }

    write_sqlite_signal(row)

    csv_path = os.getenv("LIVE_SIGNALS_PATH", f"runs/live_signals_v2_{datetime.now().strftime('%Y%m%d')}.csv")
    ok_csv, used = append_csv_with_retry(Path(csv_path), row, retries=8)

    print("\n=== OBSERVE TOPK-PERDAY (latest) ===")
    print(row)
    print(f"csv_ok: {ok_csv} | csv_path_used: {used}")
    print("sqlite_ok: runs/live_signals.sqlite3 (signals_v2)")


if __name__ == "__main__":
    main()
'@ | Set-Content -Encoding UTF8 $path

.\.venv\Scripts\python.exe -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: observe_signal_topk_perday.py reescrito com metadados + INSERT dinamico (P0 completo)" -ForegroundColor Green
Write-Host "Teste: pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once" -ForegroundColor Yellow