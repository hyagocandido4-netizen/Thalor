from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .common import as_float, as_int, break_even_from_payout, repo_context
from .summary_loader import collect_checked_summaries


def _current_blend(bootstrap_blend: float, min_blend: float, max_blend: float) -> float:
    cur_blend = as_float(os.getenv("META_ISO_BLEND", str(bootstrap_blend)), bootstrap_blend)
    return max(min(cur_blend, max_blend), min_blend)


def _keep_payload(*, decision: str, cur_blend: float, min_blend: float, max_blend: float, step: float, break_even: float, margin: float, target_tpd: float, now: datetime, summary_scan: dict) -> Dict[str, Any]:
    ctx = repo_context(now)
    return {
        "decision": decision,
        "meta_iso_blend": round(float(cur_blend), 4),
        "cur_blend": round(float(cur_blend), 4),
        "min_blend": round(float(min_blend), 4),
        "max_blend": round(float(max_blend), 4),
        "step": round(float(step), 4),
        "break_even": round(float(break_even), 6),
        "margin": round(float(margin), 4),
        "target_tpd": round(float(target_tpd), 4),
        "tpd": 0.0,
        "wr": 0.0,
        "days_used": 0,
        "trades_total": 0,
        "trades_eval_total": 0,
        "wins_eval_total": 0,
        "trades_today": 0,
        "frac_day": round(float((now.hour * 3600 + now.minute * 60 + now.second) / 86400.0), 6),
        "thr": None,
        "thr_floor": float(as_float(os.getenv("VOL_SAFE_THR_MIN", os.getenv("VOL_THR_MIN", "0.02")), 0.02)),
        "at_thr_floor": False,
        "asset": ctx.asset,
        "interval_sec": int(ctx.interval_sec),
        "runs_dir": str(ctx.runs_dir),
        "lookback_days": int(as_int(os.getenv("ISO_BLEND_LOOKBACK_DAYS", "7"), 7)),
        "summary_scan": summary_scan,
        "summary_fail_closed": True,
        "timezone": ctx.timezone,
    }


