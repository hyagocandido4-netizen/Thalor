from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..runtime.failsafe import CircuitBreakerPolicy, RuntimeFailsafe
from ..runtime.health import build_health_payload
from ..runtime.precheck import run_precheck
from ..runtime.quota import OPEN as QUOTA_OPEN, build_quota_snapshot
from ..runtime_perf import load_json_cached
from ..state.control_repo import RuntimeControlRepository, read_control_artifact, write_control_artifact
from .models import ObserveRequest
from .plan import build_context, build_runtime_app_info, build_runtime_plan, to_json_dict


def _market_context_from_ctx(ctx) -> dict[str, Any] | None:
    p = ctx.scoped_paths.get('market_context')
    if not p:
        return None
    obj = load_json_cached(p)
    return obj if isinstance(obj, dict) else None


def _failsafe_from_context(ctx) -> RuntimeFailsafe:
    cfg = dict(ctx.resolved_config or {})
    fs = dict(cfg.get('failsafe') or {})
    repo_root = Path(ctx.repo_root)
    kill_file = Path(fs.get('kill_switch_file') or 'runs/KILL_SWITCH')
    if not kill_file.is_absolute():
        kill_file = repo_root / kill_file
    drain_file = Path(fs.get('drain_mode_file') or 'runs/DRAIN_MODE')
    if not drain_file.is_absolute():
        drain_file = repo_root / drain_file
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


def _evaluate_precheck(
    *,
    ctx,
    topk: int = 3,
    sleep_align_offset_sec: int = 3,
    now_utc: datetime | None = None,
    enforce_market_context: bool = True,
) -> dict[str, Any]:
    try:
        from ..runtime.execution import precheck_reconcile_if_enabled

        precheck_reconcile_if_enabled(repo_root=ctx.repo_root, config_path=ctx.config.config_path)
    except Exception:
        pass
    quota = build_quota_snapshot(
        ctx.repo_root,
        topk=topk,
        sleep_align_offset_sec=sleep_align_offset_sec,
        now_utc=now_utc,
        config_path=ctx.config.config_path,
    )
    market_context = _market_context_from_ctx(ctx)
    control_repo = RuntimeControlRepository(Path(ctx.repo_root) / 'runs' / 'runtime_control.sqlite3')
    failsafe = _failsafe_from_context(ctx)
    decision = run_precheck(
        failsafe,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        control_repo=control_repo,
        market_context=market_context,
        quota_hard_block=quota.kind != QUOTA_OPEN,
        quota_reason=quota.kind if quota.kind != QUOTA_OPEN else None,
        env=dict(os.environ),
        now_utc=now_utc,
        enforce_market_context=bool(enforce_market_context),
    )
    payload = {
        'at_utc': (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec='seconds'),
        'blocked': bool(decision.blocked),
        'reason': decision.reason,
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'timezone': ctx.config.timezone,
            'scope_tag': ctx.scope.scope_tag,
        },
        'quota_snapshot': quota.as_dict(),
        'failsafe_snapshot': decision.snapshot.as_dict() if decision.snapshot else None,
        'breaker_snapshot': decision.breaker.as_dict() if decision.breaker else None,
        'market_context': market_context or {},
        'next_wake_utc': decision.next_wake_utc,
    }
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='quota', payload=quota.as_dict())
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='precheck', payload=payload)
    return payload


def status_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    topk: int = 3,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    info = build_runtime_app_info(config_path=config_path, repo_root=repo_root)
    payload = to_json_dict(info)
    payload['mode'] = 'control_plane'
    payload['repo_root'] = ctx.repo_root
    payload['source_trace'] = list(ctx.source_trace)
    current = _evaluate_precheck(ctx=ctx, topk=topk, sleep_align_offset_sec=3, enforce_market_context=False)
    payload['control'] = {
        'plan': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='plan'),
        'quota': current.get('quota_snapshot'),
        'precheck': current,
        'loop_status': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status'),
        'execution': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='execution'),
        'orders': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='orders'),
        'reconcile': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='reconcile'),
    }
    return payload


