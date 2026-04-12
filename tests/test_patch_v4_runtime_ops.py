from __future__ import annotations

import subprocess
from pathlib import Path

from natbin.config.loader import load_resolved_config
from natbin.portfolio.subprocess import run_python_module
from natbin.security.audit import audit_security_posture


def test_audit_security_posture_warns_missing_credentials_when_execution_disabled(tmp_path: Path) -> None:
    cfg = tmp_path / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'execution:',
                '  enabled: false',
                '  mode: disabled',
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
    resolved = load_resolved_config(repo_root=tmp_path, config_path=cfg)
    payload = audit_security_posture(
        repo_root=tmp_path,
        config_path=cfg,
        resolved_config=resolved,
        source_trace=list(resolved.source_trace),
    )
    check = next(item for item in payload['checks'] if item['name'] == 'broker_credentials_present')
    assert check['status'] == 'warn'
    assert payload['blocked'] is False
    assert payload['severity'] == 'warn'


def test_run_python_module_returns_timeout_outcome(monkeypatch, tmp_path: Path) -> None:
    class _Timeout(subprocess.TimeoutExpired):
        def __init__(self) -> None:
            super().__init__(cmd=['python', '-m', 'natbin.collect_recent'], timeout=180, output='partial stdout', stderr='partial stderr')

    def _raise_timeout(*args, **kwargs):
        raise _Timeout()

    monkeypatch.setattr(subprocess, 'run', _raise_timeout)
    outcome = run_python_module(tmp_path, name='collect_recent:test', module='natbin.collect_recent', timeout_sec=180)
    assert outcome.returncode == 124
    assert 'TimeoutExpired' in outcome.stderr_tail
    assert 'partial stdout' in outcome.stdout_tail
