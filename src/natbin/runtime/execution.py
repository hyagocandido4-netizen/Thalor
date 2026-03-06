from __future__ import annotations

import argparse
import os
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from ..brokers import FakeBrokerAdapter, IQOptionAdapter
from ..state.execution_repo import ExecutionRepository
from ..state.repos import SignalsRepository
from ..state.control_repo import write_control_artifact
from .execution_contracts import (
    EVENT_INTENT_BLOCKED,
    EVENT_INTENT_CREATED,
    EVENT_SUBMIT_ACKED,
    EVENT_SUBMIT_EXCEPTION,
    EVENT_SUBMIT_REJECTED,
    EVENT_SUBMIT_REQUESTED,
    EVENT_SUBMIT_TIMEOUT,
    INTENT_ACCEPTED_OPEN,
    INTENT_EXPIRED_UNSUBMITTED,
    INTENT_PLANNED,
    INTENT_REJECTED,
    INTENT_SUBMITTED_UNKNOWN,
    TRANSPORT_ACK,
    TRANSPORT_EXCEPTION,
    TRANSPORT_REJECT,
    TRANSPORT_TIMEOUT,
)
from .execution_models import OrderIntent, OrderSubmitAttempt, SubmitOrderRequest
from .execution_policy import (
    compute_entry_deadline_utc,
    compute_expiry_ts,
    intent_consumes_quota,
    json_dumps,
    make_attempt_id,
    make_client_order_key,
    make_intent_id,
    parse_utc_iso,
    signal_day_from_ts,
    utc_now,
    utc_now_iso,
)
from .reconciliation import reconcile_scope


def _failsafe_from_ctx(*, ctx, repo_root: Path):
    """Local helper to evaluate file/env backed gates.

    We intentionally keep this tiny and independent from the runtime daemon so
    the execution layer stays usable as a standalone subprocess.
    """

    from .failsafe import CircuitBreakerPolicy, RuntimeFailsafe

    # failsafe config lives at root level; resolved_config is a dict (Package M)
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

    cfg = _execution_cfg(ctx)
    provider = str(cfg.get('provider') or 'fake').strip().lower()
    mode = str(cfg.get('mode') or 'paper').strip().lower()
    if provider == 'fake' or mode == 'paper':
        return False
    return bool(cfg.get('enforce_entry_deadline', True))


def execution_repo_path(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / 'runs' / 'runtime_execution.sqlite3'


def _build_context(repo_root: str | Path = '.', config_path: str | Path | None = None):
    from ..control.plan import build_context

    return build_context(repo_root=repo_root, config_path=config_path)


def execution_enabled(ctx) -> bool:
    try:
        exec_cfg = ctx.resolved_config.get('execution') if isinstance(ctx.resolved_config, dict) else ctx.resolved_config.execution
    except Exception:
        exec_cfg = None
    if exec_cfg is None:
        return False
    if isinstance(exec_cfg, dict):
        return bool(exec_cfg.get('enabled')) and str(exec_cfg.get('mode') or 'disabled') != 'disabled'
    return bool(getattr(exec_cfg, 'enabled', False)) and str(getattr(exec_cfg, 'mode', 'disabled')) != 'disabled'


def _execution_cfg(ctx) -> dict[str, Any]:
    raw = ctx.resolved_config.get('execution') if isinstance(ctx.resolved_config, dict) else ctx.resolved_config.execution
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, 'model_dump'):
        return raw.model_dump(mode='python')
    return dict(raw)


def _account_mode(ctx) -> str:
    cfg = _execution_cfg(ctx)
    return str(cfg.get('account_mode') or 'PRACTICE').upper()


