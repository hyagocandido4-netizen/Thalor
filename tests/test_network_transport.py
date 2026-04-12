from __future__ import annotations

import os
import sys
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from natbin.utils.network_transport import NetworkTransportConfig, NetworkTransportManager, NetworkTransportUnavailable



def test_transport_config_from_env_builds_endpoint_and_binding(monkeypatch) -> None:
    monkeypatch.setenv('TRANSPORT_ENDPOINT', 'http://user:pass@proxy.internal:8080?name=primary&priority=10')
    monkeypatch.setenv('TRANSPORT_NO_PROXY', 'localhost,127.0.0.1')
    monkeypatch.setenv('TRANSPORT_ENABLED', '1')

    cfg = NetworkTransportConfig.from_env()

    assert cfg.enabled is True
    assert cfg.ready is True
    assert len(cfg.endpoints) == 1
    endpoint = cfg.endpoints[0]
    assert endpoint.name == 'primary'
    assert endpoint.host == 'proxy.internal'
    assert endpoint.port == 8080
    assert endpoint.priority == 10
    assert endpoint.no_proxy == ('localhost', '127.0.0.1')

    manager = NetworkTransportManager(cfg)
    binding = manager.select_binding(operation='connect')

    assert binding.enabled is True
    assert binding.env_overlay['HTTP_PROXY'] == 'http://user:pass@proxy.internal:8080'
    assert binding.env_overlay['NO_PROXY'] == 'localhost,127.0.0.1'
    assert binding.websocket_options['http_proxy_host'] == 'proxy.internal'
    assert binding.websocket_options['http_proxy_port'] == 8080
    manager.record_success(binding.endpoint, operation='connect')



def test_transport_config_from_endpoint_file_has_priority(monkeypatch, tmp_path) -> None:
    endpoint_file = tmp_path / 'transport_endpoint'
    endpoint_file.write_text('socks5://user:pass@proxy.file.internal:1080?name=file-primary', encoding='utf-8')

    monkeypatch.setenv('TRANSPORT_ENDPOINT', 'http://proxy.inline.internal:8080?name=inline-primary')
    monkeypatch.setenv('TRANSPORT_ENDPOINT_FILE', str(endpoint_file))
    monkeypatch.setenv('THALOR__NETWORK__TRANSPORT__ENABLED', '1')

    cfg = NetworkTransportConfig.from_env()

    assert cfg.enabled is True
    assert cfg.ready is True
    assert len(cfg.endpoints) == 1
    endpoint = cfg.endpoints[0]
    assert endpoint.name == 'file-primary'
    assert endpoint.scheme == 'socks5'
    assert endpoint.host == 'proxy.file.internal'
    assert endpoint.port == 1080



def test_manager_quarantines_failed_endpoint_and_recovers_via_healthcheck() -> None:
    now = {'value': 0.0}

    manager = NetworkTransportManager(
        NetworkTransportConfig.from_mapping(
            {
                'enabled': True,
                'endpoints': [
                    {'name': 'primary', 'scheme': 'http', 'host': 'proxy-a.local', 'port': 8080, 'priority': 1},
                    {'name': 'secondary', 'scheme': 'http', 'host': 'proxy-b.local', 'port': 8081, 'priority': 2},
                ],
                'failure_threshold': 1,
                'quarantine_base_s': 30.0,
                'quarantine_max_s': 30.0,
                'healthcheck_interval_s': 0.0,
                'fail_open_when_exhausted': False,
            }
        ),
        monotonic_fn=lambda: now['value'],
        random_fn=lambda: 0.0,
    )

    first = manager.select_binding(operation='connect')
    assert first.endpoint is not None
    assert first.endpoint.name == 'primary'
    manager.record_failure(first.endpoint, operation='connect', error=RuntimeError('boom'))

    second = manager.select_binding(operation='connect')
    assert second.endpoint is not None
    assert second.endpoint.name == 'secondary'
    manager.record_success(second.endpoint, operation='connect')

    manager._probe_endpoint = lambda endpoint: (endpoint.name == 'primary', None if endpoint.name == 'primary' else 'unhealthy')  # type: ignore[attr-defined]
    health = manager.run_health_checks(only_unavailable=True)

    assert health['checked'] == 1
    assert health['healthy'] == 1

    recovered = manager.select_binding(operation='connect')
    assert recovered.endpoint is not None
    assert recovered.endpoint.name == 'primary'
    manager.record_success(recovered.endpoint, operation='connect')



def test_execute_retries_with_backoff_and_restores_environment(monkeypatch) -> None:
    monkeypatch.setenv('HTTP_PROXY', 'http://old-proxy.internal:9000')
    sleeps: list[float] = []
    attempts = {'count': 0}

    manager = NetworkTransportManager(
        NetworkTransportConfig.from_mapping(
            {
                'enabled': True,
                'endpoints': ['http://proxy.internal:8080?name=primary'],
                'max_retries': 3,
                'backoff_base_s': 0.5,
                'backoff_max_s': 5.0,
                'jitter_ratio': 0.0,
            }
        ),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        random_fn=lambda: 0.0,
    )

    def _call(binding):
        attempts['count'] += 1
        assert binding.endpoint is not None
        assert os.environ['HTTP_PROXY'] == 'http://proxy.internal:8080'
        if attempts['count'] == 1:
            raise TimeoutError('temporary transport timeout')
        return 'ok'

    result = manager.execute(
        operation='connect',
        func=_call,
        retry_exceptions=(TimeoutError,),
        apply_environment_overlay=True,
    )

    assert result == 'ok'
    assert attempts['count'] == 2
    assert sleeps == [0.5]
    assert os.environ['HTTP_PROXY'] == 'http://old-proxy.internal:9000'

    snapshot = manager.snapshot()
    totals = {item['endpoint']['name']: (item['total_successes'], item['total_failures']) for item in snapshot['endpoints']}
    assert totals['primary'] == (1, 1)



