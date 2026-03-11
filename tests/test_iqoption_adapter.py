from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from natbin.brokers.base import BrokerScope
from natbin.brokers.iqoption import IQOptionAdapter
from natbin.runtime.execution_models import SubmitOrderRequest


class StubIQClient:
    def __init__(self):
        self.connected = False
        self.orders: dict[str, dict] = {}
        self.next_id = 2000

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


class SilentBackend:
    def connect(self, retries=None, sleep_s=None):
        return None

    def ensure_connection(self):
        return None

    def submit_binary_option(self, *, asset: str, amount: float, side: str, duration_min: int):
        raise AssertionError('submit_binary_option should not be called in this test')

    def get_async_order(self, order_id):
        return {}

    def get_betinfo_safe(self, order_id):
        return False, None

    def get_recent_closed_options(self, limit: int = 20):
        return {'msg': {'closed_options': []}}

    def get_option_open_by_other_pc(self):
        return {}

    def list_async_orders(self):
        return {}

    def list_socket_opened_orders(self):
        return {}

    def list_socket_closed_orders(self):
        return {}

    def asset_name_from_opcode(self, opcode):
        return None


def _req(expiry_ts: int) -> SubmitOrderRequest:
    return SubmitOrderRequest(
        intent_id='intent_iq_test',
        client_order_key='thalor-intent_iq_test',
        broker_name='iqoption',
        account_mode='PRACTICE',
        scope_tag='EURUSD-OTC_300s',
        asset='EURUSD-OTC',
        interval_sec=300,
        side='CALL',
        amount=2.0,
        currency='BRL',
        signal_ts=expiry_ts - 300,
        expiry_ts=expiry_ts,
        entry_deadline_utc=datetime.fromtimestamp(expiry_ts + 2, tz=UTC).isoformat(timespec='seconds'),
        metadata={'source': 'test'},
    )


def test_iqoption_adapter_healthcheck_with_stub_backend(tmp_path: Path) -> None:
    adapter = IQOptionAdapter(repo_root=tmp_path, account_mode='PRACTICE', execution_mode='live', backend=StubIQClient())
    health = adapter.healthcheck()
    assert health.ready is True
    assert health.healthy is True
    assert health.broker_name == 'iqoption'


def test_iqoption_adapter_submit_and_settle(tmp_path: Path) -> None:
    backend = StubIQClient()
    adapter = IQOptionAdapter(repo_root=tmp_path, account_mode='PRACTICE', execution_mode='live', backend=backend)
    expiry_ts = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    submit = adapter.submit_order(_req(expiry_ts))
    assert submit.transport_status == 'ack'
    assert submit.external_order_id is not None

    scope = BrokerScope(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s', account_mode='PRACTICE')
    open_orders = adapter.fetch_open_orders(scope)
    assert len(open_orders) == 1
    assert open_orders[0].broker_status == 'open'

    backend.settle(str(submit.external_order_id), 'win', close_time=int(datetime.now(UTC).timestamp()) + 1)
    snapshot = adapter.fetch_order(str(submit.external_order_id))
    assert snapshot is not None
    assert snapshot.broker_status == 'closed_win'
    assert snapshot.settlement_status == 'win'
    assert snapshot.net_pnl is not None and snapshot.net_pnl > 0

    closed_orders = adapter.fetch_closed_orders(scope, since_utc=datetime.now(UTC) - timedelta(hours=1))
    assert len(closed_orders) == 1
    assert closed_orders[0].broker_status == 'closed_win'


def test_iqoption_adapter_uses_local_grace_window_after_restart(tmp_path: Path) -> None:
    backend = StubIQClient()
    adapter = IQOptionAdapter(repo_root=tmp_path, account_mode='PRACTICE', execution_mode='live', backend=backend, settle_grace_sec=30)
    expiry_ts = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    submit = adapter.submit_order(_req(expiry_ts))
    assert submit.external_order_id is not None

    # New adapter/process with no live session state must still keep the order OPEN
    # until expiry + grace based on the persisted bridge-state file.
    restarted = IQOptionAdapter(repo_root=tmp_path, account_mode='PRACTICE', execution_mode='live', backend=SilentBackend(), settle_grace_sec=30)
    snapshot = restarted.fetch_order(str(submit.external_order_id))
    assert snapshot is not None
    assert snapshot.broker_status == 'open'


def test_iqoption_adapter_paper_mode_is_fail_closed(tmp_path: Path) -> None:
    adapter = IQOptionAdapter(repo_root=tmp_path, account_mode='PRACTICE', execution_mode='paper', backend=StubIQClient())
    health = adapter.healthcheck()
    assert health.ready is False
    submit = adapter.submit_order(_req(int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())))
    assert submit.transport_status == 'reject'
    assert submit.error_code == 'iqoption_live_mode_required'