def plan_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    topk: int = 3,
    lookback_candles: int = 2000,
) -> dict[str, Any]:
    return build_runtime_plan(
        repo_root=repo_root,
        config_path=config_path,
        topk=topk,
        lookback_candles=lookback_candles,
    ).as_dict()


def quota_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    topk: int = 3,
    sleep_align_offset_sec: int = 3,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    snap = build_quota_snapshot(
        ctx.repo_root,
        topk=topk,
        sleep_align_offset_sec=sleep_align_offset_sec,
        now_utc=now_utc,
        config_path=ctx.config.config_path,
    )
    payload = snap.as_dict()
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='quota', payload=payload)
    return payload


def precheck_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    topk: int = 3,
    sleep_align_offset_sec: int = 3,
    now_utc: datetime | None = None,
    enforce_market_context: bool = True,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    return _evaluate_precheck(
        ctx=ctx,
        topk=topk,
        sleep_align_offset_sec=sleep_align_offset_sec,
        now_utc=now_utc,
        enforce_market_context=enforce_market_context,
    )


def health_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    topk: int = 3,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    previous = read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='health')
    pre = _evaluate_precheck(ctx=ctx, topk=topk, sleep_align_offset_sec=3, enforce_market_context=False)
    blocked = bool(pre.get('blocked'))
    payload = build_health_payload(
        asset=ctx.config.asset,
        interval_sec=ctx.config.interval_sec,
        state='blocked' if blocked else 'healthy',
        message=str(pre.get('reason') or ('precheck_blocked' if blocked else 'healthy')),
        quota=pre.get('quota_snapshot') or {},
        failsafe=pre.get('failsafe_snapshot') or {},
        market_context=pre.get('market_context') or {},
        last_cycle_ok=(previous or {}).get('last_cycle_ok') if isinstance(previous, dict) else None,
    )
    payload['source'] = 'control_plane'
    payload['scope_tag'] = ctx.scope.scope_tag
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='health', payload=payload)
    return payload


def orders_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    from ..runtime.execution import orders_payload as _orders_payload

    return _orders_payload(repo_root=repo_root, config_path=config_path, limit=limit)


def reconcile_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    from ..runtime.execution import reconcile_payload as _reconcile_payload

    return _reconcile_payload(repo_root=repo_root, config_path=config_path)


def observe_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    request: ObserveRequest,
) -> tuple[int, dict[str, Any]]:
    from ..runtime.daemon import run_daemon, run_once

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    build_runtime_plan(
        repo_root=ctx.repo_root,
        config_path=ctx.config.config_path,
        topk=request.topk,
        lookback_candles=request.lookback_candles,
    )
    if request.once:
        payload = run_once(
            repo_root=ctx.repo_root,
            topk=request.topk,
            lookback_candles=request.lookback_candles,
            stop_on_failure=request.stop_on_failure,
            precheck_market_context=request.precheck_market_context,
        )
        return (0 if bool(payload.get('ok')) else 2, payload)
    exit_code = run_daemon(
        repo_root=ctx.repo_root,
        topk=request.topk,
        lookback_candles=request.lookback_candles,
        max_cycles=request.max_cycles,
        sleep_align_offset_sec=request.sleep_align_offset_sec,
        stop_on_failure=request.stop_on_failure,
        quota_aware_sleep=request.quota_aware_sleep,
        precheck_market_context=request.precheck_market_context,
    )
    return exit_code, {
        'phase': 'daemon',
        'ok': exit_code == 0,
        'message': 'daemon_finished',
        'exit_code': exit_code,
        'scope_tag': ctx.scope.scope_tag,
    }


# --- Package O: Portfolio control plane ---


