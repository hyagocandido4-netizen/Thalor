#!/usr/bin/env python
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.runtime_cycle import repo_python_executable
from natbin.runtime_control_repo import RuntimeControlRepository
from natbin.runtime_failsafe import CircuitBreakerSnapshot
from natbin.runtime_health import build_health_payload, build_status_payload
from natbin.runtime_scope import health_snapshot_path, loop_status_path


def ok(msg: str) -> None:
    print(f'[smoke][OK] {msg}')


def fail(msg: str) -> None:
    print(f'[smoke][FAIL] {msg}')
    raise SystemExit(2)


def _run(args, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    py = Path(repo_python_executable(cwd))
    return subprocess.run([str(py), '-m', 'natbin.runtime_daemon', *args], cwd=str(cwd), capture_output=True, text=True, env=env)


def main() -> None:
    hp = build_health_payload(asset='EURUSD-OTC', interval_sec=300, state='blocked', message='kill_switch', quota={}, failsafe={}, market_context={})
    sp = build_status_payload(asset='EURUSD-OTC', interval_sec=300, phase='precheck', state='blocked', message='kill_switch', next_wake_utc=None, sleep_reason='next_candle', report={}, quota={}, failsafe={}, market_context={})
    if hp.get('state') != 'blocked' or sp.get('phase') != 'precheck':
        fail('runtime_health payload builders broken')
    ok('runtime_health payload builders ok')

    tmp = ROOT / 'runs_smoke_daemon_fs'
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    _ = health_snapshot_path(asset='EURUSD-OTC', interval_sec=300, out_dir=tmp)
    _ = loop_status_path(asset='EURUSD-OTC', interval_sec=300, out_dir=tmp)
    ok('runtime health scope paths ok')

    # integration-level breaker precheck is covered by failsafe_integration_smoke.
    # Here we only verify that the daemon honours a blocked precheck before any cycle runs.
    env = dict(os.environ)
    env['PYTHONPATH'] = str(SRC) + ((env.get('PYTHONPATH') and (os.pathsep + env['PYTHONPATH'])) or '')
    env['THALOR_KILL_SWITCH'] = '1'
    cp = _run(['--repo-root', str(ROOT), '--once'], ROOT, env)
    if cp.returncode != 0:
        fail(f'runtime_daemon --once expected 0 on blocked precheck, got {cp.returncode}: {cp.stderr or cp.stdout}')
    try:
        payload = json.loads(cp.stdout)
    except Exception as e:
        fail(f'runtime_daemon --once output not json: {e}')
    if payload.get('phase') != 'precheck' or payload.get('message') not in {'env:THALOR_KILL_SWITCH', 'kill_switch'}:
        fail(f'expected blocked precheck by kill switch, got {payload}')
    ok('runtime_daemon precheck blocks before cycle ok')

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
