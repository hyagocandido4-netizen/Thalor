from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class DecisionInputs:
    ts_arr: np.ndarray
    proba: np.ndarray
    conf: np.ndarray
    score: np.ndarray
    regime_ok_mask: np.ndarray
    candidate_mask: np.ndarray
    threshold: float
    thresh_on: str
    k: int
    payout: float
    rolling_min: int = 0
    pacing_enabled: bool = False
    sec_of_day: int = 0
    executed_today: int = 0
    already_emitted_for_ts: bool = False
    last_executed_ts: int | None = None
    min_gap_min: int = 0
    market_open: bool = True
    market_context_stale_now: bool = False
    gate_fail_closed_active: bool = False
    cp_rejected_now: bool = False
    hard_regime_block: bool = False


@dataclass(frozen=True)
class DecisionResult:
    action: str
    reason: str
    blockers: tuple[str, ...]
    emitted_now: bool
    in_topk: bool
    rank_in_day: int
    topk_indices: tuple[int, ...]
    executed_after: int
    budget_left: int
    pacing_allowed: int
    cooldown_reason: str
    threshold_reason: str
    ev_now: float
    metric_now: float


def _stable_topk(rank: np.ndarray, cand: np.ndarray, ts_arr: np.ndarray, *, k: int, rolling_min: int) -> np.ndarray:
    order = np.argsort(-rank, kind="mergesort")
    if int(rolling_min) > 0:
        start_ts = int(ts_arr[-1]) - int(rolling_min) * 60
        win_mask = ts_arr >= start_ts
    else:
        win_mask = np.ones(len(ts_arr), dtype=bool)
    sel = order[(cand & win_mask)[order]]
    return sel[: int(max(1, k))]


def _pacing_allowed(*, k: int, pacing_enabled: bool, sec_of_day: int) -> int:
    if (not pacing_enabled) or int(k) <= 1:
        return int(k)
    sec = min(86400, max(0, int(sec_of_day)))
    frac_day = min(1.0, max(0.0, float(sec) / 86400.0))
    return min(int(k), max(1, int(np.floor(float(k) * frac_day)) + 1))


def evaluate_latest(inputs: DecisionInputs) -> DecisionResult:
    ts_arr = np.asarray(inputs.ts_arr, dtype=int)
    proba = np.asarray(inputs.proba, dtype=float)
    conf = np.asarray(inputs.conf, dtype=float)
    score = np.asarray(inputs.score, dtype=float)
    regime_ok_mask = np.asarray(inputs.regime_ok_mask, dtype=bool)
    candidate_mask = np.asarray(inputs.candidate_mask, dtype=bool)

    if len(ts_arr) == 0:
        raise ValueError("DecisionInputs.ts_arr must not be empty")
    n = len(ts_arr)
    if not (len(proba) == len(conf) == len(score) == len(regime_ok_mask) == len(candidate_mask) == n):
        raise ValueError("DecisionInputs arrays must have matching length")

    payout = float(inputs.payout)
    ev_metric = score * payout - (1.0 - score)
    thresh_on = str(inputs.thresh_on or "ev").strip().lower()
    if thresh_on == "score":
        metric = score
    elif thresh_on == "conf":
        metric = conf
    else:
        metric = ev_metric

    rank = ev_metric
    cand = candidate_mask & (metric >= float(inputs.threshold))
    topk = _stable_topk(rank, cand, ts_arr, k=int(inputs.k), rolling_min=int(inputs.rolling_min))

    now_i = n - 1
    in_topk = bool(now_i in set(topk.tolist()))
    rank_in_day = int(np.where(topk == now_i)[0][0] + 1) if in_topk else -1

    executed_today = int(inputs.executed_today)
    pacing_allowed = _pacing_allowed(k=int(inputs.k), pacing_enabled=bool(inputs.pacing_enabled), sec_of_day=int(inputs.sec_of_day))

    threshold_reason = ""
    if float(metric[now_i]) < float(inputs.threshold):
        if thresh_on == "score":
            threshold_reason = "below_score_threshold"
        elif thresh_on == "conf":
            threshold_reason = "below_conf_threshold"
        else:
            threshold_reason = "below_ev_threshold"

    pacing_reason = ""
    if bool(inputs.pacing_enabled) and executed_today >= pacing_allowed:
        pacing_reason = f"pacing_day_progress({pacing_allowed}/{int(inputs.k)})"

    cooldown_reason = ""
    if int(inputs.min_gap_min) > 0 and executed_today > 0 and inputs.last_executed_ts is not None:
        if (int(ts_arr[now_i]) - int(inputs.last_executed_ts)) < int(inputs.min_gap_min) * 60:
            cooldown_reason = f"cooldown_min_gap({int(inputs.min_gap_min)}m)"

    blockers: list[str] = []
    if bool(inputs.market_context_stale_now):
        blockers.append("market_context_stale")
    if not bool(inputs.market_open):
        blockers.append("market_closed")
    if executed_today >= int(inputs.k):
        blockers.append("max_k_reached")
    if bool(inputs.already_emitted_for_ts):
        blockers.append("already_emitted_for_ts")
    if bool(inputs.hard_regime_block):
        blockers.append("regime_block")
    if pacing_reason:
        blockers.append(pacing_reason)
    if bool(inputs.gate_fail_closed_active):
        blockers.append("gate_fail_closed")
    if bool(inputs.cp_rejected_now):
        blockers.append("cp_reject")
    if threshold_reason:
        blockers.append(threshold_reason)
    if not in_topk:
        blockers.append("not_in_topk_today")
    if cooldown_reason:
        blockers.append(cooldown_reason)

    if bool(inputs.market_context_stale_now):
        reason = "market_context_stale"
    elif not bool(inputs.market_open):
        reason = "market_closed"
    elif executed_today >= int(inputs.k):
        reason = "max_k_reached"
    elif bool(inputs.already_emitted_for_ts):
        reason = "already_emitted_for_ts"
    elif pacing_reason:
        reason = pacing_reason
    elif bool(inputs.hard_regime_block):
        reason = "regime_block"
    elif bool(inputs.gate_fail_closed_active):
        reason = "gate_fail_closed"
    elif bool(inputs.cp_rejected_now):
        reason = "cp_reject"
    elif threshold_reason:
        reason = threshold_reason
    elif not in_topk:
        reason = "not_in_topk_today"
    elif cooldown_reason:
        reason = cooldown_reason
    else:
        reason = "topk_emit"

    emitted_now = reason == "topk_emit"
    action = ("CALL" if float(proba[now_i]) >= 0.5 else "PUT") if emitted_now else "HOLD"
    executed_after = int(executed_today) + (1 if emitted_now else 0)
    budget_left = max(0, int(inputs.k) - int(executed_after))

    return DecisionResult(
        action=action,
        reason=reason,
        blockers=tuple(blockers),
        emitted_now=bool(emitted_now),
        in_topk=bool(in_topk),
        rank_in_day=int(rank_in_day),
        topk_indices=tuple(int(i) for i in topk.tolist()),
        executed_after=int(executed_after),
        budget_left=int(budget_left),
        pacing_allowed=int(pacing_allowed),
        cooldown_reason=str(cooldown_reason),
        threshold_reason=str(threshold_reason),
        ev_now=float(ev_metric[now_i]),
        metric_now=float(metric[now_i]),
    )


__all__ = [
    "DecisionInputs",
    "DecisionResult",
    "evaluate_latest",
]
