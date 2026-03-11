from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from ..config.loader import load_thalor_config
from ..runtime.execution_policy import utc_now_iso
from ..runtime.quota import compute_quota_day_context
from ..state.execution_repo import ExecutionRepository

from .models import AssetQuota, PortfolioQuota, PortfolioScope


def _resolve_day(*, tz_name: str, now_utc: datetime | None = None) -> str:
    _local, day, _sec = compute_quota_day_context(tz_name=str(tz_name), now_utc=now_utc)
    return str(day)


def _to_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def compute_asset_quotas(
    repo_root: str | Path,
    scopes: Iterable[PortfolioScope],
    *,
    config_path: str | Path | None = None,
    now_utc: datetime | None = None,
) -> list[AssetQuota]:
    root = Path(repo_root).resolve()
    cfg = load_thalor_config(config_path=config_path, repo_root=root)

    exec_repo = ExecutionRepository(root / 'runs' / 'runtime_execution.sqlite3')

    out: list[AssetQuota] = []
    for scope in scopes:
        day = _resolve_day(tz_name=scope.timezone, now_utc=now_utc)

        # Per-scope overrides fallback to global config.
        max_trades = scope.hard_max_trades_per_day
        if max_trades is None:
            try:
                max_trades = int(cfg.quota.hard_max_trades_per_day)
            except Exception:
                max_trades = 3

        max_open = scope.max_open_positions
        if max_open is None:
            try:
                max_open = int(cfg.execution.limits.max_open_positions)
            except Exception:
                max_open = 1

        max_pending = scope.max_pending_unknown
        if max_pending is None:
            try:
                max_pending = int(cfg.execution.limits.max_pending_unknown)
            except Exception:
                max_pending = 1

        try:
            executed_today = int(exec_repo.count_consuming_intents(asset=scope.asset, interval_sec=scope.interval_sec, day=day))
        except Exception:
            executed_today = 0
        try:
            pending_unknown = int(exec_repo.count_pending_unknown(asset=scope.asset, interval_sec=scope.interval_sec))
        except Exception:
            pending_unknown = 0
        try:
            open_positions = int(exec_repo.count_open_positions(asset=scope.asset, interval_sec=scope.interval_sec))
        except Exception:
            open_positions = 0

        budget_left = max(0, int(max_trades) - int(executed_today))

        kind = 'open'
        reason = ''
        if pending_unknown >= max(1, int(max_pending)):
            kind = 'pending_unknown'
            reason = f'pending_unknown>={max_pending}'
        elif open_positions >= max(1, int(max_open)):
            kind = 'open_position'
            reason = f'open_positions>={max_open}'
        elif executed_today >= int(max_trades):
            kind = 'max_trades_reached'
            reason = f'executed_today>={max_trades}'

        out.append(
            AssetQuota(
                scope_tag=scope.scope_tag,
                asset=scope.asset,
                interval_sec=int(scope.interval_sec),
                day=day,
                kind=kind,
                reason=reason,
                executed_today=int(executed_today),
                max_trades_per_day=int(max_trades),
                budget_left=int(budget_left),
                pending_unknown=int(pending_unknown),
                max_pending_unknown=int(max_pending),
                open_positions=int(open_positions),
                max_open_positions=int(max_open),
                cluster_key=str(scope.cluster_key or 'default'),
            )
        )

    return out


