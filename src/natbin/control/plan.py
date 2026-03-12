from __future__ import annotations

import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config.effective_dump import write_effective_config_latest, write_effective_config_snapshot
from ..config.loader import load_resolved_config
from ..security.redaction import collect_sensitive_values, sanitize_payload
from ..config.paths import resolve_config_path, resolve_repo_root
from ..runtime.health import build_health_payload
from ..runtime.scope import (
    build_scope,
    decision_latest_path,
    effective_env_path,
    health_snapshot_path,
    incident_jsonl_path,
    live_signals_csv_path,
    loop_status_path,
    market_context_path,
)
from ..state.control_repo import control_artifact_paths, read_control_artifact, write_control_artifact
from .models import RuntimeAppConfig, RuntimeAppCapabilities, RuntimeAppInfo, RuntimeContext, RuntimePlan, RuntimeScopeInfo


DEFAULT_CONFIG_PATH: Path | None = None


def _sanitize_scope_part(value: str) -> str:
    out = []
    for ch in value:
        out.append(ch if (ch.isalnum() or ch in '-_') else '_')
    return ''.join(out)


def _local_day(timezone: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(str(timezone or 'UTC'))
    except Exception:
        tz = UTC
    return datetime.now(tz).date().isoformat()


def load_runtime_app_config(
    config_path: str | Path | None = DEFAULT_CONFIG_PATH,
    *,
    repo_root: str | Path = '.',
    asset: str | None = None,
    interval_sec: int | None = None,
) -> RuntimeAppConfig:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    path = resolve_config_path(repo_root=root, config_path=config_path)
    rcfg = load_resolved_config(config_path=path, repo_root=root, asset=asset, interval_sec=interval_sec)
    return RuntimeAppConfig(
        asset=str(rcfg.asset),
        interval_sec=int(rcfg.interval_sec),
        timezone=str(rcfg.timezone),
        dataset_path=str(rcfg.data.dataset_path),
        config_path=str(path),
    )


def detect_capabilities() -> RuntimeAppCapabilities:
    def _has(mod_name: str) -> bool:
        try:
            __import__(mod_name)
            return True
        except Exception:
            return False

    return RuntimeAppCapabilities(
        control_app=_has('natbin.control.app'),
        runtime_cycle=_has('natbin.runtime.cycle'),
        runtime_daemon=_has('natbin.runtime.daemon'),
        runtime_quota=_has('natbin.runtime.quota'),
        runtime_scope=_has('natbin.runtime.scope'),
        runtime_repos=_has('natbin.state.repos'),
        runtime_observability=_has('natbin.runtime_observability'),
        runtime_execution=_has('natbin.runtime.execution'),
        runtime_reconciliation=_has('natbin.runtime.reconciliation'),
    )


def derive_scoped_paths(config: RuntimeAppConfig, *, repo_root: str | Path | None = None) -> dict[str, str]:
    root = Path(repo_root).resolve() if repo_root is not None else Path('.').resolve()
    runs_dir = root / 'runs'
    scope = build_scope(config.asset, int(config.interval_sec))
    local_day = _local_day(config.timezone)
    control_paths = control_artifact_paths(repo_root=root, asset=config.asset, interval_sec=config.interval_sec)
    return {
        'effective_env': str(effective_env_path(asset=config.asset, interval_sec=config.interval_sec, out_dir=runs_dir)),
        'market_context': str(market_context_path(asset=config.asset, interval_sec=config.interval_sec, out_dir=runs_dir)),
        'status': str(loop_status_path(asset=config.asset, interval_sec=config.interval_sec, out_dir=runs_dir)),
        'signals_db': str(runs_dir / 'live_signals.sqlite3'),
        'state_db': str(runs_dir / 'live_topk_state.sqlite3'),
        'log_dir': str(runs_dir / 'logs'),
        'decision_dir': str(runs_dir / 'decisions'),
        'incidents_dir': str(runs_dir / 'incidents'),
        'decision_latest': str(decision_latest_path(asset=config.asset, interval_sec=config.interval_sec, out_dir=runs_dir)),
        'incident_stream': str(incident_jsonl_path(day=local_day, asset=config.asset, interval_sec=config.interval_sec, out_dir=runs_dir)),
        'health_legacy': str(health_snapshot_path(asset=config.asset, interval_sec=config.interval_sec, out_dir=runs_dir)),
        'live_signals_csv': str(live_signals_csv_path(day=local_day, asset=config.asset, interval_sec=config.interval_sec, out_dir=runs_dir)),
        'effective_config': str((runs_dir / 'config' / f'effective_config_latest_{scope.scope_tag}.json').resolve()),
        'effective_config_snapshot_dir': str((runs_dir / 'config').resolve()),
        'effective_config_control': str(Path(control_paths['effective_config']).resolve()),
    }


def build_context(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    dump_snapshot: bool | None = None,
) -> RuntimeContext:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    rcfg = load_resolved_config(config_path=cfg_path, repo_root=root, asset=asset, interval_sec=interval_sec)

    generated_at_utc = datetime.now(UTC).isoformat(timespec='seconds')
    cycle_id = datetime.now(UTC).strftime('%H%M%S')
    latest_path = write_effective_config_latest(rcfg, repo_root=root)
    snapshot_path: Path | None = None
    if dump_snapshot is None:
        dump_snapshot = os.getenv('EFFECTIVE_CONFIG_SNAPSHOT', '1').strip() not in {'0', 'false', 'False'}
    if dump_snapshot:
        snapshot_path = write_effective_config_snapshot(rcfg, repo_root=root, day=_local_day(str(rcfg.timezone)), cycle_id=cycle_id)

    config = RuntimeAppConfig(
        asset=str(rcfg.asset),
        interval_sec=int(rcfg.interval_sec),
        timezone=str(rcfg.timezone),
        dataset_path=str(rcfg.data.dataset_path),
        config_path=str(cfg_path),
    )
    scope_obj = build_scope(config.asset, config.interval_sec)
    scope = RuntimeScopeInfo(
        asset=config.asset,
        interval_sec=config.interval_sec,
        timezone=config.timezone,
        scope_tag=scope_obj.scope_tag,
    )
    scoped_paths = derive_scoped_paths(config, repo_root=root)
    control_paths = control_artifact_paths(repo_root=root, asset=config.asset, interval_sec=config.interval_sec)
    scoped_paths['effective_config'] = str(latest_path)
    scoped_paths['effective_config_control'] = str(Path(control_paths['effective_config']).resolve())
    if snapshot_path is not None:
        scoped_paths['effective_config_snapshot'] = str(snapshot_path)
    ctx = RuntimeContext(
        repo_root=str(root),
        config=config,
        scope=scope,
        resolved_config=rcfg.as_dict(),
        source_trace=list(getattr(rcfg, 'source_trace', []) or []),
        scoped_paths=scoped_paths,
        control_paths=control_paths,
    )
    effective_payload = {
        'repo_root': str(root),
        'config_path': str(cfg_path),
        'scope': asdict(scope),
        'source_trace': ctx.source_trace,
        'resolved_config': ctx.resolved_config,
        'generated_at_utc': generated_at_utc,
        'cycle_id': cycle_id,
        'latest_path': str(latest_path),
        'snapshot_path': str(snapshot_path) if snapshot_path is not None else None,
    }
    redact_email = bool(getattr(getattr(rcfg, 'security', None), 'redact_email', True))
    redact_control_artifacts = bool(getattr(getattr(rcfg, 'security', None), 'redact_control_artifacts', True))
    if redact_control_artifacts:
        effective_payload = sanitize_payload(
            effective_payload,
            sensitive_values=collect_sensitive_values(effective_payload, redact_email=redact_email),
            redact_email=redact_email,
        )
    write_control_artifact(
        repo_root=root,
        asset=config.asset,
        interval_sec=config.interval_sec,
        name='effective_config',
        payload=effective_payload,
    )

    try:
        from ..security.audit import audit_security_posture

        if bool(getattr(getattr(rcfg, 'security', None), 'audit_on_context_build', True)):
            security_payload = audit_security_posture(
                repo_root=root,
                config_path=cfg_path,
                resolved_config=rcfg,
                source_trace=ctx.source_trace,
            )
            write_control_artifact(
                repo_root=root,
                asset=config.asset,
                interval_sec=config.interval_sec,
                name='security',
                payload=security_payload,
            )
    except Exception:
        pass
    return ctx


def build_runtime_plan(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    topk: int = 3,
    lookback_candles: int = 2000,
) -> RuntimePlan:
    from ..runtime.cycle import build_auto_cycle_plan

    ctx = build_context(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec)
    plan_steps = [asdict(s) for s in build_auto_cycle_plan(Path(ctx.repo_root), topk=topk, lookback_candles=lookback_candles)]
    notes = {
        'design': 'Package M: runtime_app is the canonical control plane. PowerShell wrappers are bootstrap-only.',
        'config_status': 'config/base.yaml is preferred when present. The observer is now config v2 aware; config.yaml is only an optional fallback for legacy tune fields.',
        'scheduler_status': 'observe_loop wrappers must call runtime_app observe and should not orchestrate runtime logic.',
    }
    payload = RuntimePlan(
        mode='control_plane',
        repo_root=ctx.repo_root,
        scope=asdict(ctx.scope),
        config_path=ctx.config.config_path,
        steps=plan_steps,
        control_paths=dict(ctx.control_paths),
        scoped_paths=dict(ctx.scoped_paths),
        notes=notes,
    )
    write_control_artifact(
        repo_root=ctx.repo_root,
        asset=ctx.config.asset,
        interval_sec=ctx.config.interval_sec,
        name='plan',
        payload=payload.as_dict(),
    )
    return payload


def build_runtime_app_info(
    config_path: str | Path | None = DEFAULT_CONFIG_PATH,
    *,
    repo_root: str | Path = '.',
    asset: str | None = None,
    interval_sec: int | None = None,
) -> RuntimeAppInfo:
    ctx = build_context(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec)
    capabilities = detect_capabilities()
    health = read_control_artifact(repo_root=ctx.repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='health')
    if not isinstance(health, dict):
        health = build_health_payload(
            asset=ctx.config.asset,
            interval_sec=ctx.config.interval_sec,
            state='startup',
            message='no_runtime_health_snapshot',
            quota=None,
            failsafe=None,
            market_context=None,
            last_cycle_ok=None,
        )
    notes = {
        'control_plane': 'runtime_app is the canonical control plane entrypoint for Package M.',
        'legacy_observer': "observe_signal_topk_perday is now config v2 aware; config.yaml is only an optional fallback for legacy tune fields (tune_dir/bounds) if base.yaml doesn't provide them.",
        'wrapper': 'observe_loop*.ps1 should only bootstrap Python and call runtime_app observe.',
    }
    return RuntimeAppInfo(
        config=ctx.config,
        scope=ctx.scope,
        capabilities=capabilities,
        scoped_paths=dict(ctx.scoped_paths),
        control_paths=dict(ctx.control_paths),
        health=health,
        notes=notes,
    )


def to_json_dict(info: RuntimeAppInfo) -> dict[str, Any]:
    return info.as_dict()
