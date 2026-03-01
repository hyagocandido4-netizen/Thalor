param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param([string]$Path, [string]$Content)
  $enc = New-Object System.Text.UTF8Encoding($false)
  $norm = $Content.Replace("`r`n","`n")
  [System.IO.File]::WriteAllText($Path, $norm, $enc)
}

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Rode o bootstrap/venv antes." }

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\p2_2_meta_selectivity_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$targets = @(
  "src\natbin\gate_meta.py",
  "src\natbin\observe_signal_topk_perday.py",
  "src\natbin\tune_multiwindow_topk.py",
  "src\natbin\paper_pnl_backtest.py"
)

foreach ($f in $targets) {
  if (Test-Path $f) { Copy-Item $f (Join-Path $backupDir ([IO.Path]::GetFileName($f))) -Force }
}

Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

# =========================
# NEW: gate_meta.py (P2.2)
# =========================
Write-Utf8NoBomFile -Path "src\natbin\gate_meta.py" -Content @'
from __future__ import annotations

import os
from typing import Any, Tuple

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


# Bump quando você mudar as meta-features (força retrain do cache no observe)
GATE_VERSION = "meta_v1"


META_FEATURES = [
    "conf",
    "proba_up",
    "signed_margin",
    "abs_margin",
    "vol",
    "bb",
    "atr",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "iso_score",
]


def _time_feats(ts: np.ndarray, tz: ZoneInfo) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    dt = pd.to_datetime(ts, unit="s", utc=True).tz_convert(tz)
    hour = dt.hour.astype(float) + dt.minute.astype(float) / 60.0
    dow = dt.dayofweek.astype(float)

    hr = 2.0 * np.pi * (hour / 24.0)
    dr = 2.0 * np.pi * (dow / 7.0)

    return np.sin(hr), np.cos(hr), np.sin(dr), np.cos(dr)


def build_meta_X(
    ts: np.ndarray,
    tz: ZoneInfo,
    proba_up: np.ndarray,
    conf: np.ndarray,
    vol: np.ndarray,
    bb: np.ndarray,
    atr: np.ndarray,
    iso_score: np.ndarray | None,
) -> np.ndarray:
    if iso_score is None:
        iso_score = conf

    signed = proba_up - 0.5
    abs_m = np.abs(signed)
    hsin, hcos, dsin, dcos = _time_feats(ts, tz)

    X = np.column_stack(
        [
            conf,
            proba_up,
            signed,
            abs_m,
            vol,
            bb,
            atr,
            hsin,
            hcos,
            dsin,
            dcos,
            iso_score,
        ]
    )
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)


def _fit_iso(conf: np.ndarray, correct: np.ndarray) -> IsotonicRegression | None:
    try:
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(conf.astype(float), correct.astype(float))
        return iso
    except Exception:
        return None


def _fit_meta_model(X: np.ndarray, y: np.ndarray, meta_model_type: str) -> Any | None:
    if len(X) < 200:
        return None
    if len(np.unique(y)) < 2:
        return None

    meta_model_type = (meta_model_type or "logreg").strip().lower()
    if meta_model_type not in ("logreg", "hgb"):
        meta_model_type = "logreg"

    if meta_model_type == "hgb":
        meta = HistGradientBoostingClassifier(
            max_depth=2,
            learning_rate=0.05,
            max_iter=250,
            random_state=0,
        )
        meta.fit(X, y)
        return meta

    # logreg default
    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(max_iter=500, solver="lbfgs")),
        ]
    )
    pipe.fit(X, y)
    return pipe


def train_base_cal_iso_meta(
    train_df: pd.DataFrame,
    feat_cols: list[str],
    tz: ZoneInfo,
    meta_model_type: str = "logreg",
) -> tuple[CalibratedClassifierCV, IsotonicRegression | None, Any | None]:
    """
    Treina:
      1) base HGB (em sub-treino)
      2) calibração sigmoid (em cal)
      3) iso gate (conf -> P(acertar))
      4) meta gate P2.2 (X_meta -> P(acertar))
    """
    n = int(len(train_df))
    if n < 500:
        raise ValueError(f"Treino insuficiente (n={n}).")

    sub_frac = float(os.getenv("SUB_FRAC", "0.70"))
    cal_frac = float(os.getenv("CAL_FRAC", "0.15"))
    min_part = int(os.getenv("MIN_PART", "200"))

    n_sub = int(n * sub_frac)
    n_cal = int(n * cal_frac)
    n_meta = n - n_sub - n_cal

    # fallback: 80/20 se ficar pequeno demais
    if n_sub < min_part or n_cal < min_part:
        n_sub = int(n * 0.80)
        n_cal = n - n_sub
        n_meta = 0

    sub_df = train_df.iloc[:n_sub]
    cal_df = train_df.iloc[n_sub : n_sub + n_cal]

    # meta: se não tiver meta suficiente, usa cal+meta juntos
    meta_start = (n_sub + n_cal) if n_meta >= min_part else n_sub
    meta_df = train_df.iloc[meta_start:]

    base = HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.05,
        max_iter=300,
        random_state=0,
    )
    base.fit(sub_df[feat_cols], sub_df["y_open_close"])

    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(cal_df[feat_cols], cal_df["y_open_close"])

    # ISO gate em cima do CAL
    p_cal = cal.predict_proba(cal_df[feat_cols])[:, 1]
    pred_cal = (p_cal >= 0.5).astype(int)
    y_cal = cal_df["y_open_close"].to_numpy(dtype=int)
    correct_cal = (pred_cal == y_cal).astype(int)
    conf_cal = np.maximum(p_cal, 1.0 - p_cal)
    iso = _fit_iso(conf_cal, correct_cal)

    # META gate em cima do META
    p_m = cal.predict_proba(meta_df[feat_cols])[:, 1]
    pred_m = (p_m >= 0.5).astype(int)
    y_m = meta_df["y_open_close"].to_numpy(dtype=int)
    correct_m = (pred_m == y_m).astype(int)
    conf_m = np.maximum(p_m, 1.0 - p_m)

    iso_m = iso.predict(conf_m.astype(float)) if iso is not None else conf_m

    X_meta = build_meta_X(
        ts=meta_df["ts"].to_numpy(dtype=int),
        tz=tz,
        proba_up=p_m.astype(float),
        conf=conf_m.astype(float),
        vol=meta_df["f_vol48"].to_numpy(dtype=float),
        bb=meta_df["f_bb_width20"].to_numpy(dtype=float),
        atr=meta_df["f_atr14"].to_numpy(dtype=float),
        iso_score=iso_m.astype(float),
    )

    meta_model = _fit_meta_model(X_meta, correct_m, meta_model_type)
    return cal, iso, meta_model


