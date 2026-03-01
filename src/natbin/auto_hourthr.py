# --- P17: auto hour threshold ---
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Tuple

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


def _load_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


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


def _keep_payload(*, decision: str, now: datetime, asset: str, interval_sec: int, runs_dir: Path, lookback_days: int, min_trades_hour: int, payout: float, thr_in_raw: str, thr_floor: float, thr_ceil: float, scan: dict) -> Dict[str, Any]:
    break_even = 1.0 / (1.0 + payout) if payout > 0 else 0.5
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
        "lookback_days": int(lookback_days),
        "days_used": 0,
        "min_trades_hour": int(min_trades_hour),
        "break_even": round(float(break_even), 6),
        "margin": round(float(_as_float(os.getenv("P17_WR_MARGIN", "0.01"), 0.01)), 4),
        "asset": asset,
        "interval_sec": int(interval_sec),
        "timezone": repo_timezone_name(),
        "runs_dir": str(runs_dir),
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
    # supports dict with keys "13"/"13:00"/"13h"/"13:00:00" or list[24]
    if isinstance(container, (list, tuple)):
        if 0 <= hour < len(container):
            return container[hour]
        return None
    if not isinstance(container, dict):
        return None

    keys = [
        f"{hour:02d}",
        str(hour),
        f"{hour:02d}:00",
        f"{hour}:00",
        f"{hour:02d}:00:00",
        f"{hour}h",
    ]
    for k in keys:
        if k in container:
            return container[k]
    return None


def _extract_hour_stats(summary: Dict[str, Any], hour: int) -> Tuple[int, int, float]:
    # returns (count, wins, ev_mean_or_nan)
    by_hour = _get_dict(summary, "by_hour", "hours", "hourly", "per_hour")
    entry = _get_hour_value(by_hour, hour) if by_hour else None

    count = 0
    wins = 0
    ev_mean = math.nan

    if isinstance(entry, dict):
        count = _as_int(entry.get("count") or entry.get("taken") or entry.get("trades") or entry.get("n") or entry.get("total"), 0)
        wins = _as_int(entry.get("won") or entry.get("wins") or entry.get("w"), 0)

        wr = _as_float(entry.get("wr") or entry.get("hit") or entry.get("win_rate"), math.nan)
        if wins == 0 and count > 0 and not math.isnan(wr):
            wins = int(round(wr * count))

        ev_mean = _as_float(
            entry.get("ev_mean") or entry.get("ev_avg") or entry.get("mean_ev") or entry.get("ev"),
            math.nan,
        )
        if math.isnan(ev_mean):
            ev_total = entry.get("ev_total") or entry.get("ev_sum")
            if ev_total is not None and count > 0:
                ev_mean = _as_float(ev_total, math.nan) / max(1, count)

    elif entry is not None:
        # sometimes by_hour[hour] == count
        count = _as_int(entry, 0)

    # fallback maps (if entry didn't carry wins/ev)
    if count > 0 and wins == 0:
        wins_map = _get_dict(summary, "wins_by_hour", "hour_wins", "wins_hour", "by_hour_wins")
        v = _get_hour_value(wins_map, hour) if wins_map else None
        if v is not None:
            wins = _as_int(v, 0)

    if count > 0 and math.isnan(ev_mean):
        ev_map = _get_dict(summary, "ev_mean_by_hour", "hour_ev_mean", "ev_by_hour_mean", "by_hour_ev_mean")
        v = _get_hour_value(ev_map, hour) if ev_map else None
        if v is not None:
            ev_mean = _as_float(v, math.nan)

    return count, wins, ev_mean


