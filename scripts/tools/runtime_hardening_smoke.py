#!/usr/bin/env python
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
import sys
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.control.plan import build_context
from natbin.ops.lockfile import acquire_lock, read_lock_info, refresh_lock, release_lock
from natbin.runtime.daemon import run_once
from natbin.runtime.health import build_health_payload, build_status_payload, write_health_payload, write_status_payload
from natbin.runtime.hardening import startup_sanitize_runtime
from natbin.runtime.scope import daemon_lock_path, repo_scope
from natbin.state.control_repo import read_control_artifact, write_control_artifact


BASE_CONFIG = ROOT / 'config' / 'base.yaml'


def ok(msg: str) -> None:
    print(f'[smoke][OK] {msg}')


def fail(msg: str) -> None:
    print(f'[smoke][FAIL] {msg}')
    raise SystemExit(2)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='thalor_m3_smoke_') as td:
        repo = Path(td)
        (repo / 'config').mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE_CONFIG, repo / 'config' / 'base.yaml')

        ctx = build_context(repo_root=repo)
        scope = repo_scope(config_path=repo / 'config' / 'base.yaml', repo_root=repo)
        lock_path = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=repo / 'runs')

        stale_status = build_status_payload(
            asset=ctx.config.asset,
            interval_sec=int(ctx.config.interval_sec),
            phase='cycle',
            state='healthy',
            message='old_status',
            next_wake_utc=None,
            sleep_reason=None,
            report={'ok': True},
            quota={},
            failsafe={},
            market_context={},
        )
        stale_status['at_utc'] = '2000-01-01T00:00:00+00:00'
        stale_health = build_health_payload(
            asset=ctx.config.asset,
            interval_sec=int(ctx.config.interval_sec),
            state='healthy',
            message='old_health',
            quota={},
            failsafe={},
            market_context={},
            last_cycle_ok=True,
        )
        stale_health['at_utc'] = '2000-01-01T00:00:00+00:00'
        write_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status', payload=stale_status)
        write_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='health', payload=stale_health)
        write_status_payload(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, payload=stale_status, out_dir=repo / 'runs')
        write_health_payload(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, payload=stale_health, out_dir=repo / 'runs')

        lock_res = acquire_lock(lock_path, owner={'mode': 'smoke'})
        if not lock_res.acquired:
            fail('expected to acquire lock')
        info_before = read_lock_info(lock_path)
        time.sleep(1.05)
        if not refresh_lock(lock_path, owner={'mode': 'smoke_refresh'}):
            fail('expected refresh_lock to succeed')
        info_after = read_lock_info(lock_path)
        if info_after.get('mode') != 'smoke_refresh':
            fail(f'refresh_lock did not persist owner metadata: {info_after}')
        if info_after.get('heartbeat_at_utc') == info_before.get('heartbeat_at_utc') and info_after.get('mtime_utc') == info_before.get('mtime_utc'):
            fail('refresh_lock did not move heartbeat/mtime')
        ok('lock heartbeat refresh ok')

        try:
            report = startup_sanitize_runtime(repo_root=repo, ctx=ctx, mode='smoke', lock_path=lock_path, owner={'mode': 'smoke'})
        finally:
            release_lock(lock_path)
        if len(report.get('stale_artifacts') or []) < 2:
            fail(f'expected stale artifacts to be detected, got {report}')
        guard = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='guard')
        lifecycle = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='lifecycle')
        loop_status = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status')
        if not isinstance(guard, dict) or not isinstance(lifecycle, dict):
            fail('guard/lifecycle artifacts missing')
        if lifecycle.get('event') != 'startup':
            fail(f'unexpected lifecycle payload: {lifecycle}')
        if not isinstance(loop_status, dict) or loop_status.get('state') != 'stale':
            fail(f'loop_status not invalidated: {loop_status}')
        ok('startup sanitize invalidates stale artifacts and writes lifecycle')

        holder = acquire_lock(lock_path, owner={'mode': 'holder'})
        if not holder.acquired:
            fail('failed to acquire holder lock for run_once check')
        try:
            rep = run_once(repo_root=repo)
        finally:
            release_lock(lock_path)
        if not str(rep.get('message') or '').startswith('lock_exists:'):
            fail(f'run_once should fail closed on existing lock, got {rep}')
        ok('run_once lock hardening ok')

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
