from __future__ import annotations

import math
import os
from datetime import datetime
from typing import Any, Dict, Tuple

from .common import as_float, as_int, break_even_from_payout, load_json, repo_context
from .summary_loader import collect_checked_summaries


def _keep_payload(*, decision: str, now: datetime, min_trades_hour: int, payout: float, thr_in_raw: str, thr_floor: float, thr_ceil: float, scan: dict) -> Dict[str, Any]:
    ctx = repo_context(now)
    break_even = break_even_from_payout(payout)
    return {
        "decision": decision,
        "hour": int(now.hour),
        "hour_trades": 0,
        "hour_wins": 0,
        "hour_wr": 0.0,
        "hour_ev_mean": 0.0,
        "hour_mult": 1.0,
        "threshold_in": thr_in_raw,
        "threshold_out": thr_in_raw,
        "thr_floor": float(thr_floor),
        "thr_ceil": float(thr_ceil),
        "lookback_days": int(as_int(os.getenv("P17_LOOKBACK_DAYS", "14"), 14)),
        "days_used": 0,
        "min_trades_hour": int(min_trades_hour),
        "break_even": round(float(break_even), 6),
        "margin": round(float(as_float(os.getenv("P17_WR_MARGIN", "0.01"), 0.01)), 4),
        "asset": ctx.asset,
        "interval_sec": int(ctx.interval_sec),
        "timezone": ctx.timezone,
        "runs_dir": str(ctx.runs_dir),
        "summary_scan": scan,
        "summary_fail_closed": True,
    }


def _get_dict(obj: Dict[str, Any], *names: str) -> Dict[str, Any] | None:
    for n in names:
        v = obj.get(n)
        if isinstance(v, dict):
            return v
    return None


def _get_hour_value(container: Any, hour: int) -> Any:
    if isinstance(container, (list, tuple)):
        if 0 <= hour < len(container):
            return container[hour]
        return None
    if not isinstance(container, dict):
        return None
    keys = [f"{hour:02d}", str(hour), f"{hour:02d}:00", f"{hour}:00", f"{hour:02d}:00:00", f"{hour}h"]
    for k in keys:
        if k in container:
            return container[k]
    return None


def _extract_hour_stats(summary: Dict[str, Any], hour: int) -> Tuple[int, int, float]:
    by_hour = _get_dict(summary, "by_hour", "hours", "hourly", "per_hour")
    entry = _get_hour_value(by_hour, hour) if by_hour else None
    count = 0
    wins = 0
    ev_mean = math.nan
    if isinstance(entry, dict):
        count = as_int(entry.get("count") or entry.get("taken") or entry.get("trades") or entry.get("n") or entry.get("total"), 0)
        wins = as_int(entry.get("won") or entry.get("wins") or entry.get("w"), 0)
        wr = as_float(entry.get("wr") or entry.get("hit") or entry.get("win_rate"), math.nan)
        if wins == 0 and count > 0 and not math.isnan(wr):
            wins = int(round(wr * count))
        ev_mean = as_float(entry.get("ev_mean") or entry.get("ev_avg") or entry.get("mean_ev") or entry.get("ev"), math.nan)
        if math.isnan(ev_mean):
            ev_total = entry.get("ev_total") or entry.get("ev_sum")
            if ev_total is not None and count > 0:
                ev_mean = as_float(ev_total, math.nan) / max(1, count)
    elif entry is not None:
        count = as_int(entry, 0)
    if count > 0 and wins == 0:
        wins_map = _get_dict(summary, "wins_by_hour", "hour_wins", "wins_hour", "by_hour_wins")
        v = _get_hour_value(wins_map, hour) if wins_map else None
        if v is not None:
            wins = as_int(v, 0)
    if count > 0 and math.isnan(ev_mean):
        ev_map = _get_dict(summary, "ev_mean_by_hour", "hour_ev_mean", "ev_by_hour_mean", "by_hour_ev_mean")
        v = _get_hour_value(ev_map, hour) if ev_map else None
        if v is not None:
            ev_mean = as_float(v, math.nan)
    return count, wins, ev_mean