def adapter_from_context(ctx, *, repo_root: str | Path):
    cfg = _execution_cfg(ctx)
    provider = str(cfg.get('provider') or 'fake').strip().lower()
    account_mode = str(cfg.get('account_mode') or 'PRACTICE').upper()
    if provider == 'fake':
        fake = dict(cfg.get('fake') or {})
        return FakeBrokerAdapter(
            repo_root=repo_root,
            account_mode=account_mode,
            state_path=fake.get('state_path'),
            submit_behavior=str(fake.get('submit_behavior') or 'ack'),
            settlement=str(fake.get('settlement') or 'open'),
            settle_after_sec=int(fake.get('settle_after_sec') or 0),
            create_order_on_timeout=bool(fake.get('create_order_on_timeout', True)),
            payout=float(fake.get('payout') or 0.80),
            heartbeat_ok=bool(fake.get('heartbeat_ok', True)),
        )
    return IQOptionAdapter(account_mode=account_mode)


def _latest_trade_row(*, repo_root: str | Path, ctx) -> dict[str, Any] | None:
    repo = SignalsRepository(Path(repo_root) / 'runs' / 'live_signals.sqlite3', default_interval=int(ctx.config.interval_sec))
    days = [signal_day_from_ts(int(time.time()), timezone_name=str(ctx.config.timezone))]
    for d in repo.distinct_recent_days(3):
        if d not in days:
            days.append(d)
    latest: dict[str, Any] | None = None
    for day in days:
        rows = repo.fetch_trade_rows(str(ctx.config.asset), int(ctx.config.interval_sec), str(day))
        for row in rows:
            latest = dict(row)
    return latest


def intent_from_signal_row(*, row: dict[str, Any], ctx) -> OrderIntent:
    cfg = _execution_cfg(ctx)
    stake = dict(cfg.get('stake') or {})
    broker_name = str(cfg.get('provider') or 'fake').strip().lower()
    account_mode = str(cfg.get('account_mode') or 'PRACTICE').upper()
    day = str(row.get('day') or signal_day_from_ts(int(row.get('ts') or 0), timezone_name=str(ctx.config.timezone)))
    signal_ts = int(row.get('ts') or 0)
    action = str(row.get('action') or '').upper()
    intent_id = make_intent_id(
        broker_name=broker_name,
        account_mode=account_mode,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        day=day,
        signal_ts=signal_ts,
        action=action,
    )
    now_iso = utc_now_iso()
    return OrderIntent(
        intent_id=intent_id,
        scope_tag=str(ctx.scope.scope_tag),
        broker_name=broker_name,
        account_mode=account_mode,
        day=day,
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        signal_ts=signal_ts,
        decision_action=action,
        decision_conf=float(row.get('conf') or 0.0) if row.get('conf') is not None else None,
        decision_score=float(row.get('score') or 0.0) if row.get('score') is not None else None,
        stake_amount=float(stake.get('amount') or 2.0),
        stake_currency=str(stake.get('currency') or 'BRL'),
        expiry_ts=compute_expiry_ts(signal_ts=signal_ts, interval_sec=int(ctx.config.interval_sec)),
        entry_deadline_utc=compute_entry_deadline_utc(
            signal_ts=signal_ts,
            interval_sec=int(ctx.config.interval_sec),
            grace_sec=int((cfg.get('submit') or {}).get('grace_sec') or 2),
        ),
        client_order_key=make_client_order_key(prefix=str(cfg.get('client_order_prefix') or 'thalor'), intent_id=intent_id),
        intent_state=INTENT_PLANNED,
        broker_status='unknown',
        created_at_utc=now_iso,
        updated_at_utc=now_iso,
    )


