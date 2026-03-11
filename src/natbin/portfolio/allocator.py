from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Mapping

from ..config.loader import load_thalor_config

from .models import AllocationItem, AssetQuota, CandidateDecision, PortfolioAllocation, PortfolioQuota, PortfolioScope


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _allocation_id(*, at_utc: str, scopes: list[PortfolioScope]) -> str:
    h = hashlib.sha1()
    h.update(at_utc.encode('utf-8'))
    for s in scopes:
        h.update(f"{s.scope_tag}|{s.weight}|{s.cluster_key}".encode('utf-8'))
    return h.hexdigest()[:16]


def _scope_weight(scope_by_tag: Mapping[str, PortfolioScope], scope_tag: str) -> float:
    s = scope_by_tag.get(scope_tag)
    try:
        return float(s.weight) if s is not None else 1.0
    except Exception:
        return 1.0


def _scope_cluster(scope_by_tag: Mapping[str, PortfolioScope], scope_tag: str) -> str:
    s = scope_by_tag.get(scope_tag)
    return str(getattr(s, 'cluster_key', 'default') or 'default')


def _item(
    *,
    scope_by_tag: Mapping[str, PortfolioScope],
    candidate: CandidateDecision,
    rank_value: float,
    selected: bool,
    reason: str,
    rank: int | None = None,
    risk_context: dict[str, Any] | None = None,
) -> AllocationItem:
    return AllocationItem(
        scope_tag=candidate.scope_tag,
        asset=candidate.asset,
        interval_sec=int(candidate.interval_sec),
        action=str(candidate.action or 'HOLD').upper(),
        score=candidate.score,
        conf=candidate.conf,
        ev=candidate.ev,
        intelligence_score=candidate.intelligence_score,
        learned_gate_prob=candidate.learned_gate_prob,
        slot_multiplier=candidate.slot_multiplier,
        drift_level=candidate.drift_level,
        coverage_bias=candidate.coverage_bias,
        rank_value=float(rank_value),
        selected=bool(selected),
        reason=str(reason),
        rank=int(rank) if rank is not None else None,
        cluster_key=_scope_cluster(scope_by_tag, candidate.scope_tag),
        risk_context=dict(risk_context or {}) or None,
        intelligence=dict(candidate.intelligence or {}) or None,
    )


