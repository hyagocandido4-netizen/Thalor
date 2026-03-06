from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Mapping

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

    # Portfolio-level block => suppress everything.
    if str(portfolio_quota.kind) != 'open':
        for c in candidates:
            s = scope_by_tag.get(c.scope_tag)
            w = float(s.weight) if s is not None else 1.0
            suppressed.append(
                AllocationItem(
                    scope_tag=c.scope_tag,
                    asset=c.asset,
                    interval_sec=int(c.interval_sec),
                    action=str(c.action),
                    score=c.score,
                    conf=c.conf,
                    ev=c.ev,
                    rank_value=c.rank_value(weight=w, prefer_ev=prefer_ev),
                    selected=False,
                    reason=f'portfolio_blocked:{portfolio_quota.kind}',
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
        )

    # Filter and rank candidates
    ranked: list[tuple[float, CandidateDecision]] = []
    for c in candidates:
        action = str(c.action or 'HOLD').upper()
        if action not in {'CALL', 'PUT'}:
            s = scope_by_tag.get(c.scope_tag)
            w = float(s.weight) if s is not None else 1.0
            suppressed.append(
                AllocationItem(
                    scope_tag=c.scope_tag,
                    asset=c.asset,
                    interval_sec=int(c.interval_sec),
                    action=action,
                    score=c.score,
                    conf=c.conf,
                    ev=c.ev,
                    rank_value=c.rank_value(weight=w, prefer_ev=prefer_ev),
                    selected=False,
                    reason='not_trade_action',
                )
            )
            continue

        fs_reason = failsafe_blocks.get(c.scope_tag)
        if fs_reason:
            s = scope_by_tag.get(c.scope_tag)
            w = float(s.weight) if s is not None else 1.0
            suppressed.append(
                AllocationItem(
                    scope_tag=c.scope_tag,
                    asset=c.asset,
                    interval_sec=int(c.interval_sec),
                    action=action,
                    score=c.score,
                    conf=c.conf,
                    ev=c.ev,
                    rank_value=c.rank_value(weight=w, prefer_ev=prefer_ev),
                    selected=False,
                    reason=f'failsafe_blocked:{fs_reason}',
                )
            )
            continue

        q = quota_by_tag.get(c.scope_tag)
        if q is not None and str(q.kind) != 'open':
            s = scope_by_tag.get(c.scope_tag)
            w = float(s.weight) if s is not None else 1.0
            suppressed.append(
                AllocationItem(
                    scope_tag=c.scope_tag,
                    asset=c.asset,
                    interval_sec=int(c.interval_sec),
                    action=action,
                    score=c.score,
                    conf=c.conf,
                    ev=c.ev,
                    rank_value=c.rank_value(weight=w, prefer_ev=prefer_ev),
                    selected=False,
                    reason=f'asset_quota_blocked:{q.kind}',
                )
            )
            continue

        s = scope_by_tag.get(c.scope_tag)
        weight = float(s.weight) if s is not None else 1.0
        score = c.rank_value(weight=weight, prefer_ev=prefer_ev)
        ranked.append((score, c))

    ranked.sort(key=lambda t: t[0], reverse=True)

    # Cluster caps
    cluster_counts: dict[str, int] = {}

    # Respect portfolio max open positions.
    headroom = max(0, int(portfolio_quota.hard_max_positions_total) - int(portfolio_quota.open_positions_total))
    max_allowed = min(int(max_select), int(headroom))
    if portfolio_quota.budget_left_total is not None:
        max_allowed = min(int(max_allowed), int(portfolio_quota.budget_left_total))

    picked = 0
    for rank, (val, c) in enumerate(ranked, start=1):
        if picked >= max_allowed:
            break
        s = scope_by_tag.get(c.scope_tag)
        cluster = str(s.cluster_key) if s is not None else 'default'
        cluster_counts.setdefault(cluster, 0)
        if cluster_counts[cluster] >= int(max_per_cluster):
            suppressed.append(
                AllocationItem(
                    scope_tag=c.scope_tag,
                    asset=c.asset,
                    interval_sec=int(c.interval_sec),
                    action=str(c.action).upper(),
                    score=c.score,
                    conf=c.conf,
                    ev=c.ev,
                    rank_value=float(val),
                    selected=False,
                    reason=f'cluster_cap:{cluster}',
                    rank=int(rank),
                )
            )
            continue

        cluster_counts[cluster] += 1
        selected.append(
            AllocationItem(
                scope_tag=c.scope_tag,
                asset=c.asset,
                interval_sec=int(c.interval_sec),
                action=str(c.action).upper(),
                score=c.score,
                conf=c.conf,
                ev=c.ev,
                rank_value=float(val),
                selected=True,
                reason='selected',
                rank=int(rank),
            )
        )
        picked += 1

    # Remaining ranked become suppressed
    selected_tags = {i.scope_tag for i in selected}
    for rank, (val, c) in enumerate(ranked, start=1):
        if c.scope_tag in selected_tags:
            continue
        suppressed.append(
            AllocationItem(
                scope_tag=c.scope_tag,
                asset=c.asset,
                interval_sec=int(c.interval_sec),
                action=str(c.action).upper(),
                score=c.score,
                conf=c.conf,
                ev=c.ev,
                rank_value=float(val),
                selected=False,
                reason='not_selected',
                rank=int(rank),
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
    )
