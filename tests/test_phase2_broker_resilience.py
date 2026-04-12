from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from natbin.adapters.iq_client import IQClient, IQConfig
from natbin.state.db import open_db, upsert_candles
from natbin.usecases import collect_recent as collect_recent_mod
from natbin.usecases import refresh_market_context as refresh_market_context_mod
from natbin.utils.network_transport import NetworkTransportManager


@dataclass(frozen=True)
class _FakeData:
    asset: str
    interval_sec: int
    db_path: str
    max_batch: int = 10
    timezone: str = 'UTC'


@dataclass(frozen=True)
class _FakeSettings:
    data: _FakeData


def _seed_db(db_path: Path, *, asset: str = 'EURUSD-OTC', interval_sec: int = 300, age_sec: int = 300) -> None:
    con = open_db(str(db_path))
    try:
        candle_ts = int((datetime.now(UTC) - timedelta(seconds=int(age_sec))).timestamp())
        upsert_candles(
            con,
            asset,
            interval_sec,
            [
                {
                    'from': candle_ts,
                    'open': 1.0,
                    'max': 1.1,
                    'min': 0.9,
                    'close': 1.05,
                    'volume': 10.0,
                }
            ],
        )
    finally:
        con.close()


class _FailingConnectClient:
    def connect(self) -> None:
        raise TimeoutError('connect timed out after 0.1s')


class _FailingMarketContextClient:
    def connect(self) -> None:
        return None

    def get_market_context(self, *, asset: str, interval_sec: int, payout_fallback: float):
        raise TimeoutError(f'market_context timed out for {asset}:{interval_sec}')


class _HangingProfitBackend:
    def __init__(self) -> None:
        self.calls = 0

    def get_all_profit(self):
        self.calls += 1
        threading.Event().wait(0.25)
        return {'EURUSD-OTC': {'turbo': 0.91}}


def _resolved_broker(**overrides):
    broker = {
        'collect_reuse_local_data_on_failure': True,
        'collect_reuse_local_max_age_sec': 3600,
        'market_context_cache_fallback_enable': True,
        'market_context_cache_max_age_sec': 21600,
    }
    broker.update(overrides)
    return SimpleNamespace(broker=SimpleNamespace(**broker))


def test_collect_recent_reuses_fresh_local_data_on_operational_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / 'market.sqlite3'
    ctx_path = tmp_path / 'market_context.json'
    _seed_db(db_path, age_sec=300)

    settings = _FakeSettings(data=_FakeData(asset='EURUSD-OTC', interval_sec=300, db_path=str(db_path), max_batch=5))
    monkeypatch.setattr(collect_recent_mod, 'load_settings', lambda: settings)
    monkeypatch.setattr(collect_recent_mod, 'resolve_repo_root', lambda **_kwargs: tmp_path)
    monkeypatch.setattr(collect_recent_mod, 'load_resolved_config', lambda **_kwargs: _resolved_broker())
    monkeypatch.setattr(collect_recent_mod, 'iqoption_dependency_status', lambda: {'available': True, 'reason': None})
    monkeypatch.setattr(collect_recent_mod.IQClient, 'from_runtime_config', classmethod(lambda cls, **_kwargs: _FailingConnectClient()))
    monkeypatch.setenv('MARKET_CONTEXT_PATH', str(ctx_path))

    with pytest.raises(SystemExit) as exc_info:
        collect_recent_mod.main()

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload['mode'] == 'broker_failure_fallback'
    assert payload['action'] == 'reuse_local_data'
    assert payload['db_usable'] is True
    assert ctx_path.exists()


