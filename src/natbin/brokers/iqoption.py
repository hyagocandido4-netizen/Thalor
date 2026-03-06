from __future__ import annotations

from datetime import UTC, datetime

from .base import BrokerScope
from ..runtime.execution_contracts import BROKER_REJECTED, TRANSPORT_REJECT
from ..runtime.execution_models import BrokerOrderSnapshot, BrokerSessionStatus, SubmitOrderRequest, SubmitOrderResult
from ..runtime.execution_policy import utc_now_iso


class IQOptionAdapter:
    """Lazy IQ Option adapter.

    Package N wires the execution contract without making the runtime import the
    IQ library at module-import time. The real submit/history integration still
    depends on the IQ client/runtime environment being present.
    """

    def __init__(self, *, account_mode: str = 'PRACTICE') -> None:
        self.account_mode = str(account_mode or 'PRACTICE').upper()

    def broker_name(self) -> str:
        return 'iqoption'

    def _import_client(self):
        try:
            from ..iq_client import IQClient  # noqa: F401
            return True, None
        except Exception as exc:  # pragma: no cover - environment-dependent
            return False, exc

    def healthcheck(self) -> BrokerSessionStatus:
        ok, exc = self._import_client()
        if not ok:
            return BrokerSessionStatus(
                broker_name=self.broker_name(),
                account_mode=self.account_mode,
                ready=False,
                healthy=False,
                reason=f'iqoption_unavailable:{type(exc).__name__}',
                checked_at_utc=utc_now_iso(),
            )
        # Package P: keep fail-closed until live execution is implemented.
        return BrokerSessionStatus(
            broker_name=self.broker_name(),
            account_mode=self.account_mode,
            ready=False,
            healthy=True,
            reason='iqoption_not_implemented',
            checked_at_utc=utc_now_iso(),
        )

    def submit_order(self, req: SubmitOrderRequest) -> SubmitOrderResult:  # pragma: no cover - live adapter not exercised in CI
        ok, exc = self._import_client()
        if not ok:
            return SubmitOrderResult(
                transport_status=TRANSPORT_REJECT,
                external_order_id=None,
                broker_status=BROKER_REJECTED,
                error_code='iqoption_unavailable',
                error_message=f'iqoption_unavailable:{type(exc).__name__}',
                response={'note': 'iqoptionapi not available in this runtime'},
            )
        return SubmitOrderResult(
            transport_status=TRANSPORT_REJECT,
            external_order_id=None,
            broker_status=BROKER_REJECTED,
            error_code='not_implemented',
            error_message='iqoption_submit_not_implemented_in_package_p',
            response={'note': 'live execution adapter not implemented yet'},
        )

    def fetch_order(self, external_order_id: str) -> BrokerOrderSnapshot | None:  # pragma: no cover - live adapter not exercised in CI
        ok, exc = self._import_client()
        if not ok:
            return None
        return None

    def fetch_open_orders(self, scope: BrokerScope) -> list[BrokerOrderSnapshot]:  # pragma: no cover - live adapter not exercised in CI
        ok, exc = self._import_client()
        if not ok:
            return []
        return []

    def fetch_closed_orders(self, scope: BrokerScope, since_utc: datetime) -> list[BrokerOrderSnapshot]:  # pragma: no cover - live adapter not exercised in CI
        ok, exc = self._import_client()
        if not ok:
            return []
        return []
