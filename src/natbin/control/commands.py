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
from ..runtime.perf import load_json_cached
from ..state.control_repo import (
    RuntimeControlRepository,
    read_control_artifact,
    read_repo_control_artifact,
    write_control_artifact,
)
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
    intelligence_current = intelligence_payload(repo_root=ctx.repo_root, config_path=ctx.config.config_path)
    practice_current = practice_payload(repo_root=ctx.repo_root, config_path=ctx.config.config_path)
    payload['control'] = {
        'plan': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='plan'),
        'quota': current.get('quota_snapshot'),
        'precheck': current,
        'loop_status': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status'),
        'execution': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='execution'),
        'orders': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='orders'),
        'reconcile': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='reconcile'),
        'guard': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='guard'),
        'protection': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='protection'),
        'lifecycle': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='lifecycle'),
        'security': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='security'),
        'intelligence': intelligence_current,
        'practice': practice_current,
        'practice_round': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='practice_round'),
        'retrain': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='retrain'),
        'release': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='release'),
        'doctor': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='doctor'),
        'retention': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='retention'),
        'alerts': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='alerts'),
        'incidents': read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='incidents'),
        'sync': read_repo_control_artifact(repo_root=ctx.repo_root, name='sync'),
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


def sync_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    base_ref: str = 'origin/main',
    write_manifest: bool = False,
    manifest_json_path: str | Path | None = None,
    manifest_md_path: str | Path | None = None,
    freeze_docs: bool = False,
    strict: bool = False,
    use_legacy_repo_sync: bool = False,
) -> dict[str, Any]:
    legacy_requested = bool(use_legacy_repo_sync) or (
        not bool(freeze_docs)
        and not bool(strict)
        and (
            bool(write_manifest)
            or manifest_json_path not in (None, '')
            or manifest_md_path not in (None, '')
            or str(base_ref or 'origin/main') not in {'', 'origin/main'}
        )
    )
    if legacy_requested:
        from ..ops.repo_sync import build_repo_sync_payload

        return build_repo_sync_payload(
            repo_root=repo_root,
            base_ref=str(base_ref or 'origin/main'),
            write_manifest=bool(write_manifest),
            manifest_json_path=manifest_json_path,
            manifest_md_path=manifest_md_path,
        )

    from ..ops.sync_state import build_sync_payload

    payload = build_sync_payload(
        repo_root=repo_root,
        config_path=config_path,
        freeze_docs=freeze_docs,
        strict=strict,
        write_artifact=True,
    )
    payload.setdefault('cli_compat', {})
    payload['cli_compat'].update(
        {
            'lightweight_entrypoint': False,
            'requested_base_ref': str(base_ref or 'origin/main'),
            'freeze_requested': bool(freeze_docs or write_manifest),
        }
    )
    return payload


def security_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    from ..security.audit import audit_security_posture

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    payload = audit_security_posture(
        repo_root=ctx.repo_root,
        config_path=ctx.config.config_path,
        resolved_config=ctx.resolved_config,
        source_trace=list(ctx.source_trace),
    )
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='security', payload=payload)
    return payload


def protection_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    from ..security.account_protection import build_account_protection_payload

    return build_account_protection_payload(
        repo_root=repo_root,
        config_path=config_path,
        write_artifact=True,
    )




def backup_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    from ..ops.production_backup import build_backup_payload

    return build_backup_payload(repo_root=repo_root, config_path=config_path, dry_run=dry_run)


def healthcheck_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    from ..ops.container_health import build_container_health_payload

    return build_container_health_payload(repo_root=repo_root, config_path=config_path)


def monte_carlo_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    initial_capital_brl: float | None = None,
    horizon_days: int | None = None,
    trials: int | None = None,
    rng_seed: int | None = None,
) -> dict[str, Any]:
    from ..monte_carlo.engine import build_monte_carlo_payload

    return build_monte_carlo_payload(
        repo_root=repo_root,
        config_path=config_path,
        initial_capital_brl=initial_capital_brl,
        horizon_days=horizon_days,
        trials=trials,
        rng_seed=rng_seed,
        write_report=True,
    )


