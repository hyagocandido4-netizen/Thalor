from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping

from .correlation import resolve_correlation_descriptor
from .models import AssetQuota, PortfolioQuota, PortfolioScope


@dataclass(frozen=True)
class ExecutionPlanItem:
    scope_tag: str
    asset: str
    interval_sec: int
    correlation_group: str
    order_index: int
    stagger_delay_sec: float
    scheduled_at_utc: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)



def _utc_now(now_utc: datetime | None = None) -> datetime:
    if now_utc is None:
        return datetime.now(tz=UTC)
    if now_utc.tzinfo is None:
        return now_utc.replace(tzinfo=UTC)
    return now_utc.astimezone(UTC)



def build_execution_plan(
    *,
    selected: Iterable[Mapping[str, Any]],
    scopes: Iterable[PortfolioScope],
    stagger_sec: float,
    now_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    scope_by_tag = {str(s.scope_tag): s for s in scopes}
    base_now = _utc_now(now_utc)
    delay = max(0.0, float(stagger_sec or 0.0))
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(list(selected or [])):
        scope_tag = str(item.get('scope_tag') or '')
        scope = scope_by_tag.get(scope_tag)
        if scope is None:
            asset = str(item.get('asset') or '')
            interval_sec = int(item.get('interval_sec') or 0)
            cluster_key = str(item.get('cluster_key') or '').strip() or None
        else:
            asset = str(scope.asset)
            interval_sec = int(scope.interval_sec)
            cluster_key = str(scope.cluster_key or '').strip() or None
        descriptor = resolve_correlation_descriptor(asset=asset, cluster_key=cluster_key)
        total_delay = float(idx) * delay
        scheduled = base_now + timedelta(seconds=total_delay)
        out.append(
            ExecutionPlanItem(
                scope_tag=scope_tag,
                asset=asset,
                interval_sec=interval_sec,
                correlation_group=str(descriptor.correlation_group),
                order_index=int(idx),
                stagger_delay_sec=float(total_delay),
                scheduled_at_utc=scheduled.isoformat(timespec='seconds'),
            ).as_dict()
        )
    return out



def _candidate_by_scope(latest_cycle: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    if not isinstance(latest_cycle, Mapping):
        return out
    for item in list(latest_cycle.get('candidates') or []):
        if not isinstance(item, Mapping):
            continue
        scope_tag = str(item.get('scope_tag') or '')
        if scope_tag:
            out[scope_tag] = item
    return out



def _execution_by_scope(latest_cycle: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    if not isinstance(latest_cycle, Mapping):
        return out
    for item in list(latest_cycle.get('execution') or []):
        if not isinstance(item, Mapping):
            continue
        scope_tag = str(item.get('scope_tag') or '')
        if scope_tag:
            out[scope_tag] = item
    return out



def _plan_by_scope(latest_cycle: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    if not isinstance(latest_cycle, Mapping):
        return out
    for item in list(latest_cycle.get('execution_plan') or []):
        if not isinstance(item, Mapping):
            continue
        scope_tag = str(item.get('scope_tag') or '')
        if scope_tag:
            out[scope_tag] = item
    return out



def _selected_by_scope(latest_allocation: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    if not isinstance(latest_allocation, Mapping):
        return out
    for item in list(latest_allocation.get('selected') or []):
        if not isinstance(item, Mapping):
            continue
        scope_tag = str(item.get('scope_tag') or '')
        if scope_tag:
            out[scope_tag] = item
    return out



def _suppressed_by_scope(latest_allocation: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    if not isinstance(latest_allocation, Mapping):
        return out
    for item in list(latest_allocation.get('suppressed') or []):
        if not isinstance(item, Mapping):
            continue
        scope_tag = str(item.get('scope_tag') or '')
        if scope_tag:
            out[scope_tag] = item
    return out



def build_asset_board(
    *,
    scopes: Iterable[PortfolioScope],
    asset_quotas: Iterable[AssetQuota],
    portfolio_quota: PortfolioQuota | None,
    latest_cycle: Mapping[str, Any] | None,
    latest_allocation: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    scope_list = list(scopes)
    quota_by_tag = {str(q.scope_tag): q for q in list(asset_quotas or [])}
    cand_by_tag = _candidate_by_scope(latest_cycle)
    exec_by_tag = _execution_by_scope(latest_cycle)
    plan_by_tag = _plan_by_scope(latest_cycle)
    selected_by_tag = _selected_by_scope(latest_allocation)
    suppressed_by_tag = _suppressed_by_scope(latest_allocation)

    rows: list[dict[str, Any]] = []
    for scope in scope_list:
        quota = quota_by_tag.get(str(scope.scope_tag))
        cand = cand_by_tag.get(str(scope.scope_tag)) or {}
        execution = exec_by_tag.get(str(scope.scope_tag)) or {}
        selected = selected_by_tag.get(str(scope.scope_tag))
        suppressed = suppressed_by_tag.get(str(scope.scope_tag))
        plan = plan_by_tag.get(str(scope.scope_tag)) or {}
        descriptor = resolve_correlation_descriptor(asset=str(scope.asset), cluster_key=str(scope.cluster_key or '').strip() or None)
        payload = execution.get('payload') if isinstance(execution, Mapping) else None
        intent = payload.get('intent') if isinstance(payload, Mapping) else None
        rows.append(
            {
                'scope_tag': str(scope.scope_tag),
                'asset': str(scope.asset),
                'interval_sec': int(scope.interval_sec),
                'weight': float(scope.weight),
                'configured_cluster_key': str(scope.cluster_key or 'default'),
                'correlation_group': str(descriptor.correlation_group),
                'correlation_source': str(descriptor.source),
                'quota_kind': str(quota.kind) if quota is not None else None,
                'quota_reason': str(quota.reason) if quota is not None else None,
                'budget_left': int(quota.budget_left) if quota is not None else None,
                'pending_unknown': int(quota.pending_unknown) if quota is not None else None,
                'open_positions': int(quota.open_positions) if quota is not None else None,
                'latest_action': cand.get('action'),
                'latest_reason': cand.get('reason'),
                'selected': bool(selected is not None),
                'selected_reason': (selected or suppressed or {}).get('reason'),
                'allocation_rank': (selected or suppressed or {}).get('rank'),
                'execution_returncode': (execution.get('outcome') or {}).get('returncode') if isinstance(execution, Mapping) else None,
                'execution_intent_state': intent.get('intent_state') if isinstance(intent, Mapping) else None,
                'execution_submit_status': payload.get('submit_transport_status') if isinstance(payload, Mapping) else None,
                'execution_stagger_delay_sec': plan.get('stagger_delay_sec'),
                'execution_scheduled_at_utc': plan.get('scheduled_at_utc'),
                'portfolio_open_positions_total': int(portfolio_quota.open_positions_total) if portfolio_quota is not None else None,
                'portfolio_pending_unknown_total': int(portfolio_quota.pending_unknown_total) if portfolio_quota is not None else None,
            }
        )
    rows.sort(key=lambda item: (0 if item.get('selected') else 1, str(item.get('scope_tag') or '')))
    return rows


__all__ = ['ExecutionPlanItem', 'build_asset_board', 'build_execution_plan']
