from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from natbin.adapters import iq_client as iq_client_mod
from natbin.utils import RequestMetrics, RequestMetricsConfig
from natbin.utils.network_transport import NetworkTransportManager


def _install_fake_pysocks(monkeypatch) -> None:
    original_find_spec = iq_client_mod.importlib.util.find_spec

    def _fake_find_spec(name: str, package=None):
        if name == 'socks':
            return object()
        return original_find_spec(name, package)

    monkeypatch.setattr(iq_client_mod.importlib.util, 'find_spec', _fake_find_spec)

    from natbin.utils import network_transport as network_transport_mod

    monkeypatch.setattr(network_transport_mod.importlib.util, 'find_spec', _fake_find_spec)


class _FakeWebSocketApp:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_forever(self, **kwargs):
        self.calls.append(dict(kwargs))
        return True


class _FakeWebsocketClient:
    instances: list['_FakeWebsocketClient'] = []

    def __init__(self, api) -> None:
        self.api = api
        self.wss = _FakeWebSocketApp()
        type(self).instances.append(self)


class _FakeIQOptionAPI:
    instances: list['_FakeIQOptionAPI'] = []
    fail_proxy_contains: str | None = None

    def __init__(self, host: str, username: str, password: str, proxies: dict[str, str] | None = None) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.proxies = dict(proxies or {})
        self.session = SimpleNamespace(verify=True, trust_env=False, proxies={})
        self.websocket_client = None
        self.connected = False
        self.headers = None
        self.cookies = None
        type(self).instances.append(self)

    def set_session(self, headers=None, cookies=None) -> None:
        self.headers = headers
        self.cookies = cookies

    def connect(self):
        ws_mod = sys.modules['iqoptionapi.ws.client']
        self.websocket_client = ws_mod.WebsocketClient(self)
        self.websocket_client.wss.run_forever()
        proxy_text = ' '.join(str(value) for value in (self.proxies or {}).values())
        if self.fail_proxy_contains and self.fail_proxy_contains in proxy_text:
            self.connected = False
            return False, f'connection refused via {self.fail_proxy_contains}'
        self.connected = True
        return True, None


class _FakeStableIQOption:
    __module__ = 'iqoptionapi.stable_api'

    def __init__(self, email: str, password: str) -> None:
        self.email = email
        self.password = password
        self.balance_mode = 'PRACTICE'
        self._connected = False
        self.api = None
        self.SESSION_HEADER = {'User-Agent': 'thalor-test'}
        self.SESSION_COOKIE = {}

    def connect(self):
        stable_mod = sys.modules[self.__class__.__module__]
        api_cls = stable_mod.IQOptionAPI
        self.api = api_cls('iqoption.com', self.email, self.password)
        self.api.set_session(headers=self.SESSION_HEADER, cookies=self.SESSION_COOKIE)
        ok, reason = self.api.connect()
        self._connected = bool(ok)
        return ok, reason

    def change_balance(self, mode: str):
        self.balance_mode = str(mode)
        return True

    def check_connect(self) -> bool:
        return self._connected


def _install_fake_iqoption_modules(monkeypatch, *, fail_proxy_contains: str | None = None) -> None:
    _FakeIQOptionAPI.instances = []
    _FakeIQOptionAPI.fail_proxy_contains = fail_proxy_contains
    _FakeWebsocketClient.instances = []

    package_mod = types.ModuleType('iqoptionapi')
    ws_package_mod = types.ModuleType('iqoptionapi.ws')
    ws_client_mod = types.ModuleType('iqoptionapi.ws.client')
    api_mod = types.ModuleType('iqoptionapi.api')
    stable_mod = types.ModuleType('iqoptionapi.stable_api')

    ws_client_mod.WebsocketClient = _FakeWebsocketClient
    api_mod.IQOptionAPI = _FakeIQOptionAPI
    stable_mod.IQOptionAPI = _FakeIQOptionAPI
    stable_mod.IQ_Option = _FakeStableIQOption

    package_mod.ws = ws_package_mod
    package_mod.api = api_mod
    package_mod.stable_api = stable_mod
    ws_package_mod.client = ws_client_mod

    monkeypatch.setitem(sys.modules, 'iqoptionapi', package_mod)
    monkeypatch.setitem(sys.modules, 'iqoptionapi.ws', ws_package_mod)
    monkeypatch.setitem(sys.modules, 'iqoptionapi.ws.client', ws_client_mod)
    monkeypatch.setitem(sys.modules, 'iqoptionapi.api', api_mod)
    monkeypatch.setitem(sys.modules, 'iqoptionapi.stable_api', stable_mod)
    monkeypatch.setattr(iq_client_mod, '_IQ_OPTION_CLASS', _FakeStableIQOption, raising=False)
    monkeypatch.setattr(iq_client_mod, '_IQ_OPTION_IMPORT_ERROR', None, raising=False)


