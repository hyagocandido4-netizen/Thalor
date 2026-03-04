#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def _ok(msg: str) -> None:
    print(f"[decision-engine-smoke][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[decision-engine-smoke][FAIL] {msg}")
    raise SystemExit(2)


def _base_inputs(*, k: int = 3):
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from natbin.decision_engine import DecisionInputs

    n = 6
    ts = np.array([100, 200, 300, 400, 500, 600], dtype=int)
    proba = np.array([0.40, 0.41, 0.42, 0.43, 0.44, 0.56], dtype=float)
    conf = np.array([0.60, 0.59, 0.58, 0.57, 0.56, 0.56], dtype=float)
    score = np.array([0.55, 0.54, 0.53, 0.52, 0.51, 0.80], dtype=float)
    regime = np.ones(n, dtype=bool)
    return DecisionInputs(
        ts_arr=ts,
        proba=proba,
        conf=conf,
        score=score,
        regime_ok_mask=regime,
        candidate_mask=regime,
        threshold=0.02,
        thresh_on="ev",
        k=k,
        payout=0.85,
        rolling_min=0,
        pacing_enabled=False,
        sec_of_day=12 * 3600,
        executed_today=0,
        already_emitted_for_ts=False,
        last_executed_ts=None,
        min_gap_min=0,
        market_open=True,
        market_context_stale_now=False,
        gate_fail_closed_active=False,
        cp_rejected_now=False,
        hard_regime_block=False,
    )


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.decision_engine import DecisionInputs, evaluate_latest

    # topk emit happy path
    inp = _base_inputs(k=3)
    out = evaluate_latest(inp)
    if out.reason != "topk_emit" or out.action != "CALL":
        _fail(f"expected topk_emit/CALL, got reason={out.reason} action={out.action}")
    if out.rank_in_day != 1:
        _fail(f"expected rank_in_day=1 got {out.rank_in_day}")
    _ok("topk emit path ok")

    # cp reject wins over threshold/topk
    inp_cp = DecisionInputs(**{**inp.__dict__, "cp_rejected_now": True})
    out_cp = evaluate_latest(inp_cp)
    if out_cp.reason != "cp_reject" or out_cp.action != "HOLD":
        _fail(f"expected cp_reject/HOLD got reason={out_cp.reason} action={out_cp.action}")
    _ok("cp reject precedence ok")

    # max_k_reached wins over everything else
    inp_k = DecisionInputs(**{**inp.__dict__, "executed_today": 3})
    out_k = evaluate_latest(inp_k)
    if out_k.reason != "max_k_reached":
        _fail(f"expected max_k_reached got {out_k.reason}")
    _ok("max_k precedence ok")

    # pacing quota blocks before topk emit when early in day
    inp_p = DecisionInputs(**{**inp.__dict__, "pacing_enabled": True, "executed_today": 1, "sec_of_day": 60})
    out_p = evaluate_latest(inp_p)
    if not out_p.reason.startswith("pacing_day_progress"):
        _fail(f"expected pacing_day_progress got {out_p.reason}")
    _ok("pacing precedence ok")

    # cooldown blocks emit when gap too small
    inp_c = DecisionInputs(**{**inp.__dict__, "executed_today": 1, "last_executed_ts": 560, "min_gap_min": 1})
    out_c = evaluate_latest(inp_c)
    if out_c.reason != "cooldown_min_gap(1m)":
        _fail(f"expected cooldown reason got {out_c.reason}")
    _ok("cooldown precedence ok")

    # market closed wins over max_k/topk/etc.
    inp_m = DecisionInputs(**{**inp.__dict__, "market_open": False})
    out_m = evaluate_latest(inp_m)
    if out_m.reason != "market_closed":
        _fail(f"expected market_closed got {out_m.reason}")
    _ok("market closed precedence ok")

    print("[decision-engine-smoke] ALL OK")


if __name__ == "__main__":
    main()
