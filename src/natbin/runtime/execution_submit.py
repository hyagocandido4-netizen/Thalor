from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from pathlib import Path

from ..state.execution_repo import ExecutionRepository
from .execution_contracts import (
    EVENT_SUBMIT_ACKED,
    EVENT_SUBMIT_EXCEPTION,
    EVENT_SUBMIT_REJECTED,
    EVENT_SUBMIT_REQUESTED,
    EVENT_SUBMIT_TIMEOUT,
    INTENT_ACCEPTED_OPEN,
    INTENT_REJECTED,
    INTENT_SUBMITTED_UNKNOWN,
    TRANSPORT_ACK,
    TRANSPORT_EXCEPTION,
    TRANSPORT_REJECT,
    TRANSPORT_TIMEOUT,
)
from .execution_models import OrderIntent, OrderSubmitAttempt, SubmitOrderRequest
from .execution_policy import json_dumps, make_attempt_id, utc_now, utc_now_iso
from .execution_signal import json_object_or_none



def submit_intent(*, repo_root: str | Path, ctx, repo: ExecutionRepository, adapter, intent: OrderIntent) -> tuple[OrderIntent, OrderSubmitAttempt]:
    now = utc_now()
    now_iso = now.isoformat(timespec='seconds')
    attempt_no = repo.next_attempt_no(intent.intent_id)
    portfolio_feedback = json_object_or_none(intent.portfolio_feedback_json)
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
        metadata={
            'scope_tag': intent.scope_tag,
            'signal_ts': intent.signal_ts,
            'allocation_batch_id': intent.allocation_batch_id,
            'cluster_key': intent.cluster_key,
            'portfolio_score': intent.portfolio_score,
            'intelligence_score': intent.intelligence_score,
            'retrain_state': intent.retrain_state,
            'retrain_priority': intent.retrain_priority,
            'allocation_reason': intent.allocation_reason,
            'allocation_rank': intent.allocation_rank,
            'portfolio_feedback': portfolio_feedback,
        },
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
    response: dict[str, object] = {}
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
    try:
        from ..security.broker_guard import note_submit_attempt

        note_submit_attempt(repo_root=repo_root, ctx=ctx, transport_status=transport_status)
    except Exception:
        pass
    try:
        from ..security.account_protection import note_protection_submit_attempt

        note_protection_submit_attempt(
            repo_root=repo_root,
            ctx=ctx,
            cluster_key=updated.cluster_key,
            transport_status=transport_status,
        )
    except Exception:
        pass
    return updated, attempt


__all__ = ['submit_intent']
