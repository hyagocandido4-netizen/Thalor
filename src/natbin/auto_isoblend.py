# --- P16: auto META_ISO_BLEND ---
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from .summary_paths import load_daily_summary_checked, repo_asset, repo_interval_sec, repo_now, repo_timezone_name


def _as_float(x: Any, default: float) -> float:
    try:
        if x is None:
            return default
        s = str(x).strip().replace(",", ".")
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


def _as_int(x: Any, default: int) -> int:
    try:
        if x is None:
            return default
        s = str(x).strip()
        if s == "":
            return default
        return int(float(s.replace(",", ".")))
    except Exception:
        return default


def _current_blend(bootstrap_blend: float, min_blend: float, max_blend: float) -> float:
    cur_blend = _as_float(os.getenv("META_ISO_BLEND", str(bootstrap_blend)), bootstrap_blend)
    return max(min(cur_blend, max_blend), min_blend)


def _collect_summaries(*, now: datetime, lookback_days: int, asset: str, interval_sec: int, runs_dir: Path) -> tuple[list[tuple[str, dict]], dict]:
    out: list[tuple[str, dict]] = []
    expected_tz = repo_timezone_name()
    requested_days: list[str] = []
    used_days: list[str] = []
    missing_days: list[str] = []
    invalid_days: list[dict] = []
    sources: list[dict] = []
    strict = True
    legacy_fallback_count = 0
    for i in range(max(1, lookback_days)):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        requested_days.append(day)
        s, spath, meta = load_daily_summary_checked(
            day=day,
            asset=asset,
            interval_sec=interval_sec,
            out_dir=runs_dir,
            expected_timezone=expected_tz,
        )
        strict = bool(meta.get("strict", strict))
        if isinstance(s, dict):
            out.append((day, s))
            used_days.append(day)
            if bool(meta.get("legacy_fallback_used", False)):
                legacy_fallback_count += 1
            sources.append({"day": day, "path": str(spath) if spath else str(meta.get("path") or ""), "source": str(meta.get("source") or "missing")})
            continue
        if str(meta.get("status") or "") == "invalid":
            invalid_days.append({"day": day, "path": str(meta.get("path") or ""), "source": str(meta.get("source") or "invalid"), "issues": meta.get("issues") or []})
        else:
            missing_days.append(day)
    return out, {
        "strict": bool(strict),
        "expected_asset": str(asset),
        "expected_interval_sec": int(interval_sec),
        "expected_timezone": expected_tz,
        "requested_days": requested_days,
        "used_days": used_days,
        "missing_days": missing_days,
        "invalid_days": invalid_days,
        "sources": sources,
        "used_count": len(used_days),
        "missing_count": len(missing_days),
        "invalid_count": len(invalid_days),
        "legacy_fallback_count": int(legacy_fallback_count),
    }


def _keep_payload(*, decision: str, cur_blend: float, min_blend: float, max_blend: float, step: float, break_even: float, margin: float, target_tpd: float, now: datetime, asset: str, runs_dir: Path, lookback_days: int, summary_scan: dict) -> Dict[str, Any]:
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
        "thr_floor": float(_as_float(os.getenv("VOL_SAFE_THR_MIN", os.getenv("VOL_THR_MIN", "0.02")), 0.02)),
        "at_thr_floor": False,
        "asset": asset,
        "interval_sec": int(repo_interval_sec()),
        "runs_dir": str(runs_dir),
        "lookback_days": int(lookback_days),
        "summary_scan": summary_scan,
        "summary_fail_closed": True,
        "timezone": repo_timezone_name(),
    }


def compute_meta_iso_blend(now: datetime | None = None) -> Dict[str, Any]:
    now = now or repo_now()

    runs_dir = Path(os.getenv("RUNS_DIR", "runs")).resolve()
    asset = repo_asset()
    interval_sec = repo_interval_sec()
    lookback_days = _as_int(os.getenv("ISO_BLEND_LOOKBACK_DAYS", "7"), 7)
    min_trades_eval = _as_int(os.getenv("ISO_BLEND_MIN_TRADES_EVAL", "30"), 30)

    # Prefer win-rate; only relax blend if we're already comfortably above breakeven
    margin = _as_float(os.getenv("ISO_BLEND_MARGIN", "0.01"), 0.01)

    min_blend = _as_float(os.getenv("ISO_BLEND_MIN", "0.75"), 0.75)
    max_blend = _as_float(os.getenv("ISO_BLEND_MAX", "1.00"), 1.00)
    step = _as_float(os.getenv("ISO_BLEND_STEP", "0.05"), 0.05)

    # Where to snap during bootstrap / no-eval: keep max blend (more conservative)
    bootstrap_blend = _as_float(os.getenv("ISO_BLEND_BOOTSTRAP", str(max_blend)), max_blend)

    payout = _as_float(os.getenv("PAYOUT", "0.8"), 0.8)
    break_even = 1.0 / (1.0 + payout) if payout > 0 else 0.5

    # Target trades/day can be inherited from auto_volume if present
    target_tpd = _as_float(os.getenv("ISO_BLEND_TARGET_TPD", os.getenv("VOL_TARGET_TRADES_PER_DAY", "1.0")), 1.0)

    # Optional: only relax blend when THRESHOLD is already near its floor.
    thr = _as_float(os.getenv("THRESHOLD", "nan"), math.nan)
    thr_floor = _as_float(os.getenv("VOL_SAFE_THR_MIN", os.getenv("VOL_THR_MIN", "0.02")), 0.02)
    at_thr_floor = (not math.isnan(thr)) and (thr <= thr_floor + 1e-12)

    summaries, scan = _collect_summaries(now=now, lookback_days=lookback_days, asset=asset, interval_sec=interval_sec, runs_dir=runs_dir)
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
            asset=asset,
            runs_dir=runs_dir,
            lookback_days=lookback_days,
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
            asset=asset,
            runs_dir=runs_dir,
            lookback_days=lookback_days,
            summary_scan=scan,
        )

    # Aggregate summaries
    days_used = 0
    trades_exec_total = 0
    trades_eval_total = 0
    wins_eval_total = 0
    today_compact = now.strftime("%Y%m%d")
    trades_today = 0

    for i, (day, s) in enumerate(summaries[:lookback_days]):
        day_key = day.replace('-', '')
        t_exec = _as_int(s.get("trades_total"), 0)
        if "trades_eval_total" in s:
            t_eval = _as_int(s.get("trades_eval_total"), 0)
        else:
            t_eval = t_exec

        if "wins_eval_total" in s:
            w_eval = _as_int(s.get("wins_eval_total"), 0)
        else:
            w_eval = _as_int(s.get("trades_won") or s.get("wins_total") or s.get("wins"), 0)

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
            new_blend = max(cur_blend, 0.95)
            new_blend = min(new_blend, max_blend)
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

    out = {
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
        "asset": asset,
        "interval_sec": int(interval_sec),
        "timezone": repo_timezone_name(),
        "runs_dir": str(runs_dir),
        "lookback_days": int(lookback_days),
        "summary_scan": scan,
        "summary_fail_closed": False,
    }
    return out


def main() -> None:
    out = compute_meta_iso_blend()
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
