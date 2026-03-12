# P14: threshold/cp_alpha sweep for paper_pnl_backtest (black-box)
from __future__ import annotations
try:
    from ..config.env import env_bool, env_float, env_int, env_str
except Exception:  # pragma: no cover
    from ..config.env import env_float, env_int, env_bool, env_str

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path


RE_BE = re.compile(r"break_even=([0-9.]+)")
RE_TAKEN = re.compile(r"taken=(\d+)\s+won=(\d+)\s+hit=([0-9.]+)")
RE_PNL = re.compile(r"pnl=([-0-9.]+)")


def _parse_float_list(spec: str) -> list[float]:
    """
    Aceita:
      - "0,0.03,0.05"
      - "0 0.03 0.05"
      - "0:0.12:0.01"  (start:stop:step, inclui stop com tolerância)
    """
    s = (spec or "").strip()
    if not s:
        return []
    if ":" in s:
        parts = [p.strip() for p in s.split(":")]
        if len(parts) != 3:
            raise ValueError(f"range spec inválido: {spec} (use start:stop:step)")
        start, stop, step = map(float, parts)
        if step <= 0:
            raise ValueError("step deve ser > 0")
        out = []
        x = start
        while x <= stop + 1e-12:
            out.append(round(x, 6))
            x += step
        return out

    toks = s.replace(" ", ",").split(",")
    out = []
    for t in toks:
        t = t.strip()
        if not t:
            continue
        out.append(float(t))
    return out


@dataclass
class Row:
    cp_alpha: float
    threshold: float
    days: int
    k: int
    payout: float
    retrain_every_days: int
    taken: int
    won: int
    hit: float
    pnl: float
    pnl_per_trade: float
    trades_per_day: float
    break_even: float
    ok: bool
    error: str


def _break_even_from_payout(payout: float) -> float:
    return 1.0 / (1.0 + payout)


