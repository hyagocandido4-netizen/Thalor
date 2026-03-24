from __future__ import annotations

import sqlite3


_ORDER_INTENT_ADDITIONAL_COLUMNS: dict[str, str] = {
    'intelligence_score': 'REAL',
    'retrain_state': 'TEXT',
    'retrain_priority': 'TEXT',
    'allocation_reason': 'TEXT',
    'allocation_rank': 'INTEGER',
    'portfolio_feedback_json': 'TEXT',
}


def _ensure_order_intents_columns(con: sqlite3.Connection) -> None:
    info = con.execute('PRAGMA table_info(order_intents)').fetchall()
    cols = {str(row[1]) for row in info}
    for name, col_type in _ORDER_INTENT_ADDITIONAL_COLUMNS.items():
        if name not in cols:
            con.execute(f'ALTER TABLE order_intents ADD COLUMN {name} {col_type}')


def ensure_execution_db(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS order_intents (
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
            intelligence_score REAL,
            retrain_state TEXT,
            retrain_priority TEXT,
            allocation_reason TEXT,
            allocation_rank INTEGER,
            portfolio_feedback_json TEXT,
            UNIQUE(broker_name, account_mode, asset, interval_sec, day, signal_ts)
        )
        """
    )
    _ensure_order_intents_columns(con)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS order_submit_attempts (
            attempt_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            requested_at_utc TEXT NOT NULL,
            finished_at_utc TEXT,
            transport_status TEXT NOT NULL,
            latency_ms INTEGER,
            external_order_id TEXT,
            error_code TEXT,
            error_message TEXT,
            request_json TEXT NOT NULL,
            response_json TEXT,
            UNIQUE(intent_id, attempt_no)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_orders (
            broker_name TEXT NOT NULL,
            account_mode TEXT NOT NULL,
            external_order_id TEXT NOT NULL,
            intent_id TEXT,
            client_order_key TEXT,
            asset TEXT NOT NULL,
            side TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            broker_status TEXT NOT NULL,
            opened_at_utc TEXT,
            expires_at_utc TEXT,
            closed_at_utc TEXT,
            gross_payout REAL,
            net_pnl REAL,
            settlement_status TEXT,
            estimated_pnl INTEGER NOT NULL DEFAULT 0,
            raw_snapshot_json TEXT NOT NULL,
            last_seen_at_utc TEXT NOT NULL,
            PRIMARY KEY (broker_name, account_mode, external_order_id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS order_events (
            event_id TEXT PRIMARY KEY,
            intent_id TEXT,
            broker_name TEXT,
            account_mode TEXT,
            external_order_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS reconcile_cursors (
            scope_tag TEXT NOT NULL,
            broker_name TEXT NOT NULL,
            account_mode TEXT NOT NULL,
            stream_name TEXT NOT NULL,
            cursor_value TEXT,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY (scope_tag, broker_name, account_mode, stream_name)
        )
        """
    )
    con.execute('CREATE INDEX IF NOT EXISTS idx_order_intents_scope_state ON order_intents(scope_tag, intent_state)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_order_intents_asset_day ON order_intents(asset, interval_sec, day)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_order_intents_external_id ON order_intents(external_order_id)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_order_submit_attempts_intent ON order_submit_attempts(intent_id, attempt_no)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_broker_orders_intent ON broker_orders(intent_id)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_broker_orders_client_key ON broker_orders(client_order_key)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_order_events_intent ON order_events(intent_id, created_at_utc)')
    con.commit()
