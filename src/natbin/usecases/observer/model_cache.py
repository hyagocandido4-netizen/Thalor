from __future__ import annotations

import hashlib
import json
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any

from natbin.domain.gate_meta import GATE_VERSION
from ...config.env import env_int
from ...state.summary_paths import sanitize_asset


def _normalize_gate_mode(value: Any) -> str:
    gate_mode = str(value or 'meta').strip().lower()
    if gate_mode in ('cp_meta_iso', 'cp_meta', 'cp-meta-iso'):
        return 'cp'
    if gate_mode in {'meta', 'iso', 'conf', 'cp'}:
        return gate_mode
    return 'meta'


def cache_supports_gate(payload: dict[str, Any] | None, gate_mode: str) -> bool:
    """Return whether a cached observer model payload supports the requested gate.

    Older caches can legitimately miss pieces that newer runtime profiles expect,
    especially CP metadata for ``gate_mode=cp``. Treat that as incompatible so the
    caller can rebuild the cache instead of running fail-closed forever.
    """

    if not isinstance(payload, dict):
        return False
    gate = _normalize_gate_mode(gate_mode)
    cal = payload.get('cal')
    if cal is None:
        return False
    if gate == 'conf':
        return True
    if gate == 'iso':
        return payload.get('iso') is not None
    meta_pack = payload.get('meta_model')
    if meta_pack is None:
        return False
    if gate == 'meta':
        model = getattr(meta_pack, 'model', meta_pack)
        return model is not None
    if gate == 'cp':
        cp_obj = getattr(meta_pack, 'cp', None)
        return cp_obj is not None
    return True


def get_model_version() -> str:
    try:
        import subprocess

        out = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], stderr=subprocess.DEVNULL)
        return out.decode('utf-8').strip()
    except Exception:
        return 'unknown'


def feat_hash(feat: list[str]) -> str:
    s = ','.join(feat).encode('utf-8')
    return hashlib.sha1(s).hexdigest()[:12]


def cache_paths(asset: str, interval_sec: int | None = None) -> tuple[Path, Path]:
    a = sanitize_asset(asset)
    stem = f'model_cache_{a}' if interval_sec is None else f'model_cache_{a}_{int(interval_sec)}s'
    pkl = Path('runs') / f'{stem}.pkl'
    meta = Path('runs') / f'{stem}.json'
    return pkl, meta


def load_cache(asset: str, interval_sec: int) -> dict[str, Any] | None:
    pkl, meta = cache_paths(asset, interval_sec)
    if (not pkl.exists() or not meta.exists()) and interval_sec is not None:
        pkl, meta = cache_paths(asset, None)
    if not pkl.exists() or not meta.exists():
        return None
    try:
        payload = pickle.loads(pkl.read_bytes())
        payload['meta'] = json.loads(meta.read_text(encoding='utf-8'))
        return payload
    except Exception:
        return None


def save_cache(asset: str, interval_sec: int, payload: dict[str, Any]) -> None:
    pkl, meta = cache_paths(asset, interval_sec)
    pkl.parent.mkdir(parents=True, exist_ok=True)

    m = payload.get('meta') or {}
    meta.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding='utf-8')

    payload2 = dict(payload)
    payload2.pop('meta', None)
    pkl.write_bytes(pickle.dumps(payload2))


def should_retrain(
    meta: dict[str, Any] | None,
    *,
    train_end_ts: int,
    best_source: str,
    fhash: str,
    interval_sec: int,
    meta_model_type: str,
) -> bool:
    if not meta:
        return True

    last_ts = int(meta.get('train_end_ts') or 0)
    last_best = str(meta.get('best_source') or '')
    last_fhash = str(meta.get('feat_hash') or '')
    last_gate = str(meta.get('gate_version') or '')
    last_mm = str(meta.get('meta_model') or '')
    try:
        last_interval = int(meta.get('interval_sec') or 0)
    except Exception:
        last_interval = 0

    if last_interval != int(interval_sec):
        return True
    if last_best != best_source:
        return True
    if last_fhash != fhash:
        return True
    if last_gate != GATE_VERSION:
        return True
    if last_mm != meta_model_type:
        return True

    retrain_every = env_int('RETRAIN_EVERY_CANDLES', '12')
    min_delta = retrain_every * interval_sec
    return (train_end_ts - last_ts) >= min_delta


__all__ = [
    'cache_paths',
    'cache_supports_gate',
    'feat_hash',
    'get_model_version',
    'load_cache',
    'save_cache',
    'should_retrain',
]
