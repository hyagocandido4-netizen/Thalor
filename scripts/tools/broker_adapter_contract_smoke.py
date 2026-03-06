#!/usr/bin/env python
from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def _ok(msg: str) -> None:
    print(f'[broker-adapter][OK] {msg}')


def _fail(msg: str) -> None:
    print(f'[broker-adapter][FAIL] {msg}')
    raise SystemExit(2)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / 'src'
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.brokers.base import BrokerScope
    from natbin.brokers.fake import FakeBrokerAdapter
    from natbin.brokers.iqoption import IQOptionAdapter
    from natbin.runtime.execution_models import SubmitOrderRequest

    with tempfile.TemporaryDirectory() as td:
        adapter = FakeBrokerAdapter(
            repo_root=td,
            account_mode='PRACTICE',
            submit_behavior='ack',
            settlement='win',
            settle_after_sec=0,
        )
        health = adapter.healthcheck()
        if not health.ready:
            _fail(f'fake adapter healthcheck expected ready, got {health.as_dict()}')
        req = SubmitOrderRequest(
            intent_id='intent_001',
            client_order_key='thalor-intent_001',
            broker_name='fake',
            account_mode='PRACTICE',
            scope_tag='EURUSD-OTC_300s',
            asset='EURUSD-OTC',
            interval_sec=300,
            side='CALL',
            amount=2.0,
            currency='BRL',
            signal_ts=1772668800,
            expiry_ts=1772669100,
            entry_deadline_utc='2026-03-05T00:05:02+00:00',
            metadata={},
        )
        res = adapter.submit_order(req)
        if res.transport_status != 'ack' or not res.external_order_id:
            _fail(f'fake adapter submit contract mismatch: {res.as_dict()}')
        scope = BrokerScope(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s', account_mode='PRACTICE')
        open_orders = adapter.fetch_open_orders(scope)
        closed_orders = adapter.fetch_closed_orders(scope, since_utc=datetime(2026, 3, 5, 0, 0, 0, tzinfo=UTC))
        if open_orders:
            _fail('fake adapter immediate-win scenario should not keep open orders')
        if not closed_orders or closed_orders[0].settlement_status != 'win':
            _fail(f'fake adapter closed order contract mismatch: {[o.as_dict() for o in closed_orders]}')
        _ok('fake adapter contract ok')

    iq = IQOptionAdapter(account_mode='PRACTICE')
    iq_health = iq.healthcheck()
    if iq_health.broker_name != 'iqoption':
        _fail('IQOptionAdapter healthcheck broker_name mismatch')
    _ok('iqoption adapter lazy contract ok')
    print('[broker-adapter] ALL OK')


if __name__ == '__main__':
    main()
