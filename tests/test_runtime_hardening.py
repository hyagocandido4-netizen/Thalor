from __future__ import annotations

import shutil
import time
from pathlib import Path

from natbin.control.plan import build_context
from natbin.ops.lockfile import acquire_lock, read_lock_info, refresh_lock, release_lock
from natbin.runtime.daemon import run_once
from natbin.runtime.health import build_health_payload, build_status_payload, write_health_payload, write_status_payload
from natbin.runtime.hardening import startup_sanitize_runtime
from natbin.runtime.scope import daemon_lock_path, repo_scope
from natbin.state.control_repo import control_artifact_paths, read_control_artifact, write_control_artifact


BASE_CONFIG = Path(__file__).resolve().parents[1] / 'config' / 'base.yaml'


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / 'repo'
    (root / 'config').mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE_CONFIG, root / 'config' / 'base.yaml')
    return root


def test_lock_refresh_updates_heartbeat_and_owner(tmp_path: Path):
    lock_path = tmp_path / 'daemon.lock'
    res = acquire_lock(lock_path, owner={'mode': 'test_once'})
    assert res.acquired is True
    info_before = read_lock_info(lock_path)
    time.sleep(1.05)
    assert refresh_lock(lock_path, owner={'mode': 'test_daemon', 'note': 'heartbeat'}) is True
    info_after = read_lock_info(lock_path)
    assert info_after['exists'] is True
    assert info_after.get('mode') == 'test_daemon'
    assert info_after.get('note') == 'heartbeat'
    assert info_after.get('mtime_utc') != info_before.get('mtime_utc') or info_after.get('heartbeat_at_utc') != info_before.get('heartbeat_at_utc')
    release_lock(lock_path)


def test_startup_sanitize_invalidates_stale_status_and_health(tmp_path: Path):
    repo_root = _make_repo(tmp_path)
    ctx = build_context(repo_root=repo_root)

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

    write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status', payload=stale_status)
    write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='health', payload=stale_health)
    write_status_payload(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, payload=stale_status, out_dir=repo_root / 'runs')
    write_health_payload(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, payload=stale_health, out_dir=repo_root / 'runs')

    scope = repo_scope(config_path=repo_root / 'config' / 'base.yaml', repo_root=repo_root)
    lock_path = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=repo_root / 'runs')
    acquire_lock(lock_path, owner={'mode': 'test_startup'})
    try:
        report = startup_sanitize_runtime(
            repo_root=repo_root,
            ctx=ctx,
            mode='test_startup',
            lock_path=lock_path,
            owner={'mode': 'test_startup'},
        )
    finally:
        release_lock(lock_path)

    loop_payload = read_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status')
    health_payload = read_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='health')
    guard_payload = read_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='guard')
    lifecycle_payload = read_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='lifecycle')

    assert isinstance(report, dict)
    assert isinstance(loop_payload, dict) and loop_payload.get('state') == 'stale'
    assert isinstance(health_payload, dict) and health_payload.get('state') == 'stale'
    assert isinstance(guard_payload, dict)
    assert len(guard_payload.get('stale_artifacts') or []) >= 2
    assert any((a or {}).get('artifact') == 'loop_status' for a in (guard_payload.get('actions') or []))
    assert isinstance(lifecycle_payload, dict) and lifecycle_payload.get('event') == 'startup'



def test_control_artifact_paths_include_guard_and_lifecycle(tmp_path: Path):
    paths = control_artifact_paths(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300)
    assert 'guard' in paths
    assert 'lifecycle' in paths

def test_run_once_returns_lock_exists_when_scope_is_already_locked(tmp_path: Path):
    repo_root = _make_repo(tmp_path)
    scope = repo_scope(config_path=repo_root / 'config' / 'base.yaml', repo_root=repo_root)
    lock_path = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=repo_root / 'runs')
    acquire_lock(lock_path, owner={'mode': 'test_lock_holder'})
    try:
        payload = run_once(repo_root=repo_root)
    finally:
        release_lock(lock_path)
    assert payload.get('phase') == 'startup'
    assert payload.get('state') == 'blocked'
    assert str(payload.get('message') or '').startswith('lock_exists:')
