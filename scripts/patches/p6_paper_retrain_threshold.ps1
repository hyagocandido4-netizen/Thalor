#requires -Version 7.0
$ErrorActionPreference = 'Stop'

function Write-Utf8NoBomFile {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Content
  )
  $dir = Split-Path -Parent $Path
  if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  $enc = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

$code = @'
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


def simulate_online_day(
    rank_ev: np.ndarray,
    cand: np.ndarray,
    correct: np.ndarray,
    idx_day: np.ndarray,
    k: int,
) -> tuple[int, int]:
    """Simula TOP-K por dia percorrendo o dia cronologicamente.

    - Mantém um "top" (por rank_ev) só entre os candidatos até o momento.
    - Executa (toma) no máximo k trades no dia.

    Retorna: taken, won
    """
    top: list[tuple[float, int]] = []
    executed = 0
    taken = 0
    won = 0

    for i in idx_day:
        if cand[i]:
            top.append((float(rank_ev[i]), int(i)))

        top.sort(key=lambda x: x[0], reverse=True)
        if len(top) > k:
            top = top[:k]

        in_top = any(j == int(i) for _, j in top)
        if in_top and executed < k:
            executed += 1
            taken += 1
            won += int(correct[i])

    return taken, won


def eval_chunk(
    *,
    feat: list[str],
    tz: ZoneInfo,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    chunk_days: list[str],
    bounds: dict[str, float],
    thr: float,
    payout: float,
    k: int,
    gate_mode: str,
    meta_model_type: str,
    thresh_on: str,
) -> tuple[int, int, float, str]:
    """Treina (base+cal+iso+meta) e avalia um "chunk" de dias do holdout."""
    cal, iso, meta = train_base_cal_iso_meta(
        train_df=train_df,
        feat_cols=feat,
        tz=tz,
        meta_model_type=meta_model_type,
    )

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

    ev_metric = score * payout - (1.0 - score)
    if thresh_on == "score":
        metric = score
    elif thresh_on == "conf":
        metric = conf
    else:
        metric = ev_metric

    mask = make_mask(test_df, bounds) if bounds else np.ones(len(test_df), dtype=bool)
    cand = mask & (metric >= thr)

    day_arr = test_df["day"].to_numpy(dtype=str)
    ts_arr = test_df["ts"].to_numpy(dtype=int)

    taken = 0
    won = 0
    pnl = 0.0

    for d in chunk_days:
        idx = np.where(day_arr == d)[0]
        if idx.size == 0:
            continue
        idx = idx[np.argsort(ts_arr[idx])]

        t, w = simulate_online_day(ev_metric, cand, correct, idx, k)
        if t == 0:
            continue
        taken += t
        won += w
        pnl += w * payout - (t - w) * 1.0

    return taken, won, pnl, gate_used


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--holdout-days", type=int, default=60)
    ap.add_argument("--payout", type=float, default=0.8)
    ap.add_argument("--thresh-on", type=str, default="score", choices=["score", "conf", "ev"])
    ap.add_argument("--gate-mode", type=str, default="meta", choices=["meta", "iso", "conf"])
    ap.add_argument("--meta-model", type=str, default="logreg", choices=["logreg", "hgb"])
    ap.add_argument("--config", type=str, default="config.yaml")

    # Novos:
    ap.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Override do threshold (se não passar, usa best.threshold do config).",
    )
    ap.add_argument(
        "--retrain-every-days",
        type=int,
        default=0,
        help="Se >0, re-treina no início de cada bloco de N dias dentro do holdout. 0 = treina 1x no início do holdout.",
    )

    args = ap.parse_args()

    cfg = load_cfg(args.config)
    best = cfg.get("best") or {}
    if not best:
        raise RuntimeError(
            "Sem bloco best no config.yaml. Rode o tune (mw) com --update-config."
        )

    thr = float(args.threshold) if args.threshold is not None else float(best.get("threshold", 0.60))
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
    if args.holdout_days > len(days):
        raise ValueError(f"holdout-days={args.holdout_days} maior que dias disponíveis ({len(days)}).")

    hold_days = days[-args.holdout_days :]

    # Avaliação
    taken = 0
    won = 0
    pnl = 0.0
    gate_counts: dict[str, int] = {}

    retrain_every = int(args.retrain_every_days)

    if retrain_every and retrain_every > 0:
        # retrain por blocos dentro do holdout
        for start in range(0, len(hold_days), retrain_every):
            chunk_days = hold_days[start : start + retrain_every]
            if not chunk_days:
                continue

            train_df = df[df["day"] < chunk_days[0]]
            test_df = df[df["day"].isin(chunk_days)].copy()
            if len(test_df) == 0:
                continue

            t, w, p, gate_used = eval_chunk(
                feat=feat,
                tz=tz,
                train_df=train_df,
                test_df=test_df,
                chunk_days=chunk_days,
                bounds=bounds,
                thr=thr,
                payout=float(args.payout),
                k=int(args.k),
                gate_mode=str(args.gate_mode),
                meta_model_type=str(args.meta_model),
                thresh_on=str(args.thresh_on),
            )

            taken += t
            won += w
            pnl += p
            gate_counts[gate_used] = gate_counts.get(gate_used, 0) + t

    else:
        # modo antigo: treina 1x no início do holdout
        train_df = df[df["day"] < hold_days[0]]
        test_df = df[df["day"].isin(hold_days)].copy()

        t, w, p, gate_used = eval_chunk(
            feat=feat,
            tz=tz,
            train_df=train_df,
            test_df=test_df,
            chunk_days=hold_days,
            bounds=bounds,
            thr=thr,
            payout=float(args.payout),
            k=int(args.k),
            gate_mode=str(args.gate_mode),
            meta_model_type=str(args.meta_model),
            thresh_on=str(args.thresh_on),
        )

        taken = t
        won = w
        pnl = p
        gate_counts = {gate_used: t}

    hit = (won / taken) if taken else 0.0
    be = 1.0 / (1.0 + float(args.payout))

    # gate label
    gate_major = "unknown"
    gate_extra = ""
    if gate_counts:
        gate_major = max(gate_counts.items(), key=lambda kv: kv[1])[0]
        extra = {k: v for k, v in gate_counts.items() if k != gate_major and v}
        if extra:
            gate_extra = " (fallback " + ", ".join(f"{k}={v}" for k, v in extra.items()) + ")"

    print("=== PNL PAPER (TOPK PER DAY, ONLINE, P2.2) ===")
    print(f"days={args.holdout_days} k={args.k} payout={args.payout}")
    print(f"break_even={be:.4f}")
    if retrain_every and retrain_every > 0:
        print(f"retrain_every_days={retrain_every}")
    print(f"gate_mode={gate_major}{gate_extra} thresh_on={args.thresh_on} threshold={thr:.2f}")
    print(f"taken={taken} won={won} hit={hit:.4f}")
    print(f"pnl={pnl:.2f} (em unidades de 1 stake)")
    print(f"run_at={datetime.now().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
'@

Write-Utf8NoBomFile -Path 'src/natbin/paper_pnl_backtest.py' -Content $code
Write-Host 'ok: src/natbin/paper_pnl_backtest.py'
