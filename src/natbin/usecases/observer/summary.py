from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

from ...state.summary_paths import daily_summary_path


def _p10_mean(xs: list[float | None]) -> float | None:
    import math

    vals: list[float] = []
    for x in xs:
        if x is None:
            continue
        try:
            fx = float(x)
        except Exception:
            continue
        if math.isnan(fx) or math.isinf(fx):
            continue
        vals.append(fx)
    if not vals:
        return None
    return float(sum(vals) / len(vals))


def write_daily_summary(
    *,
    day: str,
    tz: ZoneInfo,
    asset: str,
    interval_sec: int,
    dataset_path: str,
    db_path: str = 'runs/live_signals.sqlite3',
    out_dir: str = 'runs',
    gate_mode: str | None = None,
    meta_model: str | None = None,
    thresh_on: str | None = None,
    threshold: float | None = None,
    k: int | None = None,
    payout: float | None = None,
) -> str:
    """Generate an interval-scoped daily summary JSON from ``signals_v2``."""

    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    out_path = daily_summary_path(day=day, asset=asset, interval_sec=int(interval_sec), out_dir=out_base)
    tmp_path = out_path.with_suffix(out_path.suffix + '.tmp')

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        if str(asset or '').strip():
            try:
                rows = con.execute(
                    'SELECT ts, interval_sec, action, executed_today, score, ev, payout, threshold, k, gate_mode, meta_model, thresh_on '
                    'FROM signals_v2 WHERE day=? AND asset=? AND interval_sec=? ORDER BY ts',
                    (day, str(asset), int(interval_sec)),
                ).fetchall()
            except Exception:
                rows = con.execute(
                    'SELECT ts, action, executed_today, score, ev, payout, threshold, k, gate_mode, meta_model, thresh_on '
                    'FROM signals_v2 WHERE day=? AND asset=? ORDER BY ts',
                    (day, str(asset)),
                ).fetchall()
        else:
            rows = con.execute(
                'SELECT ts, action, executed_today, score, ev, payout, threshold, k, gate_mode, meta_model, thresh_on '
                'FROM signals_v2 WHERE day=? ORDER BY ts',
                (day,),
            ).fetchall()
    finally:
        con.close()

    hours = [f'{h:02d}' for h in range(24)]
    obs_by_hour: dict[str, int] = {h: 0 for h in hours}
    trades_by_hour: dict[str, dict[str, int]] = {h: {'total': 0, 'CALL': 0, 'PUT': 0} for h in hours}
    by_hour: dict[str, dict[str, Any]] = {h: {'trades': 0, 'wins': 0, 'ev_sum': 0.0} for h in hours}

    trades: list[dict[str, Any]] = []
    ev_all: list[float | None] = []
    ev_trades: list[float | None] = []
    last_row: dict[str, Any] | None = None

    for r in rows:
        d = dict(r)
        last_row = d
        ts = int(d.get('ts') or 0)
        h = datetime.fromtimestamp(ts, tz=tz).strftime('%H') if ts else '00'
        if h not in obs_by_hour:
            obs_by_hour[h] = 0
            trades_by_hour[h] = {'total': 0, 'CALL': 0, 'PUT': 0}
        obs_by_hour[h] += 1
        ev_all.append(d.get('ev'))
        action = str(d.get('action') or '').upper()
        if action in ('CALL', 'PUT'):
            trades.append(d)
            ev_trades.append(d.get('ev'))
            trades_by_hour[h]['total'] += 1
            trades_by_hour[h][action] += 1

    label_map: dict[int, float] = {}
    try:
        dlab = pd.read_csv(dataset_path, usecols=['ts', 'y_open_close'])
        dlab = dlab.dropna(subset=['ts'])
        dlab['ts'] = dlab['ts'].astype(int)
        for ts, y in zip(dlab['ts'].tolist(), dlab['y_open_close'].tolist()):
            try:
                fy = float(y)
            except Exception:
                continue
            label_map[int(ts)] = fy
    except Exception:
        label_map = {}

    slot_stats: dict[str, dict[str, Any]] = {}
    total_eval = 0
    total_wins = 0

    for tr in trades:
        ts = int(tr.get('ts') or 0)
        y = label_map.get(ts, None)
        if y is None:
            continue
        try:
            fy = float(y)
        except Exception:
            continue
        if np.isnan(fy):
            continue
        lbl = 1 if fy >= 0.5 else 0
        action = str(tr.get('action') or '').upper()
        pred = 1 if action == 'CALL' else 0
        won = 1 if pred == lbl else 0

        slot = int(tr.get('executed_today') or 0)
        if slot < 1:
            slot = 1
        sk = str(slot)
        st = slot_stats.setdefault(
            sk,
            {
                'slot': slot,
                'trades': 0,
                'wins': 0,
                'win_rate': None,
                'ev_avg': None,
                'score_avg': None,
            },
        )
        st['trades'] += 1
        st['wins'] += won
        total_eval += 1
        total_wins += won

        try:
            _dt2 = datetime.fromtimestamp(ts, tz=tz)
            _hh2 = f'{_dt2.hour:02d}'
        except Exception:
            _hh2 = '??'
        _ev_val = float(tr.get('ev') or 0.0)
        _bh = by_hour.setdefault(_hh2, {'trades': 0, 'wins': 0, 'ev_sum': 0.0})
        _bh['trades'] += 1
        _bh['wins'] += int(won)
        _bh['ev_sum'] += _ev_val

        st.setdefault('_ev', []).append(tr.get('ev'))
        st.setdefault('_score', []).append(tr.get('score'))

    for st in slot_stats.values():
        trades_n = int(st.get('trades') or 0)
        wins_n = int(st.get('wins') or 0)
        st['win_rate'] = float(wins_n / trades_n) if trades_n > 0 else None
        st['ev_avg'] = _p10_mean(st.pop('_ev', []))
        st['score_avg'] = _p10_mean(st.pop('_score', []))

    winrate_by_slot = {k: slot_stats[k] for k in sorted(slot_stats.keys(), key=lambda s: int(s))}

    if last_row:
        gate_mode = gate_mode or str(last_row.get('gate_mode') or '')
        meta_model = meta_model or str(last_row.get('meta_model') or '')
        thresh_on = thresh_on or str(last_row.get('thresh_on') or '')
        try:
            threshold = float(threshold if threshold is not None else last_row.get('threshold'))
        except Exception:
            threshold = None
        try:
            k = int(k if k is not None else last_row.get('k'))
        except Exception:
            k = None
        try:
            payout = float(payout if payout is not None else last_row.get('payout'))
        except Exception:
            payout = None

    break_even = None
    if payout is not None:
        try:
            break_even = float(1.0 / (1.0 + float(payout)))
        except Exception:
            break_even = None

    for h in by_hour.values():
        n = int(h.get('trades') or 0)
        w = int(h.get('wins') or 0)
        evs = float(h.get('ev_sum') or 0.0)
        h['losses'] = max(0, n - w)
        h['win_rate'] = (w / n) if n > 0 else None
        h['ev_mean'] = (evs / n) if n > 0 else None
        h.pop('ev_sum', None)

    summary = {
        'day': day,
        'asset': asset,
        'interval_sec': int(interval_sec),
        'timezone': str(getattr(tz, 'key', str(tz))),
        'summary_version': 2,
        'generated_at': datetime.now(tz=tz).isoformat(timespec='seconds'),
        'db_path': db_path,
        'dataset_path': dataset_path,
        'k': k,
        'gate_mode': gate_mode,
        'meta_model': meta_model,
        'thresh_on': thresh_on,
        'threshold': threshold,
        'payout': payout,
        'break_even': break_even,
        'rows_total': int(len(rows)),
        'trades_total': int(len(trades)),
        'trades_eval_total': int(total_eval),
        'wins_eval_total': int(total_wins),
        'win_rate_eval_total': float(total_wins / total_eval) if total_eval > 0 else None,
        'ev_avg_all': _p10_mean(ev_all),
        'ev_avg_trades': _p10_mean(ev_trades),
        'observations_by_hour': obs_by_hour,
        'trades_by_hour': trades_by_hour,
        'by_hour': by_hour,
        'winrate_by_slot': winrate_by_slot,
    }

    tmp_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp_path.replace(out_path)
    return str(out_path)


__all__ = ['_p10_mean', 'write_daily_summary']