def _run_once(
    *,
    config: str,
    k: int,
    holdout_days: int,
    payout: float,
    gate_mode: str,
    meta_model: str,
    thresh_on: str,
    retrain_every_days: int,
    threshold: float,
    cp_alpha: float,
) -> Row:
    env = os.environ.copy()
    env["CP_ALPHA"] = f"{cp_alpha:.6g}"
    # manter coerência caso alguma parte use CPREG_*
    env["CPREG_ALPHA_START"] = env["CP_ALPHA"]
    env["CPREG_ALPHA_END"] = env["CP_ALPHA"]

    cmd = [
        sys.executable,
        "-m",
        "natbin.paper_pnl_backtest",
        "--config",
        config,
        "--k",
        str(k),
        "--holdout-days",
        str(holdout_days),
        "--payout",
        str(payout),
        "--gate-mode",
        gate_mode,
        "--meta-model",
        meta_model,
        "--thresh-on",
        thresh_on,
        "--retrain-every-days",
        str(retrain_every_days),
        "--threshold",
        str(threshold),
    ]

    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    out = (p.stdout or "") + "\n" + (p.stderr or "")

    be = None
    m = RE_BE.search(out)
    if m:
        try:
            be = float(m.group(1))
        except Exception:
            be = None

    taken = won = 0
    hit = 0.0
    m2 = RE_TAKEN.search(out)
    if m2:
        taken = int(m2.group(1))
        won = int(m2.group(2))
        hit = float(m2.group(3))

    pnl = 0.0
    m3 = RE_PNL.search(out)
    if m3:
        pnl = float(m3.group(1))

    ok = (p.returncode == 0) and (m2 is not None) and (m3 is not None)
    err = ""
    if not ok:
        err = out.strip().splitlines()[-1] if out.strip() else f"returncode={p.returncode}"

    pnl_per_trade = (pnl / taken) if taken > 0 else 0.0
    tpd = (taken / holdout_days) if holdout_days > 0 else 0.0

    return Row(
        cp_alpha=float(cp_alpha),
        threshold=float(threshold),
        days=int(holdout_days),
        k=int(k),
        payout=float(payout),
        retrain_every_days=int(retrain_every_days),
        taken=int(taken),
        won=int(won),
        hit=float(hit),
        pnl=float(pnl),
        pnl_per_trade=float(pnl_per_trade),
        trades_per_day=float(tpd),
        break_even=float(be) if be is not None else _break_even_from_payout(payout),
        ok=bool(ok),
        error=str(err),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--k", type=int, required=True)
    ap.add_argument("--holdout-days", type=int, required=True)
    ap.add_argument("--payout", type=float, required=True)
    ap.add_argument("--gate-mode", default="cp")
    ap.add_argument("--meta-model", default="hgb")
    ap.add_argument("--thresh-on", default="ev")
    ap.add_argument("--retrain-every-days", type=int, default=20)

    ap.add_argument("--thresholds", required=True, help='ex: "0:0.12:0.01" ou "0,0.03,0.05,0.07,0.10"')
    ap.add_argument("--cp-alphas", default="", help='ex: "0.05,0.06,0.07,0.08". vazio => usa env CP_ALPHA ou 0.07')

    ap.add_argument("--min-taken", type=int, default=20)
    ap.add_argument("--min-hit", type=float, default=0.0)
    ap.add_argument("--out-prefix", default="runs/threshold_sweep")

    args = ap.parse_args()

    thr_list = _parse_float_list(args.thresholds)
    if not thr_list:
        raise SystemExit("Lista de thresholds vazia.")

    if args.cp_alphas.strip():
        alpha_list = _parse_float_list(args.cp_alphas)
    else:
        alpha_list = [env_float("CP_ALPHA", "0.07")]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = Path(f"{args.out_prefix}_{ts}.csv")
    out_json = Path(f"{args.out_prefix}_{ts}.json")
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    rows: list[Row] = []
    for a in alpha_list:
        for thr in thr_list:
            r = _run_once(
                config=args.config,
                k=args.k,
                holdout_days=args.holdout_days,
                payout=args.payout,
                gate_mode=args.gate_mode,
                meta_model=args.meta_model,
                thresh_on=args.thresh_on,
                retrain_every_days=args.retrain_every_days,
                threshold=thr,
                cp_alpha=a,
            )
            rows.append(r)
            status = "OK" if r.ok else "ERR"
            print(f"[P14] {status} cp_alpha={a:.3f} thr={thr:.3f} taken={r.taken} hit={r.hit:.4f} pnl={r.pnl:.2f}")

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))

    out_json.write_text(json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=2), encoding="utf-8")

    ok_rows = [r for r in rows if r.ok and r.taken >= args.min_taken and r.hit >= args.min_hit]
    best_pnl = max(ok_rows, key=lambda r: r.pnl, default=None)
    best_hit = max(ok_rows, key=lambda r: r.hit, default=None)

    print(f"[P14] saved_csv={out_csv}")
    print(f"[P14] saved_json={out_json}")

    if best_pnl:
        print(f"[P14] BEST_PNL: cp_alpha={best_pnl.cp_alpha:.3f} thr={best_pnl.threshold:.3f} "
              f"taken={best_pnl.taken} hit={best_pnl.hit:.4f} pnl={best_pnl.pnl:.2f} tpd={best_pnl.trades_per_day:.3f}")
    if best_hit:
        print(f"[P14] BEST_HIT: cp_alpha={best_hit.cp_alpha:.3f} thr={best_hit.threshold:.3f} "
              f"taken={best_hit.taken} hit={best_hit.hit:.4f} pnl={best_hit.pnl:.2f} tpd={best_hit.trades_per_day:.3f}")

    for a in alpha_list:
        cand = sorted([r for r in ok_rows if abs(r.cp_alpha - a) < 1e-12], key=lambda r: r.threshold)
        floor = next((r for r in cand if r.pnl >= 0.0), None)
        if floor:
            print(f"[P14] floor_suggest cp_alpha={a:.3f} thr>={floor.threshold:.3f} (pnl>=0)")


if __name__ == "__main__":
    main()
