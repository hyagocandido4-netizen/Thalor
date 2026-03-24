from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config.env import env_bool
from ..state.execution_repo import ExecutionRepository
from .broker_surface import execution_repo_path, reconcile_cfg, scope_from_context
from .execution_contracts import (
    BROKER_NOT_FOUND,
    EVENT_RECONCILE_AMBIGUOUS,
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
from .execution_models import BrokerOrderSnapshot, ReconciliationBatchResult
from .execution_policy import ensure_utc_iso, parse_utc_iso, utc_now, utc_now_iso
from .reconciliation_core import apply_snapshot, candidate_snapshots, event_id



def reconcile_scope(*, repo_root: str | Path, ctx, adapter, now_utc: datetime | None = None) -> tuple[ReconciliationBatchResult, dict[str, Any]]:
    now = now_utc or utc_now()
    repo = ExecutionRepository(execution_repo_path(repo_root))
    scope = scope_from_context(ctx)
    cfg = reconcile_cfg(ctx)
    history_lookback_sec = int(cfg.get('history_lookback_sec') or 3600)
    not_found_grace_sec = int(cfg.get('not_found_grace_sec') or 20)
    settle_grace_sec = int(cfg.get('settle_grace_sec') or 30)

    pending = repo.list_pending_intents(asset=scope.asset, interval_sec=scope.interval_sec, states=list(PENDING_INTENT_STATES))
    pending_before = len(pending)
    scan_without_pending = bool(cfg.get('scan_without_pending', False)) or bool(env_bool('THALOR_RECONCILE_SCAN_WITHOUT_PENDING', False))
    if pending_before == 0 and not scan_without_pending:
        started = utc_now_iso()
        finished = utc_now_iso()
        result = ReconciliationBatchResult(
            scope_tag=scope.scope_tag,
            started_at_utc=started,
            finished_at_utc=finished,
            pending_before=0,
            updated_intents=0,
            new_orphans=0,
            ambiguous_matches=0,
            terminalized=0,
            errors=[],
        )
        return result, {
            'scope_tag': scope.scope_tag,
            'pending_before': 0,
            'pending_after': 0,
            'matched_external_ids': [],
            'errors': [],
            'skipped_broker_scan': True,
            'reason': 'no_pending_intents',
            'scan_without_pending': False,
            'open_orders_count': 0,
            'closed_orders_count': 0,
        }

    open_orders = adapter.fetch_open_orders(scope)
    closed_orders = adapter.fetch_closed_orders(scope, since_utc=now - timedelta(seconds=max(60, history_lookback_sec)))
    all_snapshots = open_orders + closed_orders
    matched_external_ids: set[str] = set()
    updated_intents = 0
    terminalized = 0
    ambiguous_matches = 0
    errors: list[str] = []

    for intent in pending:
        try:
            candidates = candidate_snapshots(intent=intent, snapshots=all_snapshots, adapter=adapter)
            unique: dict[str, BrokerOrderSnapshot] = {}
            for snapshot in candidates:
                unique[snapshot.external_order_id] = snapshot
            candidates = list(unique.values())
            if len(candidates) == 1:
                snapshot = candidates[0]
                matched_external_ids.add(snapshot.external_order_id)
                before_state = intent.intent_state
                after = apply_snapshot(repo, intent, snapshot)
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
                    event_id=event_id(intent.intent_id, 'ambiguous', now_iso),
                    event_type=EVENT_RECONCILE_AMBIGUOUS,
                    created_at_utc=now_iso,
                    intent_id=intent.intent_id,
                    broker_name=intent.broker_name,
                    account_mode=intent.account_mode,
                    payload={'candidate_external_order_ids': [candidate.external_order_id for candidate in candidates]},
                )
                ambiguous_matches += 1
                terminalized += 1
                continue

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
    for snapshot in all_snapshots:
        if snapshot.external_order_id in matched_external_ids:
            continue
        existing = repo.get_broker_order(
            broker_name=snapshot.broker_name,
            account_mode=snapshot.account_mode,
            external_order_id=snapshot.external_order_id,
        )
        intent = None
        if snapshot.client_order_key:
            recent = repo.list_recent_intents(asset=scope.asset, interval_sec=scope.interval_sec, limit=500)
            for item in recent:
                if str(item.client_order_key) == str(snapshot.client_order_key):
                    intent = item
                    break
        if intent is not None:
            repo.upsert_broker_snapshot(snapshot, intent_id=intent.intent_id)
            continue
        repo.upsert_broker_snapshot(snapshot, intent_id=None)
        if existing is None:
            new_orphans += 1
            repo.add_event(
                event_id=event_id('orphan', snapshot.external_order_id, snapshot.last_seen_at_utc),
                event_type=EVENT_RECONCILE_ORPHAN,
                created_at_utc=utc_now_iso(),
                broker_name=snapshot.broker_name,
                account_mode=snapshot.account_mode,
                external_order_id=snapshot.external_order_id,
                payload=snapshot.as_dict(),
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


__all__ = ['reconcile_scope']
