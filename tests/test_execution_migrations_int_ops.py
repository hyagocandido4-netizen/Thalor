from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from natbin.runtime.execution_models import OrderIntent
from natbin.state.execution_repo import ExecutionRepository


def _create_legacy_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE order_intents (
                intent_id TEXT PRIMARY KEY,
                scope_tag TEXT NOT NULL,
                broker_name TEXT NOT NULL,
                account_mode TEXT NOT NULL,
                day TEXT NOT NULL,
                asset TEXT NOT NULL,
                interval_sec INTEGER NOT NULL,
                signal_ts INTEGER NOT NULL,
                decision_action TEXT NOT NULL,
                decision_conf REAL,
                decision_score REAL,
                stake_amount REAL NOT NULL,
                stake_currency TEXT NOT NULL,
                expiry_ts INTEGER NOT NULL,
                entry_deadline_utc TEXT NOT NULL,
                client_order_key TEXT NOT NULL,
                intent_state TEXT NOT NULL,
                broker_status TEXT NOT NULL,
                settlement_status TEXT,
                external_order_id TEXT,
                external_position_id TEXT,
                submit_attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error_code TEXT,
                last_error_message TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                submitted_at_utc TEXT,
                accepted_at_utc TEXT,
                settled_at_utc TEXT,
                last_reconcile_at_utc TEXT,
                portfolio_cycle_id TEXT,
                allocation_batch_id TEXT,
                cluster_key TEXT,
                portfolio_score REAL,
                UNIQUE(broker_name, account_mode, asset, interval_sec, day, signal_ts)
            )
            """
        )
        con.commit()
    finally:
        con.close()


def test_execution_repo_migrates_order_intents_for_intelligence_fields(tmp_path: Path) -> None:
    db_path = tmp_path / 'runs' / 'runtime_execution.sqlite3'
    _create_legacy_db(db_path)

    repo = ExecutionRepository(db_path)
    saved = repo.save_intent(
        OrderIntent(
            intent_id='intent_mig_001',
            scope_tag='EURUSD-OTC_300s',
            broker_name='fake',
            account_mode='PRACTICE',
            day='2026-03-21',
            asset='EURUSD-OTC',
            interval_sec=300,
            signal_ts=1773956100,
            decision_action='CALL',
            decision_conf=0.70,
            decision_score=0.61,
            stake_amount=2.0,
            stake_currency='BRL',
            expiry_ts=1773956400,
            entry_deadline_utc='2026-03-21T00:00:00+00:00',
            client_order_key='mig_client_order_key',
            intent_state='planned',
            broker_status='unknown',
            created_at_utc='2026-03-21T00:00:00+00:00',
            updated_at_utc='2026-03-21T00:00:00+00:00',
            allocation_batch_id='alloc_mig_001',
            cluster_key='fx',
            portfolio_score=0.72,
            intelligence_score=0.68,
            retrain_state='idle',
            retrain_priority='low',
            allocation_reason='selected_topk',
            allocation_rank=1,
            portfolio_feedback_json=json.dumps({'allocator_blocked': False, 'portfolio_score': 0.72}, ensure_ascii=False),
        )
    )

    assert saved.intelligence_score == 0.68
    assert saved.retrain_state == 'idle'
    assert saved.retrain_priority == 'low'
    assert saved.allocation_reason == 'selected_topk'
    assert saved.allocation_rank == 1
    assert json.loads(str(saved.portfolio_feedback_json))['portfolio_score'] == 0.72