def allocate(
    repo_root: str,
    *,
    scopes: list[PortfolioScope],
    candidates: list[CandidateDecision],
    asset_quotas: list[AssetQuota],
    portfolio_quota: PortfolioQuota,
    failsafe_blocks: Mapping[str, str] | None = None,
    config_path: str | None = None,
    prefer_ev: bool = True,
) -> PortfolioAllocation:
    cfg = load_thalor_config(config_path=config_path, repo_root=repo_root)

    at_utc = _utc_now_iso()
    alloc_id = _allocation_id(at_utc=at_utc, scopes=scopes)

    max_select = 1
    try:
        max_select = int(cfg.multi_asset.portfolio_topk_total)
    except Exception:
        max_select = 1
    max_select = max(0, int(max_select))

    max_per_cluster = 1
    try:
        max_per_cluster = int(getattr(cfg.multi_asset, 'max_trades_per_cluster_per_cycle', 1))
    except Exception:
        max_per_cluster = 1
    max_per_cluster = max(1, int(max_per_cluster))

    scope_by_tag: dict[str, PortfolioScope] = {s.scope_tag: s for s in scopes}
    quota_by_tag: dict[str, AssetQuota] = {q.scope_tag: q for q in asset_quotas}
    failsafe_blocks = dict(failsafe_blocks or {})

    selected: list[AllocationItem] = []
    suppressed: list[AllocationItem] = []

    correlation_filter_enable = bool(getattr(portfolio_quota, 'correlation_filter_enable', True))
    max_open_per_asset = getattr(portfolio_quota, 'hard_max_positions_per_asset', None)
    max_open_per_cluster = getattr(portfolio_quota, 'hard_max_positions_per_cluster', None)

    open_by_asset = dict(getattr(portfolio_quota, 'open_positions_by_asset', {}) or {})
    pending_by_asset = dict(getattr(portfolio_quota, 'pending_unknown_by_asset', {}) or {})
    open_by_cluster = dict(getattr(portfolio_quota, 'open_positions_by_cluster', {}) or {})
    pending_by_cluster = dict(getattr(portfolio_quota, 'pending_unknown_by_cluster', {}) or {})

    # Portfolio-level block => suppress everything.
    if str(portfolio_quota.kind) != 'open':
        for c in candidates:
            w = _scope_weight(scope_by_tag, c.scope_tag)
            suppressed.append(
                _item(
                    scope_by_tag=scope_by_tag,
                    candidate=c,
                    rank_value=c.rank_value(weight=w, prefer_ev=prefer_ev),
                    selected=False,
                    reason=f'portfolio_blocked:{portfolio_quota.kind}',
                    risk_context={
                        'portfolio_quota_kind': str(portfolio_quota.kind),
                        'portfolio_quota_reason': str(portfolio_quota.reason),
                    },
                )
            )
        return PortfolioAllocation(
            allocation_id=alloc_id,
            at_utc=at_utc,
            max_select=int(max_select),
            selected=selected,
            suppressed=suppressed,
            portfolio_quota=portfolio_quota,
            asset_quotas=asset_quotas,
            risk_summary={
                'correlation_filter_enable': bool(correlation_filter_enable),
                'max_trades_per_cluster_per_cycle': int(max_per_cluster),
                'open_positions_by_asset': open_by_asset,
                'pending_unknown_by_asset': pending_by_asset,
                'open_positions_by_cluster': open_by_cluster,
                'pending_unknown_by_cluster': pending_by_cluster,
            },
        )

    # Filter and rank candidates
    ranked: list[tuple[float, CandidateDecision]] = []
    for c in candidates:
        action = str(c.action or 'HOLD').upper()
        w = _scope_weight(scope_by_tag, c.scope_tag)
        rv = c.rank_value(weight=w, prefer_ev=prefer_ev)
        cluster = _scope_cluster(scope_by_tag, c.scope_tag)

        if action not in {'CALL', 'PUT'}:
            suppressed.append(
                _item(
                    scope_by_tag=scope_by_tag,
                    candidate=c,
                    rank_value=rv,
                    selected=False,
                    reason='not_trade_action',
                    risk_context={'cluster_key': cluster},
                )
            )
            continue

        fs_reason = failsafe_blocks.get(c.scope_tag)
        if fs_reason:
            suppressed.append(
                _item(
                    scope_by_tag=scope_by_tag,
                    candidate=c,
                    rank_value=rv,
                    selected=False,
                    reason=f'failsafe_blocked:{fs_reason}',
                    risk_context={'cluster_key': cluster},
                )
            )
            continue

        q = quota_by_tag.get(c.scope_tag)
        if q is not None and str(q.kind) != 'open':
            suppressed.append(
                _item(
                    scope_by_tag=scope_by_tag,
                    candidate=c,
                    rank_value=rv,
                    selected=False,
                    reason=f'asset_quota_blocked:{q.kind}',
                    risk_context={
                        'cluster_key': cluster,
                        'asset_quota_reason': str(q.reason),
                        'pending_unknown': int(q.pending_unknown),
                        'open_positions': int(q.open_positions),
                    },
                )
            )
            continue

        ranked.append((rv, c))

    ranked.sort(key=lambda t: t[0], reverse=True)

    cluster_counts: dict[str, int] = defaultdict(int)
    selected_by_asset: dict[str, int] = defaultdict(int)
    selected_by_cluster: dict[str, int] = defaultdict(int)

    headroom = max(0, int(portfolio_quota.hard_max_positions_total) - int(portfolio_quota.open_positions_total))
    max_allowed = min(int(max_select), int(headroom))
    if portfolio_quota.budget_left_total is not None:
        max_allowed = min(int(max_allowed), int(portfolio_quota.budget_left_total))
    if getattr(portfolio_quota, 'budget_left_pending_unknown_total', None) is not None:
        max_allowed = min(int(max_allowed), int(portfolio_quota.budget_left_pending_unknown_total))

    handled_scope_tags: set[str] = set()
    picked = 0
    for rank, (val, c) in enumerate(ranked, start=1):
        if picked >= max_allowed:
            break
        cluster = _scope_cluster(scope_by_tag, c.scope_tag)
        asset_key = str(c.asset)

        active_asset_exposure = int(open_by_asset.get(asset_key, 0)) + int(pending_by_asset.get(asset_key, 0)) + int(selected_by_asset.get(asset_key, 0))
        active_cluster_exposure = int(open_by_cluster.get(cluster, 0)) + int(pending_by_cluster.get(cluster, 0)) + int(selected_by_cluster.get(cluster, 0))

        if max_open_per_asset is not None and active_asset_exposure >= int(max_open_per_asset):
            suppressed.append(
                _item(
                    scope_by_tag=scope_by_tag,
                    candidate=c,
                    rank_value=val,
                    selected=False,
                    reason=f'asset_exposure_cap:{asset_key}',
                    rank=int(rank),
                    risk_context={
                        'cluster_key': cluster,
                        'active_asset_exposure': int(active_asset_exposure),
                        'max_positions_per_asset': int(max_open_per_asset),
                        'open_positions_asset': int(open_by_asset.get(asset_key, 0)),
                        'pending_unknown_asset': int(pending_by_asset.get(asset_key, 0)),
                        'selected_in_cycle_asset': int(selected_by_asset.get(asset_key, 0)),
                    },
                )
            )
            handled_scope_tags.add(c.scope_tag)
            continue

        if max_open_per_cluster is not None and active_cluster_exposure >= int(max_open_per_cluster):
            reason = f'cluster_exposure_cap:{cluster}'
            if correlation_filter_enable:
                reason = f'correlation_cluster_cap:{cluster}'
            suppressed.append(
                _item(
                    scope_by_tag=scope_by_tag,
                    candidate=c,
                    rank_value=val,
                    selected=False,
                    reason=reason,
                    rank=int(rank),
                    risk_context={
                        'cluster_key': cluster,
                        'active_cluster_exposure': int(active_cluster_exposure),
                        'max_positions_per_cluster': int(max_open_per_cluster),
                        'open_positions_cluster': int(open_by_cluster.get(cluster, 0)),
                        'pending_unknown_cluster': int(pending_by_cluster.get(cluster, 0)),
                        'selected_in_cycle_cluster': int(selected_by_cluster.get(cluster, 0)),
                    },
                )
            )
            handled_scope_tags.add(c.scope_tag)
            continue

        if cluster_counts[cluster] >= int(max_per_cluster):
            suppressed.append(
                _item(
                    scope_by_tag=scope_by_tag,
                    candidate=c,
                    rank_value=val,
                    selected=False,
                    reason=f'cluster_cap:{cluster}',
                    rank=int(rank),
                    risk_context={
                        'cluster_key': cluster,
                        'selected_in_cycle_cluster': int(cluster_counts[cluster]),
                        'max_trades_per_cluster_per_cycle': int(max_per_cluster),
                    },
                )
            )
            handled_scope_tags.add(c.scope_tag)
            continue

        cluster_counts[cluster] += 1
        selected_by_asset[asset_key] += 1
        selected_by_cluster[cluster] += 1
        selected.append(
            _item(
                scope_by_tag=scope_by_tag,
                candidate=c,
                rank_value=val,
                selected=True,
                reason='selected',
                rank=int(rank),
                risk_context={
                    'cluster_key': cluster,
                    'selected_in_cycle_asset': int(selected_by_asset.get(asset_key, 0)),
                    'selected_in_cycle_cluster': int(selected_by_cluster.get(cluster, 0)),
                },
            )
        )
        handled_scope_tags.add(c.scope_tag)
        picked += 1

    # Remaining ranked become suppressed
    selected_tags = {i.scope_tag for i in selected}
    default_remaining_reason = 'portfolio_capacity_reached' if picked >= max_allowed else 'not_selected'
    for rank, (val, c) in enumerate(ranked, start=1):
        if c.scope_tag in selected_tags or c.scope_tag in handled_scope_tags:
            continue
        cluster = _scope_cluster(scope_by_tag, c.scope_tag)
        suppressed.append(
            _item(
                scope_by_tag=scope_by_tag,
                candidate=c,
                rank_value=val,
                selected=False,
                reason=default_remaining_reason,
                rank=int(rank),
                risk_context={
                    'cluster_key': cluster,
                    'max_select': int(max_select),
                    'max_allowed': int(max_allowed),
                    'selected_count': int(picked),
                },
            )
        )

    risk_summary = {
        'correlation_filter_enable': bool(correlation_filter_enable),
        'max_trades_per_cluster_per_cycle': int(max_per_cluster),
        'hard_max_pending_unknown_total': getattr(portfolio_quota, 'hard_max_pending_unknown_total', None),
        'budget_left_pending_unknown_total': getattr(portfolio_quota, 'budget_left_pending_unknown_total', None),
        'hard_max_positions_per_asset': max_open_per_asset,
        'hard_max_positions_per_cluster': max_open_per_cluster,
        'selected_by_asset': dict(sorted(selected_by_asset.items())),
        'selected_by_cluster': dict(sorted(selected_by_cluster.items())),
        'open_positions_by_asset': dict(sorted(open_by_asset.items())),
        'pending_unknown_by_asset': dict(sorted(pending_by_asset.items())),
        'open_positions_by_cluster': dict(sorted(open_by_cluster.items())),
        'pending_unknown_by_cluster': dict(sorted(pending_by_cluster.items())),
        'selected_with_drift_warn_or_block': int(sum(1 for i in selected if str(getattr(i, 'drift_level', '') or '') in {'warn', 'block'})),
        'selected_with_learned_gate': int(sum(1 for i in selected if getattr(i, 'learned_gate_prob', None) is not None)),
    }

    return PortfolioAllocation(
        allocation_id=alloc_id,
        at_utc=at_utc,
        max_select=int(max_select),
        selected=selected,
        suppressed=suppressed,
        portfolio_quota=portfolio_quota,
        asset_quotas=asset_quotas,
        risk_summary=risk_summary,
    )
