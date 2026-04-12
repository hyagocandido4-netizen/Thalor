from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from natbin.adapters import iq_client as iq_client_mod
from natbin.state.db import count_candles, open_db
from natbin.usecases import collect_recent as collect_recent_mod


ACTIVE_MAP_BASE = {
    'EURUSD-OTC': 1,
    'GBPUSD-OTC': 2,
    'USDJPY-OTC': 3,
}

ACTIVE_MAP_REFRESHED = {
    **ACTIVE_MAP_BASE,
    'AUDUSD-OTC': 4,
    'BTCUSD-L': 5,
    'XAUUSD': 6,
}

OPEN_TIME_MAP = {
    'turbo': {
        'EURUSD-OTC': {'open': True},
        'GBPUSD-OTC': {'open': True},
        'AUDUSD-OTC': {'open': True},
        'USDJPY-OTC': {'open': True},
    },
    'crypto': {'BTCUSD-L': {'open': True}},
    'cfd': {'XAUUSD': {'open': True}},
}


class FakeStableIQOption:
    websocket_connected = False

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.balance_mode = 'PRACTICE'
        self._actives = dict(ACTIVE_MAP_BASE)

    def connect(self):
        self.api = SimpleNamespace(candles=SimpleNamespace(candles_data=None))
        FakeStableIQOption.websocket_connected = True
        return True, None

    def change_balance(self, mode: str):
        self.balance_mode = str(mode)
        return True

    def check_connect(self):
        return FakeStableIQOption.websocket_connected

    def update_ACTIVES_OPCODE(self):
        self._actives = dict(ACTIVE_MAP_REFRESHED)
        return None

    def get_all_ACTIVES_OPCODE(self):
        return dict(self._actives)

    def get_all_open_time(self):
        return OPEN_TIME_MAP

    def get_all_profit(self):
        return {
            'EURUSD-OTC': {'turbo': 0.80},
            'GBPUSD-OTC': {'turbo': 0.80},
            'AUDUSD-OTC': {'turbo': 0.80},
            'USDJPY-OTC': {'turbo': 0.80},
            'BTCUSD-L': {'turbo': 0.80},
            'XAUUSD': {'turbo': 0.80},
        }

    def get_candles(self, asset: str, interval_sec: int, count: int, endtime: int):
        if not hasattr(self, 'api'):
            raise AttributeError("'IQ_Option' object has no attribute 'api'")
        if asset not in self._actives:
            print(f'Asset {asset} not found on consts')
            return None
        start = int(endtime) - int(interval_sec)
        candles = [
            {
                'from': start,
                'open': 1.0,
                'close': 1.1,
                'min': 0.9,
                'max': 1.2,
                'volume': 10.0,
            }
        ]
        self.api.candles.candles_data = candles
        return candles


@dataclass(frozen=True)
class _FakeIQ:
    email: str = 'user@example.com'
    password: str = 'secret'
    balance_mode: str = 'PRACTICE'


@dataclass(frozen=True)
class _FakeData:
    asset: str
    interval_sec: int
    db_path: str
    max_batch: int = 5
    timezone: str = 'UTC'


@dataclass(frozen=True)
class _FakeSettings:
    iq: _FakeIQ
    data: _FakeData


def main() -> int:
    iq_client_mod._IQ_OPTION_CLASS = FakeStableIQOption
    iq_client_mod._IQ_OPTION_IMPORT_ERROR = None
    assets = (
        'EURUSD-OTC',
        'GBPUSD-OTC',
        'AUDUSD-OTC',
        'USDJPY-OTC',
        'BTCUSD-OTC',
        'XAUUSD-OTC',
    )

    with tempfile.TemporaryDirectory(prefix='thalor_phase3_collect_fix_') as tmp:
        root = Path(tmp)
        collect_recent_mod.time = SimpleNamespace(sleep=lambda _x: None)
        collect_recent_mod.iqoption_dependency_status = lambda: {'available': True, 'reason': None}
        for asset in assets:
            FakeStableIQOption.websocket_connected = False
            settings = _FakeSettings(
                iq=_FakeIQ(),
                data=_FakeData(asset=asset, interval_sec=300, db_path=str(root / 'data' / f'{asset}.sqlite3')),
            )
            collect_recent_mod.load_settings = lambda settings=settings: settings
            collect_recent_mod.main()
            con = open_db(settings.data.db_path)
            try:
                rows = count_candles(con, asset, 300)
            finally:
                con.close()
            if rows < 1:
                raise SystemExit(f'collect_recent did not persist candles for {asset}')

    print('OK phase3_collect_recent_iq_fix_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
