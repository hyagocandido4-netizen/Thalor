from __future__ import annotations

from pathlib import Path

import yaml

from natbin import runtime_app, runtime_daemon
from natbin.control.commands import status_payload
from natbin.state.control_repo import read_control_artifact, write_control_artifact


def _write_config(repo_root: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / 'config' / 'base.yaml'
    payload = yaml.safe_load(source.read_text(encoding='utf-8'))

    payload.setdefault('network', {}).setdefault('transport', {})
    payload['network']['transport'].update(
        {
            'enabled': True,
            'endpoint': None,
            'endpoint_file': 'secrets/transport_endpoint',
            'structured_log_path': 'runs/logs/network_transport_test.jsonl',
        }
    )
    payload.setdefault('observability', {}).setdefault('request_metrics', {})
    payload['observability']['request_metrics'].update(
        {
            'enabled': True,
            'timezone': 'America/Sao_Paulo',
            'structured_log_path': 'runs/logs/request_metrics_test.jsonl',
        }
    )

    config_dir = repo_root / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / 'base.yaml'
    config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding='utf-8')

    secrets_dir = repo_root / 'secrets'
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / 'transport_endpoint').write_text(
        'socks5://user:pass@proxy.runtime.internal:1080?name=runtime-file-primary&priority=5',
        encoding='utf-8',
    )
    return config_path


def test_runtime_app_and_daemon_register_connectivity_builders(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    app_cfg = runtime_app.load_runtime_app_config(config_path=config_path, repo_root=tmp_path)
    assert app_cfg.transport_enabled is True
    assert app_cfg.request_metrics_enabled is True

    ctx = runtime_app.build_context(repo_root=tmp_path, config_path=config_path)

    transport_cfg = runtime_app.build_runtime_network_transport_config(
        resolved_config=ctx.resolved_config,
        repo_root=tmp_path,
    )
    assert transport_cfg.enabled is True
    assert transport_cfg.ready is True
    assert transport_cfg.endpoints[0].name == 'runtime-file-primary'
    assert transport_cfg.endpoints[0].scheme == 'socks5'
    assert transport_cfg.structured_log_path == (tmp_path / 'runs' / 'logs' / 'network_transport_test.jsonl')

    transport_manager = runtime_app.build_runtime_network_transport_manager(
        resolved_config=ctx.resolved_config,
        repo_root=tmp_path,
    )
    assert transport_manager.enabled is True
    assert transport_manager.ready is True

    request_metrics_cfg = runtime_app.build_runtime_request_metrics_config(
        resolved_config=ctx.resolved_config,
        repo_root=tmp_path,
    )
    assert request_metrics_cfg.enabled is True
    assert request_metrics_cfg.timezone == 'America/Sao_Paulo'
    assert request_metrics_cfg.structured_log_path == (tmp_path / 'runs' / 'logs' / 'request_metrics_test.jsonl')

    payload = runtime_daemon.build_runtime_connectivity_payload(
        resolved_config=ctx.resolved_config,
        repo_root=tmp_path,
    )
    assert payload['transport_enabled'] is True
    assert payload['transport_ready'] is True
    assert payload['request_metrics_enabled'] is True
    assert payload['transport']['endpoints'][0]['endpoint']['name'] == 'runtime-file-primary'
    assert payload['request_metrics']['enabled'] is True
    assert payload['request_metrics']['structured_log_path'] == str(tmp_path / 'runs' / 'logs' / 'request_metrics_test.jsonl')


def test_connectivity_artifact_roundtrip_and_status_surface(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    ctx = runtime_app.build_context(repo_root=tmp_path, config_path=config_path)

    payload = runtime_daemon.build_runtime_connectivity_payload(
        resolved_config=ctx.resolved_config,
        repo_root=tmp_path,
    )
    write_control_artifact(
        repo_root=tmp_path,
        asset=ctx.config.asset,
        interval_sec=ctx.config.interval_sec,
        name='connectivity',
        payload=payload,
    )
    stored = read_control_artifact(
        repo_root=tmp_path,
        asset=ctx.config.asset,
        interval_sec=ctx.config.interval_sec,
        name='connectivity',
    )
    assert isinstance(stored, dict)
    assert stored.get('transport_enabled') is True
    assert stored.get('transport_ready') is True

    status = status_payload(repo_root=tmp_path, config_path=config_path)
    assert status['control']['connectivity_source'] == 'artifact'
    assert status['control']['connectivity']['transport_enabled'] is True
    assert status['control']['connectivity']['request_metrics_enabled'] is True


def test_status_surface_persists_connectivity_artifact_during_context_build(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    status = status_payload(repo_root=tmp_path, config_path=config_path)
    assert status['control']['connectivity_source'] == 'artifact'
    assert status['control']['connectivity']['transport_enabled'] is True
    assert status['control']['connectivity']['transport_ready'] is True
    assert status['control']['connectivity']['request_metrics_enabled'] is True

