param(
  [switch]$RunTune,
  [int]$K = 2,
  [int]$Windows = 6,
  [int]$WindowDays = 20
)

$ErrorActionPreference = "Stop"

function Write-Utf8NoBomFile {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Content
  )
  $dir = Split-Path -Parent $Path
  if ($dir -and !(Test-Path $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Resolve-Python {
  $local = Join-Path (Get-Location) ".venv\Scripts\python.exe"
  if (Test-Path $local) { return $local }
  return "python"
}

$py = Resolve-Python
Write-Host "== P1/P2 UPGRADE ==" -ForegroundColor Cyan
Write-Host "Python: $py"

# ---------------------------
# 1) tune_multiwindow_topk.py
# ---------------------------
$TuneMw = @'
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split


@dataclass
class WindowPack:
    start_day: str
    end_day: str
    day: np.ndarray
    y: np.ndarray
    conf: np.ndarray
    correct: np.ndarray
    vol: np.ndarray
    bb: np.ndarray
    atr: np.ndarray
    sorted_idx_by_day: dict[str, np.ndarray]


def load_cfg(path: str = "config.yaml") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Não achei {path} na pasta atual.")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def dump_cfg(cfg: dict[str, Any], path: str = "config.yaml") -> None:
    # escreve YAML “bonito” (multi-linha)
    Path(path).write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


def train_calibrated_hgb(X_train: pd.DataFrame, y_train: pd.Series) -> CalibratedClassifierCV:
    if len(X_train) < 200:
        raise ValueError(f"Treino muito pequeno (n={len(X_train)}).")
    # calibração sequencial (sem shuffle)
    X_sub, X_cal, y_sub, y_cal = train_test_split(
        X_train, y_train, test_size=0.2, shuffle=False
    )
    base = HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.05,
        max_iter=300,
        random_state=0,
    )
    base.fit(X_sub, y_sub)
    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(X_cal, y_cal)
    return cal


def make_mask_arrays(vol: np.ndarray, bb: np.ndarray, atr: np.ndarray, bounds: dict[str, float]) -> np.ndarray:
    m = np.ones(len(vol), dtype=bool)
    if bounds.get("vol_lo") is not None:
        m &= vol >= bounds["vol_lo"]
    if bounds.get("vol_hi") is not None:
        m &= vol <= bounds["vol_hi"]
    if bounds.get("bb_lo") is not None:
        m &= bb >= bounds["bb_lo"]
    if bounds.get("bb_hi") is not None:
        m &= bb <= bounds["bb_hi"]
    if bounds.get("atr_lo") is not None:
        m &= atr >= bounds["atr_lo"]
    if bounds.get("atr_hi") is not None:
        m &= atr <= bounds["atr_hi"]
    return m


def quant_bounds(global_train: pd.DataFrame) -> list[dict[str, float]]:
    # grids iguais ao “estilo” do tune_v2
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
                            # sanidade: lo <= hi
                            if b["vol_lo"] > b["vol_hi"]:
                                continue
                            if b["bb_lo"] > b["bb_hi"]:
                                continue
                            if b["atr_lo"] > b["atr_hi"]:
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
        raise ValueError("Não consegui montar janelas. Ajuste --windows/--window-days.")

    return win_days, win_days[0][0]


def pack_window(df_all: pd.DataFrame, feat: list[str], tz: ZoneInfo, win_days: list[str]) -> WindowPack:
    df_all = df_all.copy()
    dt_local = pd.to_datetime(df_all["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df_all["day"] = dt_local.dt.strftime("%Y-%m-%d")

    start_day = win_days[0]
    end_day = win_days[-1]

    train_df = df_all[df_all["day"] < start_day]
    test_df = df_all[df_all["day"].isin(win_days)]

    if len(test_df) == 0:
        raise ValueError("Janela sem dados.")
    if len(train_df) < 500:
        raise ValueError(f"Treino insuficiente antes da janela {start_day} (n={len(train_df)}).")

    model = train_calibrated_hgb(train_df[feat], train_df["y_open_close"])
    proba = model.predict_proba(test_df[feat])[:, 1]
    pred = (proba >= 0.5).astype(int)
    y = test_df["y_open_close"].to_numpy(dtype=int)
    correct = (pred == y).astype(int)
    conf = np.maximum(proba, 1.0 - proba)

    day = test_df["day"].to_numpy(dtype=str)

    vol = test_df["f_vol48"].to_numpy(dtype=float)
    bb = test_df["f_bb_width20"].to_numpy(dtype=float)
    atr = test_df["f_atr14"].to_numpy(dtype=float)

    sorted_idx_by_day: dict[str, np.ndarray] = {}
    for d in sorted(set(day.tolist())):
        idx = np.where(day == d)[0]
        if idx.size == 0:
            continue
        order = idx[np.argsort(-conf[idx])]
        sorted_idx_by_day[d] = order

    return WindowPack(
        start_day=start_day,
        end_day=end_day,
        day=day,
        y=y,
        conf=conf,
        correct=correct,
        vol=vol,
        bb=bb,
        atr=atr,
        sorted_idx_by_day=sorted_idx_by_day,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=int(Path().joinpath(".").exists() or 2))
    ap.add_argument("--windows", type=int, default=6)
    ap.add_argument("--window-days", type=int, default=20)
    ap.add_argument("--min-total-trades", type=int, default=50)
    ap.add_argument("--min-trades-per-window", type=int, default=6)
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

    # features
    feat = [c for c in df.columns if c.startswith("f_")]
    if not feat:
        raise ValueError("Não achei colunas f_* no dataset.")

    # required columns for bounds
    for req in ["f_vol48", "f_bb_width20", "f_atr14", "ts", "y_open_close"]:
        if req not in df.columns:
            raise ValueError(f"Dataset sem coluna obrigatória: {req}")

    # thresholds
    tmin = float(cfg.get("phase2", {}).get("threshold_min", 0.52))
    tmax = float(cfg.get("phase2", {}).get("threshold_max", 0.75))
    tstep = float(cfg.get("phase2", {}).get("threshold_step", 0.01))
    thresholds = np.round(np.arange(tmin, tmax + 1e-9, tstep), 2)

    win_days_list, first_eval_day = build_windows(df, tz, args.windows, args.window_days)

    # treino global p/ quantis dos bounds (antes do primeiro eval day)
    dt_local = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df = df.copy()
    df["day"] = dt_local.dt.strftime("%Y-%m-%d")
    global_train = df[df["day"] < first_eval_day]
    if len(global_train) < 1000:
        # fallback: primeira parte do dataset
        cut = max(1000, int(len(df) * 0.6))
        global_train = df.iloc[:cut]

    bounds_list = quant_bounds(global_train)

    # prepack windows (1 treino por janela)
    packs: list[WindowPack] = []
    for win_days in win_days_list:
        packs.append(pack_window(df, feat, tz, win_days))

    run_dir = Path("runs") / f"tune_mw_topk_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    best: dict[str, Any] | None = None
    best_key: tuple[float, float, float] | None = None

    rows: list[dict[str, Any]] = []

    for b in bounds_list:
        for thr in thresholds:
            total_taken = 0
            total_correct = 0
            total_test = 0

            perw = []
            ok = True
            min_hit = 1.0

            for p in packs:
                mask = make_mask_arrays(p.vol, p.bb, p.atr, b)
                cand = mask & (p.conf >= thr)

                taken = 0
                corr = 0
                for d, order in p.sorted_idx_by_day.items():
                    sel = order[cand[order]]
                    if sel.size == 0:
                        continue
                    take = sel[: args.k]
                    taken += int(take.size)
                    corr += int(p.correct[take].sum())

                test_n = len(p.conf)
                hit = (corr / taken) if taken else 0.0

                perw.append({
                    "start_day": p.start_day,
                    "end_day": p.end_day,
                    "taken": taken,
                    "hit": hit,
                })

                total_taken += taken
                total_correct += corr
                total_test += test_n

                if taken < args.min_trades_per_window:
                    ok = False
                    min_hit = 0.0
                else:
                    min_hit = min(min_hit, hit)

            if total_taken < args.min_total_trades:
                ok = False
                min_hit = 0.0

            hit_w = (total_correct / total_taken) if total_taken else 0.0
            cov = (total_taken / total_test) if total_test else 0.0

            row = {
                "threshold": float(thr),
                **{k: float(v) for k, v in b.items()},
                "windows": len(packs),
                "window_days": args.window_days,
                "k": args.k,
                "topk_taken_total": int(total_taken),
                "topk_hit_weighted": float(hit_w),
                "topk_cov_total": float(cov),
                "min_window_hit": float(min_hit),
                "ok": int(ok),
                "per_window": json.dumps(perw, ensure_ascii=False),
            }
            rows.append(row)

            if ok:
                key = (min_hit, hit_w, -float(total_taken))
                if best_key is None or key > best_key:
                    best_key = key
                    best = {
                        "threshold": float(thr),
                        "bounds": {k: float(v) for k, v in b.items()},
                        "k": int(args.k),
                        "windows": int(len(packs)),
                        "window_days": int(args.window_days),
                        "topk_taken_total": int(total_taken),
                        "topk_hit_weighted": float(hit_w),
                        "topk_cov_total": float(cov),
                        "min_window_hit": float(min_hit),
                        "per_window": perw,
                    }

    grid_path = run_dir / "grid_mw_topk.csv"
    pd.DataFrame(rows).to_csv(grid_path, index=False)

    summary = {
        "run_dir": str(run_dir).replace("\\", "/"),
        "dataset_path": str(dataset_path).replace("\\", "/"),
        "timezone": tzname,
        "threshold_grid": {"min": tmin, "max": tmax, "step": tstep},
        "best": best,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== TUNE MW TOPK (pseudo-futuro) ===")
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
            "notes": f"Frozen from tune_mw_topk (windows={best['windows']}, window_days={best['window_days']}, k={best['k']}, score=min_window_hit).",
        }
        dump_cfg(cfg2, args.config)
        print("config.yaml atualizado com bloco best (multiwindow).")


if __name__ == "__main__":
    main()
'@

Write-Utf8NoBomFile "src\natbin\tune_multiwindow_topk.py" $TuneMw
Write-Host "OK: src\natbin\tune_multiwindow_topk.py" -ForegroundColor Green

# ---------------------------
# 2) paper_pnl_backtest.py (opcional, mas útil)
# ---------------------------
$PaperPnl = @'
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split


def load_cfg(path: str = "config.yaml") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Não achei {path}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def train_calibrated_hgb(X_train: pd.DataFrame, y_train: pd.Series) -> CalibratedClassifierCV:
    X_sub, X_cal, y_sub, y_cal = train_test_split(X_train, y_train, test_size=0.2, shuffle=False)
    base = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, random_state=0)
    base.fit(X_sub, y_sub)
    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(X_cal, y_cal)
    return cal


def make_mask(df: pd.DataFrame, bounds: dict[str, float]) -> np.ndarray:
    m = np.ones(len(df), dtype=bool)
    m &= df["f_vol48"].to_numpy(dtype=float) >= bounds["vol_lo"]
    m &= df["f_vol48"].to_numpy(dtype=float) <= bounds["vol_hi"]
    m &= df["f_bb_width20"].to_numpy(dtype=float) >= bounds["bb_lo"]
    m &= df["f_bb_width20"].to_numpy(dtype=float) <= bounds["bb_hi"]
    m &= df["f_atr14"].to_numpy(dtype=float) >= bounds["atr_lo"]
    m &= df["f_atr14"].to_numpy(dtype=float) <= bounds["atr_hi"]
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--holdout-days", type=int, default=20)
    ap.add_argument("--payout", type=float, default=0.8)
    ap.add_argument("--config", type=str, default="config.yaml")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    best = cfg.get("best") or {}
    if not best:
        raise RuntimeError("Sem bloco best no config.yaml. Rode o tune (mw) com --update-config.")

    thr = float(best["threshold"])
    bounds = best["bounds"]

    tzname = cfg.get("data", {}).get("timezone", "UTC")
    tz = ZoneInfo(tzname)

    dataset_path = cfg.get("phase2", {}).get("dataset_path", "data/dataset_phase2.csv")
    if not Path(dataset_path).exists():
        dataset_path = "data/dataset_phase2.csv"

    df = pd.read_csv(dataset_path)
    feat = [c for c in df.columns if c.startswith("f_")]
    dt_local = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df = df.copy()
    df["day"] = dt_local.dt.strftime("%Y-%m-%d")

    days = sorted(df["day"].unique().tolist())
    hold_days = days[-args.holdout_days:]
    train_df = df[df["day"] < hold_days[0]]
    test_df = df[df["day"].isin(hold_days)]

    model = train_calibrated_hgb(train_df[feat], train_df["y_open_close"])
    proba = model.predict_proba(test_df[feat])[:, 1]
    pred = (proba >= 0.5).astype(int)
    y = test_df["y_open_close"].to_numpy(dtype=int)
    correct = (pred == y).astype(int)
    conf = np.maximum(proba, 1.0 - proba)

    mask = make_mask(test_df, bounds)
    cand = mask & (conf >= thr)

    # TOPK por dia
    taken = 0
    won = 0
    pnl = 0.0

    for d in hold_days:
        idx = np.where(test_df["day"].to_numpy(dtype=str) == d)[0]
        if idx.size == 0:
            continue
        order = idx[np.argsort(-conf[idx])]
        sel = order[cand[order]]
        take = sel[: args.k]
        if take.size == 0:
            continue
        taken += int(take.size)
        w = int(correct[take].sum())
        won += w
        pnl += w * args.payout - (int(take.size) - w) * 1.0

    hit = (won / taken) if taken else 0.0
    print("=== PNL PAPER (TOPK PER DAY) ===")
    print(f"days={args.holdout_days} k={args.k} payout={args.payout}")
    print(f"threshold(conf)={thr:.2f}")
    print(f"taken={taken} won={won} hit={hit:.4f}")
    print(f"pnl={pnl:.2f} (em unidades de 1 stake)")
    print(f"run_at={datetime.now().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
'@

Write-Utf8NoBomFile "src\natbin\paper_pnl_backtest.py" $PaperPnl
Write-Host "OK: src\natbin\paper_pnl_backtest.py" -ForegroundColor Green

# --------------------------------
# 3) observe_signal_topk_perday.py (cache + iso gate)
# --------------------------------
$Observe = @'
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

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split


BASE_FIELDS = [
    "dt_local",
    "day",
    "ts",
    "proba_up",
    "conf",
    "score",
    "gate_mode",
    "regime_ok",
    "threshold",
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
]

ALL_FIELDS = BASE_FIELDS + META_FIELDS


def load_cfg() -> tuple[dict[str, Any], dict[str, Any]]:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    best = cfg.get("best") or {}
    return cfg, best


def get_model_version() -> str:
    # tenta pegar sha do git (se existir)
    try:
        import subprocess

        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:
        return "unknown"


def train_model_and_iso(X_train: pd.DataFrame, y_train: pd.Series) -> tuple[CalibratedClassifierCV, IsotonicRegression | None]:
    if len(X_train) < 200:
        raise ValueError(f"Treino muito pequeno (n={len(X_train)}).")

    X_sub, X_cal, y_sub, y_cal = train_test_split(X_train, y_train, test_size=0.2, shuffle=False)

    base = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, random_state=0)
    base.fit(X_sub, y_sub)

    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(X_cal, y_cal)

    # Iso gate: mapeia conf -> prob(acertar direção)
    try:
        p = cal.predict_proba(X_cal)[:, 1]
        pred = (p >= 0.5).astype(int)
        conf = np.maximum(p, 1.0 - p)
        correct = (pred == y_cal.to_numpy(dtype=int)).astype(int)
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(conf.astype(float), correct.astype(float))
        return cal, iso
    except Exception:
        return cal, None


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
    # separa meta p/ leitura fácil
    m = payload.get("meta") or {}
    meta.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    payload2 = dict(payload)
    payload2.pop("meta", None)
    pkl.write_bytes(pickle.dumps(payload2))


def should_retrain(meta: dict[str, Any] | None, *, train_end_ts: int, best_source: str, fhash: str, interval_sec: int) -> bool:
    if not meta:
        return True
    try:
        last_ts = int(meta.get("train_end_ts") or 0)
        last_best = str(meta.get("best_source") or "")
        last_fhash = str(meta.get("feat_hash") or "")
    except Exception:
        return True

    # se mudou best_source ou features, retraina
    if last_best != best_source:
        return True
    if last_fhash != fhash:
        return True

    retrain_every = int(os.getenv("RETRAIN_EVERY_CANDLES", "12"))  # ~1h default (12 * 5min)
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
            regime_ok INTEGER NOT NULL,
            threshold REAL NOT NULL,
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
            PRIMARY KEY(day, ts)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_signals_v2_ts ON signals_v2(ts)")

    # migra colunas se já existia
    add_cols = {
        "score": "REAL",
        "gate_mode": "TEXT",
        "payout": "REAL",
        "ev": "REAL",
        "asset": "TEXT",
        "model_version": "TEXT",
        "train_rows": "INTEGER",
        "train_end_ts": "INTEGER",
        "best_source": "TEXT",
        "tune_dir": "TEXT",
        "rank_in_day": "INTEGER",
        "executed_today": "INTEGER",
        "close": "REAL",
    }
    cols = {r[1] for r in con.execute("PRAGMA table_info(signals_v2)").fetchall()}
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
        alt = p.with_name(p.stem + "_meta" + p.suffix)
        p = alt

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
    con.execute("CREATE INDEX IF NOT EXISTS idx_exe_asset_day ON executed(asset, day)")
    con.commit()


def state_paths() -> Path:
    return Path("runs") / "live_topk_state.sqlite3"


def executed_today_count(asset: str, day: str) -> int:
    con = sqlite3.connect(state_paths())
    try:
        ensure_state_db(con)
        cur = con.execute("SELECT COUNT(*) FROM executed WHERE asset=? AND day=?", (asset, day))
        return int(cur.fetchone()[0] or 0)
    finally:
        con.close()


def already_executed(asset: str, day: str, ts: int) -> bool:
    con = sqlite3.connect(state_paths())
    try:
        ensure_state_db(con)
        cur = con.execute("SELECT 1 FROM executed WHERE asset=? AND day=? AND ts=? LIMIT 1", (asset, day, ts))
        return cur.fetchone() is not None
    finally:
        con.close()


def mark_executed(asset: str, day: str, ts: int, action: str, conf: float, score: float) -> None:
    con = sqlite3.connect(state_paths())
    try:
        ensure_state_db(con)
        con.execute(
            "INSERT OR REPLACE INTO executed(asset, day, ts, action, conf, score) VALUES(?,?,?,?,?,?)",
            (asset, day, int(ts), action, float(conf), float(score)),
        )
        con.commit()
    finally:
        con.close()


def make_mask(df: pd.DataFrame, bounds: dict[str, float]) -> np.ndarray:
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
    tz = ZoneInfo(cfg.get("data", {}).get("timezone", "UTC"))
    asset = cfg.get("data", {}).get("asset", "UNKNOWN")
    interval_sec = int(cfg.get("data", {}).get("interval_sec", 300))

    if not best:
        raise RuntimeError("Não achei bloco 'best' em config.yaml. Rode o tune.")

    thr = float(best["threshold"])
    bounds = best["bounds"]
    tune_dir = str(best.get("tune_dir") or "")
    best_source = tune_dir

    dataset_path = cfg.get("phase2", {}).get("dataset_path", "data/dataset_phase2.csv")
    if not Path(dataset_path).exists():
        dataset_path = "data/dataset_phase2.csv"
    df = pd.read_csv(dataset_path)
    if len(df) == 0:
        raise ValueError("Dataset vazio.")

    feat = [c for c in df.columns if c.startswith("f_")]
    if not feat:
        raise ValueError("Sem features f_* no dataset.")

    # prepara tempo/local do candle
    last_ts = int(df["ts"].iloc[-1])
    last_dt = datetime.fromtimestamp(last_ts, tz=tz)
    day = last_dt.strftime("%Y-%m-%d")
    dt_local = last_dt.strftime("%Y-%m-%d %H:%M:%S")

    # treino: tudo menos os últimos 200 (anti-peek)
    min_train_rows = int(os.getenv("MIN_TRAIN_ROWS", "3000"))
    tail_holdout = int(os.getenv("TRAIN_TAIL_HOLDOUT", "200"))
    cut = max(min_train_rows, len(df) - tail_holdout)
    train = df.iloc[:cut].copy()
    train_end_ts = int(train["ts"].iloc[-1])
    train_rows = int(len(train))

    fhash = feat_hash(feat)
    cache = load_cache(asset)
    meta = (cache or {}).get("meta") if cache else None

    if should_retrain(meta, train_end_ts=train_end_ts, best_source=best_source, fhash=fhash, interval_sec=interval_sec):
        model, iso = train_model_and_iso(train[feat], train["y_open_close"])
        payload = {
            "model": model,
            "iso": iso,
            "meta": {
                "asset": asset,
                "created_at": datetime.now(tz=tz).isoformat(timespec="seconds"),
                "train_rows": train_rows,
                "train_end_ts": train_end_ts,
                "best_source": best_source,
                "feat_hash": fhash,
                "model_version": get_model_version(),
            },
        }
        save_cache(asset, payload)
        cache = payload
        meta = payload["meta"]

    model = cache["model"]
    iso = cache.get("iso", None)

    # pega candles do dia (até o último)
    dt_all = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df_day = df.loc[dt_all.dt.strftime("%Y-%m-%d") == day].copy()
    if len(df_day) == 0:
        raise ValueError("Sem dados no dia atual no dataset.")

    proba = model.predict_proba(df_day[feat])[:, 1]
    conf = np.maximum(proba, 1.0 - proba)

    mask = make_mask(df_day, bounds)
    cand = mask & (conf >= thr)

    # score para rankear TOPK
    gate_mode = os.getenv("GATE_MODE", "iso").strip().lower()
    if gate_mode == "iso" and iso is not None:
        try:
            score = iso.predict(conf.astype(float))
        except Exception:
            score = conf
            gate_mode = "conf"
    else:
        score = conf
        gate_mode = "conf"

    k = int(os.getenv("TOPK_K", "2"))
    idx = np.arange(len(df_day))
    # ordem global por score (mas aplicamos topk por dia via rank do dia inteiro, que aqui é o próprio df_day)
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
    elif float(conf[now_i]) < thr:
        reason = "below_conf_threshold"
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
        "gate_mode": gate_mode,
        "regime_ok": int(bool(mask[now_i])),
        "threshold": float(thr),
        "rank_in_day": int(rank_in_day),
        "executed_today": int(executed_today),
        "action": action,
        "reason": reason,
        "close": float(df_day["close"].iloc[now_i]) if "close" in df_day.columns else None,
        "payout": payout,
        "ev": ev,
        "asset": asset,
        "model_version": str(meta.get("model_version") if meta else get_model_version()),
        "train_rows": int(meta.get("train_rows") if meta else train_rows),
        "train_end_ts": int(meta.get("train_end_ts") if meta else train_end_ts),
        "best_source": str(meta.get("best_source") if meta else best_source),
        "tune_dir": tune_dir,
    }

    out_csv = append_csv(row)
    write_sqlite_signal(row)

    print("=== OBSERVE TOPK-PERDAY (latest) ===")
    print({k: row[k] for k in ["dt_local","day","ts","proba_up","conf","score","gate_mode","threshold","rank_in_day","executed_today","action","reason"]})
    print(f"csv_ok: {out_csv}")
    print("sqlite_ok: runs/live_signals.sqlite3 (signals_v2)")


if __name__ == "__main__":
    main()
'@

Write-Utf8NoBomFile "src\natbin\observe_signal_topk_perday.py" $Observe
Write-Host "OK: src\natbin\observe_signal_topk_perday.py" -ForegroundColor Green

# ---------------------------
# sanity: compileall
# ---------------------------
Write-Host "Compilando (compileall)..." -ForegroundColor Cyan
& $py -m compileall "src\natbin" | Out-Null

Write-Host "Patch aplicado. Agora você pode commitar." -ForegroundColor Green

if ($RunTune) {
  Write-Host "Rodando TUNE MW TOPK + update-config..." -ForegroundColor Cyan
  & $py -m natbin.tune_multiwindow_topk --k $K --windows $Windows --window-days $WindowDays --update-config
  Write-Host "Rodando PNL PAPER (opcional)..." -ForegroundColor Cyan
  & $py -m natbin.paper_pnl_backtest --k $K --holdout-days $WindowDays
}