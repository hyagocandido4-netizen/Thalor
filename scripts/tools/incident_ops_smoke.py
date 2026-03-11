#!/usr/bin/env python
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
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
    _touch(repo / 'Dockerfile', 'FROM python:3.12-slim\n')
    _touch(repo / 'docker-compose.yml', 'services: {}\n')
    _touch(repo / 'docker-compose.prod.yml', 'services: {}\n')
    _touch(repo / '.gitignore', '.env\nsecrets/\nruns/\ndata/\n')
    for name in [
        'OPERATIONS.md',
        'DOCKER.md',
        'ALERTING_M7.md',
        'PRODUCTION_CHECKLIST_M7.md',
        'DIAGRAMS_M7.md',
        'INCIDENT_RUNBOOKS_M71.md',
        'LIVE_OPS_HARDENING_M71.md',
    ]:
        _touch(repo / 'docs' / name, f'# {name}\n')
    _touch(repo / 'README_PACKAGE_M7_1_APPEND.md', '# m71\n')
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
    day = datetime.now(tz=UTC).strftime('%Y%m%d')
    inc = repo / 'runs' / 'incidents' / f'incidents_{day}_EURUSD-OTC_300s.jsonl'
    inc.parent.mkdir(parents=True, exist_ok=True)
    inc.write_text(
        json.dumps(
            {
                'kind': 'incident',
                'incident_type': 'market_context_stale',
                'severity': 'warning',
                'recorded_at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
                'asset': 'EURUSD-OTC',
                'interval_sec': 300,
                'day': datetime.now(tz=UTC).strftime('%Y-%m-%d'),
                'ts': int(datetime.now(tz=UTC).timestamp()),
            }
        )
        + '\n',
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
    with tempfile.TemporaryDirectory(prefix='thalor_m71_incident_ops_') as td:
        repo = Path(td)
        cfg = _write_repo(repo)

        status = _run(repo, 'incidents', 'status', '--repo-root', str(repo), '--config', str(cfg), '--json')
        if status.get('kind') != 'incident_status' or status.get('severity') not in {'warn', 'error'}:
            fail(f'unexpected incident status payload: {status}')
        ok('runtime_app incidents status works')

        report = _run(repo, 'incidents', 'report', '--repo-root', str(repo), '--config', str(cfg), '--json')
        if report.get('kind') != 'incident_report':
            fail(f'incident report payload mismatch: {report}')
        paths = report.get('artifacts') or {}
        if not Path(str(paths.get('report_path') or '')).exists():
            fail('incident report artifact missing')
        ok('runtime_app incidents report works')

        alert = _run(repo, 'incidents', 'alert', '--repo-root', str(repo), '--config', str(cfg), '--json')
        delivery = ((alert.get('alert') or {}).get('delivery') or {})
        if delivery.get('status') != 'queued':
            fail(f'incident alert should be queued in smoke scenario: {alert}')
        ok('runtime_app incidents alert queues telegram summary')

        drill = _run(repo, 'incidents', 'drill', '--repo-root', str(repo), '--config', str(cfg), '--scenario', 'broker_down', '--json')
        if drill.get('kind') != 'incident_drill' or drill.get('scenario') != 'broker_down':
            fail(f'incident drill payload mismatch: {drill}')
        ok('runtime_app incidents drill works')

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
