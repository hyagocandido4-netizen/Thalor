from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from ..config.compat_helpers import portable_path_str
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
    include_holds: bool = False,
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
            'FROM signals_v2 WHERE asset=? AND interval_sec=? ORDER BY ts DESC'
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
        source_action = str(row.get('action') or '').upper()
        action = source_action
        inferred_direction = False
        if action not in {'CALL', 'PUT'}:
            if not bool(include_holds):
                continue
            action = 'CALL' if _safe_float(row.get('proba_up'), 0.5) >= 0.5 else 'PUT'
            inferred_direction = True
        pred = 1 if action == 'CALL' else 0
        correct = int(pred == label)
        feat = feature_row_from_signal(row, timezone_name=timezone_name, slot_profile=slot_profile)
        feat.update({
            'correct': int(correct),
            'ts': int(ts),
            'action': action,
            'source_action': source_action,
            'inferred_direction': bool(inferred_direction),
            'direction_source': 'inferred_from_proba_up' if inferred_direction else 'signal_action',
            'source_db_path': portable_path_str(sig_path),
        })
        out.append(feat)

    out.reverse()
    return out



def _calibration_bins(probabilities: np.ndarray, labels: np.ndarray, *, bins: int = 5) -> list[dict[str, Any]]:
    if probabilities.size == 0 or labels.size == 0:
        return []
    edges = np.linspace(0.0, 1.0, int(max(2, bins)) + 1)
    rows: list[dict[str, Any]] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        if hi >= 1.0:
            mask = (probabilities >= lo) & (probabilities <= hi)
        else:
            mask = (probabilities >= lo) & (probabilities < hi)
        count = int(mask.sum())
        if count <= 0:
            continue
        mean_prob = float(probabilities[mask].mean())
        hit_rate = float(labels[mask].mean())
        rows.append({
            'bin_lo': float(lo),
            'bin_hi': float(hi),
            'count': count,
            'mean_prob': mean_prob,
            'hit_rate': hit_rate,
            'gap': float(hit_rate - mean_prob),
        })
    return rows



def _calibration_metrics(probabilities: np.ndarray, labels: np.ndarray, *, bins: int = 5) -> tuple[list[dict[str, Any]], float, float]:
    rows = _calibration_bins(probabilities, labels, bins=bins)
    if not rows:
        return [], 0.0, 0.0
    total = float(sum(max(0, int(r.get('count') or 0)) for r in rows))
    if total <= 0:
        return rows, 0.0, 0.0
    ece = 0.0
    max_gap = 0.0
    for row in rows:
        gap = abs(float(row.get('gap') or 0.0))
        count = max(0, int(row.get('count') or 0))
        ece += (float(count) / total) * gap
        max_gap = max(max_gap, gap)
    return rows, float(ece), float(max_gap)



def _fit_isotonic(probabilities: np.ndarray, labels: np.ndarray) -> tuple[dict[str, Any] | None, np.ndarray | None]:
    if probabilities.size < 20 or labels.size != probabilities.size:
        return None, None
    if np.unique(probabilities).size < 2:
        return None, None
    try:
        iso = IsotonicRegression(out_of_bounds='clip', y_min=0.0, y_max=1.0)
        iso.fit(probabilities, labels)
        calibrated = np.asarray(iso.predict(probabilities), dtype=float)
        payload = {
            'kind': 'isotonic',
            'x_thresholds': [float(x) for x in np.asarray(iso.X_thresholds_, dtype=float).tolist()],
            'y_thresholds': [float(x) for x in np.asarray(iso.y_thresholds_, dtype=float).tolist()],
            'train_rows': int(labels.size),
        }
        return payload, calibrated
    except Exception:
        return None, None



def _apply_calibrator(probability: float, calibrator: dict[str, Any] | None) -> float:
    prob = max(0.0, min(1.0, float(probability)))
    if not isinstance(calibrator, dict):
        return prob
    x = np.asarray(list(calibrator.get('x_thresholds') or []), dtype=float)
    y = np.asarray(list(calibrator.get('y_thresholds') or []), dtype=float)
    if x.size < 2 or y.size != x.size:
        return prob
    try:
        return float(np.interp(prob, x, y, left=float(y[0]), right=float(y[-1])))
    except Exception:
        return prob



