from __future__ import annotations

"""Compatibility facade for older modules that still import
``natbin.config.settings`` instead of ``natbin.settings``.
"""

from dataclasses import dataclass

from .legacy import load_cfg


@dataclass(frozen=True)
class IQCreds:
    email: str
    password: str
    balance_mode: str = 'PRACTICE'


@dataclass(frozen=True)
class DataCfg:
    asset: str
    interval_sec: int
    db_path: str
    max_batch: int
    timezone: str


@dataclass(frozen=True)
class Phase2Cfg:
    dataset_path: str = 'data/dataset_phase2.csv'
    runs_dir: str = 'runs'
    n_splits: int = 6
    threshold_min: float = 0.60
    threshold_max: float = 0.80
    threshold_step: float = 0.01


@dataclass(frozen=True)
class SettingsCompat:
    iq: IQCreds
    data: DataCfg
    phase2: Phase2Cfg

    @property
    def asset(self) -> str:
        return self.data.asset

    @property
    def interval_sec(self) -> int:
        return self.data.interval_sec

    @property
    def timezone(self) -> str:
        return self.data.timezone

    @property
    def market_db_path(self) -> str:
        return self.data.db_path

    @property
    def dataset_path(self) -> str:
        return self.phase2.dataset_path

    @property
    def payout_default(self) -> float:
        return getattr(self, '_payout_default', 0.8)  # type: ignore[attr-defined]


def load_settings(asset: str | None = None, interval_sec: int | None = None, profile: str | None = None) -> SettingsCompat:
    cfg = load_cfg(asset=asset, interval_sec=interval_sec, profile=profile)
    obj = SettingsCompat(
        iq=IQCreds(
            email=str(cfg.get('iq_email', '')),
            password=str(cfg.get('iq_password', '')),
            balance_mode=str(cfg.get('balance_mode', 'PRACTICE')),
        ),
        data=DataCfg(
            asset=str(cfg.get('asset', 'EURUSD-OTC')),
            interval_sec=int(cfg.get('interval_sec', 300)),
            db_path=str(cfg.get('db_path') or cfg.get('market_db_path') or 'data/market.sqlite3'),
            max_batch=int(cfg.get('max_batch', 1000)),
            timezone=str(cfg.get('timezone', 'America/Sao_Paulo')),
        ),
        phase2=Phase2Cfg(
            dataset_path=str(cfg.get('dataset_path', 'data/dataset_phase2.csv')),
            runs_dir=str(cfg.get('runs_dir', 'runs')),
            n_splits=int(cfg.get('n_splits', 6)),
            threshold_min=float(cfg.get('threshold_min', 0.60)),
            threshold_max=float(cfg.get('threshold_max', 0.80)),
            threshold_step=float(cfg.get('threshold_step', 0.01)),
        ),
    )
    object.__setattr__(obj, '_payout_default', float(cfg.get('payout_default', 0.8)))
    return obj


_default = load_settings()
ASSET = _default.data.asset
INTERVAL_SEC = _default.data.interval_sec
TIMEZONE = _default.data.timezone
LOOKBACK_CANDLES = int(load_cfg().get('lookback_candles', 2000))
MARKET_DB_PATH = _default.data.db_path
DATASET_PATH = _default.phase2.dataset_path
PAYOUT_DEFAULT = getattr(_default, '_payout_default', 0.8)
TOPK_K = int(load_cfg().get('topk_k', 3))
BALANCE_MODE = _default.iq.balance_mode
GATE_MODE = str(load_cfg().get('gate_mode', 'cp'))
THRESHOLD = float(load_cfg().get('threshold', 0.02))

settings = _default

__all__ = [
    'ASSET',
    'BALANCE_MODE',
    'DATASET_PATH',
    'GATE_MODE',
    'INTERVAL_SEC',
    'IQCreds',
    'LOOKBACK_CANDLES',
    'MARKET_DB_PATH',
    'PAYOUT_DEFAULT',
    'Phase2Cfg',
    'SettingsCompat',
    'THRESHOLD',
    'TIMEZONE',
    'TOPK_K',
    'DataCfg',
    'load_settings',
    'settings',
]