def test_parallel_selection_and_success_accounting_is_thread_safe() -> None:
    manager = NetworkTransportManager(
        NetworkTransportConfig.from_mapping(
            {
                'enabled': True,
                'endpoints': [
                    {'name': 'a', 'scheme': 'http', 'host': 'proxy-a.local', 'port': 8001, 'priority': 1},
                    {'name': 'b', 'scheme': 'http', 'host': 'proxy-b.local', 'port': 8002, 'priority': 1},
                ],
            }
        )
    )

    def _work(_: int) -> None:
        binding = manager.select_binding(operation='collect')
        manager.record_success(binding.endpoint, operation='collect')

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_work, range(64)))

    snapshot = manager.snapshot()
    assert sum(item['total_successes'] for item in snapshot['endpoints']) == 64
    assert sum(item['inflight'] for item in snapshot['endpoints']) == 0



def test_manager_respects_allow_fail_open_override() -> None:
    now = {'value': 0.0}

    manager = NetworkTransportManager(
        NetworkTransportConfig.from_mapping(
            {
                'enabled': True,
                'endpoints': ['http://proxy.internal:8080?name=primary'],
                'failure_threshold': 1,
                'quarantine_base_s': 60.0,
                'quarantine_max_s': 60.0,
                'healthcheck_interval_s': 999.0,
                'fail_open_when_exhausted': True,
            }
        ),
        monotonic_fn=lambda: now['value'],
        random_fn=lambda: 0.0,
    )

    binding = manager.select_binding(operation='connect')
    manager.record_failure(binding.endpoint, operation='connect', error=RuntimeError('boom'))

    with pytest.raises(NetworkTransportUnavailable):
        manager.select_binding(operation='connect', allow_fail_open=False)

    recovered = manager.select_binding(operation='connect')
    assert recovered.endpoint is not None
    assert recovered.endpoint.name == 'primary'



def test_http_proxy_healthcheck_marks_407_unhealthy(monkeypatch) -> None:
    manager = NetworkTransportManager(
        NetworkTransportConfig.from_mapping(
            {
                'enabled': True,
                'endpoints': ['http://user:pass@proxy.internal:8080?name=primary'],
                'healthcheck_mode': 'http',
                'healthcheck_url': 'https://iqoption.com/api/appinit',
                'fail_open_when_exhausted': False,
            }
        )
    )
    endpoint = manager.config.endpoints[0]

    class _FakeSocket:
        def __init__(self):
            self.sent = b''
            self._response = b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Agent: fake\r\n\r\n'
        def settimeout(self, _timeout):
            return None
        def sendall(self, data):
            self.sent += data
        def recv(self, _size):
            if not self._response:
                return b''
            data, self._response = self._response, b''
            return data
        def close(self):
            return None

    fake = _FakeSocket()
    monkeypatch.setattr('natbin.utils.network_transport.socket.create_connection', lambda *a, **k: fake)

    ok, reason = manager._probe_endpoint(endpoint)

    assert ok is False
    assert reason == 'http_status:407'
    assert b'CONNECT iqoption.com:443 HTTP/1.1' in fake.sent



def test_socks_proxy_healthcheck_uses_remote_connect(monkeypatch) -> None:
    manager = NetworkTransportManager(
        NetworkTransportConfig.from_mapping(
            {
                'enabled': True,
                'endpoints': ['socks5h://user:pass@proxy.internal:1080?name=primary'],
                'healthcheck_mode': 'http',
                'healthcheck_url': 'https://iqoption.com/api/appinit',
                'fail_open_when_exhausted': False,
            }
        )
    )
    endpoint = manager.config.endpoints[0]
    calls: dict[str, object] = {}

    class _FakeSock:
        def settimeout(self, timeout):
            calls['timeout'] = timeout
        def set_proxy(self, proxy_type, host, port, rdns=False, username=None, password=None):
            calls['proxy'] = {
                'proxy_type': proxy_type,
                'host': host,
                'port': port,
                'rdns': rdns,
                'username': username,
                'password': password,
            }
        def connect(self, target):
            calls['target'] = target
        def close(self):
            calls['closed'] = True

    fake_socks = types.SimpleNamespace(SOCKS5=5, SOCKS4=4, socksocket=lambda: _FakeSock())
    monkeypatch.setitem(sys.modules, 'socks', fake_socks)

    ok, reason = manager._probe_endpoint(endpoint)

    assert ok is True
    assert reason is None
    assert calls['target'] == ('iqoption.com', 443)
    assert calls['proxy']['host'] == 'proxy.internal'
    assert calls['proxy']['rdns'] is True
