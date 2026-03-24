from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..portfolio.latest import load_portfolio_latest_payload
from ..state.repos import SignalsRepository
from .broker_surface import execution_cfg, signals_repo_db_path
from .execution_contracts import INTENT_PLANNED
from .execution_models import OrderIntent
from .execution_policy import (
    compute_entry_deadline_utc,
    compute_expiry_ts,
    json_dumps,
    make_client_order_key,
    make_intent_id,
    signal_day_from_ts,
    utc_now_iso,
)



def float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None



def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None



def json_object_or_none(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ''):
        return None
    try:
        obj = json.loads(str(value))
    except Exception:
        return None
    return dict(obj) if isinstance(obj, dict) else None



def selected_allocation_metadata(*, repo_root: str | Path, ctx) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    cfg_path = getattr(getattr(ctx, 'config', None), 'config_path', None)
    resolved = dict(getattr(ctx, 'resolved_config', {}) or {})
    profile = str(resolved.get('profile') or '').strip() or None
    payload, _source = load_portfolio_latest_payload(
        repo_root,
        name='portfolio_allocation_latest.json',
        config_path=cfg_path,
        profile=profile,
        allow_legacy_fallback=True,
    )
    if not isinstance(payload, dict):
        return None, None

    selected = payload.get('selected') or []
    if not isinstance(selected, list):
        return payload, None

    scope_tag = str(getattr(getattr(ctx, 'scope', None), 'scope_tag', '') or '').strip()
    asset = str(getattr(getattr(ctx, 'config', None), 'asset', '') or '').strip()
    try:
        interval_sec = int(getattr(getattr(ctx, 'config', None), 'interval_sec', 0) or 0)
    except Exception:
        interval_sec = 0

    for item in selected:
        if not isinstance(item, dict):
            continue
        if scope_tag and str(item.get('scope_tag') or '').strip() != scope_tag:
            continue
        if asset and str(item.get('asset') or '').strip() != asset:
            continue
        try:
            item_interval = int(item.get('interval_sec') or 0)
        except Exception:
            item_interval = 0
        if interval_sec and item_interval != interval_sec:
            continue
        return payload, dict(item)
    return payload, None



def latest_trade_row(*, repo_root: str | Path, ctx) -> dict[str, Any] | None:
    repo = SignalsRepository(signals_repo_db_path(repo_root), default_interval=int(ctx.config.interval_sec))
    days = [signal_day_from_ts(int(time.time()), timezone_name=str(ctx.config.timezone))]
    for day in repo.distinct_recent_days(3):
        if day not in days:
            days.append(day)
    latest: dict[str, Any] | None = None
    for day in days:
        rows = repo.fetch_trade_rows(str(ctx.config.asset), int(ctx.config.interval_sec), str(day))
        for row in rows:
            latest = dict(row)
    return latest



def intent_from_signal_row(*, row: dict[str, Any], ctx, repo_root: str | Path | None = None) -> OrderIntent:
    cfg = execution_cfg(ctx)
    stake = dict(cfg.get('stake') or {})
    broker_name = str(cfg.get('provider') or 'fake').strip().lower()
    account_mode = str(cfg.get('account_mode') or 'PRACTICE').upper()
    day = str(row.get('day') or signal_day_from_ts(int(row.get('ts') or 0), timezone_name=str(ctx.config.timezone)))
    signal_ts = int(row.get('ts') or 0)
    action = str(row.get('action') or '').upper()
    allocation_payload = None
    allocation_item = None
    if repo_root is not None:
        allocation_payload, allocation_item = selected_allocation_metadata(repo_root=repo_root, ctx=ctx)

    portfolio_score = float_or_none(row.get('portfolio_score'))
    if portfolio_score is None and isinstance(allocation_item, dict):
        portfolio_score = float_or_none(allocation_item.get('portfolio_score'))

    intelligence_score = float_or_none(row.get('intelligence_score'))
    if intelligence_score is None and isinstance(allocation_item, dict):
        intelligence_score = float_or_none(allocation_item.get('intelligence_score'))

    retrain_state = str(row.get('retrain_state') or '').strip() or None
    if retrain_state is None and isinstance(allocation_item, dict):
        retrain_state = str(allocation_item.get('retrain_state') or '').strip() or None

    retrain_priority = str(row.get('retrain_priority') or '').strip() or None
    if retrain_priority is None and isinstance(allocation_item, dict):
        retrain_priority = str(allocation_item.get('retrain_priority') or '').strip() or None

    portfolio_feedback = json_object_or_none(row.get('portfolio_feedback')) or json_object_or_none(row.get('portfolio_feedback_json'))
    if portfolio_feedback is None and isinstance(allocation_item, dict):
        portfolio_feedback = json_object_or_none(allocation_item.get('portfolio_feedback'))

    cluster_key = str(row.get('cluster_key') or '').strip() or None
    if cluster_key is None and isinstance(allocation_item, dict) and allocation_item.get('cluster_key') is not None:
        cluster_key = str(allocation_item.get('cluster_key') or '').strip() or None

    allocation_batch_id = str(row.get('allocation_batch_id') or '').strip() or None
    if allocation_batch_id is None and isinstance(allocation_payload, dict) and allocation_payload.get('allocation_id') is not None:
        allocation_batch_id = str(allocation_payload.get('allocation_id') or '').strip() or None

    allocation_reason = str(row.get('allocation_reason') or '').strip() or None
    if allocation_reason is None and isinstance(allocation_item, dict):
        allocation_reason = str(allocation_item.get('reason') or '').strip() or None

    allocation_rank = int_or_none(row.get('allocation_rank'))
    if allocation_rank is None:
        allocation_rank = int_or_none(row.get('rank'))
    if allocation_rank is None and isinstance(allocation_item, dict):
        allocation_rank = int_or_none(allocation_item.get('rank'))

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
        allocation_batch_id=allocation_batch_id,
        cluster_key=cluster_key,
        portfolio_score=portfolio_score,
        intelligence_score=intelligence_score,
        retrain_state=retrain_state,
        retrain_priority=retrain_priority,
        allocation_reason=allocation_reason,
        allocation_rank=allocation_rank,
        portfolio_feedback_json=json_dumps(portfolio_feedback) if portfolio_feedback is not None else None,
    )


__all__ = [
    'float_or_none',
    'int_or_none',
    'intent_from_signal_row',
    'json_object_or_none',
    'latest_trade_row',
    'selected_allocation_metadata',
]