def test_iq_client_connect_injects_transport_into_http_and_websocket(monkeypatch) -> None:
    _install_fake_iqoption_modules(monkeypatch)
    _install_fake_pysocks(monkeypatch)
    monkeypatch.setenv('HTTP_PROXY', 'http://original-proxy.internal:9000')

    manager = NetworkTransportManager.from_mapping(
        {
            'enabled': True,
            'endpoints': ['socks5://user:pass@proxy.internal:1080?name=primary&priority=1'],
            'failure_threshold': 1,
            'backoff_base_s': 0.1,
            'backoff_max_s': 0.1,
            'jitter_ratio': 0.0,
        }
    )

    client = iq_client_mod.IQClient(
        iq_client_mod.IQConfig(email='user@example.com', password='secret', balance_mode='PRACTICE'),
        transport_manager=manager,
    )

    client.connect(retries=1, sleep_s=0.0)

    api = _FakeIQOptionAPI.instances[-1]
    ws_client = _FakeWebsocketClient.instances[-1]

    assert api.proxies == {
        'http': 'socks5://user:pass@proxy.internal:1080',
        'https': 'socks5://user:pass@proxy.internal:1080',
    }
    assert api.session.proxies == {
        'http': 'socks5://user:pass@proxy.internal:1080',
        'https': 'socks5://user:pass@proxy.internal:1080',
    }
    assert api.session.verify is True
    assert ws_client.wss.calls == [
        {
            'http_proxy_auth': ('user', 'pass'),
            'http_proxy_host': 'proxy.internal',
            'http_proxy_port': 1080,
            'proxy_type': 'socks5',
        }
    ]
    assert client.iq.balance_mode == 'PRACTICE'
    assert iq_client_mod.os.environ['HTTP_PROXY'] == 'http://original-proxy.internal:9000'

    snapshot = client.transport_snapshot()
    assert snapshot['active_binding']['endpoint']['name'] == 'primary'
    by_name = {item['endpoint']['name']: item for item in snapshot['endpoints']}
    assert by_name['primary']['total_successes'] == 1
    assert by_name['primary']['total_failures'] == 0


def test_iq_client_connect_fails_over_after_endpoint_failure(monkeypatch) -> None:
    _install_fake_iqoption_modules(monkeypatch, fail_proxy_contains='proxy-a.local')
    monkeypatch.setattr(iq_client_mod.time, 'sleep', lambda _seconds: None)

    manager = NetworkTransportManager.from_mapping(
        {
            'enabled': True,
            'endpoints': [
                {'name': 'primary', 'scheme': 'http', 'host': 'proxy-a.local', 'port': 8080, 'priority': 1},
                {'name': 'secondary', 'scheme': 'http', 'host': 'proxy-b.local', 'port': 8081, 'priority': 2},
            ],
            'failure_threshold': 1,
            'quarantine_base_s': 60.0,
            'quarantine_max_s': 60.0,
            'backoff_base_s': 0.1,
            'backoff_max_s': 0.1,
            'jitter_ratio': 0.0,
            'fail_open_when_exhausted': False,
        }
    )

    client = iq_client_mod.IQClient(
        iq_client_mod.IQConfig(email='user@example.com', password='secret', balance_mode='PRACTICE'),
        transport_manager=manager,
    )

    client.connect(retries=2, sleep_s=0.0)

    snapshot = client.transport_snapshot()
    by_name = {item['endpoint']['name']: item for item in snapshot['endpoints']}

    assert client._active_transport_binding is not None
    assert client._active_transport_binding.endpoint is not None
    assert client._active_transport_binding.endpoint.name == 'secondary'
    assert by_name['primary']['total_failures'] == 1
    assert by_name['primary']['quarantined'] is True
    assert by_name['secondary']['total_successes'] == 1