def compute_portfolio_quota(
    repo_root: str | Path,
    scopes: Iterable[PortfolioScope],
    *,
    config_path: str | Path | None = None,
    now_utc: datetime | None = None,
) -> PortfolioQuota:
    root = Path(repo_root).resolve()
    cfg = load_thalor_config(config_path=config_path, repo_root=root)

    scopes = list(scopes)
    tz_name = scopes[0].timezone if scopes else 'UTC'
    day = _resolve_day(tz_name=tz_name, now_utc=now_utc)

    exec_repo = ExecutionRepository(root / 'runs' / 'runtime_execution.sqlite3')

    executed_total = 0
    pending_total = 0
    open_total = 0

    executed_by_asset: dict[str, int] = defaultdict(int)
    pending_by_asset: dict[str, int] = defaultdict(int)
    open_by_asset: dict[str, int] = defaultdict(int)

    executed_by_cluster: dict[str, int] = defaultdict(int)
    pending_by_cluster: dict[str, int] = defaultdict(int)
    open_by_cluster: dict[str, int] = defaultdict(int)

    for scope in scopes:
        executed_scope = 0
        pending_scope = 0
        open_scope = 0
        try:
            executed_scope = int(exec_repo.count_consuming_intents(asset=scope.asset, interval_sec=scope.interval_sec, day=day))
            executed_total += executed_scope
        except Exception:
            executed_scope = 0
        try:
            pending_scope = int(exec_repo.count_pending_unknown(asset=scope.asset, interval_sec=scope.interval_sec))
            pending_total += pending_scope
        except Exception:
            pending_scope = 0
        try:
            open_scope = int(exec_repo.count_open_positions(asset=scope.asset, interval_sec=scope.interval_sec))
            open_total += open_scope
        except Exception:
            open_scope = 0

        asset_key = str(scope.asset)
        cluster_key = str(scope.cluster_key or 'default')
        executed_by_asset[asset_key] += int(executed_scope)
        pending_by_asset[asset_key] += int(pending_scope)
        open_by_asset[asset_key] += int(open_scope)

        executed_by_cluster[cluster_key] += int(executed_scope)
        pending_by_cluster[cluster_key] += int(pending_scope)
        open_by_cluster[cluster_key] += int(open_scope)

    hard_max_positions = 1
    try:
        hard_max_positions = int(cfg.multi_asset.portfolio_hard_max_positions)
    except Exception:
        hard_max_positions = 1
    hard_max_positions = max(1, int(hard_max_positions))

    hard_max_trades = None
    try:
        v = getattr(cfg.multi_asset, 'portfolio_hard_max_trades_per_day', None)
        if v is not None:
            hard_max_trades = int(v)
    except Exception:
        hard_max_trades = None

    hard_max_pending_total = _to_optional_int(getattr(cfg.multi_asset, 'portfolio_hard_max_pending_unknown_total', None))
    if hard_max_pending_total is not None:
        hard_max_pending_total = max(1, int(hard_max_pending_total))

    hard_max_positions_per_asset = _to_optional_int(getattr(cfg.multi_asset, 'portfolio_hard_max_positions_per_asset', None))
    if hard_max_positions_per_asset is not None:
        hard_max_positions_per_asset = max(1, int(hard_max_positions_per_asset))

    hard_max_positions_per_cluster = _to_optional_int(getattr(cfg.multi_asset, 'portfolio_hard_max_positions_per_cluster', None))
    if hard_max_positions_per_cluster is not None:
        hard_max_positions_per_cluster = max(1, int(hard_max_positions_per_cluster))

    correlation_filter_enable = bool(getattr(cfg.multi_asset, 'correlation_filter_enable', True))

    kind = 'open'
    reason = ''
    budget_left = None
    pending_budget_left = None

    if hard_max_pending_total is not None and pending_total >= int(hard_max_pending_total):
        kind = 'portfolio_pending_unknown'
        reason = f'pending_unknown_total>={hard_max_pending_total}'
    elif open_total >= int(hard_max_positions):
        kind = 'portfolio_open_positions'
        reason = f'open_positions_total>={hard_max_positions}'
    elif hard_max_trades is not None and executed_total >= int(hard_max_trades):
        kind = 'portfolio_max_trades_reached'
        reason = f'executed_today_total>={hard_max_trades}'

    if hard_max_trades is not None:
        budget_left = max(0, int(hard_max_trades) - int(executed_total))
    if hard_max_pending_total is not None:
        pending_budget_left = max(0, int(hard_max_pending_total) - int(pending_total))

    return PortfolioQuota(
        day=str(day),
        kind=str(kind),
        reason=str(reason),
        executed_today_total=int(executed_total),
        hard_max_trades_per_day_total=hard_max_trades,
        budget_left_total=int(budget_left) if budget_left is not None else None,
        pending_unknown_total=int(pending_total),
        open_positions_total=int(open_total),
        hard_max_positions_total=int(hard_max_positions),
        hard_max_pending_unknown_total=int(hard_max_pending_total) if hard_max_pending_total is not None else None,
        budget_left_pending_unknown_total=int(pending_budget_left) if pending_budget_left is not None else None,
        open_positions_by_asset=dict(sorted(open_by_asset.items())),
        pending_unknown_by_asset=dict(sorted(pending_by_asset.items())),
        executed_today_by_asset=dict(sorted(executed_by_asset.items())),
        open_positions_by_cluster=dict(sorted(open_by_cluster.items())),
        pending_unknown_by_cluster=dict(sorted(pending_by_cluster.items())),
        executed_today_by_cluster=dict(sorted(executed_by_cluster.items())),
        hard_max_positions_per_asset=hard_max_positions_per_asset,
        hard_max_positions_per_cluster=hard_max_positions_per_cluster,
        correlation_filter_enable=bool(correlation_filter_enable),
    )
