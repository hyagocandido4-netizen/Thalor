from __future__ import annotations

from pathlib import Path

import yaml

from natbin.config.loader import load_resolved_config
from natbin.config.paths import resolve_config_path, resolve_repo_root
from natbin.ops.docker_contract import build_docker_runtime_contract


MIN_CONFIG = """
version: "2.0"
assets:
  - asset: EURUSD-OTC
    interval_sec: 300
network:
  transport:
    enabled: false
observability:
  request_metrics:
    enabled: false
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def test_resolve_config_path_honors_repo_root_and_thalor_config_env(monkeypatch, tmp_path: Path) -> None:
    custom_cfg = tmp_path / 'config' / 'multi_asset.yaml'
    override_cfg = tmp_path / 'config' / 'override.yaml'
    _write(custom_cfg, MIN_CONFIG)
    _write(override_cfg, MIN_CONFIG)

    monkeypatch.setenv('THALOR_REPO_ROOT', str(tmp_path))
    assert resolve_repo_root(repo_root=None, config_path=None) == tmp_path.resolve()

    monkeypatch.setenv('THALOR_CONFIG', 'config/multi_asset.yaml')
    monkeypatch.delenv('THALOR_CONFIG_PATH', raising=False)
    assert resolve_config_path(repo_root=tmp_path, config_path=None) == custom_cfg.resolve()

    monkeypatch.setenv('THALOR_CONFIG_PATH', 'config/override.yaml')
    assert resolve_config_path(repo_root=tmp_path, config_path=None) == override_cfg.resolve()


def test_compat_env_source_maps_transport_and_request_metrics_aliases(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / 'config' / 'multi_asset.yaml'
    _write(cfg, MIN_CONFIG)

    monkeypatch.setenv('TRANSPORT_ENABLED', '1')
    monkeypatch.setenv('TRANSPORT_ENDPOINT_FILE', 'secrets/transport_endpoint')
    monkeypatch.setenv('REQUEST_METRICS_ENABLED', '1')
    monkeypatch.setenv('REQUEST_METRICS_LOG_PATH', 'runs/logs/request_metrics_alias.jsonl')

    resolved = load_resolved_config(repo_root=tmp_path, config_path=cfg)
    assert resolved.network.transport.enabled is True
    assert resolved.network.transport.endpoint_file == Path('secrets/transport_endpoint')
    assert resolved.observability.request_metrics.enabled is True
    assert resolved.observability.request_metrics.structured_log_path == Path('runs/logs/request_metrics_alias.jsonl')


def test_docker_contract_uses_effective_env_and_reports_ready_transport(tmp_path: Path) -> None:
    cfg = tmp_path / 'config' / 'multi_asset.yaml'
    _write(cfg, MIN_CONFIG)
    endpoint_file = tmp_path / 'secrets' / 'transport_endpoint'
    _write(endpoint_file, 'socks5://user:pass@proxy.contract.internal:1080?name=contract-primary')

    env = {
        'THALOR_REPO_ROOT': str(tmp_path),
        'THALOR_CONFIG': 'config/multi_asset.yaml',
        'TRANSPORT_ENABLED': '1',
        'TRANSPORT_ENDPOINT_FILE': 'secrets/transport_endpoint',
        'REQUEST_METRICS_ENABLED': '1',
        'REQUEST_METRICS_LOG_PATH': 'runs/logs/request_metrics.jsonl',
    }
    payload = build_docker_runtime_contract(repo_root=None, env=env)
    assert payload['ok'] is True
    assert payload['requested']['transport_enabled'] is True
    assert payload['resolved']['transport_enabled'] is True
    assert payload['resolved']['transport_ready'] is True
    assert payload['resolved']['request_metrics_enabled'] is True
    assert payload['resolved']['config_path'] == str(cfg.resolve())


def test_compose_files_use_wrapper_scripts_and_standalone_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((repo / 'docker-compose.yml').read_text(encoding='utf-8'))
    compose_vps = yaml.safe_load((repo / 'docker-compose.vps.yml').read_text(encoding='utf-8'))
    compose_prod = yaml.safe_load((repo / 'docker-compose.prod.yml').read_text(encoding='utf-8'))
    dockerfile = (repo / 'Dockerfile').read_text(encoding='utf-8')
    base_text = (repo / 'docker-compose.yml').read_text(encoding='utf-8')

    assert compose['services']['thalor-dashboard']['command'] == ['bash', '/app/scripts/docker/dashboard.sh']
    assert 'python -m natbin.dashboard --repo-root . --config "${THALOR_CONFIG}" --no-browser' not in base_text

    for payload in (compose_vps, compose_prod):
        for service_name in ('thalor-runtime', 'thalor-backup', 'thalor-dashboard'):
            service = payload['services'][service_name]
            assert 'build' in service
            assert 'env_file' in service
            assert 'command' in service
        assert payload['services']['thalor-runtime']['command'] == ['bash', '/app/scripts/docker/runtime_loop.sh']
        assert payload['services']['thalor-backup']['command'] == ['bash', '/app/scripts/docker/backup_loop.sh']
        assert payload['services']['thalor-dashboard']['command'] == ['bash', '/app/scripts/docker/dashboard.sh']
        assert payload['services']['thalor-runtime']['environment']['THALOR_CONFIG_PATH'] == '${THALOR_CONFIG:-config/multi_asset.yaml}'
        assert payload['services']['thalor-backup']['environment']['THALOR_CONFIG_PATH'] == '${THALOR_CONFIG:-config/multi_asset.yaml}'
        assert payload['services']['thalor-dashboard']['environment']['THALOR_DASHBOARD_CONFIG_PATH'] == '${THALOR_DASHBOARD_CONFIG:-config/multi_asset.yaml}'

    assert 'ulimits' in compose_prod['services']['thalor-runtime']
    assert 'CMD ["bash", "/app/scripts/docker/runtime_status.sh"]' in dockerfile
    assert 'THALOR__PRODUCTION__PROFILE=docker' in dockerfile
    assert 'THALOR__SECURITY__DEPLOYMENT_PROFILE=docker' in dockerfile