def intelligence_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    from ..ops.intelligence_surface import build_intelligence_surface_payload

    return build_intelligence_surface_payload(repo_root=repo_root, config_path=config_path, write_artifact=True)


def intelligence_refresh_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    rebuild_pack: bool = True,
    materialize_portfolio: bool = True,
) -> dict[str, Any]:
    from ..intelligence.refresh import refresh_config_intelligence

    return refresh_config_intelligence(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        rebuild_pack=rebuild_pack,
        materialize_portfolio=materialize_portfolio,
        write_legacy_portfolio=False,
    )


def release_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    from ..ops.release_readiness import build_release_readiness_payload

    return build_release_readiness_payload(repo_root=repo_root, config_path=config_path)


def practice_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    max_stake_amount: float = 5.0,
    soak_stale_after_sec: int | None = None,
) -> dict[str, Any]:
    from ..ops.practice_readiness import build_practice_readiness_payload

    return build_practice_readiness_payload(
        repo_root=repo_root,
        config_path=config_path,
        max_stake_amount=max_stake_amount,
        soak_stale_after_sec=soak_stale_after_sec,
    )





def practice_bootstrap_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    lookback_candles: int = 2000,
    soak_cycles: int = 3,
    force_prepare: bool = False,
    force_soak: bool = False,
    skip_soak: bool = False,
    max_stake_amount: float = 5.0,
    soak_stale_after_sec: int | None = None,
) -> dict[str, Any]:
    from ..ops.practice_bootstrap import build_practice_bootstrap_payload

    return build_practice_bootstrap_payload(
        repo_root=repo_root,
        config_path=config_path,
        lookback_candles=lookback_candles,
        soak_cycles=soak_cycles,
        force_prepare=force_prepare,
        force_soak=force_soak,
        skip_soak=skip_soak,
        max_stake_amount=max_stake_amount,
        soak_stale_after_sec=soak_stale_after_sec,
        write_artifact=True,
    )



def retrain_status_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
) -> dict[str, Any]:
    from ..ops.retrain_ops import build_retrain_status_payload

    return build_retrain_status_payload(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
    )


def retrain_run_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    force: bool = False,
    promote_threshold: float = 0.5,
) -> dict[str, Any]:
    from ..ops.retrain_ops import build_retrain_run_payload

    return build_retrain_run_payload(
        repo_root=repo_root,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        force=force,
        promote_threshold=promote_threshold,
    )


def practice_round_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    soak_cycles: int = 3,
    force_soak: bool = False,
    skip_soak: bool = False,
    max_stake_amount: float = 5.0,
    soak_stale_after_sec: int | None = None,
    force_send_alerts: bool = False,
    incident_limit: int = 20,
    window_hours: int = 24,
) -> dict[str, Any]:
    from ..ops.practice_round import build_practice_round_payload

    return build_practice_round_payload(
        repo_root=repo_root,
        config_path=config_path,
        soak_cycles=soak_cycles,
        force_soak=force_soak,
        skip_soak=skip_soak,
        max_stake_amount=max_stake_amount,
        soak_stale_after_sec=soak_stale_after_sec,
        force_send_alerts=force_send_alerts,
        incident_limit=incident_limit,
        window_hours=window_hours,
        write_artifact=True,
    )

def doctor_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    probe_broker: bool = False,
    relaxed: bool = False,
    market_context_max_age_sec: int | None = None,
    min_dataset_rows: int = 100,
) -> dict[str, Any]:
    from ..ops.production_doctor import build_production_doctor_payload

    return build_production_doctor_payload(
        repo_root=repo_root,
        config_path=config_path,
        probe_broker=probe_broker,
        strict_runtime_artifacts=not relaxed,
        market_context_max_age_sec=market_context_max_age_sec,
        min_dataset_rows=min_dataset_rows,
    )



def retention_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    apply: bool = False,
    days: int | None = None,
    keep_effective_config_snapshots: int = 20,
    list_limit: int = 50,
) -> dict[str, Any]:
    from ..ops.artifact_retention import build_retention_payload

    return build_retention_payload(
        repo_root=repo_root,
        config_path=config_path,
        apply=apply,
        days=days,
        keep_effective_config_snapshots=keep_effective_config_snapshots,
        list_limit=list_limit,
    )