def submit_intent(*, repo_root: str | Path, ctx, repo: ExecutionRepository, adapter, intent: OrderIntent) -> tuple[OrderIntent, OrderSubmitAttempt]:
    now = utc_now()
    now_iso = now.isoformat(timespec='seconds')
    attempt_no = repo.next_attempt_no(intent.intent_id)
    req = SubmitOrderRequest(
        intent_id=intent.intent_id,
        client_order_key=intent.client_order_key,
        broker_name=intent.broker_name,
        account_mode=intent.account_mode,
        scope_tag=intent.scope_tag,
        asset=intent.asset,
        interval_sec=intent.interval_sec,
        side=intent.decision_action,
        amount=float(intent.stake_amount),
        currency=intent.stake_currency,
        signal_ts=int(intent.signal_ts),
        expiry_ts=int(intent.expiry_ts),
        entry_deadline_utc=str(intent.entry_deadline_utc),
        metadata={'scope_tag': intent.scope_tag, 'signal_ts': intent.signal_ts},
    )
    repo.add_event(
        event_id=hashlib.sha1(f'{intent.intent_id}|submit_requested|{attempt_no}'.encode('utf-8')).hexdigest()[:32],
        event_type=EVENT_SUBMIT_REQUESTED,
        created_at_utc=now_iso,
        intent_id=intent.intent_id,
        broker_name=intent.broker_name,
        account_mode=intent.account_mode,
        payload=req.as_dict(),
    )

    t0 = time.perf_counter()
    transport_status = TRANSPORT_EXCEPTION
    external_order_id = None
    error_code = None
    error_message = None
    accepted_at_utc = None
    response: dict[str, Any] = {}
    broker_status = 'unknown'
    try:
        res = adapter.submit_order(req)
        transport_status = str(res.transport_status)
        external_order_id = res.external_order_id
        error_code = res.error_code
        error_message = res.error_message
        accepted_at_utc = res.accepted_at_utc
        response = dict(res.response or {})
        broker_status = str(res.broker_status or 'unknown')
    except Exception as exc:
        transport_status = TRANSPORT_EXCEPTION
        error_code = type(exc).__name__
        error_message = str(exc)
        response = {'exception': str(exc)}
        broker_status = 'unknown'

    latency_ms = int(max(0.0, (time.perf_counter() - t0) * 1000.0))
    attempt = OrderSubmitAttempt(
        attempt_id=make_attempt_id(intent_id=intent.intent_id, attempt_no=attempt_no),
        intent_id=intent.intent_id,
        attempt_no=attempt_no,
        requested_at_utc=now_iso,
        finished_at_utc=utc_now_iso(),
        transport_status=transport_status,
        latency_ms=latency_ms,
        external_order_id=external_order_id,
        error_code=error_code,
        error_message=error_message,
        request_json=json_dumps(req.as_dict()),
        response_json=json_dumps(response) if response else None,
    )
    repo.record_attempt(attempt)

    updated = replace(
        intent,
        submit_attempt_count=int(intent.submit_attempt_count) + 1,
        updated_at_utc=utc_now_iso(),
        submitted_at_utc=intent.submitted_at_utc or now_iso,
        external_order_id=external_order_id or intent.external_order_id,
        last_error_code=error_code,
        last_error_message=error_message,
    )
    event_type = EVENT_SUBMIT_EXCEPTION
    if transport_status == TRANSPORT_ACK:
        updated = replace(
            updated,
            intent_state=INTENT_ACCEPTED_OPEN,
            broker_status='open',
            accepted_at_utc=accepted_at_utc or utc_now_iso(),
            last_error_code=None,
            last_error_message=None,
        )
        event_type = EVENT_SUBMIT_ACKED
    elif transport_status == TRANSPORT_REJECT:
        updated = replace(updated, intent_state=INTENT_REJECTED, broker_status='rejected')
        event_type = EVENT_SUBMIT_REJECTED
    elif transport_status == TRANSPORT_TIMEOUT:
        updated = replace(updated, intent_state=INTENT_SUBMITTED_UNKNOWN, broker_status='unknown')
        event_type = EVENT_SUBMIT_TIMEOUT
    else:
        updated = replace(updated, intent_state=INTENT_SUBMITTED_UNKNOWN, broker_status='unknown')
        event_type = EVENT_SUBMIT_EXCEPTION
    updated = repo.save_intent(updated)
    repo.add_event(
        event_id=hashlib.sha1(f'{intent.intent_id}|{event_type}|{attempt_no}'.encode('utf-8')).hexdigest()[:32],
        event_type=event_type,
        created_at_utc=utc_now_iso(),
        intent_id=updated.intent_id,
        broker_name=updated.broker_name,
        account_mode=updated.account_mode,
        external_order_id=updated.external_order_id,
        payload={'attempt': attempt.as_dict(), 'intent_state': updated.intent_state, 'broker_status': broker_status},
    )
    return updated, attempt


