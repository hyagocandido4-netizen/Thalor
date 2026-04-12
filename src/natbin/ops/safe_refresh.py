from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..control.ops import drain_mode_off, drain_mode_on, gate_status
from ..control.plan import build_context
from ..runtime.cycle import classify_outcome_kind, repo_python_executable
from ..runtime.perf import load_json_cached
from ..runtime.scope import market_context_path
from ..runtime.failsafe import CircuitBreakerPolicy, CircuitBreakerSnapshot, RuntimeFailsafe
from ..state.control_repo import RuntimeControlRepository, read_control_artifact


_SKIP_KIND = 'skip'


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _parse_iso(raw: Any) -> datetime | None:
    if raw in (None, ''):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _control_artifact_age_sec(payload: dict[str, Any] | None) -> float | None:
    if not isinstance(payload, dict):
        return None
    stamp = _parse_iso(payload.get('at_utc'))
    if stamp is None:
        return None
    return max(0.0, (_now_utc() - stamp).total_seconds())


def _repo_bootstrap_state(repo: Path) -> tuple[bool, str | None]:
    """Best-effort guard to avoid spawning subprocesses in synthetic/unit-test repos.

    The real Thalor repo always has src/natbin. Unit-test temp repos often only contain
    a config file and intentionally monkeypatched call sites. In that case a subprocess
    call would be noisy, slow, or hang.
    """
    src_pkg = repo / 'src' / 'natbin'
    if not src_pkg.exists():
        return False, 'repo_missing_src_natbin'
    return True, None