def portfolio_status_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any]:
    from ..portfolio.runner import load_scopes
    from ..portfolio.paths import resolve_scope_data_paths, resolve_scope_runtime_paths
    from ..config.paths import resolve_repo_root, resolve_config_path

    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    scopes, cfg = load_scopes(repo_root=root, config_path=cfg_path)

    scoped_paths: list[dict[str, Any]] = []
    for s in scopes:
        try:
            dp = resolve_scope_data_paths(
                root,
                asset=s.asset,
                interval_sec=s.interval_sec,
                partition_enable=bool(getattr(cfg.multi_asset, 'partition_data_paths', True)) and bool(getattr(cfg.multi_asset, 'enabled', False)),
                db_template=str(getattr(cfg.multi_asset, 'data_db_template', 'data/market_{scope_tag}.sqlite3')),
                dataset_template=str(getattr(cfg.multi_asset, 'dataset_path_template', 'data/datasets/{scope_tag}/dataset.csv')),
                default_db_path=getattr(cfg.data, 'db_path', 'data/market_otc.sqlite3'),
                default_dataset_path=getattr(cfg.data, 'dataset_path', 'data/dataset_phase2.csv'),
            )

            rp = resolve_scope_runtime_paths(
                root,
                scope_tag=str(s.scope_tag),
                partition_enable=bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
            )
            scoped_paths.append({'scope': s.as_dict(), 'data_paths': dp.as_dict(), 'runtime_paths': rp.as_dict()})
        except Exception as exc:
            scoped_paths.append({'scope': s.as_dict(), 'error': f'{type(exc).__name__}:{exc}'})

    latest_cycle = None
    latest_alloc = None
    try:
        from ..portfolio.paths import portfolio_cycle_latest_path, portfolio_allocation_latest_path

        cycle_p = portfolio_cycle_latest_path(root)
        alloc_p = portfolio_allocation_latest_path(root)
        if cycle_p.exists():
            latest_cycle = json.loads(cycle_p.read_text(encoding='utf-8', errors='replace'))
        if alloc_p.exists():
            latest_alloc = json.loads(alloc_p.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass

    return {
        'phase': 'portfolio_status',
        'ok': True,
        'repo_root': str(root),
        'config_path': str(cfg_path),
        'multi_asset': {
            'enabled': bool(getattr(cfg.multi_asset, 'enabled', False)),
            'max_parallel_assets': int(getattr(cfg.multi_asset, 'max_parallel_assets', 1) or 1),
            'stagger_sec': float(getattr(cfg.multi_asset, 'stagger_sec', 0.0) or 0.0),
            'portfolio_topk_total': int(getattr(cfg.multi_asset, 'portfolio_topk_total', 1) or 1),
            'portfolio_hard_max_positions': int(getattr(cfg.multi_asset, 'portfolio_hard_max_positions', 1) or 1),
            'partition_data_paths': bool(getattr(cfg.multi_asset, 'partition_data_paths', True)),
            'data_db_template': str(getattr(cfg.multi_asset, 'data_db_template', '')),
            'dataset_path_template': str(getattr(cfg.multi_asset, 'dataset_path_template', '')),
        },
        'scopes': scoped_paths,
        'latest_cycle': latest_cycle,
        'latest_allocation': latest_alloc,
    }


def portfolio_plan_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any]:
    payload = portfolio_status_payload(repo_root=repo_root, config_path=config_path)
    scopes = payload.get('scopes') or []
    return {
        'phase': 'portfolio_plan',
        'ok': True,
        'repo_root': payload.get('repo_root'),
        'config_path': payload.get('config_path'),
        'steps': [
            {
                'name': 'prepare',
                'description': (
                    'collect_recent + make_dataset + refresh_market_context '
                    '(parallel when multi_asset.enabled + partition_data_paths + max_parallel_assets>1; '
                    'optional stagger via multi_asset.stagger_sec)'
                ),
            },
            {
                'name': 'candidate',
                'description': (
                    'observe_once per scope (observer is execution-disabled). '
                    'Parallel when multi_asset.enabled + partition_data_paths + max_parallel_assets>1; '
                    'signals/state DB partitioned by scope_tag; '
                    'optional stagger via multi_asset.stagger_sec'
                ),
            },
            {'name': 'allocate', 'description': 'portfolio_allocator chooses top candidates using quota + cluster caps'},
            {'name': 'execute', 'description': 'execution layer (Package R): create intent -> place broker order -> reconcile'},
            {'name': 'persist', 'description': 'writes runs/portfolio_cycle_latest.json + allocation_latest.json'},
        ],
        'scopes': scopes,
    }


