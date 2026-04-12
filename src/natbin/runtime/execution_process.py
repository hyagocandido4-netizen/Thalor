from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
import time
from typing import Any

from ..state.execution_repo import ExecutionRepository
from .broker_surface import (
    adapter_from_context,
    build_context,
    execution_cfg,
    execution_enabled,
    execution_repo_path,
)
from .execution_artifacts import write_execution_artifacts
from .execution_contracts import (
    EVENT_INTENT_BLOCKED,
    EVENT_INTENT_CREATED,
    INTENT_EXPIRED_UNSUBMITTED,
    INTENT_PLANNED,
)
from .execution_policy import parse_utc_iso, signal_day_from_ts, utc_now, utc_now_iso
from .execution_hardening import (
    evaluate_execution_hardening,
    execution_hardening_payload as _execution_hardening_payload,
    live_submit_guard,
    verify_live_submit,
)
from .execution_signal import intent_from_signal_row, latest_trade_row
from .execution_status import check_order_status_payload
from .execution_submit import submit_intent
from .reconciliation_flow import reconcile_scope



def _failsafe_from_ctx(*, ctx, repo_root: Path):
    """Local helper to evaluate file/env backed gates.

    We intentionally keep this tiny and independent from the runtime daemon so
    the execution layer stays usable as a standalone subprocess.
    """

    from .failsafe import CircuitBreakerPolicy, RuntimeFailsafe

    fs = {}
    try:
        if isinstance(ctx.resolved_config, dict):
            fs = dict(ctx.resolved_config.get('failsafe') or {})
        else:
            fs = dict(getattr(ctx.resolved_config, 'failsafe', {}) or {})
    except Exception:
        fs = {}

    kill_file = Path(str(fs.get('kill_switch_file') or 'runs/KILL_SWITCH'))
    if not kill_file.is_absolute():
        kill_file = repo_root / kill_file
    drain_file = Path(str(fs.get('drain_mode_file') or 'runs/DRAIN_MODE'))
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



def _enforce_entry_deadline(ctx) -> bool:
    """Whether to enforce entry-deadline for planned intents.

    In paper/fake mode we keep the pipeline permissive so deterministic tests
    and offline simulations don't depend on wall-clock time.
    """

    cfg = execution_cfg(ctx)
    provider = str(cfg.get('provider') or 'fake').strip().lower()
    mode = str(cfg.get('mode') or 'paper').strip().lower()
    if provider == 'fake' or mode == 'paper':
        return False
    return bool(cfg.get('enforce_entry_deadline', True))



def _event_hash(*parts: object) -> str:
    import hashlib

    return hashlib.sha1('|'.join(str(part) for part in parts).encode('utf-8')).hexdigest()[:32]



def _save_blocked_intent(
    repo: ExecutionRepository,
    intent,
    *,
    reason: str,
    error_code: str,
    event_payload: dict[str, Any],
    intent_state: str | None = None,
    broker_status: str | None = None,
):
    updated = replace(
        intent,
        intent_state=intent_state or intent.intent_state,
        broker_status=broker_status or intent.broker_status,
        last_error_code=error_code,
        last_error_message=reason,
        updated_at_utc=utc_now_iso(),
    )
    updated = repo.save_intent(updated)
    repo.add_event(
        event_id=_event_hash(updated.intent_id, 'intent_blocked', reason),
        event_type=EVENT_INTENT_BLOCKED,
        created_at_utc=utc_now_iso(),
        intent_id=updated.intent_id,
        broker_name=updated.broker_name,
        account_mode=updated.account_mode,
        payload=event_payload,
    )
    return updated



def _run_reconcile_phase(*, repo_root: Path, ctx, adapter, phase: str) -> tuple[Any | None, dict[str, Any]]:
    if adapter is None:
        return None, {'skipped': True, 'reason': 'execution_disabled', 'phase': phase}
    try:
        result, detail = reconcile_scope(repo_root=repo_root, ctx=ctx, adapter=adapter)
        detail = dict(detail)
        detail.setdefault('phase', phase)
        return result, detail
    except Exception as exc:
        return None, {'error': f'{type(exc).__name__}:{exc}', 'phase': phase}



