from __future__ import annotations

from pathlib import Path

from natbin.config.loader import load_resolved_config


def test_load_resolved_config_reads_external_secret_bundle(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / 'repo'
    (repo / 'config').mkdir(parents=True, exist_ok=True)
    (repo / 'secrets').mkdir(parents=True, exist_ok=True)

    (repo / 'config' / 'base.yaml').write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'security:',
                '  deployment_profile: live',
                '  secrets_file: secrets/broker.yaml',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    (repo / 'secrets' / 'broker.yaml').write_text(
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: ultra-secret',
                '  balance_mode: PRACTICE',
                '',
            ]
        ),
        encoding='utf-8',
    )

    monkeypatch.setenv('THALOR_SECRETS_FILE', 'secrets/broker.yaml')
    cfg = load_resolved_config(repo_root=repo, config_path=repo / 'config' / 'base.yaml')

    assert cfg.broker.email == 'trader@example.com'
    assert cfg.broker.password is not None
    assert cfg.broker.password.get_secret_value() == 'ultra-secret'
    assert any(str(item).startswith('secret_file:bundle:broker.yaml') for item in cfg.source_trace)
