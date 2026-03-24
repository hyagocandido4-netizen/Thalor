from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SubmitOrderRequest:
    intent_id: str
    client_order_key: str
    broker_name: str
    account_mode: str
    scope_tag: str
    asset: str
    interval_sec: int
    side: str
    amount: float
    currency: str
    signal_ts: int
    expiry_ts: int
    entry_deadline_utc: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SubmitOrderResult:
    transport_status: str
    external_order_id: str | None
    broker_status: str
    accepted_at_utc: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    response: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerSessionStatus:
    broker_name: str
    account_mode: str
    ready: bool
    healthy: bool
    reason: str | None = None
    checked_at_utc: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    scope_tag: str
    broker_name: str
    account_mode: str
    day: str
    asset: str
    interval_sec: int
    signal_ts: int
    decision_action: str
    decision_conf: float | None
    decision_score: float | None
    stake_amount: float
    stake_currency: str
    expiry_ts: int
    entry_deadline_utc: str
    client_order_key: str
    intent_state: str
    broker_status: str
    settlement_status: str | None = None
    external_order_id: str | None = None
    external_position_id: str | None = None
    submit_attempt_count: int = 0
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at_utc: str | None = None
    updated_at_utc: str | None = None
    submitted_at_utc: str | None = None
    accepted_at_utc: str | None = None
    settled_at_utc: str | None = None
    last_reconcile_at_utc: str | None = None
    portfolio_cycle_id: str | None = None
    allocation_batch_id: str | None = None
    cluster_key: str | None = None
    portfolio_score: float | None = None
    intelligence_score: float | None = None
    retrain_state: str | None = None
    retrain_priority: str | None = None
    allocation_reason: str | None = None
    allocation_rank: int | None = None
    portfolio_feedback_json: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrderSubmitAttempt:
    attempt_id: str
    intent_id: str
    attempt_no: int
    requested_at_utc: str
    finished_at_utc: str | None
    transport_status: str
    latency_ms: int | None
    external_order_id: str | None
    error_code: str | None
    error_message: str | None
    request_json: str
    response_json: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerOrderSnapshot:
    broker_name: str
    account_mode: str
    external_order_id: str
    client_order_key: str | None
    asset: str
    side: str
    amount: float
    currency: str
    broker_status: str
    opened_at_utc: str | None
    expires_at_utc: str | None
    closed_at_utc: str | None
    gross_payout: float | None
    net_pnl: float | None
    settlement_status: str | None
    estimated_pnl: bool
    raw_snapshot_json: str
    last_seen_at_utc: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconciliationBatchResult:
    scope_tag: str
    started_at_utc: str
    finished_at_utc: str
    pending_before: int
    updated_intents: int
    new_orphans: int
    ambiguous_matches: int
    terminalized: int
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