def precheck_reconcile_if_enabled(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any] | None:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    if not execution_enabled(ctx):
        return None
    repo_root_p = Path(repo_root).resolve()
    adapter = adapter_from_context(ctx, repo_root=repo_root_p)
    failsafe = _failsafe_from_ctx(ctx=ctx, repo_root=repo_root_p)
    kill_active, _ = failsafe.is_kill_switch_active(dict(os.environ))
    drain_active, _ = failsafe.is_drain_mode_active(dict(os.environ))
    result, detail = _run_reconcile_phase(repo_root=repo_root_p, ctx=ctx, adapter=adapter, phase='precheck')
    payload = {
        'reconciliation': result.as_dict() if result is not None else None,
        'detail': detail,
        'phase': 'precheck',
        'kill_switch_active': bool(kill_active),
        'drain_mode_active': bool(drain_active),
    }
    write_execution_artifacts(repo_root=repo_root, ctx=ctx, reconcile_payload=payload)
    return payload



def process_latest_signal(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    enabled = execution_enabled(ctx)

    repo_root = Path(repo_root).resolve()
    repo = ExecutionRepository(execution_repo_path(repo_root))
    adapter = adapter_from_context(ctx, repo_root=repo_root) if enabled else None

    failsafe = _failsafe_from_ctx(ctx=ctx, repo_root=repo_root)
    kill_active, kill_reason = failsafe.is_kill_switch_active(dict(os.environ))
    drain_active, drain_reason = failsafe.is_drain_mode_active(dict(os.environ))

    pre_result, pre_detail = _run_reconcile_phase(repo_root=repo_root, ctx=ctx, adapter=adapter, phase='pre')

    latest = latest_trade_row(repo_root=repo_root, ctx=ctx)
    created = False
    submitted = None
    latest_intent = None
    blocked_reason = None
    security_guard = None
    account_protection = None
    execution_hardening = None
    post_submit_verification = None
    if latest is not None and str(latest.get('action') or '').upper() in {'CALL', 'PUT'}:
        planned = intent_from_signal_row(row=latest, ctx=ctx, repo_root=repo_root)
        latest_intent, created = repo.ensure_intent(planned)
        if created:
            repo.add_event(
                event_id=_event_hash(planned.intent_id, 'intent_created'),
                event_type=EVENT_INTENT_CREATED,
                created_at_utc=utc_now_iso(),
                intent_id=planned.intent_id,
                broker_name=planned.broker_name,
                account_mode=planned.account_mode,
                payload=planned.as_dict(),
            )
        if latest_intent.intent_state == INTENT_PLANNED:
            if not enabled:
                blocked_reason = 'execution_disabled'
                latest_intent = _save_blocked_intent(
                    repo,
                    latest_intent,
                    reason=blocked_reason,
                    error_code='execution_disabled',
                    event_payload={'reason': blocked_reason},
                )
            else:
                assert adapter is not None
                health = adapter.healthcheck()
                if kill_active or drain_active:
                    blocked_reason = str(kill_reason or drain_reason or ('kill_switch' if kill_active else 'drain_mode'))
                    latest_intent = _save_blocked_intent(
                        repo,
                        latest_intent,
                        reason=blocked_reason,
                        error_code='gate_block',
                        event_payload={
                            'reason': blocked_reason,
                            'kill_switch_active': kill_active,
                            'drain_mode_active': drain_active,
                        },
                    )
                else:
                    deadline = parse_utc_iso(str(latest_intent.entry_deadline_utc))
                    if _enforce_entry_deadline(ctx) and deadline is not None and utc_now() >= deadline:
                        blocked_reason = 'entry_deadline_passed'
                        latest_intent = _save_blocked_intent(
                            repo,
                            latest_intent,
                            reason='planned intent expired without submit',
                            error_code='entry_deadline_passed',
                            event_payload={'reason': blocked_reason},
                            intent_state=INTENT_EXPIRED_UNSUBMITTED,
                            broker_status='not_found',
                        )
                    else:
                        try:
                            from ..security.broker_guard import evaluate_submit_guard

                            security_guard = evaluate_submit_guard(repo_root=repo_root, ctx=ctx)
                        except Exception as exc:
                            security_guard = {'allowed': False, 'reason': f'security_guard_error:{type(exc).__name__}'}
                        if isinstance(security_guard, dict):
                            sg_allowed = bool(security_guard.get('allowed'))
                            sg_reason = str(security_guard.get('reason') or 'security_guard_blocked')
                            sg_payload = dict(security_guard)
                        else:
                            sg_allowed = bool(getattr(security_guard, 'allowed', False))
                            sg_reason = str(getattr(security_guard, 'reason', None) or 'security_guard_blocked')
                            sg_payload = security_guard.as_dict() if hasattr(security_guard, 'as_dict') else {'reason': sg_reason}
                        if not sg_allowed:
                            blocked_reason = sg_reason
                            latest_intent = _save_blocked_intent(
                                repo,
                                latest_intent,
                                reason=blocked_reason,
                                error_code='security_guard',
                                event_payload={'reason': blocked_reason, 'security_guard': sg_payload},
                            )
                        elif not health.ready and bool(execution_cfg(ctx).get('fail_closed', True)):
                            blocked_reason = str(health.reason or 'broker_unready')
                            latest_intent = _save_blocked_intent(
                                repo,
                                latest_intent,
                                reason=blocked_reason,
                                error_code='broker_unready',
                                event_payload={'reason': blocked_reason, 'health': health.as_dict(), 'security_guard': sg_payload},
                            )
                        else:
                            try:
                                from ..security.account_protection import apply_recommended_delay, evaluate_account_protection

                                account_protection = evaluate_account_protection(
                                    repo_root=repo_root,
                                    ctx=ctx,
                                    latest_trade=latest,
                                    write_artifact=True,
                                )
                            except Exception as exc:
                                account_protection = {'allowed': False, 'reason': f'account_protection_error:{type(exc).__name__}'}
                            if isinstance(account_protection, dict):
                                ap_allowed = bool(account_protection.get('allowed'))
                                ap_reason = str(account_protection.get('reason') or 'account_protection_blocked')
                                ap_payload = dict(account_protection)
                                ap_apply_delay = bool((account_protection.get('details') or {}).get('enabled', True))
                                ap_delay = float(account_protection.get('recommended_delay_sec') or 0.0)
                            else:
                                ap_allowed = bool(getattr(account_protection, 'allowed', False))
                                ap_reason = str(getattr(account_protection, 'reason', None) or 'account_protection_blocked')
                                ap_payload = account_protection.as_dict() if hasattr(account_protection, 'as_dict') else {'reason': ap_reason}
                                ap_apply_delay = True
                                ap_delay = float(getattr(account_protection, 'recommended_delay_sec', 0.0) or 0.0)
                            if not ap_allowed:
                                blocked_reason = ap_reason
                                latest_intent = _save_blocked_intent(
                                    repo,
                                    latest_intent,
                                    reason=blocked_reason,
                                    error_code='account_protection',
                                    event_payload={
                                        'reason': blocked_reason,
                                        'health': health.as_dict(),
                                        'security_guard': sg_payload,
                                        'account_protection': ap_payload,
                                    },
                                )
                            else:
                                if not isinstance(account_protection, dict) and ap_apply_delay and ap_delay > 0:
                                    account_protection = apply_recommended_delay(account_protection)
                                execution_hardening = evaluate_execution_hardening(
                                    repo_root=repo_root,
                                    ctx=ctx,
                                    write_artifact=True,
                                )
                                if not bool(execution_hardening.allowed):
                                    blocked_reason = str(execution_hardening.reason or 'execution_hardening_blocked')
                                    latest_intent = _save_blocked_intent(
                                        repo,
                                        latest_intent,
                                        reason=blocked_reason,
                                        error_code='execution_hardening',
                                        event_payload={
                                            'reason': blocked_reason,
                                            'health': health.as_dict(),
                                            'security_guard': sg_payload,
                                            'account_protection': ap_payload,
                                            'execution_hardening': execution_hardening.as_dict(),
                                        },
                                    )
                                else:
                                    with live_submit_guard(repo_root=repo_root, ctx=ctx):
                                        latest_intent, submitted = submit_intent(
                                            repo_root=repo_root,
                                            ctx=ctx,
                                            repo=repo,
                                            adapter=adapter,
                                            intent=latest_intent,
                                        )
                                        if submitted is not None and str(submitted.transport_status) == 'ack':
                                            post_submit_verification = verify_live_submit(
                                                repo_root=repo_root,
                                                ctx=ctx,
                                                repo=repo,
                                                adapter=adapter,
                                                intent=latest_intent,
                                                external_order_id=submitted.external_order_id,
                                            )

    post_result, post_detail = _run_reconcile_phase(repo_root=repo_root, ctx=ctx, adapter=adapter, phase='post')
    if latest_intent is not None:
        latest_intent = repo.get_intent(latest_intent.intent_id) or latest_intent
    day = latest_intent.day if latest_intent is not None else signal_day_from_ts(int(time.time()), timezone_name=str(ctx.config.timezone))
    summary = repo.execution_summary(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, day=day)
    payload = {
        'enabled': bool(enabled),
        'mode': str(execution_cfg(ctx).get('mode') or 'paper'),
        'provider': str(execution_cfg(ctx).get('provider') or 'fake'),
        'scope_tag': ctx.scope.scope_tag,
        'kill_switch_active': bool(kill_active),
        'drain_mode_active': bool(drain_active),
        'latest_trade': latest,
        'intent_created': bool(created),
        'latest_intent': latest_intent.as_dict() if latest_intent is not None else None,
        'submit_attempt': submitted.as_dict() if submitted is not None else None,
        'blocked_reason': blocked_reason,
        'security_guard': security_guard.as_dict() if hasattr(security_guard, 'as_dict') else security_guard,
        'account_protection': account_protection.as_dict() if hasattr(account_protection, 'as_dict') else account_protection,
        'execution_hardening': execution_hardening.as_dict() if hasattr(execution_hardening, 'as_dict') else execution_hardening,
        'post_submit_verification': post_submit_verification.as_dict() if hasattr(post_submit_verification, 'as_dict') else post_submit_verification,
        'pre_reconcile': {'summary': pre_result.as_dict() if pre_result is not None else None, 'detail': pre_detail},
        'post_reconcile': {'summary': post_result.as_dict() if post_result is not None else None, 'detail': post_detail},
        'execution_summary': summary,
    }
    write_execution_artifacts(repo_root=repo_root, ctx=ctx, orders_payload=payload, reconcile_payload=payload.get('post_reconcile'))
    return payload



def orders_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, limit: int = 20) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    repo = ExecutionRepository(execution_repo_path(repo_root))
    recent = repo.list_recent_intents(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, limit=limit)
    day = signal_day_from_ts(int(time.time()), timezone_name=str(ctx.config.timezone))
    payload = {
        'enabled': execution_enabled(ctx),
        'scope_tag': ctx.scope.scope_tag,
        'summary': repo.execution_summary(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, day=day),
        'recent_intents': [item.as_dict() for item in recent],
    }
    write_execution_artifacts(repo_root=repo_root, ctx=ctx, orders_payload=payload)
    return payload