def test_collect_recent_keeps_fail_closed_when_local_snapshot_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / 'market.sqlite3'
    ctx_path = tmp_path / 'market_context.json'
    _seed_db(db_path, age_sec=7200)

    settings = _FakeSettings(data=_FakeData(asset='EURUSD-OTC', interval_sec=300, db_path=str(db_path), max_batch=5))
    monkeypatch.setattr(collect_recent_mod, 'load_settings', lambda: settings)
    monkeypatch.setattr(collect_recent_mod, 'resolve_repo_root', lambda **_kwargs: tmp_path)
    monkeypatch.setattr(
        collect_recent_mod,
        'load_resolved_config',
        lambda **_kwargs: _resolved_broker(collect_reuse_local_max_age_sec=3600),
    )
    monkeypatch.setattr(collect_recent_mod, 'iqoption_dependency_status', lambda: {'available': True, 'reason': None})
    monkeypatch.setattr(collect_recent_mod.IQClient, 'from_runtime_config', classmethod(lambda cls, **_kwargs: _FailingConnectClient()))
    monkeypatch.setenv('MARKET_CONTEXT_PATH', str(ctx_path))

    with pytest.raises(SystemExit) as exc_info:
        collect_recent_mod.main()

    assert exc_info.value.code == 2
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload['mode'] == 'broker_failure_fallback'
    assert payload['action'] == 'fail_local_data_stale'
    assert payload['db_usable'] is False
    assert ctx_path.exists()


def test_refresh_market_context_uses_cached_payout_on_operational_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / 'market.sqlite3'
    ctx_path = tmp_path / 'market_context.json'
    _seed_db(db_path, age_sec=300)
    ctx_path.write_text(
        json.dumps(
            {
                'asset': 'EURUSD-OTC',
                'interval_sec': 300,
                'market_open': True,
                'open_source': 'cached',
                'payout': 0.87,
                'payout_source': 'cached',
                'at_utc': datetime.now(UTC).isoformat(timespec='seconds'),
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )

    settings = _FakeSettings(data=_FakeData(asset='EURUSD-OTC', interval_sec=300, db_path=str(db_path), max_batch=5))
    monkeypatch.setattr(refresh_market_context_mod, 'load_settings', lambda: settings)
    monkeypatch.setattr(refresh_market_context_mod, 'resolve_repo_root', lambda **_kwargs: tmp_path)
    monkeypatch.setattr(refresh_market_context_mod, 'load_resolved_config', lambda **_kwargs: _resolved_broker())
    monkeypatch.setattr(refresh_market_context_mod, 'iqoption_dependency_status', lambda: {'available': True, 'reason': None})
    monkeypatch.setattr(
        refresh_market_context_mod.IQClient,
        'from_runtime_config',
        classmethod(lambda cls, **_kwargs: _FailingMarketContextClient()),
    )
    monkeypatch.setenv('MARKET_CONTEXT_PATH', str(ctx_path))

    refresh_market_context_mod.main()

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload['fallback_mode'] == 'broker_failure_cache_fallback'
    assert payload['broker_available'] is False
    assert payload['cache_used'] is True
    assert payload['payout'] == pytest.approx(0.87)
    assert payload['payout_source'] == 'cached'
    assert payload['open_source'] in {'db_fresh', 'db_stale'}


def test_iq_client_market_context_timeout_budget_returns_quickly(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _HangingProfitBackend()
    client = object.__new__(IQClient)
    client.cfg = IQConfig(
        email='user@example.com',
        password='secret',
        balance_mode='PRACTICE',
        market_context_timeout_s=0.05,
    )
    client.iq = backend
    client._logger = logging.getLogger('natbin.tests.phase2_broker_resilience')
    client._guarded_call_cooldowns = {}
    client._asset_registry = {}
    client._asset_resolution_cache = {}
    client._transport_manager = NetworkTransportManager.from_mapping({'enabled': False})
    client._request_metrics = None
    client._active_transport_binding = None
    client._maybe_throttle = lambda _label: None
    client.ensure_connection = lambda: None
    client._new_api = lambda: None
    client.resolve_asset_name = lambda asset, require_active_id=False: asset

    monkeypatch.setenv('IQ_PROFIT_RETRIES', '1')

    t0 = time.perf_counter()
    payload = client.get_market_context(asset='EURUSD-OTC', interval_sec=300, payout_fallback=0.8)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.15
    assert payload['payout'] == pytest.approx(0.8)
    assert payload['payout_source'] == 'fallback'
    assert backend.calls == 1
