#!/usr/bin/env python
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from natbin.runtime_daemon import (
    SleepPlan,
    acquire_lock,
    classify_report_ok,
    compute_day_reset_sleep,
    compute_next_candle_sleep,
    release_lock,
)
from natbin.runtime_cycle import repo_python_executable
from natbin.runtime_scope import build_scope, daemon_lock_path


def _ok(msg: str) -> None:
    print(f"[smoke][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[smoke][FAIL] {msg}")
    raise SystemExit(2)


def main() -> None:
    sp = compute_next_candle_sleep(300, offset_sec=3)
    if not isinstance(sp, SleepPlan) or sp.sleep_sec < 0 or not sp.next_wake_utc:
        _fail('compute_next_candle_sleep shape broken')
    _ok('compute_next_candle_sleep ok')

    day = compute_day_reset_sleep('UTC', offset_sec=3)
    if day.sleep_sec < 0 or not day.next_wake_utc:
        _fail('compute_day_reset_sleep shape broken')
    _ok('compute_day_reset_sleep ok')

    scope = build_scope('EURUSD-OTC', 300)
    lp = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir='runs_smoke')
    lp.parent.mkdir(parents=True, exist_ok=True)
    if not acquire_lock(lp):
        _fail('expected to acquire lock on first try')
    if acquire_lock(lp):
        _fail('expected lock re-acquire to fail')
    release_lock(lp)
    _ok('daemon lock helpers ok')

    rep = {'ok': True}
    if not classify_report_ok(rep):
        _fail('classify_report_ok expected True')
    _ok('classify_report_ok ok')

    repo = ROOT
    py = Path(repo_python_executable(repo))
    env = dict(**__import__('os').environ)
    env['PYTHONPATH'] = str(SRC) + ((env.get('PYTHONPATH') and (__import__('os').pathsep + env['PYTHONPATH'])) or '')
    cp = subprocess.run([str(py), '-m', 'natbin.runtime_daemon', '--repo-root', str(repo), '--plan-json'], cwd=str(repo), capture_output=True, text=True, env=env)
    if cp.returncode != 0:
        _fail(f'runtime_daemon --plan-json returned {cp.returncode}: {cp.stderr}')
    try:
        payload = json.loads(cp.stdout)
    except Exception as e:
        _fail(f'runtime_daemon --plan-json not json: {e}')
    if not payload.get('daemon_capable'):
        _fail('runtime_daemon --plan-json missing daemon_capable')
    _ok('runtime_daemon --plan-json ok')

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
