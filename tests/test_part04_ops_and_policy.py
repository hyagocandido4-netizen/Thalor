from __future__ import annotations

from pathlib import Path

from natbin.config.loader import load_resolved_config
from natbin.control.ops import breaker_reset, breaker_status
from natbin.ops.config_provenance import build_config_provenance_payload


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
                'security:',
                '  deployment_profile: local',
            ]
        )
        + '\n',
        encoding='utf-8',
    )
    return cfg


def test_secret_bundle_balance_mode_is_ignored_for_effective_config(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    bundle = tmp_path / 'config' / 'broker_secrets.yaml'
    bundle.write_text(
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: ultra-secret',
                '  balance_mode: REAL',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('THALOR_SECRETS_FILE', 'config/broker_secrets.yaml')

    resolved = load_resolved_config(repo_root=tmp_path, config_path=cfg)
    assert resolved.broker.balance_mode == 'PRACTICE'

    payload = build_config_provenance_payload(repo_root=tmp_path, config_path=cfg)
    item = next(check for check in payload['checks'] if check['name'] == 'secret_bundle_balance_mode_override')
    assert item['status'] == 'ok'


def test_breaker_reset_round_trip(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    first = breaker_status(repo_root=tmp_path, config_path=cfg)
    assert first['breaker']['state'] == 'closed'

    reset = breaker_reset(repo_root=tmp_path, config_path=cfg, reason='unit_test')
    assert reset['breaker']['state'] == 'closed'
    assert reset['breaker']['changed'] is True
    assert reset['breaker']['reset_reason'] == 'unit_test'
