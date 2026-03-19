from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median
from typing import Any


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def load_json(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _find_per_window(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    if isinstance(payload.get('per_window'), list):
        candidates.append(payload.get('per_window'))
    best = payload.get('best')
    if isinstance(best, dict) and isinstance(best.get('per_window'), list):
        candidates.append(best.get('per_window'))
    if isinstance(payload.get('windows'), list):
        candidates.append(payload.get('windows'))
    if isinstance(best, dict) and isinstance(best.get('windows'), list):
        candidates.append(best.get('windows'))
    for c in candidates:
        rows = [dict(x) for x in c if isinstance(x, dict)]
        if rows:
            return rows
    return []


def _extract_hit(entry: dict[str, Any]) -> float | None:
    for key in ('topk_hit_weighted', 'topk_hit', 'hit', 'accuracy', 'wr', 'win_rate', 'valid_hit', 'val_hit'):
        value = _safe_float(entry.get(key))
        if value is not None:
            return float(value)
    return None


def _extract_train_hit(entry: dict[str, Any]) -> float | None:
    for key in ('train_hit', 'train_accuracy', 'train_wr', 'train_win_rate'):
        value = _safe_float(entry.get(key))
        if value is not None:
            return float(value)
    return None


def _extract_trades(entry: dict[str, Any]) -> int:
    for key in ('topk_taken', 'taken', 'trades', 'n', 'topk_taken_total'):
        value = entry.get(key)
        if value is not None:
            return max(0, _safe_int(value, 0))
    return 0


def build_anti_overfit_report(
    payload: dict[str, Any] | None,
    *,
    min_robustness: float = 0.50,
    min_trades_window: int = 10,
    min_windows: int = 3,
    gap_penalty_weight: float = 0.10,
) -> dict[str, Any]:
    raw = dict(payload or {})
    windows = _find_per_window(raw)
    if not windows:
        best = raw.get('best') if isinstance(raw.get('best'), dict) else raw
        hit = _safe_float(best.get('topk_hit_weighted') or best.get('best_accuracy') or best.get('accuracy') or best.get('valid_hit'))
        trades = _safe_int(best.get('topk_taken_total') or best.get('best_taken') or best.get('trades'))
        min_hit = _safe_float(best.get('min_window_hit'), hit)
        train_hit = _safe_float(best.get('train_hit') or best.get('train_accuracy'))
        if hit is None:
            return {
                'kind': 'anti_overfit',
                'schema_version': 'phase1-anti-overfit-v2',
                'available': False,
                'accepted': True,
                'robustness_score': None,
                'penalty': 0.0,
                'reason': 'summary_missing_per_window',
            }
        weighted_mean = float(hit)
        min_hit_f = float(min_hit if min_hit is not None else hit)
        std_hit = 0.0
        med_hit = float(hit)
        support = min(1.0, float(trades) / max(1.0, float(min_trades_window) * 5.0))
        stability = 1.0
        gap = max(0.0, float((train_hit or hit)) - float(hit)) if train_hit is not None else 0.0
        gap_penalty = min(float(gap_penalty_weight), float(gap) * float(gap_penalty_weight))
        robustness = (0.45 * weighted_mean) + (0.25 * min_hit_f) + (0.15 * med_hit) + (0.10 * support) + (0.05 * stability) - gap_penalty
        windows_count = 1
    else:
        hits: list[float] = []
        train_hits: list[float] = []
        weights: list[int] = []
        low_trade_windows = 0
        for entry in windows:
            hit = _extract_hit(entry)
            train_hit = _extract_train_hit(entry)
            trades = _extract_trades(entry)
            if hit is None:
                continue
            hits.append(float(hit))
            if train_hit is not None:
                train_hits.append(float(train_hit))
            weights.append(max(1, int(trades)))
            if int(trades) < int(min_trades_window):
                low_trade_windows += 1
        if not hits:
            return {
                'kind': 'anti_overfit',
                'schema_version': 'phase1-anti-overfit-v2',
                'available': False,
                'accepted': True,
                'robustness_score': None,
                'penalty': 0.0,
                'reason': 'windows_without_metrics',
            }

        total_w = float(sum(weights))
        weighted_mean = float(sum(h * w for h, w in zip(hits, weights)) / total_w) if total_w > 0 else float(sum(hits) / len(hits))
        min_hit_f = float(min(hits))
        med_hit = float(median(hits))
        mean = float(sum(hits) / len(hits))
        std_hit = float((sum((h - mean) ** 2 for h in hits) / len(hits)) ** 0.5)
        support = min(1.0, total_w / max(1.0, float(min_trades_window) * max(1, len(hits))))
        stability = max(0.0, 1.0 - (std_hit / 0.20))
        low_trade_penalty = min(0.20, float(low_trade_windows) / max(1.0, float(len(hits))) * 0.20)
        train_mean = float(sum(train_hits) / len(train_hits)) if train_hits else None
        gap = max(0.0, float(train_mean) - float(weighted_mean)) if train_mean is not None else 0.0
        gap_penalty = min(float(gap_penalty_weight), float(gap) * float(gap_penalty_weight))
        windows_count = len(hits)
        window_support_penalty = 0.10 if int(windows_count) < int(min_windows) else 0.0
        robustness = (
            (0.40 * weighted_mean)
            + (0.20 * min_hit_f)
            + (0.10 * med_hit)
            + (0.15 * support)
            + (0.10 * stability)
            - low_trade_penalty
            - gap_penalty
            - window_support_penalty
        )
        robustness = max(0.0, min(1.0, robustness))

    accepted = bool(robustness >= float(min_robustness))
    penalty = 0.0 if accepted else 0.10
    if not accepted:
        penalty += min(0.05, float(gap_penalty)) if 'gap_penalty' in locals() else 0.0

    return {
        'kind': 'anti_overfit',
        'schema_version': 'phase1-anti-overfit-v2',
        'available': True,
        'accepted': bool(accepted),
        'robustness_score': float(robustness),
        'penalty': float(penalty),
        'weighted_mean_hit': float(weighted_mean),
        'min_window_hit': float(min_hit_f),
        'median_window_hit': float(med_hit),
        'std_window_hit': float(std_hit),
        'windows_count': int(windows_count),
        'min_trades_window': int(min_trades_window),
        'min_robustness': float(min_robustness),
        'min_windows': int(min_windows),
        'generalization_gap': float(gap if 'gap' in locals() else 0.0),
        'gap_penalty': float(gap_penalty if 'gap_penalty' in locals() else 0.0),
        'stability_score': float(stability if 'stability' in locals() else 1.0),
    }
