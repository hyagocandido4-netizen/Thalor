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
Require-Path "config.yaml" "Nao achei config.yaml."
Require-Path "src\natbin" "Nao achei src\natbin."
Require-Path "observe_loop.ps1" "Nao achei observe_loop.ps1."

Write-Utf8NoBomFile "src\natbin\observe_signal_topk_perday.py" @'
from __future__ import annotations

import csv
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split


def load_cfg() -> tuple[dict, str]:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")) or {}
    best = cfg.get("best")
    if not best:
        raise RuntimeError("Nao achei bloco 'best' em config.yaml (rode tuner+freeze).")
    tz = (cfg.get("data", {}) or {}).get("timezone", "America/Sao_Paulo")
    return best, tz


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
        (bb  >= b["bb_lo"])  & (bb  <= b["bb_hi"]) &
        (atr >= b["atr_lo"]) & (atr <= b["atr_hi"])
    )
    ok = np.where(np.isfinite(vol) & np.isfinite(bb) & np.isfinite(atr), ok, False)
    return ok


def ensure_signals_v2(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL;")
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
          close REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_signals_v2_day_ts ON signals_v2(day, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_signals_v2_ts ON signals_v2(ts)")


def write_sqlite_signal(row: dict, db_path: str = "runs/live_signals.sqlite3") -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    try:
        ensure_signals_v2(con)
        con.execute(
            """
            INSERT INTO signals_v2(dt_local, day, ts, proba_up, conf, regime_ok, threshold, rank_in_day, executed_today, action, reason, close)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["dt_local"], row["day"], row["ts"], row["proba_up"], row["conf"],
                row["regime_ok"], row["threshold"], row["rank_in_day"], row["executed_today"],
                row["action"], row["reason"], row["close"],
            ),
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
            time.sleep(0.25 * (2 ** i))
    return False


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

    best, tzname = load_cfg()
    thr = float(best["threshold"])
    bounds = best["bounds"]
    tz = ZoneInfo(tzname)

    df = pd.read_csv("data/dataset_phase2.csv").sort_values("ts").reset_index(drop=True)
    feat = [c for c in df.columns if c.startswith("f_")]

    # Treina sem "espiar" no final
    cut = max(1000, len(df) - 200)
    train = df.iloc[:cut].copy()
    X_train = train[feat].astype("float64").values
    y_train = train["y_open_close"].astype("int64").values

    model = train_calibrated_hgb(X_train, y_train)

    last = df.iloc[-1].copy()
    last_ts = int(last["ts"])

    # DIA (robusto)
    try:
        last_day = datetime.fromtimestamp(last_ts, tz=tz).strftime("%Y-%m-%d")
    except Exception:
        last_day = datetime.now(tz=tz).strftime("%Y-%m-%d")

    # Recorta o dia local do candle (até o ts atual)
    dts_local = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df_day = df[(dts_local.dt.strftime("%Y-%m-%d") == last_day) & (df["ts"] <= last_ts)].copy().reset_index(drop=True)

    # Probas do dia
    X_day = df_day[feat].astype("float64").values
    proba_day = model.predict_proba(X_day)[:, 1]
    mask_day = make_mask(df_day, bounds)
    conf_day = np.maximum(proba_day, 1.0 - proba_day)

    # candidato = passaria no baseline (conf>=thr) e regime ok
    cand = mask_day & (conf_day >= thr)
    idx = np.where(cand)[0]

    last_local_idx = int(len(df_day) - 1)
    proba_now = float(proba_day[last_local_idx])
    conf_now = float(conf_day[last_local_idx])
    regime_now = bool(mask_day[last_local_idx])

    # ranking do último candle no dia (1..n) se for candidato, senão -1
    rank_in_day = -1
    is_topk_now = False
    if idx.size > 0 and cand[last_local_idx]:
        order = idx[np.argsort(conf_day[idx])[::-1]]  # conf desc
        rank_in_day = int(np.where(order == last_local_idx)[0][0]) + 1
        top = set(order[:k])
        is_topk_now = (last_local_idx in top)

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
        "dt_local": datetime.now(tz=tz).isoformat(timespec="seconds"),
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
    }

    write_sqlite_signal(row)

    csv_path = os.getenv("LIVE_SIGNALS_PATH", f"runs/live_signals_{datetime.now(tz=tz).strftime('%Y%m%d')}.csv")
    ok_csv = append_csv_with_retry(Path(csv_path), row, retries=8)
    if not ok_csv:
        fb = Path("runs") / f"live_signals_fallback_{datetime.now(tz=tz).strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.csv"
        append_csv_with_retry(fb, row, retries=1)

    print("\n=== OBSERVE TOPK-PERDAY (latest) ===")
    print(row)
    print("sqlite_ok: runs/live_signals.sqlite3 (signals_v2)")


if __name__ == "__main__":
    main()
'@

# preflight
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK. observe_signal_topk_perday.py reescrito e compilado." -ForegroundColor Green
Write-Host "Teste agora: pwsh -ExecutionPolicy Bypass -File .\observe_loop.ps1 -Once" -ForegroundColor Yellow