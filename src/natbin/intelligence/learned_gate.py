
from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .slot_profile import slot_stats_for_ts


FEATURE_NAMES = [
    'base_ev',
    'base_score',
    'base_conf',
    'proba_side',
    'payout',
    'hour_sin',
    'hour_cos',
    'dow_sin',
    'dow_cos',
    'slot_multiplier',
    'executed_today_norm',
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        f = float(value)
        if math.isnan(f):
            return float(default)
        return float(f)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _dt_features(ts: int | None, timezone_name: str) -> dict[str, float]:
    if ts is None:
        ts = 0
    try:
        tz = ZoneInfo(str(timezone_name or 'UTC'))
    except Exception:
        tz = UTC
    dt = datetime.fromtimestamp(int(ts), tz=UTC).astimezone(tz)
    dow = float(dt.weekday())
    minutes = float(dt.hour * 60 + dt.minute)
    dow_rad = (2.0 * math.pi * dow) / 7.0
    min_rad = (2.0 * math.pi * minutes) / 1440.0
    return {
        'hour_sin': math.sin(min_rad),
        'hour_cos': math.cos(min_rad),
        'dow_sin': math.sin(dow_rad),
        'dow_cos': math.cos(dow_rad),
    }


def feature_row_from_signal(
    row: dict[str, Any],
    *,
    timezone_name: str,
    slot_profile: dict[str, Any] | None = None,
) -> dict[str, float]:
    ts = _safe_int(row.get('ts'), 0)
    action = str(row.get('action') or 'HOLD').upper()
    proba_up = _safe_float(row.get('proba_up'), 0.5)
    proba_side = proba_up if action == 'CALL' else (1.0 - proba_up if action == 'PUT' else 0.5)
    dtf = _dt_features(ts, timezone_name)
    slot = slot_stats_for_ts(slot_profile, ts, timezone_name=timezone_name)
    executed_today = _safe_int(row.get('executed_today'), 0)
    return {
        'base_ev': _safe_float(row.get('ev'), 0.0),
        'base_score': _safe_float(row.get('score'), 0.0),
        'base_conf': _safe_float(row.get('conf'), 0.5),
        'proba_side': float(proba_side),
        'payout': _safe_float(row.get('payout'), 0.80),
        'hour_sin': float(dtf['hour_sin']),
        'hour_cos': float(dtf['hour_cos']),
        'dow_sin': float(dtf['dow_sin']),
        'dow_cos': float(dtf['dow_cos']),
        'slot_multiplier': _safe_float(slot.get('multiplier'), 1.0),
        'executed_today_norm': min(1.0, max(0.0, float(executed_today) / 5.0)),
    }


def build_training_rows(
    *,
    signals_db_path: str | Path,
    dataset_path: str | Path,
    asset: str,
    interval_sec: int,
    timezone_name: str,
    slot_profile: dict[str, Any] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sig_path = Path(signals_db_path)
    ds_path = Path(dataset_path)
    if not sig_path.exists() or not ds_path.exists():
        return []

    con = sqlite3.connect(str(sig_path))
    con.row_factory = sqlite3.Row
    try:
        sql = (
            'SELECT ts, action, proba_up, conf, score, ev, payout, executed_today '
            'FROM signals_v2 WHERE asset=? AND interval_sec=? AND action IN ("CALL","PUT") ORDER BY ts DESC'
        )
        params: list[Any] = [str(asset), int(interval_sec)]
        if limit is not None:
            sql += ' LIMIT ?'
            params.append(int(limit))
        rows = [dict(r) for r in con.execute(sql, tuple(params)).fetchall()]
    except Exception:
        rows = []
    finally:
        con.close()

    if not rows:
        return []

    try:
        df = pd.read_csv(ds_path, usecols=['ts', 'y_open_close'])
    except Exception:
        return []
    if 'ts' not in df.columns or 'y_open_close' not in df.columns:
        return []
    df = df.dropna(subset=['ts'])
    label_map: dict[int, float] = {}
    for ts, y in zip(df['ts'].tolist(), df['y_open_close'].tolist()):
        try:
            label_map[int(ts)] = float(y)
        except Exception:
            continue

    out: list[dict[str, Any]] = []
    for row in rows:
        ts = _safe_int(row.get('ts'), 0)
        y = label_map.get(ts)
        if y is None:
            continue
        if math.isnan(float(y)):
            continue
        label = 1 if float(y) >= 0.5 else 0
        action = str(row.get('action') or '').upper()
        pred = 1 if action == 'CALL' else 0
        correct = int(pred == label)
        feat = feature_row_from_signal(row, timezone_name=timezone_name, slot_profile=slot_profile)
        feat.update({
            'correct': int(correct),
            'ts': int(ts),
            'action': action,
        })
        out.append(feat)

    out.reverse()
    return out


def fit_learned_gate(
    rows: list[dict[str, Any]],
    *,
    min_rows: int = 50,
) -> dict[str, Any] | None:
    if len(rows) < max(10, int(min_rows)):
        return None
    y = np.asarray([int(r.get('correct') or 0) for r in rows], dtype=int)
    if np.unique(y).size < 2:
        return None
    X = np.asarray([[float(r.get(name) or 0.0) for name in FEATURE_NAMES] for r in rows], dtype=float)

    means = X.mean(axis=0)
    scales = X.std(axis=0)
    scales[scales <= 1e-9] = 1.0
    Xs = (X - means) / scales

    model = LogisticRegression(max_iter=500, class_weight='balanced', random_state=42)
    model.fit(Xs, y)
    probs = model.predict_proba(Xs)[:, 1].astype(float)
    preds = (probs >= 0.5).astype(int)
    accuracy = float((preds == y).mean())

    return {
        'kind': 'learned_gate',
        'schema_version': 'm5-learned-gate-v1',
        'feature_names': list(FEATURE_NAMES),
        'means': [float(x) for x in means.tolist()],
        'scales': [float(x) for x in scales.tolist()],
        'coef': [float(x) for x in model.coef_[0].tolist()],
        'intercept': float(model.intercept_[0]),
        'train_rows': int(len(rows)),
        'positive_rate': float(y.mean()),
        'train_accuracy': float(accuracy),
    }


def predict_probability(
    model_payload: dict[str, Any] | None,
    feature_payload: dict[str, float],
) -> float | None:
    if not isinstance(model_payload, dict):
        return None
    names = list(model_payload.get('feature_names') or [])
    coef = np.asarray(list(model_payload.get('coef') or []), dtype=float)
    means = np.asarray(list(model_payload.get('means') or []), dtype=float)
    scales = np.asarray(list(model_payload.get('scales') or []), dtype=float)
    if not names or coef.size != len(names) or means.size != len(names) or scales.size != len(names):
        return None
    x = np.asarray([float(feature_payload.get(name) or 0.0) for name in names], dtype=float)
    scales = np.where(scales <= 1e-9, 1.0, scales)
    xs = (x - means) / scales
    intercept = _safe_float(model_payload.get('intercept'), 0.0)
    z = float(np.dot(xs, coef) + float(intercept))
    z = max(-60.0, min(60.0, z))
    return float(1.0 / (1.0 + math.exp(-z)))
