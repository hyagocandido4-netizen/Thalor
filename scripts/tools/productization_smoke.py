#!/usr/bin/env python
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def ok(msg: str) -> None:
    print(f'[smoke][OK] {msg}')


def fail(msg: str) -> None:
    print(f'[smoke][FAIL] {msg}')
    raise SystemExit(2)


def _touch(path: Path, body: str = 'x\n') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')


def _write_repo(repo: Path) -> Path:
    _touch(repo / 'README.md', '# repo\n')
    _touch(repo / '.env.example', 'THALOR_SECRETS_FILE="secrets/bundle.yaml"\n')
    _touch(repo / 'requirements.txt', 'pydantic>=2\n')
    _touch(repo / 'pyproject.toml', '[build-system]\nrequires=["setuptools"]\n')
    _touch(repo / 'setup.cfg', '[metadata]\nname=natbin\n')
    _touch(repo / 'src' / 'natbin' / 'runtime_app.py', '# placeholder\n')
    _touch(repo / 'src' / 'natbin' / 'incidents' / 'reporting.py', '# placeholder\n')
    _touch(repo / 'scripts' / 'tools' / 'release_bundle.py', '# placeholder\n')
    _touch(repo / 'scripts' / 'tools' / 'incident_ops_smoke.py', '# placeholder\n')
    _touch(repo / 'docs/history/package_legacy/README_PACKAGE_M7_1_APPEND.md', '# m71\n')
    _touch(repo / 'Dockerfile', 'FROM python:3.12-slim\n')
    _touch(repo / 'docker-compose.yml', 'services: {}\n')
    _touch(repo / 'docker-compose.prod.yml', 'services: {}\n')
    _touch(repo / '.gitignore', '.env\nsecrets/\nruns/\ndata/\n')
    for name in ['OPERATIONS.md', 'DOCKER.md', 'ALERTING_M7.md', 'PRODUCTION_CHECKLIST_M7.md', 'DIAGRAMS_M7.md', 'INCIDENT_RUNBOOKS_M71.md', 'LIVE_OPS_HARDENING_M71.md']:
        _touch(repo / 'docs' / name, f'# {name}\n')
    (repo / 'secrets').mkdir(parents=True, exist_ok=True)
    (repo / 'secrets' / 'bundle.yaml').write_text(
        '\n'.join(
            [
                'broker:',
                '  email: smoke@example.com',
                '  password: smoke-secret',
                '  balance_mode: PRACTICE',
                'telegram:',
                '  bot_token: 123456:ABCDEF',
                '  chat_id: "999888777"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    cfg = repo / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  startup_invalidate_stale_artifacts: true',
                '  lock_refresh_enable: true',
                'security:',
                '  deployment_profile: live',
                '  secrets_file: secrets/bundle.yaml',
                '  live_require_external_credentials: true',
                'notifications:',
                '  enabled: true',
                '  telegram:',
                '    enabled: true',
                '    send_enabled: false',
                'multi_asset:',
                '  enabled: true',
                '  max_parallel_assets: 2',
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
    return cfg


def _run(repo: Path, *args: str) -> dict:
    env = os.environ.copy()
    extra = str(SRC)
    env['PYTHONPATH'] = extra + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    out = subprocess.check_output([sys.executable, '-m', 'natbin.runtime_app', *args], cwd=str(repo), env=env, text=True)
    return json.loads(out)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='thalor_m7_productization_') as td:
        repo = Path(td)
        cfg = _write_repo(repo)

        release = _run(repo, 'release', '--repo-root', str(repo), '--config', str(cfg), '--json')
        if release.get('severity') != 'warn':
            fail(f'unexpected release severity for queued-telegram scenario: {release}')
        ok('runtime_app release works')

        status = _run(repo, 'alerts', 'status', '--repo-root', str(repo), '--config', str(cfg), '--json')
        if not (status.get('telegram') or {}).get('enabled'):
            fail(f'alerts status missing telegram section: {status}')
        ok('runtime_app alerts status works')

        test_alert = _run(repo, 'alerts', 'test', '--repo-root', str(repo), '--config', str(cfg), '--json')
        last = test_alert.get('last_test_alert') or {}
        if (last.get('delivery') or {}).get('status') != 'queued':
            fail(f'test alert should be queued in smoke scenario: {test_alert}')
        ok('runtime_app alerts test queues alert when send is disabled')

        outbox = repo / 'runs' / 'alerts' / 'telegram_outbox.jsonl'
        if not outbox.exists():
            fail('telegram outbox missing after test alert')
        ok('telegram outbox created')

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
