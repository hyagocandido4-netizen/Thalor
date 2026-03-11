#!/usr/bin/env python
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.control.commands import security_payload
from natbin.control.plan import build_context
from natbin.security.broker_guard import evaluate_submit_guard, note_submit_attempt
from natbin.state.control_repo import read_control_artifact


def ok(msg: str) -> None:
    print(f'[smoke][OK] {msg}')


def fail(msg: str) -> None:
    print(f'[smoke][FAIL] {msg}')
    raise SystemExit(2)


def _write_repo(repo: Path) -> Path:
    cfg_path = repo / 'config' / 'base.yaml'
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    (repo / 'secrets').mkdir(parents=True, exist_ok=True)
    (repo / 'secrets' / 'broker.yaml').write_text(
        '\n'.join(
            [
                'broker:',
                '  email: smoke@example.com',
                '  password: smoke-secret',
                '  balance_mode: PRACTICE',
                '',
            ]
        ),
        encoding='utf-8',
    )
    cfg_path.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'security:',
                '  deployment_profile: live',
                '  secrets_file: secrets/broker.yaml',
                '  allow_embedded_credentials: false',
                '  guard:',
                '    enabled: true',
                '    live_only: true',
                '    min_submit_spacing_sec: 10',
                '    max_submit_per_minute: 1',
                '    time_filter_enable: true',
                '    allowed_start_local: "09:00"',
                '    allowed_end_local: "17:00"',
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
    return cfg_path


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='thalor_m6_security_') as td:
        repo = Path(td)
        cfg_path = _write_repo(repo)
        env = os.environ.copy()
        env['THALOR_SECRETS_FILE'] = 'secrets/broker.yaml'
        extra_path = str(SRC)
        if env.get('PYTHONPATH'):
            env['PYTHONPATH'] = extra_path + os.pathsep + env['PYTHONPATH']
        else:
            env['PYTHONPATH'] = extra_path

        old = os.environ.get('THALOR_SECRETS_FILE')
        os.environ['THALOR_SECRETS_FILE'] = 'secrets/broker.yaml'
        try:
            ctx = build_context(repo_root=repo, config_path=cfg_path)
            security = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='security')
            if not isinstance(security, dict):
                fail('security control artifact missing')
            if security.get('credential_source') != 'external_secret_file':
                fail(f'unexpected credential source: {security}')
            ok('build_context writes security artifact with external secret source')

            eff_latest = repo / 'runs' / 'config' / 'effective_config_latest_EURUSD-OTC_300s.json'
            body = eff_latest.read_text(encoding='utf-8')
            if 'smoke@example.com' in body or 'smoke-secret' in body:
                fail('effective config leaked a broker secret')
            ok('effective config dump is redacted')

            payload = security_payload(repo_root=repo, config_path=cfg_path)
            if payload.get('blocked'):
                fail(f'security payload should be clean in external-secret scenario: {payload}')
            ok('security payload command returns ok state')

            out = subprocess.check_output(
                [
                    sys.executable,
                    '-m',
                    'natbin.runtime_app',
                    'security',
                    '--repo-root',
                    str(repo),
                    '--config',
                    str(cfg_path),
                    '--json',
                ],
                cwd=str(repo),
                env=env,
                text=True,
            )
            cli = json.loads(out)
            if cli.get('credential_source') != 'external_secret_file':
                fail(f'runtime_app security command unexpected payload: {cli}')
            ok('runtime_app security command works')

            t0 = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)
            first = evaluate_submit_guard(repo_root=repo, ctx=ctx, now_utc=t0)
            if not first.allowed:
                fail(f'guard should allow first submit: {first.as_dict()}')
            note_submit_attempt(repo_root=repo, ctx=ctx, transport_status='ack', now_utc=t0)
            spacing = evaluate_submit_guard(repo_root=repo, ctx=ctx, now_utc=t0 + timedelta(seconds=5))
            if spacing.reason != 'security_submit_spacing':
                fail(f'expected spacing block, got {spacing.as_dict()}')
            rate = evaluate_submit_guard(repo_root=repo, ctx=ctx, now_utc=t0 + timedelta(seconds=12))
            if rate.reason != 'security_submit_rate_limit':
                fail(f'expected rate-limit block, got {rate.as_dict()}')
            closed = evaluate_submit_guard(repo_root=repo, ctx=ctx, now_utc=datetime(2026, 3, 10, 20, 0, tzinfo=UTC))
            if closed.reason != 'security_time_filter_closed':
                fail(f'expected time-filter block, got {closed.as_dict()}')
            ok('broker guard enforces spacing + rate limit + time filter')
        finally:
            if old is None:
                os.environ.pop('THALOR_SECRETS_FILE', None)
            else:
                os.environ['THALOR_SECRETS_FILE'] = old

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
