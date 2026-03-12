from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class RuntimeScopeCompat:
    asset: str
    interval_sec: int
    timezone: str


def _env_first(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        val = os.getenv(key)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_secret(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        getter = getattr(value, 'get_secret_value', None)
        if callable(getter):
            return str(getter())
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return default


def _fallback_yaml_like() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        'asset': _env_first('ASSET', 'THALOR__ASSET', default='EURUSD-OTC'),
        'interval_sec': _safe_int(_env_first('INTERVAL_SEC', 'THALOR__INTERVAL_SEC', default='300'), 300),
        'timezone': _env_first('TIMEZONE', 'TZ', 'THALOR__TIMEZONE', default='America/Sao_Paulo'),
        'lookback_candles': _safe_int(_env_first('LOOKBACK_CANDLES', 'THALOR__LOOKBACK_CANDLES', default='2000'), 2000),
        'market_db_path': _env_first('MARKET_DB_PATH', 'THALOR__MARKET_DB_PATH', default='data/market.sqlite3'),
        'db_path': _env_first('MARKET_DB_PATH', 'THALOR__MARKET_DB_PATH', default='data/market.sqlite3'),
        'dataset_path': _env_first('DATASET_PATH', 'THALOR__DATASET_PATH', default='data/dataset_phase2.csv'),
        'max_batch': _safe_int(_env_first('MAX_BATCH', 'THALOR__DATA__MAX_BATCH', default='1000'), 1000),
        'payout_default': _safe_float(_env_first('PAYOUT', 'THALOR__PAYOUT', default='0.8'), 0.8),
        'topk_k': _safe_int(_env_first('TOPK_K', 'THALOR__TOPK_K', default='3'), 3),
        'balance_mode': _env_first('IQ_BALANCE_MODE', 'THALOR__BALANCE_MODE', default='PRACTICE'),
        'gate_mode': _env_first('GATE_MODE', 'THALOR__GATE_MODE', default='cp'),
        'threshold': _safe_float(_env_first('THRESHOLD', 'THALOR__THRESHOLD', default='0.02'), 0.02),
        'iq_email': _env_first('IQ_EMAIL', 'THALOR__BROKER__EMAIL', default=''),
        'iq_password': _env_first('IQ_PASSWORD', 'THALOR__BROKER__PASSWORD', default=''),
        'runs_dir': 'runs',
        'n_splits': 6,
        'threshold_min': 0.60,
        'threshold_max': 0.80,
        'threshold_step': 0.01,
    }
    return cfg


def _resolve_from_new_loader(
    asset: str | None = None,
    interval_sec: int | None = None,
    profile: str | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> Any | None:
    """Resolve config via the modern loader.

    We only fall back to the legacy env-shaped dict when the modern loader is
    genuinely unavailable (for example, during a partial bootstrap). Schema
    validation or runtime resolution errors must propagate so the caller fails
    closed instead of silently discarding modern blocks such as
    ``security``, ``notifications`` and ``intelligence``.
    """
    try:
        from natbin.config.loader import load_resolved_config  # type: ignore
    except Exception:
        return None

    kwargs: dict[str, Any] = {}
    if asset is not None:
        kwargs['asset'] = asset
    if interval_sec is not None:
        kwargs['interval_sec'] = int(interval_sec)
    if profile is not None:
        kwargs['profile'] = profile
    if cli_overrides:
        kwargs['cli_overrides'] = dict(cli_overrides)

    try:
        return load_resolved_config(**kwargs)
    except TypeError as exc:
        message = str(exc)
        if 'unexpected keyword argument' not in message and 'positional argument' not in message:
            raise
        return load_resolved_config(asset=asset, interval_sec=interval_sec)


def runtime_scope_from_resolved(resolved: Any) -> RuntimeScopeCompat:
    asset = getattr(resolved, 'asset', None) or _env_first('ASSET', default='EURUSD-OTC')
    interval_sec = getattr(resolved, 'interval_sec', None) or _safe_int(_env_first('INTERVAL_SEC', default='300'), 300)
    timezone = getattr(resolved, 'timezone', None) or _env_first('TIMEZONE', 'TZ', default='America/Sao_Paulo')
    return RuntimeScopeCompat(asset=str(asset), interval_sec=int(interval_sec), timezone=str(timezone))


def _pull(obj: Any, *path: str, default: Any = None) -> Any:
    cur = obj
    for part in path:
        if cur is None:
            return default
        cur = getattr(cur, part, None)
    return default if cur is None else cur


def resolved_to_legacy_dict(resolved: Any) -> dict[str, Any]:
    scope = runtime_scope_from_resolved(resolved)
    out: dict[str, Any] = {
        'asset': scope.asset,
        'interval_sec': scope.interval_sec,
        'timezone': scope.timezone,
        'lookback_candles': _safe_int(_pull(resolved, 'data', 'lookback_candles', default=2000), 2000),
        'market_db_path': str(_pull(resolved, 'data', 'db_path', default='data/market.sqlite3')),
        'db_path': str(_pull(resolved, 'data', 'db_path', default='data/market.sqlite3')),
        'dataset_path': str(_pull(resolved, 'data', 'dataset_path', default='data/dataset_phase2.csv')),
        'max_batch': _safe_int(_pull(resolved, 'data', 'max_batch', default=1000), 1000),
        'payout_default': _safe_float(_pull(resolved, 'runtime_overrides', 'payout', default=_env_first('PAYOUT', default='0.8')), 0.8),
        'topk_k': _safe_int(_env_first('TOPK_K', default='3'), 3),
        'balance_mode': str(_pull(resolved, 'broker', 'balance_mode', default='PRACTICE')),
        'gate_mode': str(_pull(resolved, 'decision', 'gate_mode', default='cp')),
        'threshold': _safe_float(_pull(resolved, 'runtime_overrides', 'threshold', default=_pull(resolved, 'decision', 'threshold', default=0.02)), 0.02),
        'iq_email': str(_pull(resolved, 'broker', 'email', default=_env_first('IQ_EMAIL', default=''))),
        'iq_password': _safe_secret(_pull(resolved, 'broker', 'password', default=_env_first('IQ_PASSWORD', default=''))),
        'runs_dir': 'runs',
        'n_splits': 6,
        'threshold_min': 0.60,
        'threshold_max': 0.80,
        'threshold_step': 0.01,
    }
    return out


def load_runtime_resolved_config(asset: str | None = None, interval_sec: int | None = None, profile: str | None = None, cli_overrides: Mapping[str, Any] | None = None) -> Any:
    resolved = _resolve_from_new_loader(asset=asset, interval_sec=interval_sec, profile=profile, cli_overrides=cli_overrides)
    if resolved is not None:
        return resolved
    return _fallback_yaml_like()


def load_legacy_compatible_config(asset: str | None = None, interval_sec: int | None = None, profile: str | None = None, cli_overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    resolved = load_runtime_resolved_config(asset=asset, interval_sec=interval_sec, profile=profile, cli_overrides=cli_overrides)
    if isinstance(resolved, dict):
        return dict(resolved)
    return resolved_to_legacy_dict(resolved)


def apply_resolved_to_environment(resolved_or_legacy: Any) -> dict[str, str]:
    payload = dict(resolved_or_legacy) if isinstance(resolved_or_legacy, dict) else resolved_to_legacy_dict(resolved_or_legacy)
    mapping = {
        'ASSET': str(payload.get('asset', 'EURUSD-OTC')),
        'INTERVAL_SEC': str(payload.get('interval_sec', 300)),
        'TIMEZONE': str(payload.get('timezone', 'America/Sao_Paulo')),
        'LOOKBACK_CANDLES': str(payload.get('lookback_candles', 2000)),
        'MARKET_DB_PATH': str(payload.get('market_db_path', 'data/market.sqlite3')),
        'DATASET_PATH': str(payload.get('dataset_path', 'data/dataset_phase2.csv')),
        'MAX_BATCH': str(payload.get('max_batch', 1000)),
        'PAYOUT': str(payload.get('payout_default', 0.8)),
        'TOPK_K': str(payload.get('topk_k', 3)),
        'IQ_BALANCE_MODE': str(payload.get('balance_mode', 'PRACTICE')),
        'GATE_MODE': str(payload.get('gate_mode', 'cp')),
        'THRESHOLD': str(payload.get('threshold', 0.02)),
    }
    for key, value in mapping.items():
        os.environ[key] = value
    return mapping


def dump_compat_debug_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False, default=str), encoding='utf-8')
