param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param([string]$Path, [string]$Content)
  $enc = New-Object System.Text.UTF8Encoding($false)
  # normaliza line-endings para evitar bug bobo
  $norm = $Content.Replace("`r`n","`n")
  [System.IO.File]::WriteAllText($Path, $norm, $enc)
}

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Nao achei $py. Ative/crie a venv antes." }

# Backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\online_topk_eval_$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$files = @(
  "src\natbin\tune_multiwindow_topk.py",
  "src\natbin\paper_pnl_backtest.py"
)

foreach ($f in $files) {
  if (-not (Test-Path $f)) { throw "Nao achei $f" }
  Copy-Item $f (Join-Path $backupDir ([IO.Path]::GetFileName($f))) -Force
}

Write-Host "Backup -> $backupDir" -ForegroundColor DarkGray

# ===========================
# tune_multiwindow_topk.py
# (agora SIMULA online topk)
# ===========================
Write-Utf8NoBomFile "src\natbin\tune_multiwindow_topk.py" @'
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

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split


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
        raise FileNotFoundError(f"Não achei {path} na pasta atual.")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def dump_cfg(cfg: dict[str, Any], path: str = "config.yaml") -> None:
    Path(path).write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def train_calibrated_hgb_and_iso(
    X_train: pd.DataFrame, y_train: pd.Series
) -> tuple[CalibratedClassifierCV, IsotonicRegression | None]:
    if len(X_train) < 200:
        raise ValueError(f"Treino muito pequeno (n={len(X_train)}).")

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

    # iso: conf -> P(acertar direção)
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


def make_mask_arrays(
    vol: np.ndarray, bb: np.ndarray, atr: np.ndarray, bounds: dict[str, float]
) -> np.ndarray:
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
                            if b["vol_lo"] > b["vol_hi"]:
                                continue
                            if b["bb_lo"] > b["bb_hi"]:
                                continue
                            if b["atr_lo"] > b["atr_hi"]:
                                continue
                            out.append(b)
    return out


def build_windows(
    df: pd.DataFrame, tz: ZoneInfo, windows: int, window_days: int
) -> tuple[list[list[str]], str]:
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