def portfolio_observe_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, request: ObserveRequest) -> tuple[int, dict[str, Any]]:
    from ..portfolio.runner import run_portfolio_observe

    code, payload = run_portfolio_observe(
        repo_root=repo_root,
        config_path=config_path,
        once=bool(request.once),
        max_cycles=request.max_cycles,
        topk=request.topk,
        lookback_candles=request.lookback_candles,
    )
    return int(code), payload


def asset_prepare_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, asset: str, interval_sec: int, lookback_candles: int = 2000) -> dict[str, Any]:
    from ..config.paths import resolve_repo_root, resolve_config_path
    from ..portfolio.runner import load_scopes, prepare_scope

    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    scopes, cfg = load_scopes(repo_root=root, config_path=cfg_path)
    scope = next((s for s in scopes if s.asset == asset and int(s.interval_sec) == int(interval_sec)), None)
    if scope is None:
        return {'phase': 'asset_prepare', 'ok': False, 'message': f'scope_not_found:{asset}:{interval_sec}'}

    from ..portfolio.runner import _scope_data_paths  # type: ignore

    dp = _scope_data_paths(Path(root), cfg, scope)
    runtime_paths = resolve_scope_runtime_paths(
        Path(root),
        scope_tag=str(scope.scope_tag),
        partition_enable=bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
    )
    outcomes = prepare_scope(repo_root=root, config_path=cfg_path, scope=scope, data_paths=dp, lookback_candles=lookback_candles)
    return {
        'phase': 'asset_prepare',
        'ok': all(int(o.returncode) == 0 for o in outcomes),
        'scope': scope.as_dict(),
        'data_paths': dp.as_dict(),
        'steps': [o.as_dict() for o in outcomes],
    }


def asset_candidate_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, asset: str, interval_sec: int, topk: int = 3, lookback_candles: int = 2000) -> dict[str, Any]:
    from ..config.paths import resolve_repo_root, resolve_config_path
    from ..portfolio.runner import load_scopes, candidate_scope
    from ..portfolio.paths import resolve_scope_runtime_paths

    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    scopes, cfg = load_scopes(repo_root=root, config_path=cfg_path)
    scope = next((s for s in scopes if s.asset == asset and int(s.interval_sec) == int(interval_sec)), None)
    if scope is None:
        return {'phase': 'asset_candidate', 'ok': False, 'message': f'scope_not_found:{asset}:{interval_sec}'}

    from ..portfolio.runner import _scope_data_paths  # type: ignore

    dp = _scope_data_paths(Path(root), cfg, scope)
    runtime_paths = resolve_scope_runtime_paths(
        Path(root),
        scope_tag=str(scope.scope_tag),
        partition_enable=bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
    )
    outcome, cand = candidate_scope(repo_root=root, config_path=cfg_path, scope=scope, data_paths=dp, runtime_paths=runtime_paths, topk=topk, lookback_candles=lookback_candles)
    return {
        'phase': 'asset_candidate',
        'ok': int(outcome.returncode) == 0,
        'scope': scope.as_dict(),
        'data_paths': dp.as_dict(),
        'outcome': outcome.as_dict(),
        'candidate': cand.as_dict(),
    }
