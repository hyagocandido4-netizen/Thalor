from __future__ import annotations

from natbin.runtime.execution_models import BrokerOrderSnapshot, OrderIntent
from natbin.runtime.reconciliation_core import candidate_snapshots


class _Adapter:
    def __init__(self, snapshot: BrokerOrderSnapshot | None = None):
        self.snapshot = snapshot
        self.calls: list[str] = []

    def fetch_order(self, external_order_id: str):
        self.calls.append(str(external_order_id))
        return self.snapshot



def _intent(**overrides):
    payload = dict(
        intent_id='intent_1',
        scope_tag='EURUSD-OTC_300s',
        broker_name='fake',
        account_mode='PRACTICE',
        day='2026-03-23',
        asset='EURUSD-OTC',
        interval_sec=300,
        signal_ts=1773300000,
        decision_action='CALL',
        decision_conf=0.62,
        decision_score=0.41,
        stake_amount=2.0,
        stake_currency='BRL',
        expiry_ts=1773300300,
        entry_deadline_utc='2026-03-23T10:00:02+00:00',
        client_order_key='ck_intent_1',
        intent_state='accepted_open',
        broker_status='open',
        created_at_utc='2026-03-23T10:00:00+00:00',
        updated_at_utc='2026-03-23T10:00:00+00:00',
        submitted_at_utc='2026-03-23T10:00:00+00:00',
        external_order_id='ord_123',
    )
    payload.update(overrides)
    return OrderIntent(**payload)



def _snapshot(**overrides):
    payload = dict(
        broker_name='fake',
        account_mode='PRACTICE',
        external_order_id='ord_123',
        client_order_key='ck_intent_1',
        asset='EURUSD-OTC',
        side='CALL',
        amount=2.0,
        currency='BRL',
        broker_status='open',
        opened_at_utc='2026-03-23T10:00:00+00:00',
        expires_at_utc='2026-03-23T10:05:00+00:00',
        closed_at_utc=None,
        gross_payout=None,
        net_pnl=None,
        settlement_status=None,
        estimated_pnl=False,
        raw_snapshot_json='{}',
        last_seen_at_utc='2026-03-23T10:00:01+00:00',
    )
    payload.update(overrides)
    return BrokerOrderSnapshot(**payload)



def test_candidate_snapshots_prefers_fetch_order_when_external_id_exists() -> None:
    expected = _snapshot(external_order_id='ord_123')
    adapter = _Adapter(snapshot=expected)

    matches = candidate_snapshots(intent=_intent(), snapshots=[_snapshot(external_order_id='other')], adapter=adapter)

    assert adapter.calls == ['ord_123']
    assert matches == [expected]



def test_candidate_snapshots_falls_back_to_client_order_key_match() -> None:
    adapter = _Adapter(snapshot=None)
    direct = _snapshot(external_order_id='ord_999', client_order_key='ck_intent_1')
    other = _snapshot(external_order_id='ord_other', client_order_key='different')
    intent = _intent(external_order_id=None)

    matches = candidate_snapshots(intent=intent, snapshots=[other, direct], adapter=adapter)

    assert adapter.calls == []
    assert matches == [direct]
