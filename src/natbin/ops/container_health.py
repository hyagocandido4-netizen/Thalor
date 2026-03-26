from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config.loader import load_thalor_config
from ..control.plan import build_context
from ..runtime.perf import load_json_cached
from ..runtime.scope import build_scope
from ..state.control_repo import write_repo_control_artifact


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _check(name: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    payload = {'name': name, 'status': status, 'message': message}
    payload.update(extra)
    return payload


def build_container_health_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    ctx = build_context(repo_root=repo, config_path=config_path)
    full_cfg = load_thalor_config(config_path=ctx.config.config_path, repo_root=repo)
    now_utc = _utc_now()
    production = dict((ctx.resolved_config or {}).get('production') or {})
    health_cfg = dict(production.get('healthcheck') or {})
    failsafe_cfg = dict((ctx.resolved_config or {}).get('failsafe') or {})

    checks: list[dict[str, Any]] = []
    checks.append(_check('repo_root', 'ok', 'Repository root resolved', path=str(repo)))
    checks.append(_check('config', 'ok', 'Config loaded', path=str(ctx.config.config_path), profile=str((ctx.resolved_config or {}).get('profile') or full_cfg.runtime.profile)))

    kill_switch = Path(failsafe_cfg.get('kill_switch_file') or 'runs/KILL_SWITCH')
    if not kill_switch.is_absolute():
        kill_switch = repo / kill_switch
    if bool(health_cfg.get('check_kill_switch', True)) and kill_switch.exists():
        checks.append(_check('kill_switch', 'error', 'Kill switch active', path=str(kill_switch)))
    else:
        checks.append(_check('kill_switch', 'ok', 'Kill switch inactive', path=str(kill_switch)))

    drain_mode = Path(failsafe_cfg.get('drain_mode_file') or 'runs/DRAIN_MODE')
    if not drain_mode.is_absolute():
        drain_mode = repo / drain_mode
    if bool(health_cfg.get('check_drain_mode', False)) and drain_mode.exists():
        checks.append(_check('drain_mode', 'warn', 'Drain mode active', path=str(drain_mode)))
    else:
        checks.append(_check('drain_mode', 'ok', 'Drain mode inactive', path=str(drain_mode)))

    execution_repo = repo / 'runs' / 'runtime_execution.sqlite3'
    if bool(health_cfg.get('require_execution_repo', False)) and not execution_repo.exists():
        checks.append(_check('execution_repo', 'error', 'Execution repository missing', path=str(execution_repo)))
    else:
        checks.append(_check('execution_repo', 'ok', 'Execution repository check passed', path=str(execution_repo), exists=execution_repo.exists()))

    if bool(health_cfg.get('require_loop_status', False)):
        freshness_limit = max(1, int(health_cfg.get('max_loop_status_age_sec') or 1800))
        enabled_assets = [asset for asset in list(full_cfg.assets) if bool(asset.enabled)]
        sample_limit = max(1, int(health_cfg.get('scope_sample_limit') or 6))
        for asset_cfg in enabled_assets[:sample_limit]:
            scope = build_scope(str(asset_cfg.asset), int(asset_cfg.interval_sec))
            path = repo / 'runs' / 'control' / scope.scope_tag / 'loop_status.json'
            payload = load_json_cached(path) if path.exists() else None
            stamp = _parse_iso(payload.get('at_utc') if isinstance(payload, dict) else None)
            if not path.exists() or not isinstance(payload, dict):
                checks.append(_check('loop_status', 'error', 'Loop status missing', scope_tag=scope.scope_tag, path=str(path)))
                continue
            if stamp is None:
                checks.append(_check('loop_status', 'error', 'Loop status timestamp missing', scope_tag=scope.scope_tag, path=str(path)))
                continue
            age_sec = max(0.0, (now_utc - stamp).total_seconds())
            if age_sec > freshness_limit:
                checks.append(_check('loop_status', 'error', 'Loop status stale', scope_tag=scope.scope_tag, path=str(path), age_sec=round(age_sec, 3), freshness_limit_sec=freshness_limit))
            else:
                checks.append(_check('loop_status', 'ok', 'Loop status fresh', scope_tag=scope.scope_tag, path=str(path), age_sec=round(age_sec, 3), freshness_limit_sec=freshness_limit))
    else:
        checks.append(_check('loop_status', 'ok', 'Loop status freshness not required by config'))

    severity = 'ok'
    if any(str(item.get('status')) == 'error' for item in checks):
        severity = 'error'
    elif any(str(item.get('status')) == 'warn' for item in checks):
        severity = 'warn'

    payload = {
        'ok': severity != 'error',
        'kind': 'container_health',
        'generated_at_utc': now_utc.isoformat(timespec='seconds'),
        'repo_root': str(repo),
        'config_path': str(ctx.config.config_path),
        'profile': str((ctx.resolved_config or {}).get('profile') or full_cfg.runtime.profile),
        'severity': severity,
        'checks': checks,
    }
    write_repo_control_artifact(repo_root=repo, name='healthcheck', payload=payload)
    return payload