def test_iq_client_connect_records_request_metrics(monkeypatch) -> None:
    _install_fake_iqoption_modules(monkeypatch)

    manager = NetworkTransportManager.from_mapping(
        {
            'enabled': True,
            'endpoints': ['http://proxy.metrics.internal:8080?name=primary'],
            'failure_threshold': 1,
            'backoff_base_s': 0.1,
            'backoff_max_s': 0.1,
            'jitter_ratio': 0.0,
        }
    )
    metrics = RequestMetrics.from_config(RequestMetricsConfig(enabled=True, timezone='UTC'))

    client = iq_client_mod.IQClient(
        iq_client_mod.IQConfig(email='user@example.com', password='secret', balance_mode='PRACTICE'),
        transport_manager=manager,
        request_metrics=metrics,
    )

    client.connect(retries=1, sleep_s=0.0)

    snapshot = client.request_metrics_snapshot()
    assert snapshot is not None
    current = snapshot['current']
    assert current['total_requests'] == 1
    assert current['total_successes'] == 1
    assert current['total_failures'] == 0
    assert current['operation_counts'] == {'connect': 1}
    assert current['target_counts'] == {'iqoption:primary': 1}


def test_iq_client_get_candles_records_request_metrics_events(tmp_path: Path, monkeypatch) -> None:
    _install_fake_iqoption_modules(monkeypatch)

    def _update_actives(self):
        return None

    def _get_all_actives(self):
        return {'EURUSD-OTC': 1}

    def _get_candles(self, asset: str, interval_sec: int, count: int, endtime: int):
        return [
            {
                'from': int(endtime) - int(interval_sec),
                'open': 1.0,
                'close': 1.1,
                'min': 0.9,
                'max': 1.2,
                'volume': 10.0,
            }
        ]

    monkeypatch.setattr(_FakeStableIQOption, 'update_ACTIVES_OPCODE', _update_actives, raising=False)
    monkeypatch.setattr(_FakeStableIQOption, 'get_all_ACTIVES_OPCODE', _get_all_actives, raising=False)
    monkeypatch.setattr(_FakeStableIQOption, 'get_candles', _get_candles, raising=False)

    manager = NetworkTransportManager.from_mapping(
        {
            'enabled': True,
            'endpoints': ['socks5://user:pass@proxy.metrics.internal:1080?name=primary'],
            'failure_threshold': 1,
            'backoff_base_s': 0.1,
            'backoff_max_s': 0.1,
            'jitter_ratio': 0.0,
        }
    )
    log_path = tmp_path / 'request_metrics.jsonl'
    metrics = RequestMetrics.from_config(
        RequestMetricsConfig(enabled=True, timezone='UTC', structured_log_path=log_path, emit_request_events=True)
    )

    client = iq_client_mod.IQClient(
        iq_client_mod.IQConfig(email='user@example.com', password='secret', balance_mode='PRACTICE'),
        transport_manager=manager,
        request_metrics=metrics,
    )

    client.connect(retries=1, sleep_s=0.0)
    candles = client.get_candles('EURUSD-OTC', 300, 3, 1_700_000_000)

    assert candles and candles[0]['from'] == 1_700_000_000 - 300

    snapshot = client.request_metrics_snapshot()
    assert snapshot is not None
    current = snapshot['current']
    assert current['total_requests'] == 2
    assert current['operation_counts'] == {'connect': 1, 'get_candles': 1}

    lines = [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    request_events = [item for item in lines if item.get('event') == 'request_metrics_request']
    candle_event = next(item for item in request_events if item.get('operation') == 'get_candles')
    assert candle_event['success'] is True
    assert candle_event['requested_asset'] == 'EURUSD-OTC'
    assert candle_event['broker_asset'] == 'EURUSD-OTC'
    assert candle_event['interval_sec'] == 300
    assert candle_event['count'] == 3
    assert candle_event['transport_scheme'] == 'socks5'
    assert candle_event['transport_target'] == 'primary'



def test_iq_client_from_runtime_config_applies_dotenv_transport_and_metrics(tmp_path: Path, monkeypatch) -> None:
    _install_fake_iqoption_modules(monkeypatch)
    _install_fake_pysocks(monkeypatch)

    for key in (
        'THALOR__NETWORK__TRANSPORT__ENABLED',
        'THALOR__NETWORK__TRANSPORT__ENDPOINT',
        'THALOR__NETWORK__TRANSPORT__ENDPOINT_FILE',
        'THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED',
        'TRANSPORT_ENABLED',
        'TRANSPORT_ENDPOINT',
        'TRANSPORT_ENDPOINT_FILE',
    ):
        monkeypatch.delenv(key, raising=False)

    source_config = Path(__file__).resolve().parents[1] / 'config' / 'base.yaml'
    payload = yaml.safe_load(source_config.read_text(encoding='utf-8'))
    payload.setdefault('broker', {})
    payload['broker']['email'] = 'user@example.com'
    payload['broker']['password'] = 'secret'
    payload['broker']['balance_mode'] = 'PRACTICE'
    payload.setdefault('network', {}).setdefault('transport', {})['enabled'] = False
    payload.setdefault('observability', {}).setdefault('request_metrics', {})['enabled'] = False

    config_dir = tmp_path / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / 'base.yaml'
    config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding='utf-8')

    (tmp_path / '.env').write_text(
        '\n'.join(
            [
                'THALOR__NETWORK__TRANSPORT__ENABLED=1',
                'THALOR__NETWORK__TRANSPORT__ENDPOINT_FILE=secrets/transport_endpoint',
                'THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED=1',
                'THALOR__OBSERVABILITY__REQUEST_METRICS__TIMEZONE=UTC',
            ]
        )
        + '\n',
        encoding='utf-8',
    )

    secrets_dir = tmp_path / 'secrets'
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / 'transport_endpoint').write_text(
        'socks5://dotenv-user:dotenv-pass@proxy.dotenv.internal:1080?name=dotenv-primary',
        encoding='utf-8',
    )

    direct_manager = NetworkTransportManager.from_env()
    assert direct_manager.ready is False

    client = iq_client_mod.IQClient.from_runtime_config(
        repo_root=tmp_path,
        config_path=config_path,
        asset='EURUSD-OTC',
        interval_sec=300,
    )

    client.connect(retries=1, sleep_s=0.0)

    api = _FakeIQOptionAPI.instances[-1]
    assert api.proxies == {
        'http': 'socks5://dotenv-user:dotenv-pass@proxy.dotenv.internal:1080',
        'https': 'socks5://dotenv-user:dotenv-pass@proxy.dotenv.internal:1080',
    }

    transport_snapshot = client.transport_snapshot()
    assert transport_snapshot['active_binding']['endpoint']['name'] == 'dotenv-primary'

    metrics_snapshot = client.request_metrics_snapshot()
    assert metrics_snapshot is not None
    assert metrics_snapshot['current']['total_successes'] == 1
    assert metrics_snapshot['current']['target_counts'] == {'iqoption:dotenv-primary': 1}



