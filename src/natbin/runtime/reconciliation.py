from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from typing import Any

from ..brokers.base import BrokerScope
from ..state.execution_repo import ExecutionRepository
from .execution_contracts import (
    BROKER_NOT_FOUND,
    BROKER_OPEN,
    EVENT_ORDER_CANCELLED,
    EVENT_ORDER_OPEN,
    EVENT_ORDER_SETTLED,
    EVENT_RECONCILE_AMBIGUOUS,
    EVENT_RECONCILE_MATCHED,
    EVENT_RECONCILE_ORPHAN,
    INTENT_ACCEPTED_OPEN,
    INTENT_AMBIGUOUS,
    INTENT_EXPIRED_UNCONFIRMED,
    INTENT_EXPIRED_UNSUBMITTED,
    INTENT_PLANNED,
    INTENT_REJECTED,
    INTENT_SETTLED,
    PENDING_INTENT_STATES,
)
from .execution_models import BrokerOrderSnapshot, OrderIntent, ReconciliationBatchResult
from .execution_policy import (
    ensure_utc_iso,
    intent_state_from_broker_status,
    json_dumps,
    match_fingerprint,
    parse_utc_iso,
    settlement_from_broker_status,
    utc_now,
    utc_now_iso,
)


def _event_id(*parts: object) -> str:
    seed = '|'.join(str(p) for p in parts)
    return hashlib.sha1(seed.encode('utf-8')).hexdigest()[:32]


def _candidate_snapshots(*, intent: OrderIntent, snapshots: list[BrokerOrderSnapshot], adapter) -> list[BrokerOrderSnapshot]:
    if intent.external_order_id:
        exact = adapter.fetch_order(str(intent.external_order_id))
        if exact is not None:
            return [exact]

    exact_key = [s for s in snapshots if s.client_order_key and str(s.client_order_key) == str(intent.client_order_key)]
    if exact_key:
        return exact_key

    matched = [
        s for s in snapshots
        if match_fingerprint(
            asset=intent.asset,
            side=intent.decision_action,
            amount=float(intent.stake_amount),
            expiry_ts=int(intent.expiry_ts),
            opened_at_utc=intent.submitted_at_utc,
            snapshot_asset=s.asset,
            snapshot_side=s.side,
            snapshot_amount=float(s.amount),
            snapshot_expires_at_utc=s.expires_at_utc,
            delta_sec=15,
        )
    ]
    return matched


def _apply_snapshot(repo: ExecutionRepository, intent: OrderIntent, snapshot: BrokerOrderSnapshot) -> OrderIntent:
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
        event_id=_event_id(updated.intent_id, snapshot.external_order_id, snapshot.broker_status, snapshot.last_seen_at_utc),
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
            event_id=_event_id(updated.intent_id, 'open', snapshot.external_order_id),
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
            event_id=_event_id(updated.intent_id, 'settled', snapshot.external_order_id, snapshot.closed_at_utc),
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
            event_id=_event_id(updated.intent_id, 'cancelled', snapshot.external_order_id, snapshot.closed_at_utc),
            event_type=EVENT_ORDER_CANCELLED,
            created_at_utc=now_iso,
            intent_id=updated.intent_id,
            broker_name=updated.broker_name,
            account_mode=updated.account_mode,
            external_order_id=snapshot.external_order_id,
            payload=snapshot.as_dict(),
        )
    return updated