def pack_window(
    df_all: pd.DataFrame, feat: list[str], tz: ZoneInfo, win_days: list[str]
) -> WindowPack:
    df_all = df_all.copy()
    dt_local = pd.to_datetime(df_all["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df_all["day"] = dt_local.dt.strftime("%Y-%m-%d")

    start_day = win_days[0]
    end_day = win_days[-1]

    train_df = df_all[df_all["day"] < start_day]
    test_df = df_all[df_all["day"].isin(win_days)].copy()

    if len(test_df) == 0:
        raise ValueError("Janela sem dados.")
    if len(train_df) < 500:
        raise ValueError(f"Treino insuficiente antes da janela {start_day} (n={len(train_df)}).")

    # garante ordem cronológica
    test_df = test_df.sort_values("ts").reset_index(drop=True)

    model, iso = train_calibrated_hgb_and_iso(train_df[feat], train_df["y_open_close"])

    proba = model.predict_proba(test_df[feat])[:, 1]
    pred = (proba >= 0.5).astype(int)
    y = test_df["y_open_close"].to_numpy(dtype=int)
    correct = (pred == y).astype(int)
    conf = np.maximum(proba, 1.0 - proba)

    if iso is not None:
        try:
            score = iso.predict(conf.astype(float))
        except Exception:
            score = conf
    else:
        score = conf

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
        # já está ordenado por ts, mas garante
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


def simulate_online_topk(
    score: np.ndarray,
    cand: np.ndarray,
    correct: np.ndarray,
    idx_day: np.ndarray,
    k: int,
) -> tuple[int, int]:
    """
    Simula o que o observe faz:
      - percorre candles do dia em ordem cronológica
      - mantém TOPK por score "até agora" (apenas elegíveis cand=True)
      - executa no candle atual se ele estiver no TOPK e ainda não executou k trades no dia
    """
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--windows", type=int, default=6)
    ap.add_argument("--window-days", type=int, default=20)
    ap.add_argument("--min-total-trades", type=int, default=50)
    ap.add_argument("--min-trades-per-window", type=int, default=6)
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
    if not feat:
        raise ValueError("Não achei colunas f_* no dataset.")

    for req in ["f_vol48", "f_bb_width20", "f_atr14", "ts", "y_open_close"]:
        if req not in df.columns:
            raise ValueError(f"Dataset sem coluna obrigatória: {req}")

    tmin = float(cfg.get("phase2", {}).get("threshold_min", 0.52))
    tmax = float(cfg.get("phase2", {}).get("threshold_max", 0.75))
    tstep = float(cfg.get("phase2", {}).get("threshold_step", 0.01))
    thresholds = np.round(np.arange(tmin, tmax + 1e-9, tstep), 2)

    win_days_list, first_eval_day = build_windows(df, tz, args.windows, args.window_days)

    dt_local = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df = df.copy()
    df["day"] = dt_local.dt.strftime("%Y-%m-%d")
    global_train = df[df["day"] < first_eval_day]
    if len(global_train) < 1000:
        cut = max(1000, int(len(df) * 0.6))
        global_train = df.iloc[:cut]

    bounds_list = quant_bounds(global_train)

    packs: list[WindowPack] = []
    for win_days in win_days_list:
        packs.append(pack_window(df, feat, tz, win_days))

    run_dir = Path("runs") / f"tune_mw_topk_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    best: dict[str, Any] | None = None
    best_key: tuple[float, float, float] | None = None
    rows: list[dict[str, Any]] = []

    thresh_on = args.thresh_on

    for b in bounds_list:
        for thr in thresholds:
            total_taken = 0
            total_correct = 0
            total_test = 0
            ok = True
            min_hit = 1.0
            perw: list[dict[str, Any]] = []

            for p in packs:
                mask = make_mask_arrays(p.vol, p.bb, p.atr, b)
                metric = p.score if thresh_on == "score" else p.conf
                cand = mask & (metric >= thr)

                taken_w = 0
                corr_w = 0
                for d, idx_day in p.idx_by_day_chrono.items():
                    t, c = simulate_online_topk(p.score, cand, p.correct, idx_day, args.k)
                    taken_w += t
                    corr_w += c

                test_n = len(metric)
                hit_w = (corr_w / taken_w) if taken_w else 0.0

                perw.append({"start_day": p.start_day, "end_day": p.end_day, "taken": taken_w, "hit": hit_w})

                total_taken += taken_w
                total_correct += corr_w
                total_test += test_n

                if taken_w < args.min_trades_per_window:
                    ok = False
                    min_hit = 0.0
                else:
                    min_hit = min(min_hit, hit_w)

            if total_taken < args.min_total_trades:
                ok = False
                min_hit = 0.0

            hit = (total_correct / total_taken) if total_taken else 0.0
            cov = (total_taken / total_test) if total_test else 0.0

            row = {
                "thresh_on": thresh_on,
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
                # prioriza: robustez (min_hit), depois hit médio, depois MENOS trades (sinais raros)
                key = (min_hit, hit, -float(total_taken))
                if best_key is None or key > best_key:
                    best_key = key
                    best = {
                        "thresh_on": thresh_on,
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

    summary = {
        "run_dir": str(run_dir).replace("\\", "/"),
        "dataset_path": str(dataset_path).replace("\\", "/"),
        "timezone": tzname,
        "thresh_on": thresh_on,
        "threshold_grid": {"min": tmin, "max": tmax, "step": tstep},
        "best": best,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("=== TUNE MW TOPK (pseudo-futuro, ONLINE) ===")
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
            "notes": (
                f"Frozen from ONLINE tune_mw_topk "
                f"(thresh_on={best['thresh_on']}, windows={best['windows']}, "
                f"window_days={best['window_days']}, k={best['k']})."
            ),
        }
        dump_cfg(cfg2, args.config)
        print("config.yaml atualizado com bloco best (multiwindow, ONLINE).")


if __name__ == "__main__":
    main()
'@

# ===========================
# paper_pnl_backtest.py
# (agora SIMULA online topk)
# ===========================
Write-Utf8NoBomFile "src\natbin\paper_pnl_backtest.py" @'
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
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split


def load_cfg(path: str = "config.yaml") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Não achei {path}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def train_calibrated_hgb_and_iso(
    X_train: pd.DataFrame, y_train: pd.Series
) -> tuple[CalibratedClassifierCV, IsotonicRegression | None]:
    X_sub, X_cal, y_sub, y_cal = train_test_split(
        X_train, y_train, test_size=0.2, shuffle=False
    )
    base = HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=300, random_state=0
    )
    base.fit(X_sub, y_sub)

    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(X_cal, y_cal)

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
    top: list[tuple[float,int]] = []
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
    ap.add_argument("--config", type=str, default="config.yaml")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    best = cfg.get("best") or {}
    if not best:
        raise RuntimeError("Sem bloco best no config.yaml.")

    thr = float(best["threshold"])
    bounds = best["bounds"]

    tzname = cfg.get("data", {}).get("timezone", "UTC")
    tz = ZoneInfo(tzname)

    dataset_path = cfg.get("phase2", {}).get("dataset_path", "data/dataset_phase2.csv")
    if not Path(dataset_path).exists():
        dataset_path = "data/dataset_phase2.csv"

    df = pd.read_csv(dataset_path)
    if len(df) == 0:
        raise ValueError("Dataset vazio.")
    feat = [c for c in df.columns if c.startswith("f_")]

    # garante ordem cronológica
    df = df.sort_values("ts").reset_index(drop=True)

    dt_local = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df = df.copy()
    df["day"] = dt_local.dt.strftime("%Y-%m-%d")

    days = sorted(df["day"].unique().tolist())
    hold_days = days[-args.holdout_days:]
    train_df = df[df["day"] < hold_days[0]]
    test_df = df[df["day"].isin(hold_days)].copy()

    model, iso = train_calibrated_hgb_and_iso(train_df[feat], train_df["y_open_close"])

    proba = model.predict_proba(test_df[feat])[:, 1]
    pred = (proba >= 0.5).astype(int)
    y = test_df["y_open_close"].to_numpy(dtype=int)
    correct = (pred == y).astype(int)
    conf = np.maximum(proba, 1.0 - proba)

    if iso is not None:
        try:
            score = iso.predict(conf.astype(float))
        except Exception:
            score = conf
    else:
        score = conf

    metric = score if args.thresh_on == "score" else conf
    mask = make_mask(test_df, bounds)
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
        # cronológico
        idx = idx[np.argsort(ts_arr[idx])]
        t, w = simulate_online_day(score, cand, correct, idx, args.k)
        if t == 0:
            continue
        taken += t
        won += w
        pnl += w * args.payout - (t - w) * 1.0

    hit = (won / taken) if taken else 0.0
    be = 1.0 / (1.0 + args.payout)

    print("=== PNL PAPER (TOPK PER DAY, ONLINE) ===")
    print(f"days={args.holdout_days} k={args.k} payout={args.payout}")
    print(f"break_even={be:.4f}")
    print(f"threshold({args.thresh_on})={thr:.2f}")
    print(f"taken={taken} won={won} hit={hit:.4f}")
    print(f"pnl={pnl:.2f} (em unidades de 1 stake)")
    print(f"run_at={datetime.now().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
'@

& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK: tuner e paper agora avaliam TOPK em modo ONLINE (igual ao observe)." -ForegroundColor Green
Write-Host "Rode de novo seu experimento A para validar (agora realista):" -ForegroundColor Yellow
Write-Host "  .\.venv\Scripts\python.exe -m natbin.tune_multiwindow_topk --k 1 --windows 2 --window-days 60 --thresh-on score --min-total-trades 20 --min-trades-per-window 5 --update-config" -ForegroundColor Yellow
Write-Host "E paper:" -ForegroundColor Yellow
Write-Host "  .\.venv\Scripts\python.exe -m natbin.paper_pnl_backtest --k 1 --holdout-days 60 --payout 0.8 --thresh-on score" -ForegroundColor Yellow