def compute_meta_iso_blend(now: datetime | None = None) -> Dict[str, Any]:
    now = now or repo_context().now
    ctx = repo_context(now)
    lookback_days = as_int(os.getenv("ISO_BLEND_LOOKBACK_DAYS", "7"), 7)
    min_trades_eval = as_int(os.getenv("ISO_BLEND_MIN_TRADES_EVAL", "30"), 30)
    margin = as_float(os.getenv("ISO_BLEND_MARGIN", "0.01"), 0.01)
    min_blend = as_float(os.getenv("ISO_BLEND_MIN", "0.75"), 0.75)
    max_blend = as_float(os.getenv("ISO_BLEND_MAX", "1.00"), 1.00)
    step = as_float(os.getenv("ISO_BLEND_STEP", "0.05"), 0.05)
    bootstrap_blend = as_float(os.getenv("ISO_BLEND_BOOTSTRAP", str(max_blend)), max_blend)
    payout = as_float(os.getenv("PAYOUT", "0.8"), 0.8)
    break_even = break_even_from_payout(payout)
    target_tpd = as_float(os.getenv("ISO_BLEND_TARGET_TPD", os.getenv("VOL_TARGET_TRADES_PER_DAY", "1.0")), 1.0)
    thr = as_float(os.getenv("THRESHOLD", "nan"), math.nan)
    thr_floor = as_float(os.getenv("VOL_SAFE_THR_MIN", os.getenv("VOL_THR_MIN", "0.02")), 0.02)
    at_thr_floor = (not math.isnan(thr)) and (thr <= thr_floor + 1e-12)

    scan_result = collect_checked_summaries(
        now=now,
        lookback_days=lookback_days,
        asset=ctx.asset,
        interval_sec=ctx.interval_sec,
        runs_dir=ctx.runs_dir,
    )
    summaries = scan_result.summaries
    scan = scan_result.scan
    today_key = now.strftime("%Y-%m-%d")
    cur_blend = _current_blend(bootstrap_blend, min_blend, max_blend)

    if not summaries:
        return _keep_payload(
            decision="summary_missing_keep",
            cur_blend=cur_blend,
            min_blend=min_blend,
            max_blend=max_blend,
            step=step,
            break_even=break_even,
            margin=margin,
            target_tpd=target_tpd,
            now=now,
            summary_scan=scan,
        )
    if today_key not in set(scan.get("used_days") or []):
        return _keep_payload(
            decision="summary_today_missing_keep",
            cur_blend=cur_blend,
            min_blend=min_blend,
            max_blend=max_blend,
            step=step,
            break_even=break_even,
            margin=margin,
            target_tpd=target_tpd,
            now=now,
            summary_scan=scan,
        )

    days_used = 0
    trades_exec_total = 0
    trades_eval_total = 0
    wins_eval_total = 0
    today_compact = now.strftime("%Y%m%d")
    trades_today = 0
    for day, s in summaries[:lookback_days]:
        day_key = day.replace('-', '')
        t_exec = as_int(s.get("trades_total"), 0)
        t_eval = as_int(s.get("trades_eval_total"), t_exec)
        w_eval = as_int(s.get("wins_eval_total") if "wins_eval_total" in s else (s.get("trades_won") or s.get("wins_total") or s.get("wins")), 0)
        trades_exec_total += t_exec
        trades_eval_total += t_eval
        wins_eval_total += w_eval
        days_used += 1
        if day_key == today_compact:
            trades_today = t_exec
    tpd = (trades_exec_total / days_used) if days_used > 0 else 0.0
    wr = (wins_eval_total / trades_eval_total) if trades_eval_total > 0 else 0.0
    sec = now.hour * 3600 + now.minute * 60 + now.second
    frac_day = sec / 86400.0
    decision = "keep"
    new_blend = cur_blend
    if trades_eval_total < min_trades_eval or days_used < 2:
        new_blend = max(min(bootstrap_blend, max_blend), min_blend)
        decision = "bootstrap_set_blend"
        if trades_today == 0 and frac_day >= 0.50 and at_thr_floor:
            new_blend = max(min_blend, new_blend - step)
            decision = "bootstrap_no_trades_relax_one_step_at_thr_floor"
    else:
        if wr < break_even:
            new_blend = max_blend
            decision = "wr_below_breakeven_force_max"
        elif wr < break_even + margin:
            new_blend = min(max(cur_blend, 0.95), max_blend)
            decision = "wr_thin_keep_conservative"
        else:
            if tpd < target_tpd:
                if at_thr_floor:
                    new_blend = max(min_blend, cur_blend - step)
                    decision = "tpd_low_relax_blend"
                else:
                    new_blend = cur_blend
                    decision = "tpd_low_wait_threshold"
            elif tpd > target_tpd * 1.50:
                new_blend = min(max_blend, cur_blend + step)
                decision = "tpd_high_tighten_blend"
            else:
                new_blend = cur_blend
                decision = "keep"
    return {
        "decision": decision,
        "meta_iso_blend": round(float(new_blend), 4),
        "cur_blend": round(float(cur_blend), 4),
        "min_blend": round(float(min_blend), 4),
        "max_blend": round(float(max_blend), 4),
        "step": round(float(step), 4),
        "break_even": round(float(break_even), 6),
        "margin": round(float(margin), 4),
        "target_tpd": round(float(target_tpd), 4),
        "tpd": round(float(tpd), 4),
        "wr": round(float(wr), 4),
        "days_used": int(days_used),
        "trades_total": int(trades_exec_total),
        "trades_eval_total": int(trades_eval_total),
        "wins_eval_total": int(wins_eval_total),
        "trades_today": int(trades_today),
        "frac_day": round(float(frac_day), 6),
        "thr": None if math.isnan(thr) else float(thr),
        "thr_floor": float(thr_floor),
        "at_thr_floor": bool(at_thr_floor),
        "asset": ctx.asset,
        "interval_sec": int(ctx.interval_sec),
        "timezone": ctx.timezone,
        "runs_dir": str(ctx.runs_dir),
        "lookback_days": int(lookback_days),
        "summary_scan": scan,
        "summary_fail_closed": False,
    }
