from __future__ import annotations

import json
from pathlib import Path

import yaml

from natbin.config.loader import load_resolved_config
from natbin.runtime.connectivity import build_runtime_connectivity_payload, build_runtime_network_transport_config
from natbin.runtime_app import load_runtime_app_config
from natbin.utils.network_transport import NetworkTransportManager


def _write_minimal_live_config(repo_root: Path, *, filename: str = 'live_controlled_practice.yaml') -> Path:
    config_dir = repo_root / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        'version': '2.0',
        'runtime': {'profile': 'live_controlled_practice'},
        'security': {
            'deployment_profile': 'live',
            'secrets_file': 'config/broker_secrets.yaml',
        },
        'assets': [
            {
                'asset': 'EURUSD-OTC',
                'interval_sec': 300,
                'timezone': 'America/Sao_Paulo',
            }
        ],
    }
    path = config_dir / filename
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return path



def _write_broker_bundle(repo_root: Path, text: str) -> Path:
    config_dir = repo_root / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / 'broker_secrets.yaml'
    path.write_text(text, encoding='utf-8')
    return path



def test_p0_autodiscovers_standard_transport_secret_from_broker_bundle(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / 'repo'
    config_path = _write_minimal_live_config(repo)
    _write_broker_bundle(
        repo,
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: ultra-secret',
                '  balance_mode: PRACTICE',
                '',
            ]
        ),
    )
    secrets_dir = repo / 'secrets'
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / 'transport_endpoint').write_text(
        'socks5h://proxy-user:proxy-pass@gate.provider.internal:7000?name=provider-primary&priority=5',
        encoding='utf-8',
    )

    monkeypatch.setenv('THALOR_SECRETS_FILE', 'config/broker_secrets.yaml')

    resolved = load_resolved_config(repo_root=repo, config_path=config_path)
    assert resolved.broker.email == 'trader@example.com'
    assert resolved.network.transport.enabled is True
    assert resolved.network.transport.endpoint_file == Path('secrets/transport_endpoint')
    assert any(item == 'secret_file:transport_endpoint:secrets/transport_endpoint' for item in resolved.source_trace)

    transport_cfg = build_runtime_network_transport_config(resolved_config=resolved, repo_root=repo)
    assert transport_cfg.enabled is True
    assert transport_cfg.ready is True
    assert transport_cfg.endpoints[0].name == 'provider-primary'
    assert transport_cfg.endpoints[0].host == 'gate.provider.internal'

    app_cfg = load_runtime_app_config(config_path=config_path, repo_root=repo)
    assert app_cfg.transport_enabled is True

    connectivity = build_runtime_connectivity_payload(resolved_config=resolved, repo_root=repo)
    assert connectivity['transport_enabled'] is True
    assert connectivity['transport_ready'] is True
    assert 'secret_file:transport_endpoint:secrets/transport_endpoint' in connectivity['source_trace']



def test_p0_bundle_can_define_transport_and_request_metrics_inline(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / 'repo'
    config_path = _write_minimal_live_config(repo, filename='live_controlled_real.yaml')
    _write_broker_bundle(
        repo,
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: ultra-secret',
                '  balance_mode: REAL',
                'network:',
                '  transport:',
                '    endpoint: socks5h://inline-user:inline-pass@gate.inline.internal:7001?name=inline-primary&priority=9',
                '    no_proxy: localhost,127.0.0.1',
                'observability:',
                '  request_metrics:',
                '    enabled: true',
                '    timezone: America/Sao_Paulo',
                '    structured_log_path: runs/logs/request_metrics_provider.jsonl',
                '',
            ]
        ),
    )

    monkeypatch.setenv('THALOR_SECRETS_FILE', 'config/broker_secrets.yaml')

    resolved = load_resolved_config(repo_root=repo, config_path=config_path)
    assert resolved.network.transport.enabled is True
    assert resolved.network.transport.endpoint == 'socks5h://inline-user:inline-pass@gate.inline.internal:7001?name=inline-primary&priority=9'
    assert list(resolved.network.transport.no_proxy or []) == ['localhost', '127.0.0.1']
    assert resolved.observability.request_metrics.enabled is True
    assert Path(str(resolved.observability.request_metrics.structured_log_path)).as_posix() == 'runs/logs/request_metrics_provider.jsonl'
    assert any(item == 'secret_file:transport_bundle:broker_secrets.yaml' for item in resolved.source_trace)
    assert any(item == 'secret_file:request_metrics:broker_secrets.yaml' for item in resolved.source_trace)

    transport_cfg = build_runtime_network_transport_config(resolved_config=resolved, repo_root=repo)
    assert transport_cfg.ready is True
    assert transport_cfg.endpoints[0].name == 'inline-primary'



def test_network_transport_binding_redacts_proxy_auth_everywhere() -> None:
    manager = NetworkTransportManager.from_mapping(
        {
            'enabled': True,
            'endpoint': 'socks5h://sensitive-user:sensitive-pass@gate.secure.internal:7000?name=secure-primary',
        }
    )

    binding = manager.select_binding(operation='connect')
    payload = binding.as_dict(mask_secret=True)
    blob = json.dumps(payload, ensure_ascii=False)

    assert 'sensitive-user' not in blob
    assert 'sensitive-pass' not in blob
    assert payload['websocket_options']['http_proxy_auth'] == ['***', '***']
    assert payload['env_overlay']['ALL_PROXY'] == 'socks5h://***:***@gate.secure.internal:7000'