def reconcile_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path)
    if not execution_enabled(ctx):
        payload = {'enabled': False, 'scope_tag': ctx.scope.scope_tag, 'reason': 'execution_disabled'}
        write_execution_artifacts(repo_root=repo_root, ctx=ctx, reconcile_payload=payload)
        return payload
    adapter = adapter_from_context(ctx, repo_root=repo_root)
    result, detail = reconcile_scope(repo_root=repo_root, ctx=ctx, adapter=adapter)
    payload = {'enabled': True, 'scope_tag': ctx.scope.scope_tag, 'summary': result.as_dict(), 'detail': detail}
    write_execution_artifacts(repo_root=repo_root, ctx=ctx, reconcile_payload=payload)
    return payload



def execution_hardening_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any]:
    return _execution_hardening_payload(repo_root=repo_root, config_path=config_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Package N execution layer helper')
    parser.add_argument('--repo-root', default='.')
    parser.add_argument('--config', default=None)
    parser.add_argument('--json', action='store_true')
    sub = parser.add_subparsers(dest='command', required=False)
    sub.add_parser('process')
    sub.add_parser('execute-order', aliases=['execute_order'])
    sp_orders = sub.add_parser('orders')
    sp_orders.add_argument('--limit', type=int, default=20)
    sub.add_parser('reconcile')
    sp_status = sub.add_parser('check-order-status', aliases=['check_order_status'])
    sp_status.add_argument('--external-order-id', required=True)
    sp_status.add_argument('--no-refresh', action='store_true')
    sub.add_parser('execution-hardening', aliases=['execution_hardening'])
    return parser



def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    cmd = ns.command or 'process'
    if cmd == 'orders':
        payload = orders_payload(repo_root=ns.repo_root, config_path=ns.config, limit=int(ns.limit))
    elif cmd == 'reconcile':
        payload = reconcile_payload(repo_root=ns.repo_root, config_path=ns.config)
    elif cmd in {'check-order-status', 'check_order_status'}:
        payload = check_order_status_payload(
            repo_root=ns.repo_root,
            config_path=ns.config,
            external_order_id=ns.external_order_id,
            refresh=not bool(ns.no_refresh),
        )
    elif cmd in {'execution-hardening', 'execution_hardening'}:
        payload = execution_hardening_payload(repo_root=ns.repo_root, config_path=ns.config)
    else:
        payload = process_latest_signal(repo_root=ns.repo_root, config_path=ns.config)
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str) if ns.json else json.dumps(payload, ensure_ascii=False, default=str))
    return 0


__all__ = [
    'build_parser',
    'check_order_status_payload',
    'execution_hardening_payload',
    'main',
    'orders_payload',
    'precheck_reconcile_if_enabled',
    'process_latest_signal',
    'reconcile_payload',
]
