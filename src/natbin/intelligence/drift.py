
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np


DEFAULT_FIELDS = ('score', 'conf', 'ev')


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        f = float(value)
        if math.isnan(f):
            return default
        return f
    except Exception:
        return default


def _collect_field_values(rows: Iterable[dict[str, Any]], field: str) -> np.ndarray:
    vals: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        v = _safe_float(row.get(field))
        if v is not None:
            vals.append(float(v))
    return np.asarray(vals, dtype=float)


def _probabilities(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    if values.size == 0:
        n = max(1, len(edges) - 1)
        return np.full(n, 1.0 / n, dtype=float)
    counts, _ = np.histogram(values, bins=edges)
    probs = counts.astype(float)
    probs = probs + 1e-6
    probs = probs / probs.sum()
    return probs


def _level_from_psi(value: float, *, warn_psi: float, block_psi: float) -> str:
    if value >= float(block_psi):
        return 'block'
    if value >= float(warn_psi):
        return 'warn'
    return 'ok'


def build_drift_baseline(
    rows: Iterable[dict[str, Any]],
    *,
    fields: tuple[str, ...] = DEFAULT_FIELDS,
    bins: int = 10,
) -> dict[str, Any]:
    item_rows = [dict(r) for r in rows if isinstance(r, dict)]
    payload: dict[str, Any] = {}
    for field in fields:
        values = _collect_field_values(item_rows, field)
        if values.size == 0:
            continue
        edges = np.quantile(values, np.linspace(0.0, 1.0, int(max(2, bins)) + 1)).astype(float)
        edges = np.unique(edges)
        if edges.size < 3:
            lo = float(values.min())
            hi = float(values.max())
            if hi <= lo:
                hi = lo + 1e-6
            edges = np.linspace(lo, hi, int(max(2, bins)) + 1)
        probs = _probabilities(values, edges)
        payload[field] = {
            'edges': [float(x) for x in edges.tolist()],
            'expected_probs': [float(x) for x in probs.tolist()],
            'count': int(values.size),
            'mean': float(values.mean()),
            'std': float(values.std()) if values.size > 1 else 0.0,
        }
    return {
        'kind': 'drift_baseline',
        'schema_version': 'm5-drift-baseline-v1',
        'fields': payload,
    }


def load_recent_signal_rows(
    db_path: str | Path,
    *,
    asset: str,
    interval_sec: int,
    limit: int = 200,
) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            'SELECT ts, action, proba_up, conf, score, ev, payout, reason, executed_today '
            'FROM signals_v2 WHERE asset=? AND interval_sec=? ORDER BY ts DESC LIMIT ?',
            (str(asset), int(interval_sec), int(limit)),
        ).fetchall()
    except Exception:
        try:
            rows = con.execute(
                'SELECT ts, action, proba_up, conf, score, ev, payout, reason, executed_today '
                'FROM signals_v2 WHERE asset=? ORDER BY ts DESC LIMIT ?',
                (str(asset), int(limit)),
            ).fetchall()
        except Exception:
            rows = []
    finally:
        con.close()
    return [dict(r) for r in rows]


def population_stability_index(expected_probs: Iterable[float], actual_probs: Iterable[float]) -> float:
    exp = np.asarray(list(expected_probs), dtype=float)
    act = np.asarray(list(actual_probs), dtype=float)
    if exp.size == 0 or act.size == 0 or exp.size != act.size:
        return 0.0
    exp = np.clip(exp, 1e-6, None)
    act = np.clip(act, 1e-6, None)
    return float(np.sum((act - exp) * np.log(act / exp)))


def assess_drift(
    baseline: dict[str, Any] | None,
    recent_rows: Iterable[dict[str, Any]],
    *,
    warn_psi: float = 0.15,
    block_psi: float = 0.30,
) -> dict[str, Any]:
    rows = [dict(r) for r in recent_rows if isinstance(r, dict)]
    fields = {}
    levels: list[str] = []
    for field, meta in dict((baseline or {}).get('fields') or {}).items():
        try:
            edges = np.asarray(list(meta.get('edges') or []), dtype=float)
            expected_probs = np.asarray(list(meta.get('expected_probs') or []), dtype=float)
        except Exception:
            continue
        if edges.size < 3 or expected_probs.size != (edges.size - 1):
            continue
        values = _collect_field_values(rows, field)
        actual_probs = _probabilities(values, edges)
        psi = population_stability_index(expected_probs, actual_probs)
        level = _level_from_psi(psi, warn_psi=warn_psi, block_psi=block_psi)
        levels.append(level)
        fields[field] = {
            'psi': float(psi),
            'level': level,
            'count': int(values.size),
            'mean_recent': float(values.mean()) if values.size > 0 else None,
            'expected_mean': _safe_float(meta.get('mean')),
        }

    overall = 'ok'
    if 'block' in levels:
        overall = 'block'
    elif 'warn' in levels:
        overall = 'warn'

    penalty = 0.0
    if overall == 'warn':
        penalty = 0.05
    elif overall == 'block':
        penalty = 0.20

    return {
        'kind': 'drift_report',
        'schema_version': 'm5-drift-report-v1',
        'level': overall,
        'penalty': float(penalty),
        'fields': fields,
        'recent_rows': int(len(rows)),
        'warn_psi': float(warn_psi),
        'block_psi': float(block_psi),
    }


def update_drift_state(
    state_path: str | Path,
    *,
    level: str,
    warn_streak_threshold: int = 3,
    block_streak_threshold: int = 1,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    path = Path(state_path)
    now = (now_utc or datetime.now(tz=UTC)).astimezone(UTC).isoformat(timespec='seconds')
    prev: dict[str, Any] = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            prev = {}

    warn_streak = int(prev.get('warn_streak') or 0)
    block_streak = int(prev.get('block_streak') or 0)

    lvl = str(level or 'ok').strip().lower()
    if lvl == 'warn':
        warn_streak += 1
        block_streak = 0
    elif lvl == 'block':
        block_streak += 1
        warn_streak = 0
    else:
        warn_streak = 0
        block_streak = 0

    retrain = False
    retrain_reason = None
    if lvl == 'block' and block_streak >= int(block_streak_threshold):
        retrain = True
        retrain_reason = 'drift_block_streak'
    elif lvl == 'warn' and warn_streak >= int(warn_streak_threshold):
        retrain = True
        retrain_reason = 'drift_warn_streak'

    state = {
        'kind': 'drift_state',
        'schema_version': 'm5-drift-state-v1',
        'updated_at_utc': now,
        'level': lvl,
        'warn_streak': int(warn_streak),
        'block_streak': int(block_streak),
        'retrain_recommended': bool(retrain),
        'retrain_reason': retrain_reason,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8')
    return state
