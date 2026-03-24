from __future__ import annotations

from dataclasses import replace
import hashlib

from ..state.execution_repo import ExecutionRepository
from .execution_contracts import (
    BROKER_OPEN,
    EVENT_ORDER_CANCELLED,
    EVENT_ORDER_OPEN,
    EVENT_ORDER_SETTLED,
    EVENT_RECONCILE_MATCHED,
    INTENT_REJECTED,
    INTENT_SETTLED,
)
from .execution_models import BrokerOrderSnapshot, OrderIntent
from .execution_policy import (
    intent_state_from_broker_status,
    match_fingerprint,
    settlement_from_broker_status,
    utc_now_iso,
)



def event_id(*parts: object) -> str:
    seed = '|'.join(str(p) for p in parts)
    return hashlib.sha1(seed.encode('utf-8')).hexdigest()[:32]



def candidate_snapshots(*, intent: OrderIntent, snapshots: list[BrokerOrderSnapshot], adapter) -> list[BrokerOrderSnapshot]:
    if intent.external_order_id:
        exact = adapter.fetch_order(str(intent.external_order_id))
        if exact is not None:
            return [exact]

    exact_key = [snapshot for snapshot in snapshots if snapshot.client_order_key and str(snapshot.client_order_key) == str(intent.client_order_key)]
    if exact_key:
        return exact_key

    return [
        snapshot
        for snapshot in snapshots
        if match_fingerprint(
            asset=intent.asset,
            side=intent.decision_action,
            amount=float(intent.stake_amount),
            expiry_ts=int(intent.expiry_ts),
            opened_at_utc=intent.submitted_at_utc,
            snapshot_asset=snapshot.asset,
            snapshot_side=snapshot.side,
            snapshot_amount=float(snapshot.amount),
            snapshot_expires_at_utc=snapshot.expires_at_utc,
            delta_sec=15,
        )
    ]



def apply_snapshot(repo: ExecutionRepository, intent: OrderIntent, snapshot: BrokerOrderSnapshot) -> OrderIntent:
    now_iso = utc_now_iso()
    new_state = intent_state_from_broker_status(snapshot.broker_status)
    settlement = settlement_from_broker_status(snapshot.broker_status)
    updated = replace(
        intent,
        intent_state=new_state if new_state != 'unknown' else intent.intent_state,
        broker_status=snapshot.broker_status,
        settlement_status=settlement,
        external_order_id=snapshot.external_order_id,
        updated_at_utc=now_iso,
        last_reconcile_at_utc=now_iso,
        submitted_at_utc=intent.submitted_at_utc or snapshot.opened_at_utc or now_iso,
        accepted_at_utc=intent.accepted_at_utc or snapshot.opened_at_utc,
        settled_at_utc=(snapshot.closed_at_utc if new_state in {INTENT_SETTLED, INTENT_REJECTED} else intent.settled_at_utc),
        last_error_code=None,
        last_error_message=None,
    )
    updated = repo.save_intent(updated)
    repo.upsert_broker_snapshot(snapshot, intent_id=updated.intent_id)
    repo.add_event(
        event_id=event_id(updated.intent_id, snapshot.external_order_id, snapshot.broker_status, snapshot.last_seen_at_utc),
        event_type=EVENT_RECONCILE_MATCHED,
        created_at_utc=now_iso,
        intent_id=updated.intent_id,
        broker_name=updated.broker_name,
        account_mode=updated.account_mode,
        external_order_id=snapshot.external_order_id,
        payload={'intent_state': updated.intent_state, 'broker_status': snapshot.broker_status, 'settlement_status': settlement},
    )
    if snapshot.broker_status == BROKER_OPEN:
        repo.add_event(
            event_id=event_id(updated.intent_id, 'open', snapshot.external_order_id),
            event_type=EVENT_ORDER_OPEN,
            created_at_utc=now_iso,
            intent_id=updated.intent_id,
            broker_name=updated.broker_name,
            account_mode=updated.account_mode,
            external_order_id=snapshot.external_order_id,
            payload=snapshot.as_dict(),
        )
    elif updated.intent_state == INTENT_SETTLED:
        repo.add_event(
            event_id=event_id(updated.intent_id, 'settled', snapshot.external_order_id, snapshot.closed_at_utc),
            event_type=EVENT_ORDER_SETTLED,
            created_at_utc=now_iso,
            intent_id=updated.intent_id,
            broker_name=updated.broker_name,
            account_mode=updated.account_mode,
            external_order_id=snapshot.external_order_id,
            payload=snapshot.as_dict(),
        )
    elif updated.intent_state == INTENT_REJECTED:
        repo.add_event(
            event_id=event_id(updated.intent_id, 'cancelled', snapshot.external_order_id, snapshot.closed_at_utc),
            event_type=EVENT_ORDER_CANCELLED,
            created_at_utc=now_iso,
            intent_id=updated.intent_id,
            broker_name=updated.broker_name,
            account_mode=updated.account_mode,
            external_order_id=snapshot.external_order_id,
            payload=snapshot.as_dict(),
        )
    return updated


__all__ = ['apply_snapshot', 'candidate_snapshots', 'event_id']
