from __future__ import annotations

import json
from pathlib import Path

from natbin.config.loader import load_resolved_config
from natbin.ops.provider_probe import build_provider_probe_payload
from natbin.ops.redaction_audit import build_redaction_audit_payload


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: live_controlled_practice',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: PRACTICE',
                'broker:',
                '  provider: iqoption',
                '  balance_mode: PRACTICE',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                'network:',
                '  transport:',
                '    enabled: true',
                '    endpoint_file: secrets/transport_endpoint',
                'observability:',
                '  request_metrics:',
                '    enabled: true',
            ]
        )
        + '\n',
        encoding='utf-8',
    )
    return cfg


def _write_bundle(repo_root: Path) -> None:
    (repo_root / 'config' / 'broker_secrets.yaml').write_text(
        'broker:\n'
        '  email: trader@example.com\n'
        '  password: ultra-secret\n'
        '  balance_mode: PRACTICE\n',
        encoding='utf-8',
    )


def test_loader_autodiscovers_config_broker_secret_bundle(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    _write_bundle(tmp_path)
    secrets_dir = tmp_path / 'secrets'
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / 'transport_endpoint').write_text('socks5h://user:pass@gate.example.internal:7000', encoding='utf-8')
    monkeypatch.delenv('THALOR_SECRETS_FILE', raising=False)

    resolved = load_resolved_config(repo_root=tmp_path, config_path=cfg)

    assert resolved.broker.email == 'trader@example.com'
    assert any(item == 'secret_file:bundle:broker_secrets.yaml' for item in resolved.source_trace)


def test_provider_probe_uses_autodiscovered_bundle_for_credentials(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    _write_bundle(tmp_path)
    monkeypatch.delenv('THALOR_SECRETS_FILE', raising=False)

    from natbin.ops import provider_probe as module

    monkeypatch.setattr(
        module,
        'audit_security_posture',
        lambda *args, **kwargs: {
            'ok': True,
            'blocked': False,
            'severity': 'ok',
            'credential_source': 'external_secret_file',
            'checks': [],
            'source_trace': [],
        },
    )

    payload = build_provider_probe_payload(repo_root=tmp_path, config_path=cfg, active=False)

    cred_check = next(item for item in payload['checks'] if item['name'] == 'provider_credentials')
    assert cred_check['status'] == 'ok'


def test_redaction_audit_ignores_masked_proxy_urls(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _write_bundle(tmp_path)
    control_dir = tmp_path / 'runs' / 'control' / 'EURUSD-OTC_300s'
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / 'connectivity.json').write_text(
        json.dumps(
            {
                'transport': {
                    'config': {
                        'endpoints': [
                            {'proxy_url': 'socks5h://***:***@gate.decodo.com:7000'}
                        ]
                    }
                }
            },
            indent=2,
        ),
        encoding='utf-8',
    )

    payload = build_redaction_audit_payload(repo_root=tmp_path, config_path=cfg)

    assert payload['ok'] is True
    assert payload['findings'] == []
