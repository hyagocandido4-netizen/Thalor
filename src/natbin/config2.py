from __future__ import annotations

"""Legacy configuration shim backed by natbin.config.

This file preserves the classic ``load_config()`` and flat constants expected by
older operational modules, but the source of truth is the modern config loader.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from natbin.config.compat_runtime import (
    RuntimeScopeCompat,
    apply_resolved_to_environment,
    load_legacy_compatible_config,
    load_runtime_resolved_config,
    runtime_scope_from_resolved,
)


@dataclass(frozen=True)
class DataConfig:
    asset: str
    interval_sec: int
    db_path: str
    timezone: str
    max_batch: int = 1000


@dataclass(frozen=True)
class Phase2Config:
    dataset_path: str = 'data/dataset_phase2.csv'
    runs_dir: str = 'runs'
    n_splits: int = 6
    threshold_min: float = 0.60
    threshold_max: float = 0.80
    threshold_step: float = 0.01


@dataclass(frozen=True)
class Config:
    data: DataConfig
    phase2: Phase2Config


@lru_cache(maxsize=8)
def resolved_config(asset: str | None = None, interval_sec: int | None = None, profile: str | None = None) -> Any:
    return load_runtime_resolved_config(asset=asset, interval_sec=interval_sec, profile=profile)


@lru_cache(maxsize=8)
def load_cfg(asset: str | None = None, interval_sec: int | None = None, profile: str | None = None) -> dict[str, Any]:
    return load_legacy_compatible_config(asset=asset, interval_sec=interval_sec, profile=profile)


def load_config(asset: str | None = None, interval_sec: int | None = None, profile: str | None = None) -> Config:
    cfg = load_cfg(asset=asset, interval_sec=interval_sec, profile=profile)
    return Config(
        data=DataConfig(
            asset=str(cfg.get('asset', 'EURUSD-OTC')),
            interval_sec=int(cfg.get('interval_sec', 300)),
            db_path=str(cfg.get('db_path') or cfg.get('market_db_path') or 'data/market.sqlite3'),
            timezone=str(cfg.get('timezone', 'America/Sao_Paulo')),
            max_batch=int(cfg.get('max_batch', 1000)),
        ),
        phase2=Phase2Config(
            dataset_path=str(cfg.get('dataset_path', 'data/dataset_phase2.csv')),
            runs_dir=str(cfg.get('runs_dir', 'runs')),
            n_splits=int(cfg.get('n_splits', 6)),
            threshold_min=float(cfg.get('threshold_min', 0.60)),
            threshold_max=float(cfg.get('threshold_max', 0.80)),
            threshold_step=float(cfg.get('threshold_step', 0.01)),
        ),
    )


def scope(asset: str | None = None, interval_sec: int | None = None, profile: str | None = None) -> RuntimeScopeCompat:
    resolved = resolved_config(asset=asset, interval_sec=interval_sec, profile=profile)
    if isinstance(resolved, dict):
        return RuntimeScopeCompat(
            asset=str(resolved.get('asset', 'EURUSD-OTC')),
            interval_sec=int(resolved.get('interval_sec', 300)),
            timezone=str(resolved.get('timezone', 'America/Sao_Paulo')),
        )
    return runtime_scope_from_resolved(resolved)


def export_env(asset: str | None = None, interval_sec: int | None = None, profile: str | None = None) -> dict[str, str]:
    resolved = resolved_config(asset=asset, interval_sec=interval_sec, profile=profile)
    return apply_resolved_to_environment(resolved)


_default_cfg = load_cfg()
ASSET = str(_default_cfg.get('asset', 'EURUSD-OTC'))
INTERVAL_SEC = int(_default_cfg.get('interval_sec', 300))
TIMEZONE = str(_default_cfg.get('timezone', 'America/Sao_Paulo'))
LOOKBACK_CANDLES = int(_default_cfg.get('lookback_candles', 2000))
MARKET_DB_PATH = str(_default_cfg.get('market_db_path', 'data/market.sqlite3'))
DATASET_PATH = str(_default_cfg.get('dataset_path', 'data/dataset_phase2.csv'))
PAYOUT_DEFAULT = float(_default_cfg.get('payout_default', 0.8))
TOPK_K = int(_default_cfg.get('topk_k', 3))
BALANCE_MODE = str(_default_cfg.get('balance_mode', 'PRACTICE'))
GATE_MODE = str(_default_cfg.get('gate_mode', 'cp'))
THRESHOLD = float(_default_cfg.get('threshold', 0.02))