def _build_repo_env(repo: Path, config_path: str | Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env['THALOR_REPO_ROOT'] = str(repo)
    if config_path not in (None, ''):
        env['THALOR_CONFIG_PATH'] = str(config_path)
        env['THALOR_CONFIG'] = str(config_path)
    src_dir = str((repo / 'src').resolve())
    existing = str(env.get('PYTHONPATH') or '').strip()
    env['PYTHONPATH'] = src_dir if not existing else src_dir + os.pathsep + existing
    return env


def _failsafe_from_context(ctx, repo: Path) -> RuntimeFailsafe:
    cfg = dict(ctx.resolved_config or {})
    fs = dict(cfg.get('failsafe') or {})
    kill_file = Path(str(fs.get('kill_switch_file') or 'runs/KILL_SWITCH'))
    if not kill_file.is_absolute():
        kill_file = repo / kill_file
    drain_file = Path(str(fs.get('drain_mode_file') or 'runs/DRAIN_MODE'))
    if not drain_file.is_absolute():
        drain_file = repo / drain_file
    policy = CircuitBreakerPolicy(
        failures_to_open=int(fs.get('breaker_failures_to_open') or 3),
        cooldown_minutes=int(fs.get('breaker_cooldown_minutes') or 15),
        half_open_trials=int(fs.get('breaker_half_open_trials') or 1),
    )
    return RuntimeFailsafe(
        kill_switch_file=kill_file,
        kill_switch_env_var=str(fs.get('kill_switch_env_var') or 'THALOR_KILL_SWITCH'),
        drain_mode_file=drain_file,
        drain_mode_env_var=str(fs.get('drain_mode_env_var') or 'THALOR_DRAIN_MODE'),
        global_fail_closed=bool(fs.get('global_fail_closed', True)),
        market_context_fail_closed=bool(fs.get('market_context_fail_closed', True)),
        policy=policy,
    )


def _age_sec(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return max(0.0, (_now_utc() - dt).total_seconds())


def inspect_circuit_breaker(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str,
    interval_sec: int,
    stale_after_sec: int | None = None,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    failsafe = _failsafe_from_context(ctx, repo)
    control_repo = RuntimeControlRepository(repo / 'runs' / 'runtime_control.sqlite3')
    current = _now_utc()
    snap = control_repo.load_breaker(str(ctx.config.asset), int(ctx.config.interval_sec))
    snap = failsafe.evaluate_circuit(snap, current)
    last_failure_age = _age_sec(snap.last_failure_utc)
    opened_until_age = _age_sec(snap.opened_until_utc)
    stale_after = int(stale_after_sec or max(int(failsafe.policy.cooldown_minutes) * 60 * 4, int(ctx.config.interval_sec) * 6, 1800))
    stale_reason = None
    if str(snap.state) == 'half_open' and int(snap.half_open_trials_used) >= int(failsafe.policy.half_open_trials):
        if last_failure_age is not None and last_failure_age >= stale_after:
            stale_reason = 'stale_half_open_exhausted'
    elif str(snap.state) == 'open' and snap.opened_until_utc is not None:
        if current >= snap.opened_until_utc and opened_until_age is not None and opened_until_age >= stale_after:
            stale_reason = 'stale_open_cooldown_elapsed'
    return {
        'asset': str(ctx.config.asset),
        'interval_sec': int(ctx.config.interval_sec),
        'scope_tag': str(ctx.scope.scope_tag),
        'policy': {
            'failures_to_open': int(failsafe.policy.failures_to_open),
            'cooldown_minutes': int(failsafe.policy.cooldown_minutes),
            'half_open_trials': int(failsafe.policy.half_open_trials),
        },
        'snapshot': snap.as_dict(),
        'state': str(snap.state),
        'last_failure_age_sec': None if last_failure_age is None else round(last_failure_age, 3),
        'opened_until_age_sec': None if opened_until_age is None else round(opened_until_age, 3),
        'stale_after_sec': int(stale_after),
        'stale': bool(stale_reason),
        'stale_reason': stale_reason,
    }


def maybe_heal_breaker(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str,
    interval_sec: int,
    enabled: bool,
    dry_run: bool = False,
    stale_after_sec: int | None = None,
) -> dict[str, Any]:
    before = inspect_circuit_breaker(
        repo_root=repo_root,
        config_path=config_path,
        asset=str(asset),
        interval_sec=int(interval_sec),
        stale_after_sec=stale_after_sec,
    )
    payload: dict[str, Any] = {
        'name': 'circuit_breaker',
        'safe': True,
        'potentially_submits': False,
        'enabled': bool(enabled),
        'dry_run': bool(dry_run),
        'before': before,
        'attempted': False,
        'status': 'skip',
        'message': 'repair_disabled' if not enabled else ('circuit_breaker_closed' if str(before.get('state')) == 'closed' else 'circuit_breaker_not_stale'),
    }
    if not enabled:
        return payload
    if str(before.get('state')) == 'closed':
        return payload
    if not bool(before.get('stale')):
        return payload
    if dry_run:
        payload.update({'status': 'planned', 'message': 'would_reset_stale_circuit_breaker'})
        return payload

    ctx = build_context(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    control_repo = RuntimeControlRepository(repo / 'runs' / 'runtime_control.sqlite3')
    previous = control_repo.load_breaker(str(ctx.config.asset), int(ctx.config.interval_sec))
    payload['previous_snapshot'] = previous.as_dict()
    try:
        control_repo.save_breaker(CircuitBreakerSnapshot(asset=str(ctx.config.asset), interval_sec=int(ctx.config.interval_sec)))
    except Exception as exc:
        payload.update({'attempted': True, 'status': 'error', 'message': 'circuit_breaker_reset_failed', 'error': f'{type(exc).__name__}: {exc}'})
        return payload

    after = inspect_circuit_breaker(
        repo_root=repo,
        config_path=ctx.config.config_path,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        stale_after_sec=stale_after_sec,
    )
    payload.update({'attempted': True, 'after': after})
    if str(after.get('state')) == 'closed':
        payload.update({'status': 'ok', 'message': str(before.get('stale_reason') or 'circuit_breaker_reset')})
    else:
        payload.update({'status': 'error', 'message': 'circuit_breaker_reset_incomplete'})
    return payload


def _returncode_ok(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    value = payload.get('returncode')
    try:
        return int(value) == 0
    except Exception:
        return False

def _run_subprocess_step(
    *,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_sec: int,
    capture_limit: int = 1200,
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_sec)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            'kind': classify_outcome_kind(returncode=124, timed_out=True, interrupted=False),
            'returncode': 124,
            'command': list(cmd),
            'cwd': str(cwd),
            'stdout_tail': str(getattr(exc, 'stdout', '') or '')[-capture_limit:],
            'stderr_tail': str(getattr(exc, 'stderr', '') or '')[-capture_limit:],
            'timed_out': True,
        }
    stdout = str(proc.stdout or '')
    stderr = str(proc.stderr or '')
    return {
        'kind': classify_outcome_kind(returncode=proc.returncode, timed_out=False, interrupted=False),
        'returncode': int(proc.returncode),
        'command': list(cmd),
        'cwd': str(cwd),
        'stdout_tail': stdout[-capture_limit:],
        'stderr_tail': stderr[-capture_limit:],
    }

def inspect_market_context_artifact(
    *,
    repo_root: str | Path = '.',
    asset: str,
    interval_sec: int,
    max_age_sec: int,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    path = market_context_path(asset=str(asset), interval_sec=int(interval_sec), out_dir=repo / 'runs')
    payload = load_json_cached(str(path)) if path.exists() else None
    stamp = _parse_iso((payload or {}).get('at_utc')) if isinstance(payload, dict) else None
    if stamp is None and path.exists():
        try:
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except Exception:
            stamp = None
    age_sec = None if stamp is None else max(0.0, (_now_utc() - stamp).total_seconds())
    stale = bool(not path.exists() or not isinstance(payload, dict) or age_sec is None or age_sec > max(1, int(max_age_sec)))
    return {
        'path': str(path),
        'exists': bool(path.exists()),
        'max_age_sec': int(max_age_sec),
        'age_sec': None if age_sec is None else round(age_sec, 3),
        'stale': stale,
        'at_utc': None if stamp is None else stamp.isoformat(timespec='seconds'),
        'payload': payload,
    }


def inspect_control_freshness_artifacts(
    *,
    repo_root: str | Path = '.',
    asset: str,
    interval_sec: int,
    freshness_limit_sec: int,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    loop_status = read_control_artifact(repo_root=repo, asset=asset, interval_sec=int(interval_sec), name='loop_status')
    health = read_control_artifact(repo_root=repo, asset=asset, interval_sec=int(interval_sec), name='health')
    loop_age = _control_artifact_age_sec(loop_status)
    health_age = _control_artifact_age_sec(health)

    def _entry(name: str, payload: dict[str, Any] | None, age_sec: float | None) -> dict[str, Any]:
        return {
            'name': name,
            'exists': isinstance(payload, dict),
            'at_utc': payload.get('at_utc') if isinstance(payload, dict) else None,
            'age_sec': None if age_sec is None else round(age_sec, 3),
            'stale': bool(age_sec is None or age_sec > max(1, int(freshness_limit_sec))),
            'payload': payload,
        }

    loop_entry = _entry('loop_status', loop_status, loop_age)
    health_entry = _entry('health', health, health_age)
    stale = bool(loop_entry['stale'] or health_entry['stale'])
    return {
        'freshness_limit_sec': int(freshness_limit_sec),
        'loop_status': loop_entry,
        'health': health_entry,
        'stale': stale,
    }


def refresh_market_context_safe(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str,
    interval_sec: int,
    timeout_sec: int = 90,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    bootstrap_ok, bootstrap_reason = _repo_bootstrap_state(repo)
    if not bootstrap_ok:
        return {
            'kind': _SKIP_KIND,
            'returncode': None,
            'command': None,
            'cwd': str(repo),
            'stdout_tail': '',
            'stderr_tail': '',
            'skipped_reason': bootstrap_reason,
        }
    python_exe = repo_python_executable(repo)
    env = _build_repo_env(repo, config_path)
    env['ASSET'] = str(asset)
    env['INTERVAL_SEC'] = str(int(interval_sec))
    cmd = [python_exe, '-m', 'natbin.refresh_market_context']
    primary = _run_subprocess_step(
        cmd=cmd,
        cwd=repo,
        env=env,
        timeout_sec=int(timeout_sec),
        capture_limit=1200,
    )
    primary['strategy'] = 'primary'
    primary['local_only'] = False
    if _returncode_ok(primary) and not bool(primary.get('timed_out')):
        return primary

    fallback_env = dict(env)
    fallback_env['THALOR_FORCE_IQOPTIONAPI_MISSING'] = '1'
    fallback = _run_subprocess_step(
        cmd=cmd,
        cwd=repo,
        env=fallback_env,
        timeout_sec=min(max(15, int(timeout_sec) // 2), 45),
        capture_limit=1200,
    )
    fallback['strategy'] = 'local_only_fallback'
    fallback['local_only'] = True

    if _returncode_ok(fallback) and not bool(fallback.get('timed_out')):
        return {
            **fallback,
            'fallback_used': True,
            'primary': primary,
        }

    primary['fallback_used'] = False
    primary['fallback'] = fallback
    return primary


def _observe_once_safe(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    timeout_sec: int = 240,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    bootstrap_ok, bootstrap_reason = _repo_bootstrap_state(repo)
    if not bootstrap_ok:
        return {
            'kind': _SKIP_KIND,
            'returncode': None,
            'command': None,
            'cwd': str(repo),
            'stdout_tail': '',
            'stderr_tail': '',
            'skipped_reason': bootstrap_reason,
        }
    python_exe = repo_python_executable(repo)
    env = _build_repo_env(repo, config_path)
    cmd = [python_exe, '-m', 'natbin.runtime_app', '--repo-root', str(repo)]
    if config_path not in (None, ''):
        cmd.extend(['--config', str(config_path)])
    cmd.extend(['observe', '--once', '--json'])
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_sec)),
            check=False,
        )
        stdout = str(proc.stdout or '')
        stderr = str(proc.stderr or '')
        return {
            'kind': classify_outcome_kind(returncode=proc.returncode, timed_out=False, interrupted=False),
            'returncode': int(proc.returncode),
            'command': cmd,
            'cwd': str(repo),
            'stdout_tail': stdout[-2400:],
            'stderr_tail': stderr[-2400:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            'kind': classify_outcome_kind(returncode=124, timed_out=True, interrupted=False),
            'returncode': 124,
            'command': cmd,
            'cwd': str(repo),
            'stdout_tail': str(getattr(exc, 'stdout', '') or '')[-2400:],
            'stderr_tail': str(getattr(exc, 'stderr', '') or '')[-2400:],
            'timed_out': True,
        }


def refresh_control_freshness_safe(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str,
    interval_sec: int,
    freshness_limit_sec: int,
    timeout_sec: int = 240,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    bootstrap_ok, bootstrap_reason = _repo_bootstrap_state(repo)
    if not bootstrap_ok:
        return {
            'kind': _SKIP_KIND,
            'returncode': None,
            'command': None,
            'cwd': str(repo),
            'stdout_tail': '',
            'stderr_tail': '',
            'skipped_reason': bootstrap_reason,
            'after': inspect_control_freshness_artifacts(
                repo_root=repo,
                asset=str(asset),
                interval_sec=int(interval_sec),
                freshness_limit_sec=int(freshness_limit_sec),
            ),
        }
    gates_before = gate_status(repo_root=repo, config_path=config_path, asset=asset, interval_sec=int(interval_sec))
    drain_before_active = bool((gates_before.get('drain_mode') or {}).get('active'))
    drain_armed = False
    drain_transition = None
    if not drain_before_active:
        drain_transition = drain_mode_on(
            repo_root=repo,
            config_path=config_path,
            asset=asset,
            interval_sec=int(interval_sec),
            reason='safe_refresh_control_freshness',
        )
        drain_armed = True
    try:
        step = _observe_once_safe(repo_root=repo, config_path=config_path, timeout_sec=int(timeout_sec))
    finally:
        if drain_armed:
            drain_mode_off(
                repo_root=repo,
                config_path=config_path,
                asset=asset,
                interval_sec=int(interval_sec),
                reason='safe_refresh_control_freshness_restore',
            )
    gates_after = gate_status(repo_root=repo, config_path=config_path, asset=asset, interval_sec=int(interval_sec))
    after = inspect_control_freshness_artifacts(
        repo_root=repo,
        asset=asset,
        interval_sec=int(interval_sec),
        freshness_limit_sec=int(freshness_limit_sec),
    )
    step['drain'] = {
        'before_active': drain_before_active,
        'armed_temporarily': drain_armed,
        'after_active': bool((gates_after.get('drain_mode') or {}).get('active')),
        'transition': drain_transition,
    }
    return {
        **step,
        'gates_before': gates_before,
        'gates_after': gates_after,
        'after': after,
    }


def maybe_heal_market_context(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str,
    interval_sec: int,
    max_age_sec: int,
    enabled: bool,
    dry_run: bool = False,
    timeout_sec: int = 90,
) -> dict[str, Any]:
    before = inspect_market_context_artifact(
        repo_root=repo_root,
        asset=str(asset),
        interval_sec=int(interval_sec),
        max_age_sec=int(max_age_sec),
    )
    payload: dict[str, Any] = {
        'name': 'market_context',
        'safe': True,
        'potentially_submits': False,
        'enabled': bool(enabled),
        'dry_run': bool(dry_run),
        'before': before,
        'attempted': False,
        'status': 'skip',
        'message': 'repair_disabled' if not enabled else 'market_context_fresh',
    }
    if not enabled:
        return payload
    if not bool(before.get('stale')):
        return payload
    if dry_run:
        payload.update({'status': 'planned', 'message': 'would_refresh_market_context'})
        return payload

    step = refresh_market_context_safe(
        repo_root=repo_root,
        config_path=config_path,
        asset=str(asset),
        interval_sec=int(interval_sec),
        timeout_sec=int(timeout_sec),
    )
    after = inspect_market_context_artifact(
        repo_root=repo_root,
        asset=str(asset),
        interval_sec=int(interval_sec),
        max_age_sec=int(max_age_sec),
    )
    payload.update(
        {
            'attempted': str(step.get('kind')) != _SKIP_KIND,
            'step': step,
            'after': after,
        }
    )
    if str(step.get('kind')) == _SKIP_KIND:
        payload.update({'status': 'skip', 'message': str(step.get('skipped_reason') or 'market_context_refresh_unavailable')})
    elif _returncode_ok(step) and not bool(after.get('stale')):
        if bool(step.get('local_only')) or bool(step.get('fallback_used')):
            payload.update({'status': 'ok', 'message': 'market_context_refreshed_local_fallback'})
        else:
            payload.update({'status': 'ok', 'message': 'market_context_refreshed'})
    elif bool(step.get('timed_out')):
        payload.update({'status': 'error', 'message': 'market_context_refresh_timed_out'})
    elif _returncode_ok(step):
        payload.update({'status': 'warn', 'message': 'market_context_refresh_completed_but_still_stale'})
    elif bool((step.get('fallback') or {}).get('timed_out')):
        payload.update({'status': 'error', 'message': 'market_context_refresh_timed_out'})
    else:
        payload.update({'status': 'error', 'message': 'market_context_refresh_failed'})
    return payload


def maybe_heal_control_freshness(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str,
    interval_sec: int,
    freshness_limit_sec: int,
    enabled: bool,
    dry_run: bool = False,
    timeout_sec: int = 240,
) -> dict[str, Any]:
    before = inspect_control_freshness_artifacts(
        repo_root=repo_root,
        asset=str(asset),
        interval_sec=int(interval_sec),
        freshness_limit_sec=int(freshness_limit_sec),
    )
    payload: dict[str, Any] = {
        'name': 'control_freshness',
        'safe': True,
        'potentially_submits': False,
        'enabled': bool(enabled),
        'dry_run': bool(dry_run),
        'before': before,
        'attempted': False,
        'status': 'skip',
        'message': 'repair_disabled' if not enabled else 'control_freshness_fresh',
        'guard': {
            'drain_mode_enforced': True,
        },
    }
    if not enabled:
        return payload
    if not bool(before.get('stale')):
        return payload
    if dry_run:
        payload.update({'status': 'planned', 'message': 'would_refresh_control_freshness'})
        return payload

    step = refresh_control_freshness_safe(
        repo_root=repo_root,
        config_path=config_path,
        asset=str(asset),
        interval_sec=int(interval_sec),
        freshness_limit_sec=int(freshness_limit_sec),
        timeout_sec=int(timeout_sec),
    )
    after = dict(step.get('after') or {})
    payload.update(
        {
            'attempted': str(step.get('kind')) != _SKIP_KIND,
            'step': step,
            'after': after,
        }
    )
    if str(step.get('kind')) == _SKIP_KIND:
        payload.update({'status': 'skip', 'message': str(step.get('skipped_reason') or 'control_freshness_refresh_unavailable')})
    elif bool(step.get('timed_out')):
        payload.update({'status': 'error', 'message': 'control_freshness_refresh_timed_out'})
    elif not bool(after.get('stale')):
        payload.update({'status': 'ok', 'message': 'control_freshness_refreshed'})
    elif _returncode_ok(step):
        payload.update({'status': 'warn', 'message': 'control_freshness_refresh_completed_but_artifacts_remain_stale'})
    else:
        payload.update({'status': 'error', 'message': 'control_freshness_refresh_failed'})
    return payload


__all__ = [
    'inspect_circuit_breaker',
    'maybe_heal_breaker',
    'inspect_market_context_artifact',
    'refresh_market_context_safe',
    'maybe_heal_market_context',
    'inspect_control_freshness_artifacts',
    'refresh_control_freshness_safe',
    'maybe_heal_control_freshness',
]