def test_iq_client_connect_normalizes_iqoption_jsondecodeerror(monkeypatch) -> None:
    _install_fake_iqoption_modules(monkeypatch)

    class _BrokenStableIQOption(_FakeStableIQOption):
        def connect(self):
            raise json.JSONDecodeError('Expecting value', '', 0)

    global_value_mod = types.ModuleType('iqoptionapi.global_value')
    global_value_mod.websocket_error_reason = '502 Bad Gateway from proxy upstream'
    global_value_mod.check_websocket_if_connect = 0
    monkeypatch.setitem(sys.modules, 'iqoptionapi.global_value', global_value_mod)

    stable_mod = sys.modules['iqoptionapi.stable_api']
    stable_mod.IQ_Option = _BrokenStableIQOption
    monkeypatch.setattr(iq_client_mod, '_IQ_OPTION_CLASS', _BrokenStableIQOption, raising=False)

    manager = NetworkTransportManager.from_mapping(
        {
            'enabled': True,
            'endpoints': ['http://proxy-bad-json.internal:8080?name=primary'],
            'failure_threshold': 1,
            'backoff_base_s': 0.1,
            'backoff_max_s': 0.1,
            'jitter_ratio': 0.0,
        }
    )

    client = iq_client_mod.IQClient(
        iq_client_mod.IQConfig(email='user@example.com', password='secret', balance_mode='PRACTICE'),
        transport_manager=manager,
    )

    with pytest.raises(RuntimeError, match='non-JSON failure reason') as exc_info:
        client.connect(retries=1, sleep_s=0.0)

    assert '502 Bad Gateway from proxy upstream' in str(exc_info.value)

    snapshot = client.transport_snapshot()
    by_name = {item['endpoint']['name']: item for item in snapshot['endpoints']}
    assert by_name['primary']['total_failures'] == 1
    assert snapshot['active_binding'] is None



