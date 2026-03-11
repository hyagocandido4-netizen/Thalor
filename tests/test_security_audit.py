from __future__ import annotations

from pathlib import Path

from natbin.control.plan import build_context
from natbin.state.control_repo import read_control_artifact


def test_build_context_writes_security_artifact_and_redacted_effective_config(tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    (repo / 'config').mkdir(parents=True, exist_ok=True)
    (repo / 'config' / 'base.yaml').write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'security:',
                '  allow_embedded_credentials: false',
                '  deployment_profile: live',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                'broker:',
                '  email: trader@example.com',
                '  password: plain-secret',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )

    ctx = build_context(repo_root=repo, config_path=repo / 'config' / 'base.yaml')
    security = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='security')
    assert isinstance(security, dict)
    assert security.get('blocked') is True
    checks = {item['name']: item['status'] for item in security.get('checks') or []}
    assert checks.get('embedded_credentials') == 'error'

    eff_latest = repo / 'runs' / 'config' / 'effective_config_latest_EURUSD-OTC_300s.json'
    text = eff_latest.read_text(encoding='utf-8')
    assert 'plain-secret' not in text
    assert 'trader@example.com' not in text
    assert '***REDACTED***' in text
