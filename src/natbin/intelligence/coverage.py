
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _iter_summaries(summaries: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in summaries:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], dict):
            out.append(dict(item[1]))
        elif isinstance(item, dict):
            out.append(dict(item))
    return out


def build_coverage_profile(
    summaries: Iterable[Any],
    *,
    target_trades_per_day: float | None = None,
) -> dict[str, Any]:
    items = _iter_summaries(summaries)
    hours = [f"{i:02d}" for i in range(24)]
    trades_by_hour = {h: 0.0 for h in hours}
    total_days = 0
    total_trades = 0.0

    for summary in items:
        total_days += 1
        raw = summary.get('trades_by_hour') or {}
        by_hour = summary.get('by_hour') or {}
        for h in hours:
            count = 0
            entry = raw.get(h) if isinstance(raw, dict) else None
            if isinstance(entry, dict):
                count = _safe_int(entry.get('total') or entry.get('trades') or entry.get('count') or entry.get('n'), 0)
            elif entry is not None:
                count = _safe_int(entry, 0)
            elif isinstance(by_hour, dict) and isinstance(by_hour.get(h), dict):
                count = _safe_int(by_hour[h].get('trades') or by_hour[h].get('count') or by_hour[h].get('n'), 0)
            trades_by_hour[h] += float(count)
            total_trades += float(count)

    trades_per_day = (total_trades / total_days) if total_days > 0 else 0.0
    target = float(target_trades_per_day) if target_trades_per_day is not None else float(trades_per_day)

    cumulative_share: dict[str, float] = {}
    running = 0.0
    for h in hours:
        running += float(trades_by_hour[h])
        cumulative_share[h] = (running / total_trades) if total_trades > 0 else ((int(h) + 1) / 24.0)

    return {
        'kind': 'coverage_profile',
        'schema_version': 'm5-coverage-profile-v1',
        'days_used': int(total_days),
        'trades_per_day_observed': float(trades_per_day),
        'target_trades_per_day': float(target),
        'hourly_trade_share': {
            h: ((float(trades_by_hour[h]) / float(total_trades)) if total_trades > 0 else (1.0 / 24.0))
            for h in hours
        },
        'cumulative_trade_share': cumulative_share,
    }


def _hour_key_from_ts(ts: int | None, timezone_name: str) -> str:
    if ts is None:
        return '00'
    try:
        tz = ZoneInfo(str(timezone_name or 'UTC'))
    except Exception:
        tz = UTC
    dt = datetime.fromtimestamp(int(ts), tz=UTC).astimezone(tz)
    return f'{int(dt.hour):02d}'


def coverage_bias(
    profile: dict[str, Any] | None,
    *,
    ts: int | None,
    timezone_name: str,
    executed_today: int,
    target_trades_per_day: float,
    tolerance: float = 0.5,
    bias_weight: float = 0.04,
) -> dict[str, Any]:
    hour = _hour_key_from_ts(ts, timezone_name)
    cumulative = {}
    if isinstance(profile, dict):
        cumulative = dict(profile.get('cumulative_trade_share') or {})
    frac = _safe_float(cumulative.get(hour), ((int(hour) + 1) / 24.0))
    target_now = float(target_trades_per_day) * float(frac)
    delta = float(target_now) - float(executed_today)

    bias = 0.0
    if delta > float(tolerance):
        bias = min(float(delta) * float(bias_weight), float(bias_weight) * 2.0)
    elif delta < -float(tolerance):
        bias = max(float(delta) * float(bias_weight), -float(bias_weight) * 2.0)

    return {
        'hour': hour,
        'cumulative_trade_share': float(frac),
        'target_trades_per_day': float(target_trades_per_day),
        'target_trades_now': float(target_now),
        'executed_today': int(executed_today),
        'delta_vs_target_now': float(delta),
        'bias': float(bias),
    }
