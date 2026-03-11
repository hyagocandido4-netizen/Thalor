#!/usr/bin/env python
from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


class StubIQClient:
    def __init__(self):
        self.connected = False
        self.orders: dict[str, dict] = {}
        self.next_id = 1000

    def connect(self, retries=None, sleep_s=None):
        self.connected = True

    def ensure_connection(self):
        if not self.connected:
            raise RuntimeError('not_connected')

    def submit_binary_option(self, *, asset: str, amount: float, side: str, duration_min: int):
        self.ensure_connection()
        self.next_id += 1
        order_id = str(self.next_id)
        now_ts = int(datetime.now(UTC).timestamp())
        self.orders[order_id] = {
            'asset': asset,
            'side': side,
            'amount': amount,
            'duration_min': duration_min,
            'open_time': now_ts,
            'expiration_time': now_ts + duration_min * 60,
            'status': 'open',
        }
        return True, order_id

    def get_async_order(self, order_id):
        order = self.orders.get(str(order_id))
        if not order:
            return {}
        if order['status'] == 'open':
            return {
                'option-opened': {
                    'name': 'option-opened',
                    'msg': {
                        'option_id': int(order_id),
                        'active': order['asset'],
                        'direction': order['side'],
                        'value': order['amount'],
                        'open_time': order['open_time'],
                        'expiration_time': order['expiration_time'],
                    },
                }
            }
        return {
            'option-closed': {
                'name': 'option-closed',
                'msg': {
                    'option_id': int(order_id),
                    'active': order['asset'],
                    'direction': order['side'],
                    'sum': order['amount'],
                    'win': order['status'],
                    'win_amount': order['win_amount'],
                    'close_time': order['close_time'],
                    'expiration_time': order['expiration_time'],
                },
            }
        }

    def get_betinfo_safe(self, order_id):
        order = self.orders.get(str(order_id))
        if not order or order['status'] == 'open':
            return False, None
        return True, {
            'result': {
                'data': {
                    str(order_id): {
                        'win': order['status'],
                        'profit': order['win_amount'],
                        'deposit': order['amount'],
                        'expiration_time': order['expiration_time'],
                    }
                }
            }
        }

    def get_recent_closed_options(self, limit: int = 20):
        rows = []
        for order_id, order in self.orders.items():
            if order['status'] == 'open':
                continue
            rows.append({
                'id': [int(order_id)],
                'active': order['asset'],
                'direction': order['side'],
                'amount': order['amount'],
                'win': order['status'],
                'win_amount': order['win_amount'],
                'close_time': order['close_time'],
                'expiration_time': order['expiration_time'],
            })
        return {'msg': {'closed_options': rows[-limit:]}}

    def get_option_open_by_other_pc(self):
        out = {}
        for order_id, order in self.orders.items():
            if order['status'] != 'open':
                continue
            out[str(order_id)] = {
                'name': 'socket-option-opened',
                'msg': {
                    'id': int(order_id),
                    'active': order['asset'],
                    'direction': order['side'],
                    'amount': order['amount'],
                    'open_time': order['open_time'],
                    'expiration_time': order['expiration_time'],
                },
            }
        return out

    def list_async_orders(self):
        return {str(order_id): self.get_async_order(order_id) for order_id in self.orders}

    def list_socket_opened_orders(self):
        return self.get_option_open_by_other_pc()

    def list_socket_closed_orders(self):
        out = {}
        for order_id, order in self.orders.items():
            if order['status'] == 'open':
                continue
            out[str(order_id)] = {
                'name': 'socket-option-closed',
                'msg': {
                    'id': int(order_id),
                    'active': order['asset'],
                    'direction': order['side'],
                    'sum': order['amount'],
                    'win': order['status'],
                    'win_amount': order['win_amount'],
                    'close_time': order['close_time'],
                    'expiration_time': order['expiration_time'],
                },
            }
        return out

    def asset_name_from_opcode(self, opcode):
        return None

    def settle(self, order_id: str, status: str, *, close_time: int | None = None):
        order = self.orders[str(order_id)]
        status_norm = str(status).strip().lower()
        if status_norm == 'win':
            win_amount = float(order['amount']) * 1.8
        elif status_norm in {'equal', 'refund'}:
            status_norm = 'equal'
            win_amount = float(order['amount'])
        else:
            status_norm = 'loose'
            win_amount = 0.0
        order['status'] = status_norm
        order['win_amount'] = win_amount
        order['close_time'] = int(close_time or (order['expiration_time'] + 1))


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

    with tempfile.TemporaryDirectory() as td:
        backend = StubIQClient()
        iq = IQOptionAdapter(repo_root=td, account_mode='PRACTICE', execution_mode='live', backend=backend)
        iq_health = iq.healthcheck()
        if iq_health.broker_name != 'iqoption' or not iq_health.ready:
            _fail(f'IQOptionAdapter healthcheck mismatch: {iq_health.as_dict()}')

        req = SubmitOrderRequest(
            intent_id='intent_iq_001',
            client_order_key='thalor-intent_iq_001',
            broker_name='iqoption',
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
        submit = iq.submit_order(req)
        if submit.transport_status != 'ack' or not submit.external_order_id:
            _fail(f'IQOptionAdapter submit mismatch: {submit.as_dict()}')
        scope = BrokerScope(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s', account_mode='PRACTICE')
        open_orders = iq.fetch_open_orders(scope)
        if len(open_orders) != 1 or open_orders[0].broker_status != 'open':
            _fail(f'IQOptionAdapter open snapshot mismatch: {[o.as_dict() for o in open_orders]}')
        backend.settle(str(submit.external_order_id), 'win', close_time=int(datetime.now(UTC).timestamp()) + 1)
        snap = iq.fetch_order(str(submit.external_order_id))
        if snap is None or snap.broker_status != 'closed_win' or snap.settlement_status != 'win':
            _fail(f'IQOptionAdapter closed snapshot mismatch: {snap.as_dict() if snap else None}')
        closed = iq.fetch_closed_orders(scope, since_utc=datetime.now(UTC) - timedelta(hours=1))
        if not closed or closed[0].broker_status != 'closed_win':
            _fail(f'IQOptionAdapter closed order list mismatch: {[o.as_dict() for o in closed]}')
        _ok('iqoption live bridge contract ok (stubbed backend)')

    print('[broker-adapter] ALL OK')


if __name__ == '__main__':
    main()
