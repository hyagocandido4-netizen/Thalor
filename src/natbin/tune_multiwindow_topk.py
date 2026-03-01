from __future__ import annotations
try:
    from .envutil import env_bool, env_float, env_int, env_str
except Exception:  # pragma: no cover
    from envutil import env_float, env_int, env_bool, env_str

import os
import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from .dsio import read_dataset_csv
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
    ap.add_argument("--gate-mode", type=str, default="meta", choices=["meta", "iso", "conf", "cp"])
    ap.add_argument("--meta-model", type=str, default="hgb", choices=["logreg", "hgb"])
    ap.add_argument("--thresh-on", type=str, default="score", choices=["score","conf","ev"])
    ap.add_argument("--update-config", action="store_true")
    ap.add_argument("--config", type=str, default="config.yaml")
    args = ap.parse_args()
    payout = env_float("PAYOUT", "0.8")
    cfg = load_cfg(args.config)
    tzname = cfg.get("data", {}).get("timezone", "UTC")
    tz = ZoneInfo(tzname)

    dataset_path = cfg.get("phase2", {}).get("dataset_path", "data/dataset_phase2.csv")
    if not Path(dataset_path).exists():
        dataset_path = "data/dataset_phase2.csv"

    df = read_dataset_csv(dataset_path, label_col="y_open_close")
    if len(df) == 0:
        raise ValueError("Dataset vazio.")

    df = df.sort_values("ts").reset_index(drop=True)
    df = df[df["y_open_close"].notna()].copy()
    feat = [c for c in df.columns if c.startswith("f_")]
    for req in ["f_vol48", "f_bb_width20", "f_atr14", "ts", "y_open_close"]:
        if req not in df.columns:
            raise ValueError(f"Dataset sem coluna obrigatória: {req}")

    phase2 = cfg.get("phase2", {}) or {}
    if args.thresh_on == "ev":
        tmin = float(phase2.get("ev_threshold_min", -0.05))
        tmax = float(phase2.get("ev_threshold_max", 0.40))
        tstep = float(phase2.get("ev_threshold_step", 0.01))
    else:
        tmin = float(phase2.get("threshold_min", 0.55))
        tmax = float(phase2.get("threshold_max", 0.85))
        tstep = float(phase2.get("threshold_step", 0.01))
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
                if args.thresh_on == "score":
                    metric = p.score
                elif args.thresh_on == "conf":
                    metric = p.conf
                else:
                    metric = p.score * payout - (1.0 - p.score)
                cand = mask & (metric >= thr)

                taken_w = 0
                corr_w = 0
                for d, idx_day in p.idx_by_day_chrono.items():
                    t, c = simulate_online_topk(p.score * payout - (1.0 - p.score), cand, p.correct, idx_day, args.k)
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