def alerts_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    from ..alerting.telegram import alerts_status_payload

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    payload = alerts_status_payload(repo_root=ctx.repo_root, resolved_config=ctx.resolved_config, limit=limit)
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='alerts', payload=payload)
    return payload


def alerts_test_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    force_send: bool | None = None,
) -> dict[str, Any]:
    from ..alerting.telegram import dispatch_telegram_alert

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    payload = dispatch_telegram_alert(
        repo_root=ctx.repo_root,
        resolved_config=ctx.resolved_config,
        title='Thalor M7 test alert',
        lines=[
            f"scope={ctx.scope.scope_tag}",
            f"repo_root={ctx.repo_root}",
            'alert generated by runtime_app alerts test',
        ],
        severity='info',
        source='runtime_app.alerts_test',
        force_send=force_send,
    )
    summary = alerts_payload(repo_root=ctx.repo_root, config_path=ctx.config.config_path, limit=20)
    summary['last_test_alert'] = payload
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='alerts', payload=summary)
    return summary


def alerts_release_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    force_send: bool | None = None,
) -> dict[str, Any]:
    from ..alerting.telegram import dispatch_telegram_alert

    release = release_payload(repo_root=repo_root, config_path=config_path)
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    checks = list(release.get('checks') or [])
    blocking = [item.get('name') for item in checks if str(item.get('status')) == 'error']
    warning = [item.get('name') for item in checks if str(item.get('status')) == 'warn']
    lines = [
        f"ready_for_live={release.get('ready_for_live')}",
        f"ready_for_practice={release.get('ready_for_practice')}",
        f"ready_for_real={release.get('ready_for_real')}",
        f"severity={release.get('severity')}",
        f"execution_live={release.get('execution_live')}",
    ]
    if blocking:
        lines.append(f"blocking={','.join(str(x) for x in blocking)}")
    if warning:
        lines.append(f"warnings={','.join(str(x) for x in warning[:8])}")
    alert = dispatch_telegram_alert(
        repo_root=ctx.repo_root,
        resolved_config=ctx.resolved_config,
        title='Thalor release readiness',
        lines=lines,
        severity=str(release.get('severity') or 'info'),
        source='runtime_app.alerts_release',
        force_send=force_send,
    )
    payload = alerts_payload(repo_root=ctx.repo_root, config_path=ctx.config.config_path, limit=20)
    payload['release'] = {
        'ready_for_live': release.get('ready_for_live'),
        'ready_for_practice': release.get('ready_for_practice'),
        'ready_for_real': release.get('ready_for_real'),
        'severity': release.get('severity'),
        'execution_live': release.get('execution_live'),
        'execution_account_mode': release.get('execution_account_mode'),
        'blocking_checks': blocking,
        'warning_checks': warning,
    }
    payload['last_release_alert'] = alert
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='alerts', payload=payload)
    return payload


def alerts_flush_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    from ..alerting.telegram import flush_pending_alerts

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    flushed = flush_pending_alerts(repo_root=ctx.repo_root, resolved_config=ctx.resolved_config, limit=limit)
    payload = alerts_payload(repo_root=ctx.repo_root, config_path=ctx.config.config_path, limit=20)
    payload['flush'] = flushed
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='alerts', payload=payload)
    return payload


def incidents_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    limit: int = 20,
    window_hours: int = 24,
) -> dict[str, Any]:
    from ..incidents.reporting import incident_status_payload

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    payload = incident_status_payload(
        repo_root=ctx.repo_root,
        config_path=ctx.config.config_path,
        limit=limit,
        window_hours=window_hours,
        write_artifact=True,
    )
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='incidents', payload=payload)
    return payload


def incidents_report_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    limit: int = 20,
    window_hours: int = 24,
) -> dict[str, Any]:
    from ..incidents.reporting import incident_report_payload

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    payload = incident_report_payload(
        repo_root=ctx.repo_root,
        config_path=ctx.config.config_path,
        limit=limit,
        window_hours=window_hours,
        write_artifact=True,
    )
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='incidents', payload=payload)
    return payload