def _reliability_score(*, ece: float, max_gap: float, lift_vs_base: float) -> float:
    penalty = max(0.0, float(ece)) + (0.5 * max(0.0, float(max_gap)))
    bonus = min(0.10, max(0.0, float(lift_vs_base)))
    score = 1.0 - penalty + bonus
    return max(0.0, min(1.0, float(score)))



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
    raw_probs = model.predict_proba(Xs)[:, 1].astype(float)
    raw_preds = (raw_probs >= 0.5).astype(int)
    accuracy = float((raw_preds == y).mean())

    # lightweight diagnostics for stacking / calibration
    base = np.asarray([float(r.get('base_conf') or r.get('base_score') or 0.5) for r in rows], dtype=float)
    base_preds = (base >= 0.5).astype(int)
    base_accuracy = float((base_preds == y).mean())
    raw_brier = float(np.mean((raw_probs - y) ** 2))
    raw_bins, raw_ece, raw_max_gap = _calibration_metrics(raw_probs, y, bins=5)

    calibrator, calibrated_probs = _fit_isotonic(raw_probs, y)
    if calibrated_probs is not None:
        probs = calibrated_probs
        probability_source = 'calibrated_isotonic'
    else:
        probs = raw_probs
        probability_source = 'raw_logistic'

    preds = (probs >= 0.5).astype(int)
    calibrated_accuracy = float((preds == y).mean())
    calibrated_brier = float(np.mean((probs - y) ** 2))
    calibration, calibrated_ece, calibrated_max_gap = _calibration_metrics(probs, y, bins=5)
    lift_vs_base = float(calibrated_accuracy - base_accuracy)
    reliability = _reliability_score(ece=calibrated_ece, max_gap=calibrated_max_gap, lift_vs_base=lift_vs_base)

    return {
        'kind': 'learned_gate',
        'schema_version': 'phase1-learned-gate-v3',
        'feature_names': list(FEATURE_NAMES),
        'means': [float(x) for x in means.tolist()],
        'scales': [float(x) for x in scales.tolist()],
        'coef': [float(x) for x in model.coef_[0].tolist()],
        'intercept': float(model.intercept_[0]),
        'train_rows': int(len(rows)),
        'positive_rate': float(y.mean()),
        'train_accuracy': float(accuracy),
        'base_accuracy': float(base_accuracy),
        'calibrated_accuracy': float(calibrated_accuracy),
        'lift_vs_base': float(lift_vs_base),
        'train_brier': calibrated_brier,
        'raw_train_brier': raw_brier,
        'calibration_bins': calibration,
        'raw_calibration_bins': raw_bins,
        'calibration_ece': float(calibrated_ece),
        'calibration_max_gap': float(calibrated_max_gap),
        'raw_calibration_ece': float(raw_ece),
        'raw_calibration_max_gap': float(raw_max_gap),
        'reliability_score': float(reliability),
        'reliability_status': 'trusted' if reliability >= 0.70 else ('guarded' if reliability >= 0.50 else 'weak'),
        'probability_source': probability_source,
        'calibrator': calibrator,
    }



def predict_probability(
    model_payload: dict[str, Any] | None,
    feature_payload: dict[str, float],
    *,
    apply_calibration: bool = True,
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
    prob = float(1.0 / (1.0 + math.exp(-z)))
    if apply_calibration:
        prob = _apply_calibrator(prob, model_payload.get('calibrator'))
    return max(0.0, min(1.0, float(prob)))



def stack_decision(
    *,
    base_quality: float,
    learned_prob: float | None,
    weight: float = 0.60,
    promote_above: float = 0.62,
    suppress_below: float = 0.42,
    abstain_band: float = 0.03,
    reliability_score: float | None = None,
    min_reliability: float = 0.50,
    neutralize_low_reliability: bool = True,
    max_bonus: float = 0.05,
    max_penalty: float = 0.05,
) -> dict[str, Any]:
    base = max(0.0, min(1.0, float(base_quality)))
    prob = None if learned_prob is None else max(0.0, min(1.0, float(learned_prob)))
    reliability = None if reliability_score is None else max(0.0, min(1.0, float(reliability_score)))
    if prob is None:
        return {
            'available': False,
            'base_quality': float(base),
            'learned_prob': None,
            'blended_quality': float(base),
            'delta': 0.0,
            'decision': 'neutral',
            'reason': 'learned_gate_unavailable',
            'reliability_score': reliability,
        }
    w = max(0.0, min(1.0, float(weight)))
    blended = ((1.0 - w) * float(base)) + (w * float(prob))
    raw_delta = float(blended - base)
    if raw_delta >= 0.0:
        delta = min(float(max_bonus), float(raw_delta))
    else:
        delta = -min(float(max_penalty), abs(float(raw_delta)))
    blended = max(0.0, min(1.0, float(base) + float(delta)))
    decision = 'neutral'
    reason = 'within_band'
    if reliability is not None and reliability < float(min_reliability) and bool(neutralize_low_reliability):
        decision = 'abstain'
        reason = 'learned_low_reliability'
        blended = float(base)
        delta = 0.0
    elif abs(float(prob) - 0.5) <= float(abstain_band):
        decision = 'abstain'
        reason = 'learned_prob_near_half'
    elif blended >= float(promote_above) and delta > 0.0:
        decision = 'promote'
        reason = 'blended_quality_high'
    elif blended <= float(suppress_below) and delta < 0.0:
        decision = 'suppress'
        reason = 'blended_quality_low'

    return {
        'available': True,
        'base_quality': float(base),
        'learned_prob': float(prob),
        'blended_quality': float(blended),
        'delta': float(delta),
        'raw_delta': float(raw_delta),
        'decision': decision,
        'reason': reason,
        'reliability_score': reliability,
        'thresholds': {
            'promote_above': float(promote_above),
            'suppress_below': float(suppress_below),
            'abstain_band': float(abstain_band),
            'weight': float(w),
            'min_reliability': float(min_reliability),
            'max_bonus': float(max_bonus),
            'max_penalty': float(max_penalty),
        },
    }