def _write_execution_artifacts(*, repo_root: str | Path, ctx, orders_payload: dict[str, Any] | None = None, reconcile_payload: dict[str, Any] | None = None) -> None:
    if orders_payload is not None:
        write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='orders', payload=orders_payload)
        write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='execution', payload=orders_payload)
    if reconcile_payload is not None:
        write_control_artifact(repo_root=repo_root, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='reconcile', payload=reconcile_payload)


def precheck_reconcile_if_enabled(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any] | None:
    ctx = _build_context(repo_root=repo_root, config_path=config_path)
    if not execution_enabled(ctx):
        return None
    repo_root_p = Path(repo_root).resolve()
    adapter = adapter_from_context(ctx, repo_root=repo_root_p)
    failsafe = _failsafe_from_ctx(ctx=ctx, repo_root=repo_root_p)
    kill_active, _ = failsafe.is_kill_switch_active(dict(os.environ))
    drain_active, _ = failsafe.is_drain_mode_active(dict(os.environ))
    try:
        result, detail = reconcile_scope(repo_root=repo_root_p, ctx=ctx, adapter=adapter)
        payload = {
            'reconciliation': result.as_dict(),
            'detail': detail,
            'phase': 'precheck',
            'kill_switch_active': bool(kill_active),
            'drain_mode_active': bool(drain_active),
        }
    except Exception as exc:
        payload = {
            'phase': 'precheck',
            'ok': False,
            'error': f'{type(exc).__name__}:{exc}',
            'kill_switch_active': bool(kill_active),
            'drain_mode_active': bool(drain_active),
        }
    _write_execution_artifacts(repo_root=repo_root, ctx=ctx, reconcile_payload=payload)
    return payload