def incidents_alert_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    limit: int = 20,
    window_hours: int = 24,
    force_send: bool | None = None,
) -> dict[str, Any]:
    from ..incidents.reporting import incident_alert_payload

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    payload = incident_alert_payload(
        repo_root=ctx.repo_root,
        config_path=ctx.config.config_path,
        limit=limit,
        window_hours=window_hours,
        force_send=force_send,
    )
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='incidents', payload=payload)
    return payload


def incidents_drill_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    scenario: str = 'broker_down',
) -> dict[str, Any]:
    from ..incidents.reporting import incident_drill_payload

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    payload = incident_drill_payload(repo_root=ctx.repo_root, config_path=ctx.config.config_path, scenario=scenario)
    write_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='incidents', payload=payload)
    return payload


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
    payload['security'] = read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='security')
    payload['intelligence'] = read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='intelligence') or intelligence_payload(repo_root=ctx.repo_root, config_path=ctx.config.config_path)
    payload['practice'] = read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='practice')
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


def execute_order_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    from ..runtime.execution import process_latest_signal as _process_latest_signal

    return _process_latest_signal(repo_root=repo_root, config_path=config_path)


def check_order_status_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    external_order_id: str,
    refresh: bool = True,
) -> dict[str, Any]:
    from ..runtime.execution import check_order_status_payload as _check_order_status_payload

    return _check_order_status_payload(
        repo_root=repo_root,
        config_path=config_path,
        external_order_id=external_order_id,
        refresh=refresh,
    )


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
            config_path=ctx.config.config_path,
            topk=request.topk,
            lookback_candles=request.lookback_candles,
            stop_on_failure=request.stop_on_failure,
            precheck_market_context=request.precheck_market_context,
        )
        msg = str(payload.get('message') or '')
        code = 0 if bool(payload.get('ok')) else (3 if msg.startswith('lock_exists:') else 2)
        return (code, payload)
    exit_code = run_daemon(
        repo_root=ctx.repo_root,
        config_path=ctx.config.config_path,
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
    from ..portfolio.board import build_asset_board
    from ..portfolio.quota import compute_asset_quotas, compute_portfolio_quota
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
    latest_cycle_source: dict[str, Any] | None = None
    latest_alloc_source: dict[str, Any] | None = None
    try:
        from ..portfolio.latest import load_portfolio_latest_payload, portfolio_profile_key

        runtime_profile = str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default')
        latest_cycle, latest_cycle_source = load_portfolio_latest_payload(
            root,
            name='portfolio_cycle_latest.json',
            config_path=cfg_path,
            profile=runtime_profile,
            allow_legacy_fallback=True,
        )
        latest_alloc, latest_alloc_source = load_portfolio_latest_payload(
            root,
            name='portfolio_allocation_latest.json',
            config_path=cfg_path,
            profile=runtime_profile,
            allow_legacy_fallback=True,
        )
        current_profile_key = portfolio_profile_key(root, config_path=cfg_path, profile=runtime_profile)
    except Exception:
        runtime_profile = str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default')
        current_profile_key = None

    asset_quotas = compute_asset_quotas(root, [s for s in scopes], config_path=cfg_path)
    portfolio_quota = compute_portfolio_quota(root, [s for s in scopes], config_path=cfg_path)
    asset_board = build_asset_board(
        scopes=scopes,
        asset_quotas=asset_quotas,
        portfolio_quota=portfolio_quota,
        latest_cycle=latest_cycle,
        latest_allocation=latest_alloc,
    )

    from ..ops.intelligence_surface import build_portfolio_intelligence_payload

    portfolio_intelligence = build_portfolio_intelligence_payload(repo_root=root, config_path=cfg_path)

    return {
        'phase': 'portfolio_status',
        'ok': True,
        'repo_root': str(root),
        'config_path': str(cfg_path),
        'runtime_profile': runtime_profile,
        'profile_key': current_profile_key,
        'multi_asset': {
            'enabled': bool(getattr(cfg.multi_asset, 'enabled', False)),
            'max_parallel_assets': int(getattr(cfg.multi_asset, 'max_parallel_assets', 1) or 1),
            'stagger_sec': float(getattr(cfg.multi_asset, 'stagger_sec', 0.0) or 0.0),
            'execution_stagger_sec': float(getattr(cfg.multi_asset, 'execution_stagger_sec', 0.0) or 0.0),
            'portfolio_topk_total': int(getattr(cfg.multi_asset, 'portfolio_topk_total', 1) or 1),
            'portfolio_hard_max_positions': int(getattr(cfg.multi_asset, 'portfolio_hard_max_positions', 1) or 1),
            'portfolio_hard_max_trades_per_day': getattr(cfg.multi_asset, 'portfolio_hard_max_trades_per_day', None),
            'portfolio_hard_max_pending_unknown_total': getattr(cfg.multi_asset, 'portfolio_hard_max_pending_unknown_total', None),
            'asset_quota_default_trades_per_day': getattr(cfg.multi_asset, 'asset_quota_default_trades_per_day', None),
            'asset_quota_default_max_open_positions': getattr(cfg.multi_asset, 'asset_quota_default_max_open_positions', None),
            'asset_quota_default_max_pending_unknown': getattr(cfg.multi_asset, 'asset_quota_default_max_pending_unknown', None),
            'portfolio_hard_max_positions_per_asset': getattr(cfg.multi_asset, 'portfolio_hard_max_positions_per_asset', None),
            'portfolio_hard_max_positions_per_cluster': getattr(cfg.multi_asset, 'portfolio_hard_max_positions_per_cluster', None),
            'correlation_filter_enable': bool(getattr(cfg.multi_asset, 'correlation_filter_enable', True)),
            'max_trades_per_cluster_per_cycle': int(getattr(cfg.multi_asset, 'max_trades_per_cluster_per_cycle', 1) or 1),
            'partition_data_paths': bool(getattr(cfg.multi_asset, 'partition_data_paths', True)),
            'data_db_template': str(getattr(cfg.multi_asset, 'data_db_template', '')),
            'dataset_path_template': str(getattr(cfg.multi_asset, 'dataset_path_template', '')),
            'asset_count': len(scoped_paths),
        },
        'scopes': scoped_paths,
        'asset_quotas': [q.as_dict() for q in asset_quotas],
        'portfolio_quota': portfolio_quota.as_dict(),
        'asset_board': asset_board,
        'latest_cycle': latest_cycle,
        'latest_allocation': latest_alloc,
        'latest_sources': {
            'cycle': latest_cycle_source,
            'allocation': latest_alloc_source,
        },
        'intelligence': portfolio_intelligence,
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
            {'name': 'allocate', 'description': 'portfolio_allocator chooses top candidates using shared quota + per-asset quota + exposure/correlation caps'},
            {'name': 'execute', 'description': 'execution layer (Package R): create intent -> place broker order -> reconcile; selected assets are staggered by multi_asset.execution_stagger_sec'},
            {'name': 'persist', 'description': 'writes runs/portfolio_cycle_latest.json + allocation_latest.json with unified asset board inputs'},
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
    outcome, cand = candidate_scope(
        repo_root=root,
        config_path=cfg_path,
        scope=scope,
        data_paths=dp,
        runtime_paths=runtime_paths,
        topk=topk,
        lookback_candles=lookback_candles,
        cfg=cfg,
    )
    materialized_portfolio = None
    try:
        from ..portfolio.materialize import materialize_portfolio_latest_payloads

        materialized_portfolio = materialize_portfolio_latest_payloads(
            repo_root=root,
            config_path=cfg_path,
            scopes=[scope],
            candidates=[cand],
            message='asset_candidate_materialized',
            write_legacy=False,
        )
    except Exception as exc:
        materialized_portfolio = {'ok': False, 'message': 'materialize_failed', 'error': f'{type(exc).__name__}:{exc}'}
    return {
        'phase': 'asset_candidate',
        'ok': int(outcome.returncode) == 0,
        'scope': scope.as_dict(),
        'data_paths': dp.as_dict(),
        'outcome': outcome.as_dict(),
        'candidate': cand.as_dict(),
        'materialized_portfolio': materialized_portfolio,
    }