def reconcile_scope(*, repo_root: str | Path, ctx, adapter, now_utc: datetime | None = None) -> tuple[ReconciliationBatchResult, dict[str, Any]]:
    now = now_utc or utc_now()
    repo = ExecutionRepository(Path(repo_root) / 'runs' / 'runtime_execution.sqlite3')
    exec_cfg_raw = ctx.resolved_config.get('execution') if isinstance(ctx.resolved_config, dict) else getattr(ctx.resolved_config, 'execution', None)
    if hasattr(exec_cfg_raw, 'model_dump'):
        exec_cfg_raw = exec_cfg_raw.model_dump(mode='python')
    exec_cfg = dict(exec_cfg_raw or {})
    scope = BrokerScope(
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        scope_tag=str(ctx.scope.scope_tag),
        account_mode=str(exec_cfg.get('account_mode') or 'PRACTICE').upper(),
    )
    reconcile_cfg = (ctx.resolved_config.get('execution') or {}).get('reconcile') if isinstance(ctx.resolved_config, dict) else None
    if reconcile_cfg is None and hasattr(ctx.resolved_config.get('execution', None), 'reconcile'):
        reconcile_cfg = ctx.resolved_config['execution'].reconcile.model_dump(mode='python')  # type: ignore[index]
    reconcile_cfg = dict(reconcile_cfg or {})
    history_lookback_sec = int(reconcile_cfg.get('history_lookback_sec') or 3600)
    not_found_grace_sec = int(reconcile_cfg.get('not_found_grace_sec') or 20)
    settle_grace_sec = int(reconcile_cfg.get('settle_grace_sec') or 30)

    open_orders = adapter.fetch_open_orders(scope)
    closed_orders = adapter.fetch_closed_orders(scope, since_utc=now - timedelta(seconds=max(60, history_lookback_sec)))
    all_snapshots = open_orders + closed_orders
    snapshot_by_external = {s.external_order_id: s for s in all_snapshots}

    pending = repo.list_pending_intents(asset=scope.asset, interval_sec=scope.interval_sec, states=list(PENDING_INTENT_STATES))
    pending_before = len(pending)
    matched_external_ids: set[str] = set()
    updated_intents = 0
    terminalized = 0
    ambiguous_matches = 0
    errors: list[str] = []

    for intent in pending:
        try:
            candidates = _candidate_snapshots(intent=intent, snapshots=all_snapshots, adapter=adapter)
            unique: dict[str, BrokerOrderSnapshot] = {}
            for snap in candidates:
                unique[snap.external_order_id] = snap
            candidates = list(unique.values())
            if len(candidates) == 1:
                snap = candidates[0]
                matched_external_ids.add(snap.external_order_id)
                before_state = intent.intent_state
                after = _apply_snapshot(repo, intent, snap)
                updated_intents += 1
                if after.intent_state in {INTENT_SETTLED, INTENT_REJECTED} and after.intent_state != before_state:
                    terminalized += 1
                continue
            if len(candidates) > 1:
                now_iso = utc_now_iso()
                ambiguous = replace(
                    intent,
                    intent_state=INTENT_AMBIGUOUS,
                    broker_status='unknown',
                    updated_at_utc=now_iso,
                    last_reconcile_at_utc=now_iso,
                    last_error_code='ambiguous_match',
                    last_error_message=f'ambiguous_matches={len(candidates)}',
                )
                repo.save_intent(ambiguous)
                repo.add_event(
                    event_id=_event_id(intent.intent_id, 'ambiguous', now_iso),
                    event_type=EVENT_RECONCILE_AMBIGUOUS,
                    created_at_utc=now_iso,
                    intent_id=intent.intent_id,
                    broker_name=intent.broker_name,
                    account_mode=intent.account_mode,
                    payload={'candidate_external_order_ids': [c.external_order_id for c in candidates]},
                )
                ambiguous_matches += 1
                terminalized += 1
                continue

            # No match.
            now_iso = utc_now_iso()
            deadline = parse_utc_iso(intent.entry_deadline_utc)
            submitted_at = parse_utc_iso(intent.submitted_at_utc)
            expiry_at = datetime.fromtimestamp(int(intent.expiry_ts), tz=UTC)
            if intent.intent_state == INTENT_PLANNED and deadline is not None and now >= deadline:
                expired = replace(
                    intent,
                    intent_state=INTENT_EXPIRED_UNSUBMITTED,
                    broker_status=BROKER_NOT_FOUND,
                    updated_at_utc=now_iso,
                    last_reconcile_at_utc=now_iso,
                    last_error_code='entry_deadline_passed',
                    last_error_message='planned intent expired without submit',
                )
                repo.save_intent(expired)
                terminalized += 1
            elif intent.intent_state in {'submitted_unknown', INTENT_ACCEPTED_OPEN}:
                grace_anchor = submitted_at or expiry_at
                grace = not_found_grace_sec if intent.intent_state == 'submitted_unknown' else settle_grace_sec
                if grace_anchor is not None and now >= grace_anchor + timedelta(seconds=max(1, grace)):
                    missing = replace(
                        intent,
                        intent_state=INTENT_EXPIRED_UNCONFIRMED,
                        broker_status=BROKER_NOT_FOUND,
                        updated_at_utc=now_iso,
                        last_reconcile_at_utc=now_iso,
                        last_error_code='broker_not_found',
                        last_error_message='reconciliation grace expired without broker match',
                    )
                    repo.save_intent(missing)
                    terminalized += 1
        except Exception as exc:
            errors.append(f'{intent.intent_id}:{type(exc).__name__}:{exc}')

    new_orphans = 0
    for snap in all_snapshots:
        if snap.external_order_id in matched_external_ids:
            continue
        existing = repo.get_broker_order(
            broker_name=snap.broker_name,
            account_mode=snap.account_mode,
            external_order_id=snap.external_order_id,
        )
        intent = None
        if snap.client_order_key:
            recent = repo.list_recent_intents(asset=scope.asset, interval_sec=scope.interval_sec, limit=500)
            for item in recent:
                if str(item.client_order_key) == str(snap.client_order_key):
                    intent = item
                    break
        if intent is not None:
            repo.upsert_broker_snapshot(snap, intent_id=intent.intent_id)
            continue
        repo.upsert_broker_snapshot(snap, intent_id=None)
        if existing is None:
            new_orphans += 1
            repo.add_event(
                event_id=_event_id('orphan', snap.external_order_id, snap.last_seen_at_utc),
                event_type=EVENT_RECONCILE_ORPHAN,
                created_at_utc=utc_now_iso(),
                broker_name=snap.broker_name,
                account_mode=snap.account_mode,
                external_order_id=snap.external_order_id,
                payload=snap.as_dict(),
            )

    result = ReconciliationBatchResult(
        scope_tag=scope.scope_tag,
        started_at_utc=ensure_utc_iso(now) or utc_now_iso(),
        finished_at_utc=utc_now_iso(),
        pending_before=pending_before,
        updated_intents=updated_intents,
        new_orphans=new_orphans,
        ambiguous_matches=ambiguous_matches,
        terminalized=terminalized,
        errors=errors,
    )
    detail = {
        'scope_tag': scope.scope_tag,
        'asset': scope.asset,
        'interval_sec': scope.interval_sec,
        'open_orders_seen': len(open_orders),
        'closed_orders_seen': len(closed_orders),
        'pending_before': pending_before,
        'updated_intents': updated_intents,
        'new_orphans': new_orphans,
        'ambiguous_matches': ambiguous_matches,
        'terminalized': terminalized,
        'errors': errors,
    }
    return result, detail
