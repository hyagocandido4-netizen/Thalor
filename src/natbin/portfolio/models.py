from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PortfolioScope:
    asset: str
    interval_sec: int
    timezone: str
    scope_tag: str

    # Allocation hints
    weight: float = 1.0
    cluster_key: str = 'default'

    # Per-scope policy overrides (optional)
    topk_k: int = 3
    hard_max_trades_per_day: int | None = None
    max_open_positions: int | None = None
    max_pending_unknown: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateDecision:
    scope_tag: str
    asset: str
    interval_sec: int
    day: str | None
    ts: int | None
    action: str
    score: float | None
    conf: float | None
    ev: float | None
    reason: str | None
    blockers: str | None
    decision_path: str | None
    raw: dict[str, Any]

    def rank_value(self, *, weight: float = 1.0, prefer_ev: bool = True) -> float:
        """Rank value used by the portfolio allocator.

        Priority:
        1) ev (expected value) when present and prefer_ev=True
        2) score
        3) conf

        The returned value is multiplied by the provided weight.
        """
        base = 0.0
        if prefer_ev and self.ev is not None:
            try:
                base = float(self.ev)
            except Exception:
                base = 0.0
        elif self.score is not None:
            try:
                base = float(self.score)
            except Exception:
                base = 0.0
        elif self.conf is not None:
            try:
                base = float(self.conf)
            except Exception:
                base = 0.0
        try:
            w = float(weight)
        except Exception:
            w = 1.0
        return float(base) * w

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssetQuota:
    scope_tag: str
    asset: str
    interval_sec: int
    day: str

    kind: str
    reason: str

    executed_today: int
    max_trades_per_day: int
    budget_left: int

    pending_unknown: int
    max_pending_unknown: int

    open_positions: int
    max_open_positions: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioQuota:
    day: str

    kind: str
    reason: str

    executed_today_total: int
    hard_max_trades_per_day_total: int | None
    budget_left_total: int | None

    pending_unknown_total: int

    open_positions_total: int
    hard_max_positions_total: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AllocationItem:
    scope_tag: str
    asset: str
    interval_sec: int
    action: str

    score: float | None
    conf: float | None
    ev: float | None

    rank_value: float
    selected: bool
    reason: str
    rank: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioAllocation:
    allocation_id: str
    at_utc: str

    max_select: int
    selected: list[AllocationItem]
    suppressed: list[AllocationItem]

    portfolio_quota: PortfolioQuota
    asset_quotas: list[AssetQuota]

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            'selected': [i.as_dict() for i in self.selected],
            'suppressed': [i.as_dict() for i in self.suppressed],
            'portfolio_quota': self.portfolio_quota.as_dict(),
            'asset_quotas': [q.as_dict() for q in self.asset_quotas],
        }


@dataclass(frozen=True)
class PortfolioCycleReport:
    cycle_id: str
    started_at_utc: str
    finished_at_utc: str
    ok: bool
    message: str

    scopes: list[dict[str, Any]]
    prepare: list[dict[str, Any]]
    candidate_results: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    allocation: dict[str, Any] | None
    execution: list[dict[str, Any]]
    errors: list[str]

    # Package P: operational gates (kill-switch/drain) and per-scope failsafe blocks.
    gates: dict[str, Any] | None = None
    failsafe_blocks: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