def compute_scores(
    df: pd.DataFrame,
    feat_cols: list[str],
    tz: ZoneInfo,
    cal_model: CalibratedClassifierCV,
    iso: IsotonicRegression | None,
    meta_model: Any | None,
    gate_mode: str = "meta",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    """
    Retorna: proba_up, conf, score, gate_used
    score = P(acertar) se gate meta/iso; fallback score=conf.
    """
    gate_mode = (gate_mode or "meta").strip().lower()
    if gate_mode not in ("meta", "iso", "conf"):
        gate_mode = "meta"

    proba = cal_model.predict_proba(df[feat_cols])[:, 1].astype(float)
    conf = np.maximum(proba, 1.0 - proba).astype(float)

    iso_score = iso.predict(conf.astype(float)).astype(float) if iso is not None else conf

    if gate_mode == "meta" and meta_model is not None:
        X = build_meta_X(
            ts=df["ts"].to_numpy(dtype=int),
            tz=tz,
            proba_up=proba,
            conf=conf,
            vol=df["f_vol48"].to_numpy(dtype=float),
            bb=df["f_bb_width20"].to_numpy(dtype=float),
            atr=df["f_atr14"].to_numpy(dtype=float),
            iso_score=iso_score,
        )
        try:
            s = meta_model.predict_proba(X)[:, 1].astype(float)
            return proba, conf, s, "meta"
        except Exception:
            pass

    if gate_mode == "iso" and iso is not None:
        return proba, conf, iso_score, "iso"

    return proba, conf, conf, "conf"
'@

# =====================================
# observe_signal_topk_perday.py (LIVE)
# =====================================
Write-Utf8NoBomFile -Path "src\natbin\observe_signal_topk_perday.py" -Content @'
from __future__ import annotations

import csv
import hashlib
import json
import os
import pickle
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from .gate_meta import GATE_VERSION, META_FEATURES, compute_scores, train_base_cal_iso_meta


BASE_FIELDS = [
    "dt_local",
    "day",
    "ts",
    "proba_up",
    "conf",
    "score",
    "gate_mode",
    "thresh_on",
    "threshold",
    "k",
    "rank_in_day",
    "executed_today",
    "action",
    "reason",
    "close",
    "payout",
    "ev",
]
META_FIELDS = [
    "asset",
    "model_version",
    "train_rows",
    "train_end_ts",
    "best_source",
    "tune_dir",
    "feat_hash",
    "gate_version",
    "meta_model",
]
ALL_FIELDS = BASE_FIELDS + META_FIELDS


def load_cfg() -> tuple[dict[str, Any], dict[str, Any]]:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    best = cfg.get("best") or {}
    return cfg, best


def get_model_version() -> str:
    try:
        import subprocess

        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:
        return "unknown"


def feat_hash(feat: list[str]) -> str:
    s = ",".join(feat).encode("utf-8")
    return hashlib.sha1(s).hexdigest()[:12]


def sanitize_asset(asset: str) -> str:
    out = []
    for ch in asset:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


def cache_paths(asset: str) -> tuple[Path, Path]:
    a = sanitize_asset(asset)
    pkl = Path("runs") / f"model_cache_{a}.pkl"
    meta = Path("runs") / f"model_cache_{a}.json"
    return pkl, meta


def load_cache(asset: str) -> dict[str, Any] | None:
    pkl, meta = cache_paths(asset)
    if not pkl.exists() or not meta.exists():
        return None
    try:
        payload = pickle.loads(pkl.read_bytes())
        m = json.loads(meta.read_text(encoding="utf-8"))
        payload["meta"] = m
        return payload
    except Exception:
        return None


def save_cache(asset: str, payload: dict[str, Any]) -> None:
    pkl, meta = cache_paths(asset)
    pkl.parent.mkdir(parents=True, exist_ok=True)

    m = payload.get("meta") or {}
    meta.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    payload2 = dict(payload)
    payload2.pop("meta", None)
    pkl.write_bytes(pickle.dumps(payload2))


def should_retrain(
    meta: dict[str, Any] | None,
    *,
    train_end_ts: int,
    best_source: str,
    fhash: str,
    interval_sec: int,
    meta_model_type: str,
) -> bool:
    if not meta:
        return True

    last_ts = int(meta.get("train_end_ts") or 0)
    last_best = str(meta.get("best_source") or "")
    last_fhash = str(meta.get("feat_hash") or "")
    last_gate = str(meta.get("gate_version") or "")
    last_mm = str(meta.get("meta_model") or "")

    if last_best != best_source:
        return True
    if last_fhash != fhash:
        return True
    if last_gate != GATE_VERSION:
        return True
    if last_mm != meta_model_type:
        return True

    retrain_every = int(os.getenv("RETRAIN_EVERY_CANDLES", "12"))
    min_delta = retrain_every * interval_sec
    return (train_end_ts - last_ts) >= min_delta


def ensure_signals_v2(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS signals_v2 (
          dt_local TEXT NOT NULL,
          day TEXT NOT NULL,
          ts INTEGER NOT NULL,
          proba_up REAL NOT NULL,
          conf REAL NOT NULL,
          score REAL,
          gate_mode TEXT,
          thresh_on TEXT,
          threshold REAL NOT NULL,
          k INTEGER,
          rank_in_day INTEGER,
          executed_today INTEGER,
          action TEXT NOT NULL,
          reason TEXT NOT NULL,
          close REAL,
          payout REAL,
          ev REAL,
          asset TEXT,
          model_version TEXT,
          train_rows INTEGER,
          train_end_ts INTEGER,
          best_source TEXT,
          tune_dir TEXT,
          feat_hash TEXT,
          gate_version TEXT,
          meta_model TEXT,
          PRIMARY KEY(day, ts)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_signals_v2_ts ON signals_v2(ts)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(signals_v2)").fetchall()}

    add_cols = {
        "score": "REAL",
        "gate_mode": "TEXT",
        "thresh_on": "TEXT",
        "k": "INTEGER",
        "rank_in_day": "INTEGER",
        "executed_today": "INTEGER",
        "close": "REAL",
        "payout": "REAL",
        "ev": "REAL",
        "asset": "TEXT",
        "model_version": "TEXT",
        "train_rows": "INTEGER",
        "train_end_ts": "INTEGER",
        "best_source": "TEXT",
        "tune_dir": "TEXT",
        "feat_hash": "TEXT",
        "gate_version": "TEXT",
        "meta_model": "TEXT",
    }
    for c, typ in add_cols.items():
        if c not in cols:
            con.execute(f"ALTER TABLE signals_v2 ADD COLUMN {c} {typ}")
    con.commit()


def write_sqlite_signal(row: dict[str, Any], db_path: str = "runs/live_signals.sqlite3") -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        ensure_signals_v2(con)
        cols = list(row.keys())
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT OR REPLACE INTO signals_v2 ({','.join(cols)}) VALUES ({placeholders})"
        con.execute(sql, [row[c] for c in cols])
        con.commit()
    finally:
        con.close()


def append_csv(row: dict[str, Any]) -> str:
    path = os.getenv("LIVE_SIGNALS_PATH", "runs/live_signals_v2.csv")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def read_header(pp: Path) -> list[str] | None:
        if not pp.exists():
            return None
        try:
            with pp.open("r", encoding="utf-8", newline="") as f:
                r = csv.reader(f)
                return next(r, None)
        except Exception:
            return None

    header = read_header(p)
    if header and header != ALL_FIELDS:
        p = p.with_name(p.stem + "_meta" + p.suffix)

    for attempt in range(8):
        try:
            new_file = not p.exists()
            with p.open("a", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=ALL_FIELDS)
                if new_file:
                    w.writeheader()
                w.writerow({k: row.get(k, "") for k in ALL_FIELDS})
            return str(p)
        except PermissionError:
            time.sleep(0.25 * (attempt + 1))

    return str(p)


def ensure_state_db(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL;")
    cols = {r[1] for r in con.execute("PRAGMA table_info(executed)").fetchall()}
    if cols and "asset" not in cols:
        con.execute("ALTER TABLE executed RENAME TO executed_legacy")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS executed (
          asset TEXT NOT NULL,
          day TEXT NOT NULL,
          ts INTEGER NOT NULL,
          action TEXT NOT NULL,
          conf REAL NOT NULL,
          score REAL,
          PRIMARY KEY(asset, day, ts)
        )
        """
    )

    legacy = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='executed_legacy'"
    ).fetchone()
    if legacy:
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO executed(asset, day, ts, action, conf, score)
                SELECT 'LEGACY', day, ts, action, conf, NULL
                FROM executed_legacy
                """
            )
        except Exception:
            pass
        con.execute("DROP TABLE executed_legacy")

    con.execute("CREATE INDEX IF NOT EXISTS idx_exe_asset_day ON executed(asset, day)")
    con.commit()


def state_path() -> Path:
    return Path("runs") / "live_topk_state.sqlite3"


def executed_today_count(asset: str, day: str) -> int:
    con = sqlite3.connect(state_path())
    try:
        ensure_state_db(con)
        cur = con.execute("SELECT COUNT(*) FROM executed WHERE asset=? AND day=?", (asset, day))
        return int(cur.fetchone()[0] or 0)
    finally:
        con.close()


def already_executed(asset: str, day: str, ts: int) -> bool:
    con = sqlite3.connect(state_path())
    try:
        ensure_state_db(con)
        cur = con.execute(
            "SELECT 1 FROM executed WHERE asset=? AND day=? AND ts=? LIMIT 1",
            (asset, day, ts),
        )
        return cur.fetchone() is not None
    finally:
        con.close()


def mark_executed(asset: str, day: str, ts: int, action: str, conf: float, score: float) -> None:
    con = sqlite3.connect(state_path())
    try:
        ensure_state_db(con)
        con.execute(
            "INSERT OR REPLACE INTO executed(asset, day, ts, action, conf, score) VALUES(?,?,?,?,?,?)",
            (asset, day, int(ts), action, float(conf), float(score)),
        )
        con.commit()
    finally:
        con.close()


def make_regime_mask(df: pd.DataFrame, bounds: dict[str, float]) -> np.ndarray:
    vol = df["f_vol48"].to_numpy(dtype=float)
    bb = df["f_bb_width20"].to_numpy(dtype=float)
    atr = df["f_atr14"].to_numpy(dtype=float)

    m = np.ones(len(df), dtype=bool)
    m &= vol >= float(bounds["vol_lo"])
    m &= vol <= float(bounds["vol_hi"])
    m &= bb >= float(bounds["bb_lo"])
    m &= bb <= float(bounds["bb_hi"])
    m &= atr >= float(bounds["atr_lo"])
    m &= atr <= float(bounds["atr_hi"])
    return m


def main() -> None:
    cfg, best = load_cfg()
    if not best:
        raise RuntimeError("Nao achei bloco 'best' em config.yaml. Rode o tune.")

    tz = ZoneInfo(cfg.get("data", {}).get("timezone", "UTC"))
    asset = cfg.get("data", {}).get("asset", "UNKNOWN")
    interval_sec = int(cfg.get("data", {}).get("interval_sec", 300))

    thr = float(best.get("threshold", 0.60))
    bounds = best.get("bounds") or {}
    tune_dir = str(best.get("tune_dir") or "")
    best_source = tune_dir or "unknown"

    # defaults (env tem prioridade)
    k_env = os.getenv("TOPK_K", "").strip()
    try:
        k = int(k_env) if k_env else int(best.get("k", 1))
    except Exception:
        k = 1
    if k < 1:
        k = 1

    thresh_on_env = os.getenv("THRESH_ON", "").strip()
    thresh_on = (thresh_on_env or str(best.get("thresh_on", "score"))).strip().lower()
    if thresh_on not in ("score", "conf"):
        thresh_on = "score"

    gate_env = os.getenv("GATE_MODE", "").strip()
    gate_mode = (gate_env or str(best.get("gate_mode", "meta"))).strip().lower()
    if gate_mode not in ("meta", "iso", "conf"):
        gate_mode = "meta"

    meta_model_type = os.getenv("META_MODEL", "logreg").strip().lower()
    if meta_model_type not in ("logreg", "hgb"):
        meta_model_type = "logreg"

    dataset_path = cfg.get("phase2", {}).get("dataset_path", "data/dataset_phase2.csv")
    if not Path(dataset_path).exists():
        dataset_path = "data/dataset_phase2.csv"

    df = pd.read_csv(dataset_path)
    if len(df) == 0:
        raise ValueError("Dataset vazio.")

    feat = [c for c in df.columns if c.startswith("f_")]
    for req in ("ts", "y_open_close", "f_vol48", "f_bb_width20", "f_atr14"):
        if req not in df.columns:
            raise ValueError(f"Dataset sem coluna obrigatoria: {req}")

    # candle atual
    last_ts = int(df["ts"].iloc[-1])
    last_dt = datetime.fromtimestamp(last_ts, tz=tz)
    day = last_dt.strftime("%Y-%m-%d")
    dt_local = last_dt.strftime("%Y-%m-%d %H:%M:%S")

    # treino: tudo menos tail (anti-peek)
    min_train_rows = int(os.getenv("MIN_TRAIN_ROWS", "3000"))
    tail_holdout = int(os.getenv("TRAIN_TAIL_HOLDOUT", "200"))
    cut = max(min_train_rows, len(df) - tail_holdout)
    train = df.iloc[:cut].copy()

    train_end_ts = int(train["ts"].iloc[-1])
    train_rows = int(len(train))
    fhash = feat_hash(feat)

    cache = load_cache(asset)
    meta = (cache or {}).get("meta") if cache else None

    if should_retrain(
        meta,
        train_end_ts=train_end_ts,
        best_source=best_source,
        fhash=fhash,
        interval_sec=interval_sec,
        meta_model_type=meta_model_type,
    ):
        cal, iso, meta_model = train_base_cal_iso_meta(
            train_df=train, feat_cols=feat, tz=tz, meta_model_type=meta_model_type
        )
        payload = {
            "cal": cal,
            "iso": iso,
            "meta_model": meta_model,
            "meta": {
                "asset": asset,
                "created_at": datetime.now(tz=tz).isoformat(timespec="seconds"),
                "train_rows": train_rows,
                "train_end_ts": train_end_ts,
                "best_source": best_source,
                "feat_hash": fhash,
                "gate_version": GATE_VERSION,
                "meta_model": meta_model_type,
                "model_version": get_model_version(),
            },
        }
        save_cache(asset, payload)
        cache = payload
        meta = payload["meta"]

    cal = cache["cal"]
    iso = cache.get("iso", None)
    meta_model = cache.get("meta_model", None)

    # dados do dia (até o último candle)
    dt_all = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df_day = df.loc[dt_all.dt.strftime("%Y-%m-%d") == day].copy()
    if len(df_day) == 0:
        raise ValueError("Sem dados no dia atual no dataset.")

    proba, conf, score, gate_used = compute_scores(
        df=df_day,
        feat_cols=feat,
        tz=tz,
        cal_model=cal,
        iso=iso,
        meta_model=meta_model,
        gate_mode=gate_mode,
    )

    mask = make_regime_mask(df_day, bounds) if bounds else np.ones(len(df_day), dtype=bool)
    metric = score if thresh_on == "score" else conf
    cand = mask & (metric >= thr)

    idx = np.arange(len(df_day))
    order = idx[np.argsort(-score)]
    sel = order[cand[order]]
    topk = sel[:k]

    now_i = len(df_day) - 1
    in_topk = bool(now_i in set(topk.tolist()))
    rank_in_day = int(np.where(topk == now_i)[0][0] + 1) if in_topk else -1

    executed_today = executed_today_count(asset, day)

    action = "HOLD"
    reason = "ok"

    if not bool(mask[now_i]):
        reason = "regime_block"
    elif float(metric[now_i]) < thr:
        reason = "below_score_threshold" if thresh_on == "score" else "below_conf_threshold"
    elif executed_today >= k:
        reason = "max_k_reached"
    elif not in_topk:
        reason = "not_in_topk_today"
    elif already_executed(asset, day, last_ts):
        reason = "already_emitted_for_ts"
    else:
        action = "CALL" if float(proba[now_i]) >= 0.5 else "PUT"
        reason = "topk_emit"
        mark_executed(asset, day, last_ts, action, float(conf[now_i]), float(score[now_i]))
        executed_today = executed_today_count(asset, day)

    payout = float(os.getenv("PAYOUT", "0.8"))
    ev = float(score[now_i]) * payout - (1.0 - float(score[now_i])) if action != "HOLD" else 0.0

    row = {
        "dt_local": dt_local,
        "day": day,
        "ts": int(last_ts),
        "proba_up": float(proba[now_i]),
        "conf": float(conf[now_i]),
        "score": float(score[now_i]),
        "gate_mode": gate_used,
        "thresh_on": thresh_on,
        "threshold": float(thr),
        "k": int(k),
        "rank_in_day": int(rank_in_day),
        "executed_today": int(executed_today),
        "action": action,
        "reason": reason,
        "close": float(df_day["close"].iloc[now_i]) if "close" in df_day.columns else None,
        "payout": float(payout),
        "ev": float(ev),
        "asset": asset,
        "model_version": str(meta.get("model_version") if meta else get_model_version()),
        "train_rows": int(meta.get("train_rows") if meta else train_rows),
        "train_end_ts": int(meta.get("train_end_ts") if meta else train_end_ts),
        "best_source": str(meta.get("best_source") if meta else best_source),
        "tune_dir": tune_dir,
        "feat_hash": fhash,
        "gate_version": GATE_VERSION,
        "meta_model": meta_model_type,
    }

    out_csv = append_csv(row)
    write_sqlite_signal(row)

    print("=== OBSERVE TOPK-PERDAY (latest) ===")
    print(
        {
            "dt_local": row["dt_local"],
            "day": row["day"],
            "ts": row["ts"],
            "proba_up": row["proba_up"],
            "conf": row["conf"],
            "score": row["score"],
            "gate_mode": row["gate_mode"],
            "thresh_on": row["thresh_on"],
            "threshold": row["threshold"],
            "k": row["k"],
            "rank_in_day": row["rank_in_day"],
            "executed_today": row["executed_today"],
            "action": row["action"],
            "reason": row["reason"],
        }
    )
    print(f"csv_ok: {out_csv}")
    print("sqlite_ok: runs/live_signals.sqlite3 (signals_v2)")


if __name__ == "__main__":
    main()
'@

# ==========================================
# tune_multiwindow_topk.py (TUNER ONLINE)
# ==========================================
Write-Utf8NoBomFile -Path "src\natbin\tune_multiwindow_topk.py" -Content @'
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from .gate_meta import GATE_VERSION, compute_scores, train_base_cal_iso_meta


@dataclass
class WindowPack:
    start_day: str
    end_day: str
    day: np.ndarray
    ts: np.ndarray
    conf: np.ndarray
    score: np.ndarray
    correct: np.ndarray
    vol: np.ndarray
    bb: np.ndarray
    atr: np.ndarray
    idx_by_day_chrono: dict[str, np.ndarray]


def load_cfg(path: str = "config.yaml") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Não achei {path}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def dump_cfg(cfg: dict[str, Any], path: str = "config.yaml") -> None:
    Path(path).write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


def make_mask_arrays(vol: np.ndarray, bb: np.ndarray, atr: np.ndarray, bounds: dict[str, float]) -> np.ndarray:
    m = np.ones(len(vol), dtype=bool)
    m &= vol >= bounds["vol_lo"]
    m &= vol <= bounds["vol_hi"]
    m &= bb >= bounds["bb_lo"]
    m &= bb <= bounds["bb_hi"]
    m &= atr >= bounds["atr_lo"]
    m &= atr <= bounds["atr_hi"]
    return m


def quant_bounds(global_train: pd.DataFrame) -> list[dict[str, float]]:
    vlo_q = [0.10, 0.20, 0.30]
    vhi_q = [0.85, 0.90, 0.95]
    bb_lo_q = [0.10, 0.20]
    bb_hi_q = [0.80, 0.90]
    atr_lo_q = [0.10, 0.20]
    atr_hi_q = [0.80, 0.90]

    vol = global_train["f_vol48"].to_numpy(dtype=float)
    bb = global_train["f_bb_width20"].to_numpy(dtype=float)
    atr = global_train["f_atr14"].to_numpy(dtype=float)

    out: list[dict[str, float]] = []
    for vlo in vlo_q:
        for vhi in vhi_q:
            for bblo in bb_lo_q:
                for bbhi in bb_hi_q:
                    for atro in atr_lo_q:
                        for atrhi in atr_hi_q:
                            b = {
                                "vol_lo": float(np.nanquantile(vol, vlo)),
                                "vol_hi": float(np.nanquantile(vol, vhi)),
                                "bb_lo": float(np.nanquantile(bb, bblo)),
                                "bb_hi": float(np.nanquantile(bb, bbhi)),
                                "atr_lo": float(np.nanquantile(atr, atro)),
                                "atr_hi": float(np.nanquantile(atr, atrhi)),
                            }
                            if b["vol_lo"] > b["vol_hi"] or b["bb_lo"] > b["bb_hi"] or b["atr_lo"] > b["atr_hi"]:
                                continue
                            out.append(b)
    return out


def build_windows(df: pd.DataFrame, tz: ZoneInfo, windows: int, window_days: int) -> tuple[list[list[str]], str]:
    dt_local = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df = df.copy()
    df["day"] = dt_local.dt.strftime("%Y-%m-%d")
    days = sorted(df["day"].unique().tolist())

    need = windows * window_days
    if len(days) < window_days * 2:
        raise ValueError(f"Poucos dias no dataset ({len(days)}).")

    if len(days) < need:
        windows = max(1, len(days) // window_days)
        need = windows * window_days

    eval_days = days[-need:]
    win_days: list[list[str]] = []
    for i in range(windows):
        chunk = eval_days[i * window_days : (i + 1) * window_days]
        if len(chunk) == window_days:
            win_days.append(chunk)

    if not win_days:
        raise ValueError("Não consegui montar janelas.")
    return win_days, win_days[0][0]


def simulate_online_topk(score: np.ndarray, cand: np.ndarray, correct: np.ndarray, idx_day: np.ndarray, k: int) -> tuple[int, int]:
    top: list[tuple[float, int]] = []
    executed = 0
    taken = 0
    corr = 0

    for i in idx_day:
        if cand[i]:
            top.append((float(score[i]), int(i)))
            top.sort(key=lambda x: x[0], reverse=True)
            if len(top) > k:
                top = top[:k]

        in_top = any(j == int(i) for _, j in top)
        if in_top and executed < k:
            executed += 1
            taken += 1
            corr += int(correct[i])

    return taken, corr


def pack_window(df_all: pd.DataFrame, feat: list[str], tz: ZoneInfo, win_days: list[str], gate_mode: str, meta_model: str) -> WindowPack:
    df_all = df_all.copy()
    dt_local = pd.to_datetime(df_all["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df_all["day"] = dt_local.dt.strftime("%Y-%m-%d")

    start_day = win_days[0]
    end_day = win_days[-1]

    train_df = df_all[df_all["day"] < start_day]
    test_df = df_all[df_all["day"].isin(win_days)].copy()
    if len(test_df) == 0:
        raise ValueError("Janela sem dados.")
    if len(train_df) < 800:
        raise ValueError(f"Treino insuficiente antes da janela {start_day} (n={len(train_df)}).")

    test_df = test_df.sort_values("ts").reset_index(drop=True)

    cal, iso, meta = train_base_cal_iso_meta(train_df=train_df, feat_cols=feat, tz=tz, meta_model_type=meta_model)

    proba, conf, score, gate_used = compute_scores(
        df=test_df,
        feat_cols=feat,
        tz=tz,
        cal_model=cal,
        iso=iso,
        meta_model=meta,
        gate_mode=gate_mode,
    )

    y = test_df["y_open_close"].to_numpy(dtype=int)
    pred = (proba >= 0.5).astype(int)
    correct = (pred == y).astype(int)

    day = test_df["day"].to_numpy(dtype=str)
    ts = test_df["ts"].to_numpy(dtype=int)

    vol = test_df["f_vol48"].to_numpy(dtype=float)
    bb = test_df["f_bb_width20"].to_numpy(dtype=float)
    atr = test_df["f_atr14"].to_numpy(dtype=float)

    idx_by_day: dict[str, np.ndarray] = {}
    for d in sorted(set(day.tolist())):
        idx = np.where(day == d)[0]
        if idx.size == 0:
            continue
        idx = idx[np.argsort(ts[idx])]
        idx_by_day[d] = idx

    return WindowPack(
        start_day=start_day,
        end_day=end_day,
        day=day,
        ts=ts,
        conf=conf,
        score=score,
        correct=correct,
        vol=vol,
        bb=bb,
        atr=atr,
        idx_by_day_chrono=idx_by_day,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--windows", type=int, default=2)
    ap.add_argument("--window-days", type=int, default=60)
    ap.add_argument("--min-total-trades", type=int, default=20)
    ap.add_argument("--min-trades-per-window", type=int, default=5)
    ap.add_argument("--gate-mode", type=str, default="meta", choices=["meta", "iso", "conf"])
    ap.add_argument("--meta-model", type=str, default="logreg", choices=["logreg", "hgb"])
    ap.add_argument("--thresh-on", type=str, default="score", choices=["score", "conf"])
    ap.add_argument("--update-config", action="store_true")
    ap.add_argument("--config", type=str, default="config.yaml")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    tzname = cfg.get("data", {}).get("timezone", "UTC")
    tz = ZoneInfo(tzname)

    dataset_path = cfg.get("phase2", {}).get("dataset_path", "data/dataset_phase2.csv")
    if not Path(dataset_path).exists():
        dataset_path = "data/dataset_phase2.csv"

    df = pd.read_csv(dataset_path)
    if len(df) == 0:
        raise ValueError("Dataset vazio.")

    feat = [c for c in df.columns if c.startswith("f_")]
    for req in ["f_vol48", "f_bb_width20", "f_atr14", "ts", "y_open_close"]:
        if req not in df.columns:
            raise ValueError(f"Dataset sem coluna obrigatória: {req}")

    tmin = float(cfg.get("phase2", {}).get("threshold_min", 0.55))
    tmax = float(cfg.get("phase2", {}).get("threshold_max", 0.85))
    tstep = float(cfg.get("phase2", {}).get("threshold_step", 0.01))
    thresholds = np.round(np.arange(tmin, tmax + 1e-9, tstep), 2)

    win_days_list, first_eval_day = build_windows(df, tz, args.windows, args.window_days)

    dt_local = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df = df.copy()
    df["day"] = dt_local.dt.strftime("%Y-%m-%d")
    global_train = df[df["day"] < first_eval_day]
    if len(global_train) < 1000:
        global_train = df.iloc[: max(1000, int(len(df) * 0.6))]

    bounds_list = quant_bounds(global_train)

    packs: list[WindowPack] = []
    for wd in win_days_list:
        packs.append(pack_window(df, feat, tz, wd, gate_mode=args.gate_mode, meta_model=args.meta_model))

    run_dir = Path("runs") / f"tune_mw_topk_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    best: dict[str, Any] | None = None
    best_key: tuple[float, float, float] | None = None
    rows: list[dict[str, Any]] = []

    for b in bounds_list:
        for thr in thresholds:
            total_taken = 0
            total_corr = 0
            total_test = 0
            ok = True
            min_hit = 1.0
            perw: list[dict[str, Any]] = []

            for p in packs:
                mask = make_mask_arrays(p.vol, p.bb, p.atr, b)
                metric = p.score if args.thresh_on == "score" else p.conf
                cand = mask & (metric >= thr)

                taken_w = 0
                corr_w = 0
                for d, idx_day in p.idx_by_day_chrono.items():
                    t, c = simulate_online_topk(p.score, cand, p.correct, idx_day, args.k)
                    taken_w += t
                    corr_w += c

                hit_w = (corr_w / taken_w) if taken_w else 0.0
                perw.append({"start_day": p.start_day, "end_day": p.end_day, "taken": taken_w, "hit": hit_w})

                total_taken += taken_w
                total_corr += corr_w
                total_test += len(p.score)

                if taken_w < args.min_trades_per_window:
                    ok = False
                    min_hit = 0.0
                else:
                    min_hit = min(min_hit, hit_w)

            if total_taken < args.min_total_trades:
                ok = False
                min_hit = 0.0

            hit = (total_corr / total_taken) if total_taken else 0.0
            cov = (total_taken / total_test) if total_test else 0.0

            row = {
                "gate_mode": args.gate_mode,
                "meta_model": args.meta_model,
                "thresh_on": args.thresh_on,
                "threshold": float(thr),
                **{k: float(v) for k, v in b.items()},
                "windows": len(packs),
                "window_days": args.window_days,
                "k": args.k,
                "topk_taken_total": int(total_taken),
                "topk_hit_weighted": float(hit),
                "topk_cov_total": float(cov),
                "min_window_hit": float(min_hit),
                "ok": int(ok),
                "per_window": json.dumps(perw, ensure_ascii=False),
            }
            rows.append(row)

            if ok:
                key = (min_hit, hit, -float(total_taken))
                if best_key is None or key > best_key:
                    best_key = key
                    best = {
                        "gate_mode": args.gate_mode,
                        "meta_model": args.meta_model,
                        "thresh_on": args.thresh_on,
                        "threshold": float(thr),
                        "bounds": {k: float(v) for k, v in b.items()},
                        "k": int(args.k),
                        "windows": int(len(packs)),
                        "window_days": int(args.window_days),
                        "topk_taken_total": int(total_taken),
                        "topk_hit_weighted": float(hit),
                        "topk_cov_total": float(cov),
                        "min_window_hit": float(min_hit),
                        "per_window": perw,
                    }

    pd.DataFrame(rows).to_csv(run_dir / "grid_mw_topk.csv", index=False)
    (run_dir / "summary.json").write_text(
        json.dumps({"run_dir": str(run_dir).replace("\\", "/"), "best": best}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("=== TUNE MW TOPK (pseudo-futuro, ONLINE, P2.2 META) ===")
    if not best:
        print("Nenhuma config passou nos mínimos. Veja grid_mw_topk.csv.")
        return

    print(json.dumps(best, indent=2, ensure_ascii=False))
    print(f"Saved: {run_dir}")

    if args.update_config:
        cfg2 = load_cfg(args.config)
        cfg2["best"] = {
            "tune_dir": str(run_dir).replace("\\", "/"),
            "threshold": float(best["threshold"]),
            "bounds": best["bounds"],
            "k": int(best["k"]),
            "thresh_on": str(best["thresh_on"]),
            "gate_mode": str(best["gate_mode"]),
            "meta_model": str(best["meta_model"]),
            "gate_version": GATE_VERSION,
            "notes": f"Frozen from ONLINE tune_mw_topk (P2.2 meta gate, gate={best['gate_mode']}, meta={best['meta_model']}).",
        }
        dump_cfg(cfg2, args.config)
        print("config.yaml atualizado com bloco best (ONLINE, P2.2).")


if __name__ == "__main__":
    main()
'@

# =====================================
# paper_pnl_backtest.py (PAPER ONLINE)
# =====================================
Write-Utf8NoBomFile -Path "src\natbin\paper_pnl_backtest.py" -Content @'
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from .gate_meta import compute_scores, train_base_cal_iso_meta


def load_cfg(path: str = "config.yaml") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Não achei {path}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def make_mask(df: pd.DataFrame, bounds: dict[str, float]) -> np.ndarray:
    m = np.ones(len(df), dtype=bool)
    m &= df["f_vol48"].to_numpy(dtype=float) >= bounds["vol_lo"]
    m &= df["f_vol48"].to_numpy(dtype=float) <= bounds["vol_hi"]
    m &= df["f_bb_width20"].to_numpy(dtype=float) >= bounds["bb_lo"]
    m &= df["f_bb_width20"].to_numpy(dtype=float) <= bounds["bb_hi"]
    m &= df["f_atr14"].to_numpy(dtype=float) >= bounds["atr_lo"]
    m &= df["f_atr14"].to_numpy(dtype=float) <= bounds["atr_hi"]
    return m


def simulate_online_day(score: np.ndarray, cand: np.ndarray, correct: np.ndarray, idx_day: np.ndarray, k: int) -> tuple[int, int]:
    top: list[tuple[float, int]] = []
    executed = 0
    taken = 0
    won = 0

    for i in idx_day:
        if cand[i]:
            top.append((float(score[i]), int(i)))
            top.sort(key=lambda x: x[0], reverse=True)
            if len(top) > k:
                top = top[:k]

        in_top = any(j == int(i) for _, j in top)
        if in_top and executed < k:
            executed += 1
            taken += 1
            won += int(correct[i])

    return taken, won


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--holdout-days", type=int, default=60)
    ap.add_argument("--payout", type=float, default=0.8)
    ap.add_argument("--thresh-on", type=str, default="score", choices=["score", "conf"])
    ap.add_argument("--gate-mode", type=str, default="meta", choices=["meta", "iso", "conf"])
    ap.add_argument("--meta-model", type=str, default="logreg", choices=["logreg", "hgb"])
    ap.add_argument("--config", type=str, default="config.yaml")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    best = cfg.get("best") or {}
    if not best:
        raise RuntimeError("Sem bloco best no config.yaml. Rode o tune (mw) com --update-config.")

    thr = float(best.get("threshold", 0.60))
    bounds = best.get("bounds") or {}
    tzname = cfg.get("data", {}).get("timezone", "UTC")
    tz = ZoneInfo(tzname)

    dataset_path = cfg.get("phase2", {}).get("dataset_path", "data/dataset_phase2.csv")
    if not Path(dataset_path).exists():
        dataset_path = "data/dataset_phase2.csv"

    df = pd.read_csv(dataset_path)
    if len(df) == 0:
        raise ValueError("Dataset vazio.")
    df = df.sort_values("ts").reset_index(drop=True)

    feat = [c for c in df.columns if c.startswith("f_")]
    dt_local = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df = df.copy()
    df["day"] = dt_local.dt.strftime("%Y-%m-%d")

    days = sorted(df["day"].unique().tolist())
    hold_days = days[-args.holdout_days:]
    train_df = df[df["day"] < hold_days[0]]
    test_df = df[df["day"].isin(hold_days)].copy()

    cal, iso, meta = train_base_cal_iso_meta(train_df=train_df, feat_cols=feat, tz=tz, meta_model_type=args.meta_model)

    proba, conf, score, gate_used = compute_scores(
        df=test_df,
        feat_cols=feat,
        tz=tz,
        cal_model=cal,
        iso=iso,
        meta_model=meta,
        gate_mode=args.gate_mode,
    )

    y = test_df["y_open_close"].to_numpy(dtype=int)
    pred = (proba >= 0.5).astype(int)
    correct = (pred == y).astype(int)

    metric = score if args.thresh_on == "score" else conf
    mask = make_mask(test_df, bounds) if bounds else np.ones(len(test_df), dtype=bool)
    cand = mask & (metric >= thr)

    day_arr = test_df["day"].to_numpy(dtype=str)
    ts_arr = test_df["ts"].to_numpy(dtype=int)

    taken = 0
    won = 0
    pnl = 0.0

    for d in hold_days:
        idx = np.where(day_arr == d)[0]
        if idx.size == 0:
            continue
        idx = idx[np.argsort(ts_arr[idx])]
        t, w = simulate_online_day(score, cand, correct, idx, args.k)
        if t == 0:
            continue
        taken += t
        won += w
        pnl += w * args.payout - (t - w) * 1.0

    hit = (won / taken) if taken else 0.0
    be = 1.0 / (1.0 + args.payout)

    print("=== PNL PAPER (TOPK PER DAY, ONLINE, P2.2) ===")
    print(f"days={args.holdout_days} k={args.k} payout={args.payout}")
    print(f"break_even={be:.4f}")
    print(f"gate_mode={gate_used} thresh_on={args.thresh_on} threshold={thr:.2f}")
    print(f"taken={taken} won={won} hit={hit:.4f}")
    print(f"pnl={pnl:.2f} (em unidades de 1 stake)")
    print(f"run_at={datetime.now().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
'@

# Compile
& $py -m compileall .\src\natbin
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: P2.2 aplicado (meta-modelo de seletividade)." -ForegroundColor Green
Write-Host ""
Write-Host "PROXIMOS PASSOS:" -ForegroundColor Yellow
Write-Host "1) (Opcional) Ajuste o grid de score para buscar em 0.55..0.85" -ForegroundColor Yellow
Write-Host "2) Rode o tuner ONLINE (P2.2) e congele no config:" -ForegroundColor Yellow
Write-Host "   .\.venv\Scripts\python.exe -m natbin.tune_multiwindow_topk --k 1 --windows 2 --window-days 60 --gate-mode meta --meta-model logreg --thresh-on score --min-total-trades 20 --min-trades-per-window 5 --update-config" -ForegroundColor Yellow
Write-Host "3) Paper ONLINE:" -ForegroundColor Yellow
Write-Host "   .\.venv\Scripts\python.exe -m natbin.paper_pnl_backtest --k 1 --holdout-days 60 --payout 0.8 --gate-mode meta --meta-model logreg --thresh-on score" -ForegroundColor Yellow
Write-Host "4) Observe Once:" -ForegroundColor Yellow
Write-Host "   pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once" -ForegroundColor Yellow
Write-Host ""
Write-Host "Dica: defaults do live agora sao GATE_MODE=meta, META_MODEL=logreg, THRESH_ON=score." -ForegroundColor DarkGray