#!/usr/bin/env python
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _ok(msg: str) -> None:
    print(f'[execution-repo][OK] {msg}')


def _fail(msg: str) -> None:
    print(f'[execution-repo][FAIL] {msg}')
    raise SystemExit(2)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / 'src'
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.runtime.execution_models import OrderIntent, OrderSubmitAttempt, BrokerOrderSnapshot
    from natbin.runtime.execution_policy import make_attempt_id, utc_now_iso
    from natbin.state.execution_repo import ExecutionRepository

    with tempfile.TemporaryDirectory() as td:
        repo = ExecutionRepository(Path(td) / 'runtime_execution.sqlite3')
        now = utc_now_iso()
        intent = OrderIntent(
            intent_id='intent_smoke_001',
            scope_tag='EURUSD-OTC_300s',
            broker_name='fake',
            account_mode='PRACTICE',
            day='2026-03-05',
            asset='EURUSD-OTC',
            interval_sec=300,
            signal_ts=1772668800,
            decision_action='CALL',
            decision_conf=0.61,
            decision_score=0.71,
            stake_amount=2.0,
            stake_currency='BRL',
            expiry_ts=1772669100,
            entry_deadline_utc='2026-03-05T00:05:02+00:00',
            client_order_key='thalor-intent_smoke_001',
            intent_state='planned',
            broker_status='unknown',
            created_at_utc=now,
            updated_at_utc=now,
        )
        stored, created = repo.ensure_intent(intent)
        if not created:
            _fail('first ensure_intent should create a row')
        again, created_again = repo.ensure_intent(intent)
        if created_again or again.intent_id != intent.intent_id:
            _fail('ensure_intent idempotency failed')
        _ok('idempotent intent creation ok')

        attempt = OrderSubmitAttempt(
            attempt_id=make_attempt_id(intent_id=intent.intent_id, attempt_no=1),
            intent_id=intent.intent_id,
            attempt_no=1,
            requested_at_utc=now,
            finished_at_utc=now,
            transport_status='ack',
            latency_ms=12,
            external_order_id='fake_order_001',
            error_code=None,
            error_message=None,
            request_json='{}',
            response_json='{}',
        )
        repo.record_attempt(attempt)
        if repo.next_attempt_no(intent.intent_id) != 2:
            _fail('next_attempt_no mismatch after first attempt')
        _ok('attempt journal ok')

        settled = repo.save_intent(intent.__class__(**{
            **stored.as_dict(),
            'intent_state': 'settled',
            'broker_status': 'closed_win',
            'settlement_status': 'win',
            'external_order_id': 'fake_order_001',
            'submit_attempt_count': 1,
            'submitted_at_utc': now,
            'accepted_at_utc': now,
            'settled_at_utc': now,
            'updated_at_utc': now,
            'last_reconcile_at_utc': now,
        }))
        repo.upsert_broker_snapshot(BrokerOrderSnapshot(
            broker_name='fake',
            account_mode='PRACTICE',
            external_order_id='fake_order_001',
            client_order_key='thalor-intent_smoke_001',
            asset='EURUSD-OTC',
            side='CALL',
            amount=2.0,
            currency='BRL',
            broker_status='closed_win',
            opened_at_utc=now,
            expires_at_utc='2026-03-05T00:05:00+00:00',
            closed_at_utc=now,
            gross_payout=3.6,
            net_pnl=1.6,
            settlement_status='win',
            estimated_pnl=False,
            raw_snapshot_json='{}',
            last_seen_at_utc=now,
        ), intent_id=settled.intent_id)
        if repo.count_consuming_intents(asset='EURUSD-OTC', interval_sec=300, day='2026-03-05') != 1:
            _fail('settled intent should consume quota')
        if repo.count_open_positions(asset='EURUSD-OTC', interval_sec=300) != 0:
            _fail('settled intent should not count as open position')
        _ok('quota summary over execution ledger ok')

    print('[execution-repo] ALL OK')


if __name__ == '__main__':
    main()
