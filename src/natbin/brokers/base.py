from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..runtime.execution_models import BrokerOrderSnapshot, BrokerSessionStatus, SubmitOrderRequest, SubmitOrderResult


@dataclass(frozen=True)
class BrokerScope:
    asset: str
    interval_sec: int
    scope_tag: str
    account_mode: str


class BrokerAdapter(Protocol):
    def broker_name(self) -> str: ...

    def healthcheck(self) -> BrokerSessionStatus: ...

    def submit_order(self, req: SubmitOrderRequest) -> SubmitOrderResult: ...

    def fetch_order(self, external_order_id: str) -> BrokerOrderSnapshot | None: ...

    def fetch_open_orders(self, scope: BrokerScope) -> list[BrokerOrderSnapshot]: ...

    def fetch_closed_orders(self, scope: BrokerScope, since_utc: datetime) -> list[BrokerOrderSnapshot]: ...
