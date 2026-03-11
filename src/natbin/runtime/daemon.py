from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import argparse
import json
import os
import time
from typing import Any

from ..config.paths import resolve_config_path
from ..state.control_repo import RuntimeControlRepository, write_control_artifact
from .cycle import OK, build_auto_cycle_plan, report_from_plan, run_plan
from .failsafe import CircuitBreakerPolicy, RuntimeFailsafe
from .health import build_health_payload, build_status_payload, write_health_payload, write_status_payload
from ..runtime_perf import load_json_cached
from ..telemetry import TelemetryServer, TelemetryState
from ..telemetry.metrics import REGISTRY
from ..ops.lockfile import acquire_lock as acquire_lockfile
from ..ops.lockfile import release_lock as release_lockfile
from ..ops.structured_log import append_jsonl
from .hardening import refresh_runtime_lock, startup_sanitize_runtime, write_runtime_lifecycle
from .precheck import run_precheck
from .quota import OPEN as QUOTA_OPEN, MAX_K_REACHED, PACING_QUOTA_REACHED, build_quota_snapshot
from .scope import daemon_lock_path, repo_scope


@dataclass(frozen=True)
class SleepPlan:
    reason: str
    sleep_sec: int
    next_wake_utc: str


@dataclass(frozen=True)
class DaemonStatus:
    phase: str
    ok: bool | None
    now_utc: str
    next_wake_utc: str | None
    sleep_reason: str | None
    steps: list[dict[str, Any]] | None = None
    outcomes: list[dict[str, Any]] | None = None



def _utcnow() -> datetime:
    return datetime.now(UTC)



def _fmt_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec='seconds')



def _parse_now_utc(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except Exception as e:  # pragma: no cover - defensive/CLI only
        raise SystemExit(f'invalid --now-utc: {s!r} ({e})')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)



