from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .base import BrokerScope
from ..runtime.execution_contracts import (
    BROKER_CANCELLED,
    BROKER_CLOSED_LOSS,
    BROKER_CLOSED_REFUND,
    BROKER_CLOSED_WIN,
    BROKER_OPEN,
    BROKER_UNKNOWN,
    SETTLEMENT_LOSS,
    SETTLEMENT_REFUND,
    SETTLEMENT_WIN,
    TRANSPORT_ACK,
    TRANSPORT_EXCEPTION,
    TRANSPORT_REJECT,
    TRANSPORT_TIMEOUT,
)
from ..runtime.execution_models import BrokerOrderSnapshot, BrokerSessionStatus, SubmitOrderRequest, SubmitOrderResult
from ..runtime.execution_policy import ensure_utc_iso, json_dumps, parse_utc_iso, utc_now_iso


class FakeBrokerAdapter:
    """Deterministic broker adapter used by Package N tests and paper mode.

    The fake adapter persists its own broker-side state in a JSON file under
    ``runs/`` so submit/reconcile flows survive subprocess boundaries.
    """

    def __init__(
        self,
        *,
        repo_root: str | Path,
        account_mode: str = 'PRACTICE',
        state_path: str | Path | None = None,
        submit_behavior: str = 'ack',
        settlement: str = 'open',
        settle_after_sec: int = 0,
        create_order_on_timeout: bool = True,
        payout: float = 0.80,
        heartbeat_ok: bool = True,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.account_mode = str(account_mode or 'PRACTICE').upper()
        self.path = Path(state_path) if state_path is not None else (self.repo_root / 'runs' / 'fake_broker_state.json')
        if not self.path.is_absolute():
            self.path = self.repo_root / self.path
        self.submit_behavior = str(submit_behavior or 'ack').strip().lower()
        self.settlement = str(settlement or 'open').strip().lower()
        self.settle_after_sec = max(0, int(settle_after_sec))
        self.create_order_on_timeout = bool(create_order_on_timeout)
        self.payout = float(payout)
        self.heartbeat_ok = bool(heartbeat_ok)

    def broker_name(self) -> str:
        return 'fake'

    def healthcheck(self) -> BrokerSessionStatus:
        ok = bool(self.heartbeat_ok)
        return BrokerSessionStatus(
            broker_name=self.broker_name(),
            account_mode=self.account_mode,
            ready=ok,
            healthy=ok,
            reason=None if ok else 'fake_broker_unhealthy',
            checked_at_utc=utc_now_iso(),
        )

    def _load_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return {'orders': {}}
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except Exception:
            return {'orders': {}}
        if not isinstance(raw, dict):
            return {'orders': {}}
        raw.setdefault('orders', {})
        return raw

    def _save_state(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + '.tmp')
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        tmp.replace(self.path)

    @staticmethod
    def _make_external_order_id(seed: str) -> str:
        return hashlib.sha1(seed.encode('utf-8')).hexdigest()[:20]

    def _build_order_record(self, req: SubmitOrderRequest) -> dict[str, Any]:
        now_iso = utc_now_iso()
        external_order_id = self._make_external_order_id(f'{req.client_order_key}|{now_iso}')
        expires_at_utc = ensure_utc_iso(datetime.fromtimestamp(int(req.expiry_ts), tz=UTC))
        close_after_sec = max(0, self.settle_after_sec)
        return {
            'external_order_id': external_order_id,
            'client_order_key': req.client_order_key,
            'account_mode': self.account_mode,
            'asset': req.asset,
            'side': req.side,
            'amount': float(req.amount),
            'currency': req.currency,
            'opened_at_utc': now_iso,
            'expires_at_utc': expires_at_utc,
            'submit_behavior': self.submit_behavior,
            'settlement': self.settlement,
            'close_after_sec': close_after_sec,
            'payout': float(self.payout),
            'raw_request': req.as_dict(),
        }

    def _materialize_record(self, record: dict[str, Any]) -> BrokerOrderSnapshot:
        opened = parse_utc_iso(record.get('opened_at_utc')) or datetime.now(UTC)
        expires_at = parse_utc_iso(record.get('expires_at_utc'))
        settlement = str(record.get('settlement') or 'open').strip().lower()
        close_after_sec = max(0, int(record.get('close_after_sec') or 0))
        now = datetime.now(UTC)
        close_at = opened + timedelta(seconds=close_after_sec)
        is_closed = settlement != 'open' and now >= close_at

        broker_status = BROKER_OPEN
        settlement_status = None
        gross_payout = None
        net_pnl = None
        closed_at_utc = None
        if is_closed:
            closed_at_utc = ensure_utc_iso(close_at)
            if settlement == 'win':
                broker_status = BROKER_CLOSED_WIN
                settlement_status = SETTLEMENT_WIN
                gross_payout = round(float(record.get('amount') or 0.0) * (1.0 + float(record.get('payout') or 0.8)), 8)
                net_pnl = round(float(record.get('amount') or 0.0) * float(record.get('payout') or 0.8), 8)
            elif settlement == 'loss':
                broker_status = BROKER_CLOSED_LOSS
                settlement_status = SETTLEMENT_LOSS
                gross_payout = 0.0
                net_pnl = round(-float(record.get('amount') or 0.0), 8)
            elif settlement == 'refund':
                broker_status = BROKER_CLOSED_REFUND
                settlement_status = SETTLEMENT_REFUND
                gross_payout = float(record.get('amount') or 0.0)
                net_pnl = 0.0
            elif settlement == 'cancelled':
                broker_status = BROKER_CANCELLED
                gross_payout = float(record.get('amount') or 0.0)
                net_pnl = 0.0
            else:
                broker_status = BROKER_UNKNOWN
        return BrokerOrderSnapshot(
            broker_name=self.broker_name(),
            account_mode=self.account_mode,
            external_order_id=str(record.get('external_order_id') or ''),
            client_order_key=record.get('client_order_key'),
            asset=str(record.get('asset') or ''),
            side=str(record.get('side') or ''),
            amount=float(record.get('amount') or 0.0),
            currency=str(record.get('currency') or 'BRL'),
            broker_status=broker_status,
            opened_at_utc=ensure_utc_iso(record.get('opened_at_utc')),
            expires_at_utc=ensure_utc_iso(record.get('expires_at_utc')),
            closed_at_utc=closed_at_utc,
            gross_payout=gross_payout,
            net_pnl=net_pnl,
            settlement_status=settlement_status,
            estimated_pnl=False,
            raw_snapshot_json=json_dumps(record),
            last_seen_at_utc=utc_now_iso(),
        )

    def submit_order(self, req: SubmitOrderRequest) -> SubmitOrderResult:
        if self.submit_behavior == 'exception':
            raise RuntimeError('fake_submit_exception')
        if self.submit_behavior == 'reject':
            return SubmitOrderResult(
                transport_status=TRANSPORT_REJECT,
                external_order_id=None,
                broker_status='rejected',
                accepted_at_utc=None,
                error_code='fake_reject',
                error_message='fake broker rejected order',
                response={'client_order_key': req.client_order_key},
            )

        state = self._load_state()
        record = self._build_order_record(req)
        state.setdefault('orders', {})[record['external_order_id']] = record
        self._save_state(state)

        if self.submit_behavior == 'timeout':
            if not self.create_order_on_timeout:
                state['orders'].pop(record['external_order_id'], None)
                self._save_state(state)
            return SubmitOrderResult(
                transport_status=TRANSPORT_TIMEOUT,
                external_order_id=None,
                broker_status='unknown',
                accepted_at_utc=None,
                error_code='fake_timeout',
                error_message='fake broker submit timed out',
                response={'client_order_key': req.client_order_key},
            )

        return SubmitOrderResult(
            transport_status=TRANSPORT_ACK,
            external_order_id=str(record['external_order_id']),
            broker_status='open',
            accepted_at_utc=utc_now_iso(),
            response={'client_order_key': req.client_order_key},
        )

    def fetch_order(self, external_order_id: str) -> BrokerOrderSnapshot | None:
        state = self._load_state()
        record = (state.get('orders') or {}).get(str(external_order_id))
        if not isinstance(record, dict):
            return None
        return self._materialize_record(record)

    def fetch_open_orders(self, scope: BrokerScope) -> list[BrokerOrderSnapshot]:
        state = self._load_state()
        out: list[BrokerOrderSnapshot] = []
        for record in (state.get('orders') or {}).values():
            if not isinstance(record, dict):
                continue
            if str(record.get('account_mode') or '').upper() != str(scope.account_mode).upper():
                continue
            if str(record.get('asset') or '') != str(scope.asset):
                continue
            snap = self._materialize_record(record)
            if snap.broker_status == BROKER_OPEN:
                out.append(snap)
        return out

    def fetch_closed_orders(self, scope: BrokerScope, since_utc: datetime) -> list[BrokerOrderSnapshot]:
        since = since_utc.astimezone(UTC)
        state = self._load_state()
        out: list[BrokerOrderSnapshot] = []
        for record in (state.get('orders') or {}).values():
            if not isinstance(record, dict):
                continue
            if str(record.get('account_mode') or '').upper() != str(scope.account_mode).upper():
                continue
            if str(record.get('asset') or '') != str(scope.asset):
                continue
            snap = self._materialize_record(record)
            if snap.broker_status == BROKER_OPEN:
                continue
            closed = parse_utc_iso(snap.closed_at_utc) or parse_utc_iso(snap.opened_at_utc)
            if closed is None or closed >= since:
                out.append(snap)
        return out