def process_latest_signal(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any]:
    ctx = _build_context(repo_root=repo_root, config_path=config_path)
    if not execution_enabled(ctx):
        payload = {
            'enabled': False,
            'mode': 'disabled',
            'reason': 'execution_disabled',
            'scope_tag': ctx.scope.scope_tag,
        }
        _write_execution_artifacts(repo_root=repo_root, ctx=ctx, orders_payload=payload)
        return payload

    repo_root = Path(repo_root).resolve()
    repo = ExecutionRepository(execution_repo_path(repo_root))
    adapter = adapter_from_context(ctx, repo_root=repo_root)

    # Global gates (file/env backed) must be enforced even when running as a
    # subprocess from the portfolio runtime.
    failsafe = _failsafe_from_ctx(ctx=ctx, repo_root=repo_root)
    kill_active, kill_reason = failsafe.is_kill_switch_active(dict(os.environ))
    drain_active, drain_reason = failsafe.is_drain_mode_active(dict(os.environ))

    pre_result = None
    pre_detail: dict[str, Any] = {}
    try:
        pre_result, pre_detail = reconcile_scope(repo_root=repo_root, ctx=ctx, adapter=adapter)
    except Exception as exc:
        pre_detail = {'error': f'{type(exc).__name__}:{exc}'}
    latest = _latest_trade_row(repo_root=repo_root, ctx=ctx)
    created = False
    submitted = None
    latest_intent = None
    blocked_reason = None
    if latest is not None and str(latest.get('action') or '').upper() in {'CALL', 'PUT'}:
        planned = intent_from_signal_row(row=latest, ctx=ctx)
        latest_intent, created = repo.ensure_intent(planned)
        if created:
            repo.add_event(
                event_id=hashlib.sha1(f'{planned.intent_id}|intent_created'.encode('utf-8')).hexdigest()[:32],
                event_type=EVENT_INTENT_CREATED,
                created_at_utc=utc_now_iso(),
                intent_id=planned.intent_id,
                broker_name=planned.broker_name,
                account_mode=planned.account_mode,
                payload=planned.as_dict(),
            )
        health = adapter.healthcheck()
        if latest_intent.intent_state == INTENT_PLANNED:
            # Gate 1: explicit operators controls
            if kill_active or drain_active:
                blocked_reason = str(kill_reason or drain_reason or ('kill_switch' if kill_active else 'drain_mode'))
                latest_intent = repo.save_intent(
                    replace(
                        latest_intent,
                        last_error_code='gate_block',
                        last_error_message=blocked_reason,
                        updated_at_utc=utc_now_iso(),
                    )
                )
                repo.add_event(
                    event_id=hashlib.sha1(f'{latest_intent.intent_id}|intent_blocked|{blocked_reason}'.encode('utf-8')).hexdigest()[:32],
                    event_type=EVENT_INTENT_BLOCKED,
                    created_at_utc=utc_now_iso(),
                    intent_id=latest_intent.intent_id,
                    broker_name=latest_intent.broker_name,
                    account_mode=latest_intent.account_mode,
                    payload={'reason': blocked_reason, 'kill_switch_active': kill_active, 'drain_mode_active': drain_active},
                )
            else:
                # Gate 2: entry deadline (never submit stale intents)
                deadline = parse_utc_iso(str(latest_intent.entry_deadline_utc))
                if _enforce_entry_deadline(ctx) and deadline is not None and utc_now() >= deadline:
                    blocked_reason = 'entry_deadline_passed'
                    latest_intent = repo.save_intent(
                        replace(
                            latest_intent,
                            intent_state=INTENT_EXPIRED_UNSUBMITTED,
                            broker_status='not_found',
                            last_error_code='entry_deadline_passed',
                            last_error_message='planned intent expired without submit',
                            updated_at_utc=utc_now_iso(),
                        )
                    )
                    repo.add_event(
                        event_id=hashlib.sha1(f'{latest_intent.intent_id}|intent_blocked|{blocked_reason}'.encode('utf-8')).hexdigest()[:32],
                        event_type=EVENT_INTENT_BLOCKED,
                        created_at_utc=utc_now_iso(),
                        intent_id=latest_intent.intent_id,
                        broker_name=latest_intent.broker_name,
                        account_mode=latest_intent.account_mode,
                        payload={'reason': blocked_reason},
                    )
                else:
                    # Gate 3: broker health / fail-closed
                    if not health.ready and bool(_execution_cfg(ctx).get('fail_closed', True)):
                        blocked_reason = str(health.reason or 'broker_unready')
                        latest_intent = repo.save_intent(
                            replace(
                                latest_intent,
                                last_error_code='broker_unready',
                                last_error_message=blocked_reason,
                                updated_at_utc=utc_now_iso(),
                            )
                        )
                        repo.add_event(
                            event_id=hashlib.sha1(f'{latest_intent.intent_id}|intent_blocked|{blocked_reason}'.encode('utf-8')).hexdigest()[:32],
                            event_type=EVENT_INTENT_BLOCKED,
                            created_at_utc=utc_now_iso(),
                            intent_id=latest_intent.intent_id,
                            broker_name=latest_intent.broker_name,
                            account_mode=latest_intent.account_mode,
                            payload={'reason': blocked_reason, 'health': health.as_dict()},
                        )
                    else:
                        latest_intent, submitted = submit_intent(repo_root=repo_root, ctx=ctx, repo=repo, adapter=adapter, intent=latest_intent)

    post_result = None
    post_detail: dict[str, Any] = {}
    try:
        post_result, post_detail = reconcile_scope(repo_root=repo_root, ctx=ctx, adapter=adapter)
    except Exception as exc:
        post_detail = {'error': f'{type(exc).__name__}:{exc}'}
    if latest_intent is not None:
        latest_intent = repo.get_intent(latest_intent.intent_id) or latest_intent
    day = latest_intent.day if latest_intent is not None else signal_day_from_ts(int(time.time()), timezone_name=str(ctx.config.timezone))
    summary = repo.execution_summary(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, day=day)
    payload = {
        'enabled': True,
        'mode': str(_execution_cfg(ctx).get('mode') or 'paper'),
        'provider': str(_execution_cfg(ctx).get('provider') or 'fake'),
        'scope_tag': ctx.scope.scope_tag,
        'kill_switch_active': bool(kill_active),
        'drain_mode_active': bool(drain_active),
        'latest_trade': latest,
        'intent_created': bool(created),
        'latest_intent': latest_intent.as_dict() if latest_intent is not None else None,
        'submit_attempt': submitted.as_dict() if submitted is not None else None,
        'blocked_reason': blocked_reason,
        'pre_reconcile': {'summary': pre_result.as_dict() if pre_result is not None else None, 'detail': pre_detail},
        'post_reconcile': {'summary': post_result.as_dict() if post_result is not None else None, 'detail': post_detail},
        'execution_summary': summary,
    }
    _write_execution_artifacts(repo_root=repo_root, ctx=ctx, orders_payload=payload, reconcile_payload=payload.get('post_reconcile'))
    return payload