def compute_next_candle_sleep(interval_sec: int, *, offset_sec: int = 3, now_utc: datetime | None = None) -> SleepPlan:
    now = now_utc or _utcnow()
    epoch = int(now.timestamp())
    interval_sec = max(1, int(interval_sec))
    offset_sec = max(0, int(offset_sec))
    nxt = ((epoch // interval_sec) + 1) * interval_sec + offset_sec
    nxt_dt = datetime.fromtimestamp(nxt, tz=UTC)
    sleep_sec = max(0, int((nxt_dt - now).total_seconds()))
    return SleepPlan(reason='next_candle', sleep_sec=sleep_sec, next_wake_utc=_fmt_utc(nxt_dt) or '')



def compute_day_reset_sleep(tz_name: str, *, offset_sec: int = 3, now_utc: datetime | None = None) -> SleepPlan:
    now = now_utc or _utcnow()
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(str(tz_name or 'UTC'))
    except Exception:
        tz = UTC
    local = now.astimezone(tz)
    next_local_day = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    next_utc = next_local_day.astimezone(UTC) + timedelta(seconds=max(0, int(offset_sec)))
    sleep_sec = max(0, int((next_utc - now).total_seconds()))
    return SleepPlan(reason='next_repo_day', sleep_sec=sleep_sec, next_wake_utc=_fmt_utc(next_utc) or '')



def classify_report_ok(report: dict[str, Any]) -> bool:
    return bool(report.get('ok'))



def acquire_lock(lock_path: Path, *, owner: dict[str, Any] | None = None):
    return acquire_lockfile(lock_path, owner=owner)



def release_lock(lock_path: Path) -> None:
    release_lockfile(lock_path)



def _lock_owner(*, repo_root: Path, scope, mode: str) -> dict[str, Any]:
    return {
        'repo_root': str(repo_root),
        'scope_tag': str(scope.scope_tag),
        'asset': str(scope.asset),
        'interval_sec': int(scope.interval_sec),
        'mode': str(mode),
    }



def _scope_from_repo_root(repo_root: Path):
    config_path = resolve_config_path(repo_root=repo_root)
    return repo_scope(config_path=str(config_path), repo_root=repo_root)



def _make_context(repo_root: Path, *, asset: str | None = None, interval_sec: int | None = None):
    from ..control.plan import build_context

    return build_context(repo_root=repo_root, asset=asset, interval_sec=interval_sec)



def _failsafe_from_context(ctx, *, repo_root: Path) -> RuntimeFailsafe:
    cfg = dict(ctx.resolved_config or {})
    fs = dict(cfg.get('failsafe') or {})
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



def _market_context_from_ctx(ctx) -> dict[str, Any] | None:
    p = ctx.scoped_paths.get('market_context')
    if not p:
        return None
    obj = load_json_cached(p)
    return obj if isinstance(obj, dict) else None



def _precheck_payload(*, repo_root: Path, ctx, scope, topk: int, sleep_align_offset_sec: int, now_utc: datetime | None = None, enforce_market_context: bool = False):
    try:
        from .execution import precheck_reconcile_if_enabled

        precheck_reconcile_if_enabled(repo_root=repo_root, config_path=ctx.config.config_path)
    except Exception:
        pass
    quota = build_quota_snapshot(repo_root, topk=topk, sleep_align_offset_sec=sleep_align_offset_sec, now_utc=now_utc, config_path=ctx.config.config_path)
    control_repo = RuntimeControlRepository(repo_root / 'runs' / 'runtime_control.sqlite3')
    failsafe = _failsafe_from_context(ctx, repo_root=repo_root)
    market_context = _market_context_from_ctx(ctx)
    decision = run_precheck(
        failsafe,
        asset=str(getattr(scope, 'asset', None) or ctx.resolved_config.get('asset') or ctx.resolved_config.get('scope', {}).get('asset') or 'UNKNOWN'),
        interval_sec=int(getattr(scope, 'interval_sec', None) or ctx.resolved_config.get('interval_sec') or 0),
        control_repo=control_repo,
        market_context=market_context,
        quota_hard_block=quota.kind != QUOTA_OPEN,
        quota_reason=quota.kind if quota.kind != QUOTA_OPEN else None,
        env=dict(os.environ),
        now_utc=now_utc,
        enforce_market_context=bool(enforce_market_context),
    )
    return decision, quota, market_context, control_repo, failsafe



def _sleep_plan_for_block(reason: str | None, *, scope, quota, decision, now_utc: datetime | None = None, offset_sec: int = 3) -> SleepPlan:
    if reason == MAX_K_REACHED:
        return SleepPlan(reason=reason, sleep_sec=int(quota.sleep_sec), next_wake_utc=str(quota.next_wake_utc or ''))
    if reason == PACING_QUOTA_REACHED:
        return SleepPlan(reason=reason, sleep_sec=int(quota.sleep_sec), next_wake_utc=str(quota.next_wake_utc or ''))
    breaker = getattr(decision, 'breaker', None)
    if reason == 'circuit_open' and breaker is not None and getattr(breaker, 'opened_until_utc', None) is not None:
        nxt = breaker.opened_until_utc.astimezone(UTC)
        now = now_utc or _utcnow()
        return SleepPlan(reason=reason, sleep_sec=max(0, int((nxt - now).total_seconds())), next_wake_utc=_fmt_utc(nxt) or '')
    return compute_next_candle_sleep(scope.interval_sec, offset_sec=offset_sec, now_utc=now_utc)



def _write_runtime_state(*, repo_root: Path, ctx, phase: str, state: str, message: str, next_wake_utc: str | None, sleep_reason: str | None, report: dict[str, Any] | None, quota: dict[str, Any] | None, failsafe: dict[str, Any] | None, market_context: dict[str, Any] | None, last_cycle_ok: bool | None) -> None:
    asset = str(ctx.resolved_config.get('asset') or 'UNKNOWN')
    interval_sec = int(ctx.resolved_config.get('interval_sec') or 0)
    status = build_status_payload(
        asset=asset,
        interval_sec=interval_sec,
        phase=phase,
        state=state,
        message=message,
        next_wake_utc=next_wake_utc,
        sleep_reason=sleep_reason,
        report=report,
        quota=quota,
        failsafe=failsafe,
        market_context=market_context,
    )
    write_status_payload(asset=asset, interval_sec=interval_sec, payload=status, out_dir=repo_root / 'runs')
    health = build_health_payload(
        asset=asset,
        interval_sec=interval_sec,
        state=state,
        message=message,
        quota=quota,
        failsafe=failsafe,
        market_context=market_context,
        last_cycle_ok=last_cycle_ok,
    )
    write_health_payload(asset=asset, interval_sec=interval_sec, payload=health, out_dir=repo_root / 'runs')

    write_control_artifact(repo_root=repo_root, asset=asset, interval_sec=interval_sec, name='loop_status', payload=status)
    write_control_artifact(repo_root=repo_root, asset=asset, interval_sec=interval_sec, name='health', payload=health)
    if quota is not None:
        write_control_artifact(repo_root=repo_root, asset=asset, interval_sec=interval_sec, name='quota', payload=quota)
    if phase == 'precheck' and report is not None:
        write_control_artifact(repo_root=repo_root, asset=asset, interval_sec=interval_sec, name='precheck', payload=report)



def _blocked_precheck_report(*, repo_root: Path, ctx, scope, decision, quota, market_context, offset_sec: int, now_utc: datetime | None = None) -> tuple[dict[str, Any], SleepPlan]:
    sleep_plan = _sleep_plan_for_block(decision.reason, scope=scope, quota=quota, decision=decision, now_utc=now_utc, offset_sec=offset_sec)
    rep = {
        'phase': 'precheck',
        'ok': True,
        'state': 'blocked',
        'message': str(decision.reason or 'precheck_blocked'),
        'now_utc': _fmt_utc(_utcnow()),
        'next_wake_utc': sleep_plan.next_wake_utc,
        'sleep_reason': sleep_plan.reason,
        'sleep_sec': int(sleep_plan.sleep_sec),
        'failsafe_snapshot': decision.snapshot.as_dict() if decision.snapshot else None,
        'quota_snapshot': quota.as_dict(),
        'market_context': market_context or {},
        'effective_config_latest': ctx.scoped_paths.get('effective_config'),
    }
    _write_runtime_state(
        repo_root=repo_root,
        ctx=ctx,
        phase='precheck',
        state='blocked',
        message=str(decision.reason or 'precheck_blocked'),
        next_wake_utc=sleep_plan.next_wake_utc,
        sleep_reason=sleep_plan.reason,
        report=rep,
        quota=quota.as_dict(),
        failsafe=decision.snapshot.as_dict() if decision.snapshot else {},
        market_context=market_context or {},
        last_cycle_ok=True,
    )
    return rep, sleep_plan



def _run_cycle(*, repo_root: Path, ctx, scope, topk: int, lookback_candles: int, stop_on_failure: bool, decision, quota, market_context, control_repo: RuntimeControlRepository, failsafe: RuntimeFailsafe, sleep_align_offset_sec: int = 3) -> dict[str, Any]:
    if decision.blocked:
        rep, _ = _blocked_precheck_report(
            repo_root=repo_root,
            ctx=ctx,
            scope=scope,
            decision=decision,
            quota=quota,
            market_context=market_context,
            offset_sec=sleep_align_offset_sec,
        )
        return rep

    plan = build_auto_cycle_plan(repo_root, topk=topk, lookback_candles=lookback_candles)
    outcomes = run_plan(plan, stop_on_failure=stop_on_failure)
    rep = asdict(report_from_plan(repo_root, plan, outcomes))
    rep['phase'] = 'cycle'
    rep['state'] = 'healthy' if rep.get('ok') else 'failed'
    rep['message'] = 'cycle_ok' if rep.get('ok') else 'cycle_failed'
    rep['now_utc'] = _fmt_utc(_utcnow())
    rep['effective_config_latest'] = ctx.scoped_paths.get('effective_config')
    rep['quota_snapshot'] = quota.as_dict()
    rep['failsafe_snapshot'] = decision.snapshot.as_dict() if decision.snapshot else None
    rep['market_context'] = market_context or {}

    breaker = decision.breaker
    if breaker is not None:
        now_utc = _utcnow()
        if rep.get('ok'):
            breaker = failsafe.record_success(breaker)
        else:
            failed_step = None
            for out in outcomes:
                if out.kind != OK:
                    failed_step = f"{out.name}:{out.kind}"
                    break
            breaker = failsafe.record_failure(breaker, failed_step or 'cycle_failed', now_utc)
        control_repo.save_breaker(breaker)
        if rep.get('failsafe_snapshot'):
            rep['failsafe_snapshot']['circuit_state'] = breaker.state
            rep['failsafe_snapshot']['circuit_reason'] = breaker.reason

    _write_runtime_state(
        repo_root=repo_root,
        ctx=ctx,
        phase='cycle',
        state='healthy' if rep.get('ok') else 'failed',
        message=str(rep.get('message') or ''),
        next_wake_utc=None,
        sleep_reason=None,
        report=rep,
        quota=quota.as_dict(),
        failsafe=rep.get('failsafe_snapshot') or {},
        market_context=market_context or {},
        last_cycle_ok=bool(rep.get('ok')),
    )
    return rep




def _lock_block_payload(*, lock_path: Path, lock_res) -> dict[str, Any]:
    return {
        'phase': 'startup',
        'ok': False,
        'state': 'blocked',
        'message': f'lock_exists:{lock_path.name}',
        'lock_path': str(lock_path),
        'lock_pid': getattr(lock_res, 'pid', None),
        'lock_age_sec': getattr(lock_res, 'age_sec', None),
        'lock_detail': getattr(lock_res, 'detail', None),
    }


def run_once(*, repo_root: str | Path = '.', topk: int = 3, lookback_candles: int = 2000, stop_on_failure: bool = True, precheck_market_context: bool = False) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    scope = _scope_from_repo_root(repo_root)
    owner = _lock_owner(repo_root=repo_root, scope=scope, mode='once')
    lock_path = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=repo_root / 'runs')
    lock_res = acquire_lock(lock_path, owner=owner)
    if not bool(getattr(lock_res, 'acquired', False)):
        return _lock_block_payload(lock_path=lock_path, lock_res=lock_res)

    ctx = None
    rep: dict[str, Any] | None = None
    try:
        ctx = _make_context(repo_root, asset=scope.asset, interval_sec=scope.interval_sec)
        startup_sanitize_runtime(repo_root=repo_root, ctx=ctx, mode='once', lock_path=lock_path, owner=owner)
        refresh_runtime_lock(lock_path=lock_path, ctx=ctx, owner=owner)
        decision, quota, market_context, control_repo, failsafe = _precheck_payload(
            repo_root=repo_root,
            ctx=ctx,
            scope=scope,
            topk=topk,
            sleep_align_offset_sec=3,
            enforce_market_context=bool(precheck_market_context),
        )
        refresh_runtime_lock(lock_path=lock_path, ctx=ctx, owner=owner)
        rep = _run_cycle(
            repo_root=repo_root,
            ctx=ctx,
            scope=scope,
            topk=topk,
            lookback_candles=lookback_candles,
            stop_on_failure=stop_on_failure,
            decision=decision,
            quota=quota,
            market_context=market_context,
            control_repo=control_repo,
            failsafe=failsafe,
            sleep_align_offset_sec=3,
        )
        return rep
    finally:
        try:
            if ctx is not None:
                write_runtime_lifecycle(
                    repo_root=repo_root,
                    ctx=ctx,
                    event='shutdown',
                    payload={
                        'mode': 'once',
                        'lock_path': str(lock_path),
                        'last_phase': rep.get('phase') if isinstance(rep, dict) else None,
                        'last_ok': rep.get('ok') if isinstance(rep, dict) else None,
                    },
                )
        except Exception:
            pass
        release_lock(lock_path)


def run_daemon(*, repo_root: str | Path = '.', topk: int = 3, lookback_candles: int = 2000, max_cycles: int | None = None, sleep_align_offset_sec: int = 3, stop_on_failure: bool = True, quota_aware_sleep: bool = False, precheck_market_context: bool = False) -> int:
    repo_root = Path(repo_root).resolve()
    scope = _scope_from_repo_root(repo_root)
    owner = _lock_owner(repo_root=repo_root, scope=scope, mode='daemon')
    lock_path = daemon_lock_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=repo_root / 'runs')
    lock_res = acquire_lock(lock_path, owner=owner)
    if not bool(getattr(lock_res, 'acquired', False)):
        print(json.dumps(_lock_block_payload(lock_path=lock_path, lock_res=lock_res), ensure_ascii=False))
        return 3

    telemetry_state = TelemetryState()
    telemetry_server: TelemetryServer | None = None

    m_cycle_total = REGISTRY.counter(
        'thalor_runtime_cycle_total',
        help='Total number of runtime daemon loop iterations',
        labelnames=('scope_tag', 'phase', 'ok'),
    )
    m_cycle_seconds = REGISTRY.histogram(
        'thalor_runtime_cycle_duration_seconds',
        help='Runtime daemon cycle duration (seconds)',
        labelnames=('scope_tag', 'phase'),
    )

    ctx_current = None
    last_rep: dict[str, Any] | None = None
    cycles = 0
    exit_code = 0

    try:
        try:
            ctx_current = _make_context(repo_root, asset=scope.asset, interval_sec=scope.interval_sec)
            startup_sanitize_runtime(repo_root=repo_root, ctx=ctx_current, mode='daemon', lock_path=lock_path, owner=owner)
            refresh_runtime_lock(lock_path=lock_path, ctx=ctx_current, owner=owner)
            obs = dict((ctx_current.resolved_config or {}).get('observability') or {})
            if bool(obs.get('metrics_enable')):
                bind = str(obs.get('metrics_bind') or '127.0.0.1:9108')
                telemetry_server = TelemetryServer(bind=bind, state=telemetry_state)
                telemetry_server.start()
                telemetry_state.update(ready=True, ready_reason='ok')
        except Exception:
            telemetry_server = None

        while True:
            t0 = time.perf_counter()
            ctx_current = _make_context(repo_root, asset=scope.asset, interval_sec=scope.interval_sec)
            refresh_runtime_lock(lock_path=lock_path, ctx=ctx_current, owner=owner)
            decision, quota, market_context, control_repo, failsafe = _precheck_payload(
                repo_root=repo_root,
                ctx=ctx_current,
                scope=scope,
                topk=topk,
                sleep_align_offset_sec=sleep_align_offset_sec,
                enforce_market_context=bool(precheck_market_context),
            )
            refresh_runtime_lock(lock_path=lock_path, ctx=ctx_current, owner=owner)
            rep = _run_cycle(
                repo_root=repo_root,
                ctx=ctx_current,
                scope=scope,
                topk=topk,
                lookback_candles=lookback_candles,
                stop_on_failure=stop_on_failure,
                decision=decision,
                quota=quota,
                market_context=market_context,
                control_repo=control_repo,
                failsafe=failsafe,
                sleep_align_offset_sec=sleep_align_offset_sec,
            )
            last_rep = rep
            print(json.dumps(rep, ensure_ascii=False))

            try:
                phase = str(rep.get('phase') or 'cycle')
                ok = bool(rep.get('ok')) if rep.get('ok') is not None else False
                m_cycle_total.inc(1, scope_tag=str(scope.scope_tag), phase=phase, ok=str(ok).lower())
                m_cycle_seconds.observe(max(0.0, time.perf_counter() - t0), scope_tag=str(scope.scope_tag), phase=phase)
                telemetry_state.scope_update(
                    str(scope.scope_tag),
                    last_cycle_ok=bool(rep.get('ok')),
                    last_cycle_id=rep.get('cycle_id'),
                    last_phase=phase,
                    blocked=bool(phase == 'precheck'),
                )
                telemetry_state.update(
                    last_cycle_ok=bool(rep.get('ok')),
                    last_cycle_id=rep.get('cycle_id'),
                    last_cycle_message=str(rep.get('message') or ''),
                    kill_switch_active=bool((rep.get('failsafe_snapshot') or {}).get('kill_switch_active')),
                    drain_mode_active=bool((rep.get('failsafe_snapshot') or {}).get('drain_mode_active')),
                    ready=(phase not in {'startup', 'precheck'}),
                    ready_reason=('ok' if phase != 'precheck' else str(rep.get('sleep_reason') or rep.get('message') or 'precheck_blocked')),
                )
                obs_cfg = dict((ctx_current.resolved_config or {}).get('observability') or {})
                if bool(obs_cfg.get('structured_logs_enable', True)):
                    log_path = obs_cfg.get('structured_logs_path') or 'runs/logs/runtime_structured.jsonl'
                    if not Path(str(log_path)).is_absolute():
                        log_path = repo_root / Path(str(log_path))
                    append_jsonl(
                        log_path,
                        {
                            'event': 'runtime_daemon_cycle',
                            'scope_tag': str(scope.scope_tag),
                            'phase': phase,
                            'ok': bool(rep.get('ok')),
                            'cycle_id': rep.get('cycle_id'),
                            'blocked_reason': rep.get('message') if phase == 'precheck' else None,
                        },
                    )
            except Exception:
                pass

            cycles += 1
            if max_cycles is not None and cycles >= int(max_cycles):
                break

            if rep.get('phase') == 'precheck':
                sleep_plan = SleepPlan(
                    reason=str(rep.get('sleep_reason') or rep.get('message') or 'precheck_blocked'),
                    sleep_sec=int(rep.get('sleep_sec') or 0),
                    next_wake_utc=str(rep.get('next_wake_utc') or ''),
                )
                refresh_runtime_lock(
                    lock_path=lock_path,
                    ctx=ctx_current,
                    owner={**owner, 'sleep_reason': sleep_plan.reason, 'next_wake_utc': sleep_plan.next_wake_utc},
                )
                time.sleep(max(0, int(sleep_plan.sleep_sec)))
                continue

            if quota_aware_sleep:
                q = rep.get('quota_snapshot') or {}
                if isinstance(q, dict) and str(q.get('kind') or '') != QUOTA_OPEN:
                    sleep_plan = SleepPlan(reason=str(q.get('kind') or 'quota_blocked'), sleep_sec=int(q.get('sleep_sec') or 0), next_wake_utc=str(q.get('next_wake_utc') or ''))
                else:
                    sleep_plan = compute_next_candle_sleep(scope.interval_sec, offset_sec=sleep_align_offset_sec)
            else:
                sleep_plan = compute_next_candle_sleep(scope.interval_sec, offset_sec=sleep_align_offset_sec)
            print(json.dumps({'phase': 'sleep', 'reason': sleep_plan.reason, 'sleep_sec': sleep_plan.sleep_sec, 'next_wake_utc': sleep_plan.next_wake_utc}, ensure_ascii=False))
            refresh_runtime_lock(
                lock_path=lock_path,
                ctx=ctx_current,
                owner={**owner, 'sleep_reason': sleep_plan.reason, 'next_wake_utc': sleep_plan.next_wake_utc},
            )
            time.sleep(max(0, int(sleep_plan.sleep_sec)))
        exit_code = 0
        return 0
    except Exception:
        exit_code = 2
        raise
    finally:
        try:
            if telemetry_server is not None:
                telemetry_server.stop()
        except Exception:
            pass
        try:
            if ctx_current is not None:
                write_runtime_lifecycle(
                    repo_root=repo_root,
                    ctx=ctx_current,
                    event='shutdown',
                    payload={
                        'mode': 'daemon',
                        'lock_path': str(lock_path),
                        'cycles': int(cycles),
                        'last_phase': last_rep.get('phase') if isinstance(last_rep, dict) else None,
                        'last_ok': last_rep.get('ok') if isinstance(last_rep, dict) else None,
                        'exit_code': int(exit_code),
                    },
                )
        except Exception:
            pass
        release_lock(lock_path)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Python-native daemon for the Thalor runtime cycle (Package J foundation).')
    p.add_argument('--repo-root', default='.')
    p.add_argument('--topk', type=int, default=3)
    p.add_argument('--lookback-candles', type=int, default=2000)
    p.add_argument('--once', action='store_true', help='Run a single cycle and exit')
    p.add_argument('--max-cycles', type=int, default=None, help='Loop for N cycles and exit')
    p.add_argument('--sleep-align-offset-sec', type=int, default=3)
    p.add_argument('--quota-aware-sleep', action='store_true', help='Use Python quota/pacing precheck to sleep until next relevant window/day')
    p.add_argument('--precheck-market-context', action='store_true', help='Also fail precheck on stale/closed market context before running cycle')
    p.add_argument('--quota-json', action='store_true', help='Emit current quota snapshot JSON and exit')
    p.add_argument('--now-utc', default=None, help='Optional ISO8601 UTC override for deterministic quota/sleep evaluation in tests/debugging')
    p.add_argument('--no-stop-on-failure', action='store_true')
    p.add_argument('--plan-json', action='store_true', help='Emit the canonical plan JSON and exit')
    return p



