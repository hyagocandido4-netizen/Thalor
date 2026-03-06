from __future__ import annotations

"""Legacy settings facade backed by the new config foundation.

Compatibility goals:
- keep ``load_settings().iq.email`` / ``load_settings().data.asset`` working
- keep flat constants like ``ASSET`` / ``MARKET_DB_PATH`` working
- avoid becoming an independent source of truth
"""

from dataclasses import dataclass
from typing import Any

from natbin import config2 as _config2


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
        # legacy convenience; not a first-class nested field here
        return getattr(self, '_payout_default', 0.8)  # type: ignore[attr-defined]


def load_settings(asset: str | None = None, interval_sec: int | None = None, profile: str | None = None) -> SettingsCompat:
    cfg = _config2.load_cfg(asset=asset, interval_sec=interval_sec, profile=profile)
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
LOOKBACK_CANDLES = int(_config2.load_cfg().get('lookback_candles', 2000))
MARKET_DB_PATH = _default.data.db_path
DATASET_PATH = _default.phase2.dataset_path
PAYOUT_DEFAULT = getattr(_default, '_payout_default', 0.8)
TOPK_K = int(_config2.load_cfg().get('topk_k', 3))
BALANCE_MODE = _default.iq.balance_mode
GATE_MODE = str(_config2.load_cfg().get('gate_mode', 'cp'))
THRESHOLD = float(_config2.load_cfg().get('threshold', 0.02))

settings = _default
