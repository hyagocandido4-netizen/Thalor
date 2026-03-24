from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ..config.compat_helpers import portable_path_str
from ..portfolio.paths import resolve_scope_runtime_paths
from .learned_gate import build_training_rows


def _existing_unique(paths: Iterable[str | Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        p = Path(raw)
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        if rp.exists():
            out.append(rp)
    return out


def discover_signal_db_paths(
    *,
    repo_root: str | Path,
    scope_tag: str,
    explicit_signals_db_path: str | Path | None = None,
) -> list[Path]:
    root = Path(repo_root).resolve()
    candidates: list[Path] = []
    if explicit_signals_db_path not in (None, ''):
        p = Path(explicit_signals_db_path)
        if not p.is_absolute():
            p = root / p
        candidates.append(p)

    candidates.extend(
        [
            resolve_scope_runtime_paths(root, scope_tag=scope_tag, partition_enable=False).signals_db_path,
            resolve_scope_runtime_paths(root, scope_tag=scope_tag, partition_enable=True).signals_db_path,
            root / 'runs' / 'signals' / str(scope_tag) / 'live_signals.sqlite3',
            root / 'runs' / 'live_signals.sqlite3',
        ]
    )
    return _existing_unique(candidates)


def _prefer_row(current: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if current is None:
        return dict(candidate)
    cur_inferred = bool(current.get('inferred_direction'))
    cand_inferred = bool(candidate.get('inferred_direction'))
    if cur_inferred and not cand_inferred:
        return dict(candidate)
    cur_ts = int(current.get('ts') or 0)
    cand_ts = int(candidate.get('ts') or 0)
    if cand_ts > cur_ts:
        return dict(candidate)
    return dict(current)


def recover_training_rows(
    *,
    repo_root: str | Path,
    scope_tag: str,
    dataset_path: str | Path,
    asset: str,
    interval_sec: int,
    timezone_name: str,
    slot_profile: dict[str, Any] | None = None,
    explicit_signals_db_path: str | Path | None = None,
    min_rows: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    dataset_p = Path(dataset_path)
    if not dataset_p.is_absolute():
        dataset_p = Path(repo_root).resolve() / dataset_p

    signal_paths = discover_signal_db_paths(
        repo_root=repo_root,
        scope_tag=scope_tag,
        explicit_signals_db_path=explicit_signals_db_path,
    )

    by_ts: dict[int, dict[str, Any]] = {}
    source_stats: list[dict[str, Any]] = []
    explicit_total = 0
    inferred_total = 0

    for sig_path in signal_paths:
        explicit_rows = build_training_rows(
            signals_db_path=sig_path,
            dataset_path=dataset_p,
            asset=asset,
            interval_sec=interval_sec,
            timezone_name=timezone_name,
            slot_profile=slot_profile,
            limit=None,
            include_holds=False,
        )
        inferred_rows: list[dict[str, Any]] = []
        if len(explicit_rows) < int(max(1, min_rows)):
            inferred_rows = build_training_rows(
                signals_db_path=sig_path,
                dataset_path=dataset_p,
                asset=asset,
                interval_sec=interval_sec,
                timezone_name=timezone_name,
                slot_profile=slot_profile,
                limit=None,
                include_holds=True,
            )
        for row in explicit_rows + inferred_rows:
            try:
                ts = int(row.get('ts') or 0)
            except Exception:
                continue
            by_ts[ts] = _prefer_row(by_ts.get(ts), row)
        explicit_total += sum(1 for row in explicit_rows if not bool(row.get('inferred_direction')))
        inferred_total += sum(1 for row in inferred_rows if bool(row.get('inferred_direction')))
        source_stats.append(
            {
                'signals_db_path': portable_path_str(sig_path),
                'explicit_rows': int(sum(1 for row in explicit_rows if not bool(row.get('inferred_direction')))),
                'inferred_rows': int(sum(1 for row in inferred_rows if bool(row.get('inferred_direction')))),
            }
        )

    rows = [dict(by_ts[k]) for k in sorted(by_ts)]
    strategy = 'explicit_trades'
    if len(rows) < int(max(1, min_rows)):
        strategy = 'explicit_and_inferred_insufficient'
    elif any(bool(row.get('inferred_direction')) for row in rows):
        strategy = 'recovered_with_inferred_hold_rows'

    meta = {
        'training_strategy': strategy,
        'training_sources': source_stats,
        'explicit_trade_rows': int(sum(1 for row in rows if not bool(row.get('inferred_direction')))),
        'inferred_hold_rows': int(sum(1 for row in rows if bool(row.get('inferred_direction')))),
        'discovered_signal_db_paths': [portable_path_str(p) for p in signal_paths],
        'min_rows_target': int(max(1, min_rows)),
        'raw_explicit_rows_total': int(explicit_total),
        'raw_inferred_rows_total': int(inferred_total),
    }
    return rows, meta



def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _window_id_for_ts(ts: int, timezone_name: str) -> str:
    try:
        tz = ZoneInfo(str(timezone_name or 'UTC'))
    except Exception:
        tz = UTC
    dt = datetime.fromtimestamp(int(ts), tz=UTC).astimezone(tz)
    return dt.strftime('%Y-%m-%d')


def _chunk_training_rows(rows: list[dict[str, Any]], *, target_windows: int) -> list[list[dict[str, Any]]]:
    if not rows:
        return []
    target = max(1, min(int(target_windows), len(rows)))
    chunk_size = max(1, (len(rows) + target - 1) // target)
    out: list[list[dict[str, Any]]] = []
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        if chunk:
            out.append(chunk)
    return out


def synthesize_multiwindow_summary_from_training_rows(
    rows: Iterable[dict[str, Any]],
    *,
    timezone_name: str,
    min_windows: int = 3,
) -> dict[str, Any] | None:
    ordered = sorted([dict(row) for row in rows if isinstance(row, dict)], key=lambda row: int(row.get('ts') or 0))
    if not ordered:
        return None

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in ordered:
        try:
            ts = int(row.get('ts') or 0)
        except Exception:
            continue
        key = _window_id_for_ts(ts, timezone_name)
        grouped.setdefault(key, []).append(row)

    windows: list[dict[str, Any]] = []
    window_strategy = 'local_day'
    if len(grouped) >= max(1, int(min_windows)):
        for key in sorted(grouped):
            batch = grouped[key]
            if not batch:
                continue
            hits = [int(bool(item.get('correct'))) for item in batch]
            trades = len(hits)
            hit_rate = float(sum(hits) / trades) if trades > 0 else None
            if hit_rate is None:
                continue
            windows.append({
                'window_id': str(key),
                'topk_hit_weighted': float(hit_rate),
                'topk_taken': int(trades),
                'source': 'training_rows',
            })
    else:
        window_strategy = 'sequential_chunks'
        for idx, batch in enumerate(_chunk_training_rows(ordered, target_windows=max(1, int(min_windows))), start=1):
            if not batch:
                continue
            hits = [int(bool(item.get('correct'))) for item in batch]
            trades = len(hits)
            hit_rate = float(sum(hits) / trades) if trades > 0 else None
            if hit_rate is None:
                continue
            try:
                start_ts = int(batch[0].get('ts') or 0)
                end_ts = int(batch[-1].get('ts') or 0)
            except Exception:
                start_ts = 0
                end_ts = 0
            windows.append({
                'window_id': f'chunk_{idx:02d}',
                'topk_hit_weighted': float(hit_rate),
                'topk_taken': int(trades),
                'source': 'training_rows',
                'start_ts': int(start_ts),
                'end_ts': int(end_ts),
            })

    if not windows:
        return None

    total_taken = sum(max(1, int(item.get('topk_taken') or 0)) for item in windows)
    weighted_hit = sum(float(item.get('topk_hit_weighted') or 0.0) * max(1, int(item.get('topk_taken') or 0)) for item in windows) / max(1, total_taken)
    return {
        'kind': 'synthetic_multiwindow_summary',
        'schema_version': 'phase1-intelligence-recovery-v2',
        'source': 'training_rows_fallback',
        'window_strategy': window_strategy,
        'per_window': windows,
        'best': {
            'topk_hit_weighted': float(weighted_hit),
            'topk_taken_total': int(total_taken),
            'per_window': windows,
        },
    }

def _window_payload_from_rows(window_id: str, rows: list[dict[str, Any]], *, source: str) -> dict[str, Any] | None:
    if not rows:
        return None
    hits = [int(bool(item.get('correct'))) for item in rows]
    trades = int(len(hits))
    if trades <= 0:
        return None
    explicit_rows = int(sum(1 for item in rows if not bool(item.get('inferred_direction'))))
    inferred_rows = int(sum(1 for item in rows if bool(item.get('inferred_direction'))))
    try:
        start_ts = int(rows[0].get('ts') or 0)
        end_ts = int(rows[-1].get('ts') or 0)
    except Exception:
        start_ts = 0
        end_ts = 0
    return {
        'window_id': str(window_id),
        'topk_hit_weighted': float(sum(hits) / trades),
        'topk_taken': trades,
        'source': str(source),
        'explicit_rows': explicit_rows,
        'inferred_rows': inferred_rows,
        'start_ts': int(start_ts),
        'end_ts': int(end_ts),
    }


def _window_id_for_ts_hour(ts: int, timezone_name: str) -> str:
    try:
        tz = ZoneInfo(str(timezone_name or 'UTC'))
    except Exception:
        tz = UTC
    dt = datetime.fromtimestamp(int(ts), tz=UTC).astimezone(tz)
    return dt.strftime('%Y-%m-%dT%H')


def synthesize_multiwindow_summary_from_signal_rows(
    rows: Iterable[dict[str, Any]],
    *,
    timezone_name: str,
    min_windows: int = 3,
    min_trades_window: int = 1,
) -> dict[str, Any] | None:
    ordered = sorted([dict(row) for row in rows if isinstance(row, dict)], key=lambda row: int(row.get('ts') or 0))
    if not ordered:
        return None

    day_groups: dict[str, list[dict[str, Any]]] = {}
    for row in ordered:
        try:
            ts = int(row.get('ts') or 0)
        except Exception:
            continue
        key = _window_id_for_ts(ts, timezone_name)
        day_groups.setdefault(key, []).append(row)

    windows: list[dict[str, Any]] = []
    window_strategy = 'local_day'
    for key in sorted(day_groups):
        batch = day_groups[key]
        if len(batch) < int(max(1, min_trades_window)):
            continue
        payload = _window_payload_from_rows(str(key), batch, source='signals_eval')
        if payload is not None:
            windows.append(payload)

    if len(windows) < max(1, int(min_windows)):
        window_strategy = 'day_hour'
        windows = []
        hour_groups: dict[str, list[dict[str, Any]]] = {}
        for row in ordered:
            try:
                ts = int(row.get('ts') or 0)
            except Exception:
                continue
            key = _window_id_for_ts_hour(ts, timezone_name)
            hour_groups.setdefault(key, []).append(row)
        for key in sorted(hour_groups):
            batch = hour_groups[key]
            if len(batch) < int(max(1, min_trades_window)):
                continue
            payload = _window_payload_from_rows(str(key), batch, source='signals_eval')
            if payload is not None:
                windows.append(payload)

    if len(windows) < max(1, int(min_windows)):
        window_strategy = 'sequential_chunks'
        windows = []
        for idx, batch in enumerate(_chunk_training_rows(ordered, target_windows=max(1, int(min_windows))), start=1):
            if len(batch) < int(max(1, min_trades_window)):
                continue
            payload = _window_payload_from_rows(f'chunk_{idx:02d}', batch, source='signals_eval')
            if payload is not None:
                windows.append(payload)

    if not windows:
        return None

    total_taken = sum(max(1, int(item.get('topk_taken') or 0)) for item in windows)
    weighted_hit = sum(float(item.get('topk_hit_weighted') or 0.0) * max(1, int(item.get('topk_taken') or 0)) for item in windows) / max(1, total_taken)
    explicit_total = sum(max(0, int(item.get('explicit_rows') or 0)) for item in windows)
    inferred_total = sum(max(0, int(item.get('inferred_rows') or 0)) for item in windows)
    return {
        'kind': 'signals_eval_multiwindow_summary',
        'schema_version': 'phase1-intelligence-data-v1',
        'source': 'signals_eval_fallback',
        'window_strategy': window_strategy,
        'per_window': windows,
        'best': {
            'topk_hit_weighted': float(weighted_hit),
            'topk_taken_total': int(total_taken),
            'explicit_rows_total': int(explicit_total),
            'inferred_rows_total': int(inferred_total),
            'per_window': windows,
        },
    }


def synthesize_multiwindow_summary_from_daily_hourly_summaries(
    summaries: Iterable[tuple[str, dict[str, Any]]],
    *,
    min_windows: int = 3,
    min_trades_window: int = 1,
) -> dict[str, Any] | None:
    hourly_windows: list[dict[str, Any]] = []
    slot_windows: list[dict[str, Any]] = []
    for day, payload in summaries:
        if not isinstance(payload, dict):
            continue
        by_hour = payload.get('by_hour') if isinstance(payload.get('by_hour'), dict) else {}
        for hour, item in sorted(by_hour.items()):
            if not isinstance(item, dict):
                continue
            trades = _safe_int(item.get('trades'), 0)
            if trades < int(max(1, min_trades_window)):
                continue
            hit = _safe_float(item.get('win_rate'))
            if hit is None:
                wins = _safe_float(item.get('wins'))
                hit = (wins / float(trades)) if wins is not None and trades > 0 else None
            if hit is None:
                continue
            hourly_windows.append({
                'window_id': f'{day}T{str(hour).zfill(2)}',
                'topk_hit_weighted': float(hit),
                'topk_taken': int(trades),
                'source': 'daily_summary.by_hour',
                'day': str(day),
                'hour': str(hour).zfill(2),
                'wins': _safe_int(item.get('wins'), 0),
                'losses': _safe_int(item.get('losses'), 0),
                'ev_mean': _safe_float(item.get('ev_mean')),
            })
        by_slot = payload.get('winrate_by_slot') if isinstance(payload.get('winrate_by_slot'), dict) else {}
        for slot_key, item in sorted(by_slot.items(), key=lambda pair: _safe_int(pair[0], 0)):
            if not isinstance(item, dict):
                continue
            trades = _safe_int(item.get('trades'), 0)
            if trades < int(max(1, min_trades_window)):
                continue
            hit = _safe_float(item.get('win_rate'))
            if hit is None:
                continue
            slot_windows.append({
                'window_id': f'{day}#slot_{slot_key}',
                'topk_hit_weighted': float(hit),
                'topk_taken': int(trades),
                'source': 'daily_summary.winrate_by_slot',
                'day': str(day),
                'slot': int(_safe_int(item.get('slot'), _safe_int(slot_key, 0)) or 0),
                'wins': _safe_int(item.get('wins'), 0),
                'ev_avg': _safe_float(item.get('ev_avg')),
                'score_avg': _safe_float(item.get('score_avg')),
            })

    windows = hourly_windows if len(hourly_windows) >= max(1, int(min_windows)) else slot_windows
    if not windows:
        return None
    total_taken = sum(max(1, int(item.get('topk_taken') or 0)) for item in windows)
    weighted_hit = sum(float(item.get('topk_hit_weighted') or 0.0) * max(1, int(item.get('topk_taken') or 0)) for item in windows) / max(1, total_taken)
    return {
        'kind': 'daily_hourly_multiwindow_summary',
        'schema_version': 'phase1-intelligence-data-v1',
        'source': 'daily_hourly_summary_fallback',
        'window_strategy': 'day_hour' if windows is hourly_windows else 'slot',
        'per_window': windows,
        'best': {
            'topk_hit_weighted': float(weighted_hit),
            'topk_taken_total': int(total_taken),
            'per_window': windows,
        },
    }


def synthesize_multiwindow_summary_from_daily_summaries(
    summaries: Iterable[tuple[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    windows: list[dict[str, Any]] = []
    for day, payload in summaries:
        if not isinstance(payload, dict):
            continue
        trades = payload.get('trades_eval_total')
        if trades in (None, ''):
            trades = payload.get('trades_total')
        try:
            trades_i = int(trades or 0)
        except Exception:
            trades_i = 0
        if trades_i <= 0:
            continue
        hit = payload.get('win_rate_eval_total')
        if hit in (None, ''):
            try:
                wins = float(payload.get('wins_eval_total') or 0.0)
                hit = wins / float(trades_i) if trades_i > 0 else None
            except Exception:
                hit = None
        try:
            hit_f = float(hit)
        except Exception:
            hit_f = None
        if hit_f is None:
            continue
        windows.append(
            {
                'window_id': str(day),
                'topk_hit_weighted': float(hit_f),
                'topk_taken': int(trades_i),
                'source': 'daily_summary',
            }
        )
    if not windows:
        return None
    total_taken = sum(max(1, int(item.get('topk_taken') or 0)) for item in windows)
    weighted_hit = sum(float(item.get('topk_hit_weighted') or 0.0) * max(1, int(item.get('topk_taken') or 0)) for item in windows) / max(1, total_taken)
    return {
        'kind': 'synthetic_multiwindow_summary',
        'schema_version': 'phase1-intelligence-recovery-v1',
        'source': 'daily_summary_fallback',
        'per_window': windows,
        'best': {
            'topk_hit_weighted': float(weighted_hit),
            'topk_taken_total': int(total_taken),
            'per_window': windows,
        },
    }


__all__ = [
    'discover_signal_db_paths',
    'recover_training_rows',
    'synthesize_multiwindow_summary_from_daily_hourly_summaries',
    'synthesize_multiwindow_summary_from_daily_summaries',
    'synthesize_multiwindow_summary_from_signal_rows',
    'synthesize_multiwindow_summary_from_training_rows',
]
