from __future__ import annotations

"""Control-plane operations (Package P).

This module is used by ``runtime_app ops ...`` to manage global runtime gates.
The gates are file-backed so they work across processes and schedulers.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runtime.failsafe import CircuitBreakerPolicy, CircuitBreakerSnapshot, RuntimeFailsafe
from ..state.control_repo import RuntimeControlRepository
from .plan import build_context


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _write_gate_file(path: Path, *, reason: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        'enabled': True,
        'at_utc': _utc_now_iso(),
        'reason': str(reason or '').strip() or None,
    }
    try:
        path.write_text(f"{body}\n", encoding='utf-8')
    except Exception:
        # last resort: touch
        try:
            path.touch(exist_ok=True)
        except Exception:
            pass


def _remove_gate_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def _failsafe_from_context(ctx) -> RuntimeFailsafe:
    fs = ctx.resolved_config.get('failsafe') if isinstance(ctx.resolved_config, dict) else None
    if hasattr(fs, 'model_dump'):
        fs = fs.model_dump(mode='python')
    fs = dict(fs or {})

    ks_file = Path(str(fs.get('kill_switch_file') or 'runs/KILL_SWITCH'))
    dr_file = Path(str(fs.get('drain_mode_file') or 'runs/DRAIN_MODE'))
    if not ks_file.is_absolute():
        ks_file = Path(ctx.repo_root) / ks_file
    if not dr_file.is_absolute():
        dr_file = Path(ctx.repo_root) / dr_file

    policy = CircuitBreakerPolicy(
        failures_to_open=int(fs.get('breaker_failures_to_open') or 3),
        cooldown_minutes=int(fs.get('breaker_cooldown_minutes') or 15),
        half_open_trials=int(fs.get('breaker_half_open_trials') or 1),
    )
    return RuntimeFailsafe(
        kill_switch_file=ks_file,
        kill_switch_env_var=str(fs.get('kill_switch_env_var') or 'THALOR_KILL_SWITCH'),
        drain_mode_file=dr_file,
        drain_mode_env_var=str(fs.get('drain_mode_env_var') or 'THALOR_DRAIN_MODE'),
        global_fail_closed=bool(fs.get('global_fail_closed', True)),
        market_context_fail_closed=bool(fs.get('market_context_fail_closed', True)),
        policy=policy,
    )


def breaker_status(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    failsafe = _failsafe_from_context(ctx)
    control_repo = RuntimeControlRepository(repo / 'runs' / 'runtime_control.sqlite3')
    breaker = control_repo.load_breaker(str(ctx.config.asset), int(ctx.config.interval_sec))
    breaker = failsafe.evaluate_circuit(breaker, datetime.now(tz=UTC))
    try:
        control_repo.save_breaker(breaker)
    except Exception:
        pass
    return {
        'at_utc': _utc_now_iso(),
        'repo_root': str(repo),
        'scope': {
            'asset': str(ctx.config.asset),
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': str(ctx.scope.scope_tag),
        },
        'policy': {
            'failures_to_open': int(failsafe.policy.failures_to_open),
            'cooldown_minutes': int(failsafe.policy.cooldown_minutes),
            'half_open_trials': int(failsafe.policy.half_open_trials),
        },
        'breaker': breaker.as_dict(),
        'gates': gate_status(repo_root=repo, config_path=ctx.config.config_path, asset=str(ctx.config.asset), interval_sec=int(ctx.config.interval_sec)),
    }


def breaker_reset(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    control_repo = RuntimeControlRepository(repo / 'runs' / 'runtime_control.sqlite3')
    previous = control_repo.load_breaker(str(ctx.config.asset), int(ctx.config.interval_sec))
    reset_snapshot = CircuitBreakerSnapshot(asset=str(ctx.config.asset), interval_sec=int(ctx.config.interval_sec))
    control_repo.save_breaker(reset_snapshot)
    payload = breaker_status(repo_root=repo, config_path=ctx.config.config_path, asset=str(ctx.config.asset), interval_sec=int(ctx.config.interval_sec))
    payload['previous_breaker'] = previous.as_dict()
    payload['breaker']['changed'] = True
    if reason not in (None, ''):
        payload['breaker']['reset_reason'] = str(reason)
    return payload


def gate_status(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec, dump_snapshot=False)
    fs = ctx.resolved_config.get('failsafe') if isinstance(ctx.resolved_config, dict) else None
    if hasattr(fs, 'model_dump'):
        fs = fs.model_dump(mode='python')
    fs = dict(fs or {})

    ks_file = Path(str(fs.get('kill_switch_file') or 'runs/KILL_SWITCH'))
    dr_file = Path(str(fs.get('drain_mode_file') or 'runs/DRAIN_MODE'))
    if not ks_file.is_absolute():
        ks_file = Path(ctx.repo_root) / ks_file
    if not dr_file.is_absolute():
        dr_file = Path(ctx.repo_root) / dr_file

    return {
        'at_utc': _utc_now_iso(),
        'repo_root': str(ctx.repo_root),
        'kill_switch': {
            'active': bool(ks_file.exists()),
            'path': str(ks_file),
            'env_var': str(fs.get('kill_switch_env_var') or 'THALOR_KILL_SWITCH'),
        },
        'drain_mode': {
            'active': bool(dr_file.exists()),
            'path': str(dr_file),
            'env_var': str(fs.get('drain_mode_env_var') or 'THALOR_DRAIN_MODE'),
        },
    }


def kill_switch_on(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    status = gate_status(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec)
    path = Path(status['kill_switch']['path'])
    _write_gate_file(path, reason=reason)
    status['kill_switch']['active'] = True
    status['kill_switch']['changed'] = True
    if reason is not None:
        status['kill_switch']['reason'] = str(reason)
    return status


def kill_switch_off(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    status = gate_status(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec)
    path = Path(status['kill_switch']['path'])
    _remove_gate_file(path)
    status['kill_switch']['active'] = False
    status['kill_switch']['changed'] = True
    if reason is not None:
        status['kill_switch']['reason'] = str(reason)
    return status


def drain_mode_on(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    status = gate_status(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec)
    path = Path(status['drain_mode']['path'])
    _write_gate_file(path, reason=reason)
    status['drain_mode']['active'] = True
    status['drain_mode']['changed'] = True
    if reason is not None:
        status['drain_mode']['reason'] = str(reason)
    return status


def drain_mode_off(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    status = gate_status(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec)
    path = Path(status['drain_mode']['path'])
    _remove_gate_file(path)
    status['drain_mode']['active'] = False
    status['drain_mode']['changed'] = True
    if reason is not None:
        status['drain_mode']['reason'] = str(reason)
    return status