def orders_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None, limit: int = 20) -> dict[str, Any]:
    ctx = _build_context(repo_root=repo_root, config_path=config_path)
    repo = ExecutionRepository(execution_repo_path(repo_root))
    recent = repo.list_recent_intents(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, limit=limit)
    day = signal_day_from_ts(int(time.time()), timezone_name=str(ctx.config.timezone))
    payload = {
        'enabled': execution_enabled(ctx),
        'scope_tag': ctx.scope.scope_tag,
        'summary': repo.execution_summary(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, day=day),
        'recent_intents': [item.as_dict() for item in recent],
    }
    _write_execution_artifacts(repo_root=repo_root, ctx=ctx, orders_payload=payload)
    return payload


def reconcile_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any]:
    ctx = _build_context(repo_root=repo_root, config_path=config_path)
    if not execution_enabled(ctx):
        payload = {'enabled': False, 'scope_tag': ctx.scope.scope_tag, 'reason': 'execution_disabled'}
        _write_execution_artifacts(repo_root=repo_root, ctx=ctx, reconcile_payload=payload)
        return payload
    adapter = adapter_from_context(ctx, repo_root=repo_root)
    result, detail = reconcile_scope(repo_root=repo_root, ctx=ctx, adapter=adapter)
    payload = {'enabled': True, 'scope_tag': ctx.scope.scope_tag, 'summary': result.as_dict(), 'detail': detail}
    _write_execution_artifacts(repo_root=repo_root, ctx=ctx, reconcile_payload=payload)
    return payload


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Package N execution layer helper')
    p.add_argument('--repo-root', default='.')
    p.add_argument('--config', default=None)
    p.add_argument('--json', action='store_true')
    sub = p.add_subparsers(dest='command', required=False)
    sub.add_parser('process')
    sp_orders = sub.add_parser('orders')
    sp_orders.add_argument('--limit', type=int, default=20)
    sub.add_parser('reconcile')
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    cmd = ns.command or 'process'
    if cmd == 'orders':
        payload = orders_payload(repo_root=ns.repo_root, config_path=ns.config, limit=int(ns.limit))
    elif cmd == 'reconcile':
        payload = reconcile_payload(repo_root=ns.repo_root, config_path=ns.config)
    else:
        payload = process_latest_signal(repo_root=ns.repo_root, config_path=ns.config)
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str) if ns.json else json.dumps(payload, ensure_ascii=False, default=str))
    return 0
