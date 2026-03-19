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


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _iter_summaries(summaries: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in summaries:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], dict):
            out.append(dict(item[1]))
        elif isinstance(item, dict):
            out.append(dict(item))
    return out


def build_slot_profile(
    summaries: Iterable[Any],
    *,
    min_trades: int = 6,
    prior_weight: float = 8.0,
    multiplier_min: float = 0.85,
    multiplier_max: float = 1.15,
    score_delta_cap: float = 0.05,
    threshold_delta_cap: float = 0.03,
) -> dict[str, Any]:
    items = _iter_summaries(summaries)
    hours = [f"{i:02d}" for i in range(24)]
    agg: dict[str, dict[str, float]] = {h: {'trades': 0.0, 'wins': 0.0, 'losses': 0.0, 'ev_sum': 0.0, 'obs': 0.0} for h in hours}

    total_trades = 0.0
    total_wins = 0.0
    total_ev_sum = 0.0
    days_used = 0

    for summary in items:
        days_used += 1
        by_hour = summary.get('by_hour') or {}
        obs_by_hour = summary.get('observations_by_hour') or {}
        for h in hours:
            entry = by_hour.get(h) if isinstance(by_hour, dict) else None
            obs = obs_by_hour.get(h, 0) if isinstance(obs_by_hour, dict) else 0
            trades = wins = losses = 0
            ev_mean = 0.0
            if isinstance(entry, dict):
                trades = _safe_int(entry.get('trades') or entry.get('total') or entry.get('count') or entry.get('n'), 0)
                wins = _safe_int(entry.get('wins') or entry.get('won') or entry.get('w'), 0)
                losses = _safe_int(entry.get('losses'), max(0, trades - wins))
                ev_mean = _safe_float(entry.get('ev_mean') or entry.get('ev_avg') or entry.get('mean_ev'), 0.0)
            elif entry is not None:
                trades = _safe_int(entry, 0)
                wins = 0
                losses = trades
                ev_mean = 0.0

            agg[h]['trades'] += float(trades)
            agg[h]['wins'] += float(wins)
            agg[h]['losses'] += float(losses)
            agg[h]['ev_sum'] += float(ev_mean) * float(trades)
            agg[h]['obs'] += float(_safe_int(obs, 0))

            total_trades += float(trades)
            total_wins += float(wins)
            total_ev_sum += float(ev_mean) * float(trades)

    global_wr = (total_wins / total_trades) if total_trades > 0 else 0.5
    global_ev = (total_ev_sum / total_trades) if total_trades > 0 else 0.0

    slot_stats: dict[str, Any] = {}
    for h in hours:
        trades = int(round(agg[h]['trades']))
        wins = int(round(agg[h]['wins']))
        losses = int(round(max(0.0, agg[h]['losses'])))
        obs = int(round(agg[h]['obs']))
        raw_wr = (wins / trades) if trades > 0 else None
        ev_mean = (agg[h]['ev_sum'] / trades) if trades > 0 else None

        # Empirical Bayes shrinkage toward the global baseline.
        denom = trades + float(prior_weight)
        shrunk_wr = ((wins + (global_wr * float(prior_weight))) / denom) if denom > 0 else global_wr
        shrunk_ev = ((agg[h]['ev_sum'] + (global_ev * float(prior_weight))) / denom) if denom > 0 else global_ev
        strength = (trades / denom) if denom > 0 else 0.0
        confidence = strength if trades >= int(min_trades) else (strength * 0.5)

        quality = (0.70 * (shrunk_wr - global_wr)) + (0.30 * (shrunk_ev - global_ev))
        quality_strength = float(quality) * float(strength)
        multiplier = 1.0 + (2.0 * quality_strength)
        multiplier = max(float(multiplier_min), min(float(multiplier_max), float(multiplier)))

        score_delta = _clamp(quality_strength * 0.25, -float(score_delta_cap), float(score_delta_cap))
        threshold_delta = _clamp(-score_delta * 0.60, -float(threshold_delta_cap), float(threshold_delta_cap))
        alpha_delta = _clamp(quality_strength * 0.05, -0.02, 0.02)

        state = 'neutral'
        if trades >= int(min_trades):
            if score_delta >= 0.01:
                state = 'promote'
            elif score_delta <= -0.01:
                state = 'suppress'

        slot_stats[h] = {
            'hour': h,
            'trades': trades,
            'wins': wins,
            'losses': losses,
            'observations': obs,
            'win_rate': raw_wr,
            'ev_mean': ev_mean,
            'shrunk_win_rate': float(shrunk_wr),
            'shrunk_ev_mean': float(shrunk_ev),
            'quality': float(quality_strength),
            'confidence': float(confidence),
            'multiplier': float(multiplier),
            'eligible': bool(trades >= int(min_trades)),
            'expected_share': (float(trades) / float(total_trades)) if total_trades > 0 else 0.0,
            'recommendation': {
                'state': state,
                'score_delta': float(score_delta),
                'threshold_delta': float(threshold_delta),
                'alpha_delta': float(alpha_delta),
            },
        }

    return {
        'kind': 'slot_profile',
        'schema_version': 'phase1-slot-profile-v2',
        'days_used': int(days_used),
        'global': {
            'trades': int(round(total_trades)),
            'wins': int(round(total_wins)),
            'win_rate': float(global_wr),
            'ev_mean': float(global_ev),
            'min_trades': int(min_trades),
            'prior_weight': float(prior_weight),
            'multiplier_min': float(multiplier_min),
            'multiplier_max': float(multiplier_max),
            'score_delta_cap': float(score_delta_cap),
            'threshold_delta_cap': float(threshold_delta_cap),
        },
        'hours': slot_stats,
    }


def slot_key_from_ts(ts: int | None, *, timezone_name: str = 'UTC') -> str:
    if ts is None:
        return '00'
    try:
        tz = ZoneInfo(str(timezone_name or 'UTC'))
    except Exception:
        tz = UTC
    dt = datetime.fromtimestamp(int(ts), tz=UTC).astimezone(tz)
    return f'{int(dt.hour):02d}'


def slot_stats_for_ts(profile: dict[str, Any] | None, ts: int | None, *, timezone_name: str = 'UTC') -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {
            'hour': slot_key_from_ts(ts, timezone_name=timezone_name),
            'multiplier': 1.0,
            'quality': 0.0,
            'eligible': False,
            'recommendation': {
                'state': 'neutral',
                'score_delta': 0.0,
                'threshold_delta': 0.0,
                'alpha_delta': 0.0,
            },
        }
    hour = slot_key_from_ts(ts, timezone_name=timezone_name)
    hours = profile.get('hours') or {}
    entry = dict(hours.get(hour) or {})
    if not entry:
        entry = {'hour': hour, 'multiplier': 1.0, 'quality': 0.0, 'eligible': False}
    entry.setdefault('hour', hour)
    entry.setdefault('multiplier', 1.0)
    entry.setdefault('quality', 0.0)
    entry.setdefault('confidence', 0.0)
    entry.setdefault('eligible', False)
    entry.setdefault('recommendation', {
        'state': 'neutral',
        'score_delta': 0.0,
        'threshold_delta': 0.0,
        'alpha_delta': 0.0,
    })
    return entry
