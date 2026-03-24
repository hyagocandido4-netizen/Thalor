from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ...config.env import env_bool, env_float, env_int


@dataclass(frozen=True)
class ObserverSettings:
    threshold: float
    bounds: dict[str, float]
    tune_dir: str
    k: int
    thresh_on: str
    gate_mode_requested: str
    meta_model_type: str
    dataset_path: str
    payout: float
    regime_mode: str
    rolling_min: int
    pacing_enabled: bool
    min_gap_min: int
    market_open: bool
    market_context_stale: bool
    market_context_fail_closed: bool


@dataclass(frozen=True)
class TopKSelection:
    metric: np.ndarray
    ev_metric: np.ndarray
    mask: np.ndarray
    cand: np.ndarray
    order: np.ndarray
    topk: np.ndarray
    now_i: int
    in_topk: bool
    rank_in_day: int
    pacing_allowed: int


def make_regime_mask(df: pd.DataFrame, bounds: dict[str, float]) -> np.ndarray:
    vol = df['f_vol48'].to_numpy(dtype=float)
    bb = df['f_bb_width20'].to_numpy(dtype=float)
    atr = df['f_atr14'].to_numpy(dtype=float)

    m = np.ones(len(df), dtype=bool)
    m &= vol >= float(bounds['vol_lo'])
    m &= vol <= float(bounds['vol_hi'])
    m &= bb >= float(bounds['bb_lo'])
    m &= bb <= float(bounds['bb_hi'])
    m &= atr >= float(bounds['atr_lo'])
    m &= atr <= float(bounds['atr_hi'])
    return m


def _truthy_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    s = str(raw).strip().lower()
    if s == '':
        return bool(default)
    return s not in ('0', 'false', 'f', 'no', 'n', 'off')


def _normalize_gate_mode(value: Any) -> str:
    gate_mode = str(value or 'meta').strip().lower()
    if gate_mode in ('cp_meta_iso', 'cp_meta', 'cp-meta-iso'):
        gate_mode = 'cp'
    elif gate_mode in ('meta_iso', 'meta-iso'):
        gate_mode = 'meta'
    if gate_mode not in ('meta', 'iso', 'conf', 'cp'):
        gate_mode = 'meta'
    return gate_mode


def _normalize_meta_model(value: Any) -> str:
    meta_model_type = str(value or 'hgb').strip().lower()
    if meta_model_type not in ('logreg', 'hgb'):
        return 'hgb'
    return meta_model_type


def _normalize_thresh_on(value: Any) -> str:
    thresh_on = str(value or 'score').strip().lower()
    if thresh_on not in ('score', 'conf', 'ev'):
        return 'score'
    return thresh_on


def resolve_observer_settings(cfg: dict[str, Any], best: dict[str, Any]) -> ObserverSettings:
    thr = float(best.get('threshold', 0.60))
    thr_env = os.getenv('THRESHOLD', '').strip()
    if thr_env:
        try:
            thr = float(thr_env)
        except Exception:
            pass

    k_env = os.getenv('TOPK_K', '').strip()
    try:
        k = int(k_env) if k_env else int(best.get('k', 1))
    except Exception:
        k = 1
    if k < 1:
        k = 1

    dataset_path = (
        os.getenv('DATASET_PATH')
        or os.getenv('THALOR__DATA__DATASET_PATH')
        or cfg.get('phase2', {}).get('dataset_path')
        or 'data/dataset_phase2.csv'
    )
    dataset_path = str(dataset_path)
    if not Path(dataset_path).exists():
        raise FileNotFoundError(f'dataset_not_found:{dataset_path} (run make_dataset before observe)')

    regime_mode = str(os.getenv('REGIME_MODE', 'hard')).strip().lower()
    if regime_mode not in ('hard', 'soft', 'off'):
        regime_mode = 'hard'

    return ObserverSettings(
        threshold=float(thr),
        bounds=dict(best.get('bounds') or {}),
        tune_dir=str(best.get('tune_dir') or ''),
        k=int(k),
        thresh_on=_normalize_thresh_on(os.getenv('THRESH_ON', '').strip() or best.get('thresh_on', 'score')),
        gate_mode_requested=_normalize_gate_mode(os.getenv('GATE_MODE', '').strip() or best.get('gate_mode', 'meta')),
        meta_model_type=_normalize_meta_model(os.getenv('META_MODEL', '').strip() or best.get('meta_model', 'hgb')),
        dataset_path=dataset_path,
        payout=float(env_float('PAYOUT', 0.8)),
        regime_mode=regime_mode,
        rolling_min=int(env_int('TOPK_ROLLING_MINUTES', '0')),
        pacing_enabled=_truthy_env('TOPK_PACING_ENABLE', default=False),
        min_gap_min=int(env_int('TOPK_MIN_GAP_MINUTES', '0')),
        market_open=_truthy_env('MARKET_OPEN', default=True),
        market_context_stale=env_bool('MARKET_CONTEXT_STALE', False),
        market_context_fail_closed=env_bool('MARKET_CONTEXT_FAIL_CLOSED', True),
    )


def select_topk(
    *,
    df_day: pd.DataFrame,
    last_ts: int,
    metric: np.ndarray,
    score: np.ndarray,
    threshold: float,
    k: int,
    payout: float,
    regime_mask: np.ndarray,
    regime_mode: str,
    rolling_min: int,
    pacing_enabled: bool,
    tz,
) -> TopKSelection:
    ev_metric = score * payout - (1.0 - score)
    mask_gate = regime_mask if regime_mode == 'hard' else np.ones(len(regime_mask), dtype=bool)
    cand = mask_gate & (metric >= threshold)

    order = np.argsort(-(score * payout - (1.0 - score)), kind='mergesort')

    if int(k) < 1:
        k = 1

    if rolling_min > 0:
        start_ts = int(last_ts) - int(rolling_min) * 60
        win_mask = df_day['ts'].to_numpy(dtype=int) >= start_ts
    else:
        win_mask = np.ones(len(df_day), dtype=bool)

    sel = order[(cand & win_mask)[order]]
    topk = sel[:k]

    now_i = len(df_day) - 1
    in_topk = bool(now_i in set(topk.tolist()))
    rank_in_day = int(np.where(topk == now_i)[0][0] + 1) if in_topk else -1

    pacing_allowed = int(k)
    if pacing_enabled and int(k) > 1:
        dt_now = pd.Timestamp(int(last_ts), unit='s', tz='UTC').tz_convert(tz)
        sec_of_day = int(dt_now.hour) * 3600 + int(dt_now.minute) * 60 + int(dt_now.second)
        frac_day = min(1.0, max(0.0, float(sec_of_day) / 86400.0))
        pacing_allowed = min(int(k), max(1, int(np.floor(float(k) * frac_day)) + 1))

    return TopKSelection(
        metric=metric,
        ev_metric=ev_metric,
        mask=regime_mask,
        cand=cand,
        order=order,
        topk=topk,
        now_i=now_i,
        in_topk=in_topk,
        rank_in_day=rank_in_day,
        pacing_allowed=pacing_allowed,
    )


__all__ = [
    'ObserverSettings',
    'TopKSelection',
    'make_regime_mask',
    'resolve_observer_settings',
    'select_topk',
]