def compute_hour_threshold(now: datetime | None = None) -> Dict[str, Any]:
    now = now or repo_context().now
    if os.getenv("P17_ENABLE", "1") == "0":
        return {"decision": "disabled"}
    ctx = repo_context(now)
    lookback_days = as_int(os.getenv("P17_LOOKBACK_DAYS", "14"), 14)
    min_trades_hour = as_int(os.getenv("P17_MIN_TRADES_HOUR", "8"), 8)
    payout = as_float(os.getenv("PAYOUT", "0.8"), 0.8)
    break_even = break_even_from_payout(payout)
    margin = as_float(os.getenv("P17_WR_MARGIN", "0.01"), 0.01)
    good_ev = as_float(os.getenv("P17_GOOD_EV", "0.02"), 0.02)
    bad_ev = as_float(os.getenv("P17_BAD_EV", "-0.02"), -0.02)
    mult_good = as_float(os.getenv("P17_MULT_GOOD", "0.95"), 0.95)
    mult_bad = as_float(os.getenv("P17_MULT_BAD", "1.05"), 1.05)
    mult_min = as_float(os.getenv("P17_MULT_MIN", "0.90"), 0.90)
    mult_max = as_float(os.getenv("P17_MULT_MAX", "1.10"), 1.10)
    thr_floor = as_float(os.getenv("VOL_SAFE_THR_MIN", os.getenv("VOL_THR_MIN", "0.02")), 0.02)
    thr_ceil = as_float(os.getenv("P17_THR_CEIL", "0.20"), 0.20)
    thr_in_raw = os.getenv("THRESHOLD", "")
    thr_in = as_float(thr_in_raw, math.nan)
    hour = now.hour
    scan_result = collect_checked_summaries(
        now=now,
        lookback_days=lookback_days,
        asset=ctx.asset,
        interval_sec=ctx.interval_sec,
        runs_dir=ctx.runs_dir,
    )
    summaries = scan_result.summaries
    scan = scan_result.scan
    today_day = now.strftime("%Y-%m-%d")
    if not summaries:
        return _keep_payload(decision="summary_missing_keep", now=now, min_trades_hour=min_trades_hour, payout=payout, thr_in_raw=thr_in_raw, thr_floor=thr_floor, thr_ceil=thr_ceil, scan=scan)
    if today_day not in set(scan.get("used_days") or []):
        return _keep_payload(decision="summary_today_missing_keep", now=now, min_trades_hour=min_trades_hour, payout=payout, thr_in_raw=thr_in_raw, thr_floor=thr_floor, thr_ceil=thr_ceil, scan=scan)
    days_used = 0
    h_trades = 0
    h_wins = 0
    ev_weight_sum = 0.0
    ev_weight_n = 0
    for _day, s in summaries[:lookback_days]:
        c, w, evm = _extract_hour_stats(s, hour)
        days_used += 1
        if c <= 0:
            continue
        h_trades += c
        h_wins += w
        if not math.isnan(evm):
            ev_weight_sum += float(evm) * float(c)
            ev_weight_n += int(c)
    hour_wr = (h_wins / h_trades) if h_trades > 0 else 0.0
    hour_ev_mean = (ev_weight_sum / ev_weight_n) if ev_weight_n > 0 else 0.0
    mult = 1.0
    decision = "keep"
    if h_trades < min_trades_hour:
        mult = 1.0
        decision = "insufficient_hour_trades_keep"
    elif hour_wr < break_even:
        mult = mult_bad
        decision = "hour_wr_bad_tighten"
    elif hour_wr >= break_even + margin and hour_ev_mean >= good_ev:
        mult = mult_good
        decision = "hour_good_relax"
    elif hour_ev_mean <= bad_ev:
        mult = mult_bad
        decision = "hour_ev_bad_tighten"
    mult = max(mult_min, min(mult_max, mult))
    thr_out = thr_in_raw
    if not math.isnan(thr_in):
        thr_new = min(thr_ceil, max(thr_floor, float(thr_in) * float(mult)))
        thr_out = f"{thr_new:.4f}".rstrip("0").rstrip(".") if "." in f"{thr_new:.4f}" else f"{thr_new:.4f}"
    return {
        "decision": decision,
        "hour": int(hour),
        "hour_trades": int(h_trades),
        "hour_wins": int(h_wins),
        "hour_wr": round(float(hour_wr), 6),
        "hour_ev_mean": round(float(hour_ev_mean), 6),
        "hour_mult": round(float(mult), 4),
        "threshold_in": thr_in_raw,
        "threshold_out": thr_out,
        "thr_floor": float(thr_floor),
        "thr_ceil": float(thr_ceil),
        "lookback_days": int(lookback_days),
        "days_used": int(days_used),
        "min_trades_hour": int(min_trades_hour),
        "break_even": round(float(break_even), 6),
        "margin": round(float(margin), 4),
        "asset": ctx.asset,
        "interval_sec": int(ctx.interval_sec),
        "timezone": ctx.timezone,
        "runs_dir": str(ctx.runs_dir),
        "summary_scan": scan,
        "summary_fail_closed": False,
    }