def test_iq_client_connect_respects_connect_timeout(monkeypatch) -> None:
    _install_fake_iqoption_modules(monkeypatch)

    class _SlowStableIQOption(_FakeStableIQOption):
        def connect(self):
            import time as _time
            _time.sleep(0.2)
            return True, None

    stable_mod = sys.modules['iqoptionapi.stable_api']
    stable_mod.IQ_Option = _SlowStableIQOption
    monkeypatch.setattr(iq_client_mod, '_IQ_OPTION_CLASS', _SlowStableIQOption, raising=False)

    manager = NetworkTransportManager.from_mapping(
        {
            'enabled': True,
            'endpoints': ['http://proxy-timeout.internal:8080?name=primary'],
            'failure_threshold': 1,
            'backoff_base_s': 0.1,
            'backoff_max_s': 0.1,
            'jitter_ratio': 0.0,
            'fail_open_when_exhausted': False,
        }
    )

    client = iq_client_mod.IQClient(
        iq_client_mod.IQConfig(email='user@example.com', password='secret', balance_mode='PRACTICE', connect_timeout_s=0.05),
        transport_manager=manager,
    )

    with pytest.raises(RuntimeError, match='timed out'):
        client.connect(retries=1, sleep_s=0.0)

    snapshot = client.transport_snapshot()
    by_name = {item['endpoint']['name']: item for item in snapshot['endpoints']}
    assert by_name['primary']['total_failures'] == 1



def test_iq_client_connect_fails_closed_when_transport_is_unavailable(monkeypatch) -> None:
    _install_fake_iqoption_modules(monkeypatch)

    manager = NetworkTransportManager.from_mapping(
        {
            'enabled': True,
            'endpoints': ['socks5://user:pass@proxy-dead.internal:1080?name=primary'],
            'failure_threshold': 1,
            'quarantine_base_s': 60.0,
            'quarantine_max_s': 60.0,
            'healthcheck_interval_s': 999.0,
            'fail_open_when_exhausted': False,
        }
    )
    dead_binding = manager.select_binding(operation='prime')
    manager.record_failure(dead_binding.endpoint, operation='prime', error=RuntimeError('boom'))

    client = iq_client_mod.IQClient(
        iq_client_mod.IQConfig(email='user@example.com', password='secret', balance_mode='PRACTICE'),
        transport_manager=manager,
    )

    from natbin.utils.network_transport import NetworkTransportUnavailable

    before = len(_FakeIQOptionAPI.instances)
    with pytest.raises(NetworkTransportUnavailable):
        client.connect(retries=1, sleep_s=0.0)
    assert len(_FakeIQOptionAPI.instances) == before