def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    repo_root = Path(ns.repo_root).resolve()
    now_override = _parse_now_utc(ns.now_utc)
    if ns.plan_json:
        plan = build_auto_cycle_plan(repo_root, topk=ns.topk, lookback_candles=ns.lookback_candles)
        rep = asdict(report_from_plan(repo_root, plan))
        rep['daemon_capable'] = True
        rep['quota_capable'] = True
        rep['failsafe_capable'] = True
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    if ns.quota_json:
        snap = build_quota_snapshot(
            repo_root,
            topk=ns.topk,
            sleep_align_offset_sec=ns.sleep_align_offset_sec,
            now_utc=now_override,
        )
        print(json.dumps(snap.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if ns.once:
        rep = run_once(repo_root=repo_root, topk=ns.topk, lookback_candles=ns.lookback_candles, stop_on_failure=not ns.no_stop_on_failure, precheck_market_context=bool(ns.precheck_market_context))
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        msg = str(rep.get('message') or '')
        return 0 if classify_report_ok(rep) else (3 if msg.startswith('lock_exists:') else 2)
    return run_daemon(repo_root=repo_root, topk=ns.topk, lookback_candles=ns.lookback_candles, max_cycles=ns.max_cycles, sleep_align_offset_sec=ns.sleep_align_offset_sec, stop_on_failure=not ns.no_stop_on_failure, quota_aware_sleep=bool(ns.quota_aware_sleep), precheck_market_context=bool(ns.precheck_market_context))


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