def compute_hour_threshold(now: datetime | None = None) -> Dict[str, Any]:
    now = now or repo_now()

    if os.getenv("P17_ENABLE", "1") == "0":
        return {"decision": "disabled"}

    runs_dir = Path(os.getenv("RUNS_DIR", "runs")).resolve()
    asset = repo_asset()
    interval_sec = repo_interval_sec()
    lookback_days = _as_int(os.getenv("P17_LOOKBACK_DAYS", "14"), 14)
    min_trades_hour = _as_int(os.getenv("P17_MIN_TRADES_HOUR", "8"), 8)

    payout = _as_float(os.getenv("PAYOUT", "0.8"), 0.8)
    break_even = 1.0 / (1.0 + payout) if payout > 0 else 0.5
    margin = _as_float(os.getenv("P17_WR_MARGIN", "0.01"), 0.01)

    good_ev = _as_float(os.getenv("P17_GOOD_EV", "0.02"), 0.02)
    bad_ev = _as_float(os.getenv("P17_BAD_EV", "-0.02"), -0.02)

    mult_good = _as_float(os.getenv("P17_MULT_GOOD", "0.95"), 0.95)
    mult_bad = _as_float(os.getenv("P17_MULT_BAD", "1.05"), 1.05)
    mult_min = _as_float(os.getenv("P17_MULT_MIN", "0.90"), 0.90)
    mult_max = _as_float(os.getenv("P17_MULT_MAX", "1.10"), 1.10)

    thr_floor = _as_float(os.getenv("VOL_SAFE_THR_MIN", os.getenv("VOL_THR_MIN", "0.02")), 0.02)
    thr_ceil = _as_float(os.getenv("P17_THR_CEIL", "0.20"), 0.20)

    thr_in_raw = os.getenv("THRESHOLD", "")
    thr_in = _as_float(thr_in_raw, math.nan)

    hour = now.hour

    summaries, scan = _collect_summaries(now=now, lookback_days=lookback_days, asset=asset, interval_sec=interval_sec, runs_dir=runs_dir)
    today_day = now.strftime("%Y-%m-%d")
    if not summaries:
        return _keep_payload(
            decision="summary_missing_keep",
            now=now, asset=asset, interval_sec=interval_sec, runs_dir=runs_dir,
            lookback_days=lookback_days, min_trades_hour=min_trades_hour, payout=payout,
            thr_in_raw=thr_in_raw, thr_floor=thr_floor, thr_ceil=thr_ceil, scan=scan,
        )
    if today_day not in set(scan.get("used_days") or []):
        return _keep_payload(
            decision="summary_today_missing_keep",
            now=now, asset=asset, interval_sec=interval_sec, runs_dir=runs_dir,
            lookback_days=lookback_days, min_trades_hour=min_trades_hour, payout=payout,
            thr_in_raw=thr_in_raw, thr_floor=thr_floor, thr_ceil=thr_ceil, scan=scan,
        )

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
            ev_weight_sum += float(evm) * c
            ev_weight_n += c

    wr_hour = (h_wins / h_trades) if h_trades > 0 else 0.0

    if ev_weight_n > 0:
        ev_hour = ev_weight_sum / ev_weight_n
    elif h_trades > 0:
        # estimate EV from WR + payout
        ev_hour = wr_hour * (1.0 + payout) - 1.0
    else:
        ev_hour = 0.0

    decision = "keep"
    hour_mult = 1.0

    if h_trades < min_trades_hour:
        decision = "insufficient_hour_trades_keep"
        hour_mult = 1.0
    else:
        if (ev_hour <= bad_ev) or (wr_hour < break_even):
            decision = "bad_hour_tighten"
            hour_mult = mult_bad
        elif (ev_hour >= good_ev) and (wr_hour >= break_even + margin):
            decision = "good_hour_relax"
            hour_mult = mult_good
        else:
            decision = "neutral_hour_keep"
            hour_mult = 1.0

    hour_mult = max(mult_min, min(mult_max, float(hour_mult)))

    thr_out = thr_in
    thr_out_str = thr_in_raw

    if not math.isnan(thr_in):
        thr_out = thr_in * hour_mult
        thr_out = max(thr_floor, min(thr_ceil, thr_out))
        thr_out_str = f"{thr_out:.2f}"

    out = {
        "decision": decision,
        "hour": int(hour),
        "hour_trades": int(h_trades),
        "hour_wins": int(h_wins),
        "hour_wr": round(float(wr_hour), 4),
        "hour_ev_mean": round(float(ev_hour), 6),
        "hour_mult": round(float(hour_mult), 4),
        "threshold_in": thr_in_raw,
        "threshold_out": thr_out_str,
        "thr_floor": float(thr_floor),
        "thr_ceil": float(thr_ceil),
        "lookback_days": int(lookback_days),
        "days_used": int(days_used),
        "min_trades_hour": int(min_trades_hour),
        "break_even": round(float(break_even), 6),
        "margin": round(float(margin), 4),
        "asset": asset,
        "interval_sec": int(interval_sec),
        "timezone": repo_timezone_name(),
        "runs_dir": str(runs_dir),
        "summary_scan": scan,
        "summary_fail_closed": False,
    }
    return out


def main() -> None:
    out = compute_hour_threshold()
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
