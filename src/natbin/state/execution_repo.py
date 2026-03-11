from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..runtime.execution_contracts import CONSUMES_QUOTA_STATES, PENDING_INTENT_STATES
from ..runtime.execution_models import BrokerOrderSnapshot, OrderIntent, OrderSubmitAttempt
from ..runtime.execution_policy import json_dumps, utc_now_iso
from ..runtime_perf import apply_runtime_sqlite_pragmas
from .execution_migrations import ensure_execution_db


class ExecutionRepository:
    def __init__(self, db_path: str | Path = 'runs/runtime_execution.sqlite3') -> None:
        self.path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path))
        apply_runtime_sqlite_pragmas(con)
        con.row_factory = sqlite3.Row
        ensure_execution_db(con)
        return con

    @staticmethod
    def _intent_from_row(row: sqlite3.Row | None) -> OrderIntent | None:
        if row is None:
            return None
        return OrderIntent(**dict(row))

    @staticmethod
    def _attempt_from_row(row: sqlite3.Row | None) -> OrderSubmitAttempt | None:
        if row is None:
            return None
        return OrderSubmitAttempt(**dict(row))

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row | None) -> BrokerOrderSnapshot | None:
        if row is None:
            return None
        data = dict(row)
        data['estimated_pnl'] = bool(data.get('estimated_pnl'))
        return BrokerOrderSnapshot(**data)

    def get_intent(self, intent_id: str) -> OrderIntent | None:
        con = self._connect()
        try:
            row = con.execute('SELECT * FROM order_intents WHERE intent_id=?', (str(intent_id),)).fetchone()
            return self._intent_from_row(row)
        finally:
            con.close()

    def get_intent_by_signal(self, *, broker_name: str, account_mode: str, asset: str, interval_sec: int, day: str, signal_ts: int) -> OrderIntent | None:
        con = self._connect()
        try:
            row = con.execute(
                'SELECT * FROM order_intents WHERE broker_name=? AND account_mode=? AND asset=? AND interval_sec=? AND day=? AND signal_ts=?',
                (str(broker_name), str(account_mode), str(asset), int(interval_sec), str(day), int(signal_ts)),
            ).fetchone()
            return self._intent_from_row(row)
        finally:
            con.close()

    def save_intent(self, intent: OrderIntent) -> OrderIntent:
        payload = intent.as_dict()
        cols = list(payload.keys())
        placeholders = ','.join('?' for _ in cols)
        updates = ','.join(f'{c}=excluded.{c}' for c in cols if c != 'intent_id')
        con = self._connect()
        try:
            con.execute(
                f"INSERT INTO order_intents ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(intent_id) DO UPDATE SET {updates}",
                [payload[c] for c in cols],
            )
            con.commit()
            row = con.execute('SELECT * FROM order_intents WHERE intent_id=?', (intent.intent_id,)).fetchone()
            return self._intent_from_row(row) or intent
        finally:
            con.close()

    def ensure_intent(self, intent: OrderIntent) -> tuple[OrderIntent, bool]:
        existing = self.get_intent_by_signal(
            broker_name=intent.broker_name,
            account_mode=intent.account_mode,
            asset=intent.asset,
            interval_sec=intent.interval_sec,
            day=intent.day,
            signal_ts=intent.signal_ts,
        )
        if existing is not None:
            return existing, False
        return self.save_intent(intent), True

    def record_attempt(self, attempt: OrderSubmitAttempt) -> OrderSubmitAttempt:
        payload = attempt.as_dict()
        cols = list(payload.keys())
        placeholders = ','.join('?' for _ in cols)
        updates = ','.join(f'{c}=excluded.{c}' for c in cols if c != 'attempt_id')
        con = self._connect()
        try:
            con.execute(
                f"INSERT INTO order_submit_attempts ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(attempt_id) DO UPDATE SET {updates}",
                [payload[c] for c in cols],
            )
            con.commit()
            row = con.execute('SELECT * FROM order_submit_attempts WHERE attempt_id=?', (attempt.attempt_id,)).fetchone()
            return self._attempt_from_row(row) or attempt
        finally:
            con.close()

    def list_attempts(self, intent_id: str) -> list[OrderSubmitAttempt]:
        con = self._connect()
        try:
            rows = con.execute(
                'SELECT * FROM order_submit_attempts WHERE intent_id=? ORDER BY attempt_no ASC',
                (str(intent_id),),
            ).fetchall()
            return [self._attempt_from_row(r) for r in rows if self._attempt_from_row(r) is not None]
        finally:
            con.close()

    def next_attempt_no(self, intent_id: str) -> int:
        con = self._connect()
        try:
            row = con.execute('SELECT COALESCE(MAX(attempt_no), 0) FROM order_submit_attempts WHERE intent_id=?', (str(intent_id),)).fetchone()
            return int((row[0] if row else 0) or 0) + 1
        finally:
            con.close()

    def add_event(self, *, event_id: str, event_type: str, created_at_utc: str | None = None, intent_id: str | None = None, broker_name: str | None = None, account_mode: str | None = None, external_order_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
        con = self._connect()
        try:
            con.execute(
                'INSERT OR REPLACE INTO order_events(event_id, intent_id, broker_name, account_mode, external_order_id, event_type, payload_json, created_at_utc) VALUES(?,?,?,?,?,?,?,?)',
                (
                    str(event_id),
                    intent_id,
                    broker_name,
                    account_mode,
                    external_order_id,
                    str(event_type),
                    json_dumps(payload or {}),
                    str(created_at_utc or utc_now_iso()),
                ),
            )
            con.commit()

            # Best-effort JSONL logging (easy tail/grep) — never fail the main path.
            # Tests expect this file to exist after add_event.
            try:
                from ..ops.structured_log import append_jsonl
                from ..security.redaction import collect_sensitive_values, sanitize_payload

                log_path = self.path.parent / 'logs' / 'execution_events.jsonl'
                event_payload = {
                    'event_id': str(event_id),
                    'intent_id': str(intent_id or ''),
                    'event_type': str(event_type),
                    'created_at_utc': str(created_at_utc or utc_now_iso()),
                    'broker_name': str(broker_name or ''),
                    'account_mode': str(account_mode or ''),
                    'external_order_id': str(external_order_id or ''),
                    'payload': payload or {},
                }
                event_payload = sanitize_payload(
                    event_payload,
                    sensitive_values=collect_sensitive_values(event_payload),
                    redact_email=True,
                )
                append_jsonl(log_path, event_payload)
            except Exception:
                pass
        finally:
            con.close()

    def upsert_broker_snapshot(self, snapshot: BrokerOrderSnapshot, *, intent_id: str | None = None) -> BrokerOrderSnapshot:
        payload = snapshot.as_dict()
        payload['intent_id'] = intent_id
        cols = list(payload.keys())
        placeholders = ','.join('?' for _ in cols)
        updates = ','.join(f'{c}=excluded.{c}' for c in cols if c not in {'broker_name', 'account_mode', 'external_order_id'})
        con = self._connect()
        try:
            con.execute(
                f"INSERT INTO broker_orders ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(broker_name, account_mode, external_order_id) DO UPDATE SET {updates}",
                [payload[c] for c in cols],
            )
            con.commit()
            row = con.execute(
                'SELECT broker_name, account_mode, external_order_id, client_order_key, asset, side, amount, currency, broker_status, opened_at_utc, expires_at_utc, closed_at_utc, gross_payout, net_pnl, settlement_status, estimated_pnl, raw_snapshot_json, last_seen_at_utc FROM broker_orders WHERE broker_name=? AND account_mode=? AND external_order_id=?',
                (snapshot.broker_name, snapshot.account_mode, snapshot.external_order_id),
            ).fetchone()
            return self._snapshot_from_row(row) or snapshot
        finally:
            con.close()

    def get_broker_order(self, *, broker_name: str, account_mode: str, external_order_id: str) -> BrokerOrderSnapshot | None:
        con = self._connect()
        try:
            row = con.execute(
                'SELECT broker_name, account_mode, external_order_id, client_order_key, asset, side, amount, currency, broker_status, opened_at_utc, expires_at_utc, closed_at_utc, gross_payout, net_pnl, settlement_status, estimated_pnl, raw_snapshot_json, last_seen_at_utc FROM broker_orders WHERE broker_name=? AND account_mode=? AND external_order_id=?',
                (str(broker_name), str(account_mode), str(external_order_id)),
            ).fetchone()
            return self._snapshot_from_row(row)
        finally:
            con.close()

    def list_recent_intents(self, *, asset: str | None = None, interval_sec: int | None = None, states: Sequence[str] | None = None, limit: int = 20) -> list[OrderIntent]:
        sql = 'SELECT * FROM order_intents WHERE 1=1'
        params: list[Any] = []
        if asset is not None:
            sql += ' AND asset=?'
            params.append(str(asset))
        if interval_sec is not None:
            sql += ' AND interval_sec=?'
            params.append(int(interval_sec))
        if states:
            sql += ' AND intent_state IN (' + ','.join('?' for _ in states) + ')'
            params.extend([str(s) for s in states])
        sql += ' ORDER BY signal_ts DESC LIMIT ?'
        params.append(max(1, int(limit)))
        con = self._connect()
        try:
            rows = con.execute(sql, tuple(params)).fetchall()
            out: list[OrderIntent] = []
            for row in rows:
                item = self._intent_from_row(row)
                if item is not None:
                    out.append(item)
            return out
        finally:
            con.close()

    def list_pending_intents(self, *, asset: str | None = None, interval_sec: int | None = None, states: Sequence[str] | None = None) -> list[OrderIntent]:
        states = list(states or PENDING_INTENT_STATES)
        return self.list_recent_intents(asset=asset, interval_sec=interval_sec, states=states, limit=500)

    def count_consuming_intents(self, *, asset: str, interval_sec: int, day: str) -> int:
        con = self._connect()
        try:
            marks = ','.join('?' for _ in CONSUMES_QUOTA_STATES)
            row = con.execute(
                f'SELECT COUNT(*) FROM order_intents WHERE asset=? AND interval_sec=? AND day=? AND intent_state IN ({marks})',
                (str(asset), int(interval_sec), str(day), *sorted(CONSUMES_QUOTA_STATES)),
            ).fetchone()
            return int((row[0] if row else 0) or 0)
        finally:
            con.close()

    def count_pending_unknown(self, *, asset: str, interval_sec: int) -> int:
        con = self._connect()
        try:
            row = con.execute(
                'SELECT COUNT(*) FROM order_intents WHERE asset=? AND interval_sec=? AND intent_state=?',
                (str(asset), int(interval_sec), 'submitted_unknown'),
            ).fetchone()
            return int((row[0] if row else 0) or 0)
        finally:
            con.close()

    def count_open_positions(self, *, asset: str, interval_sec: int) -> int:
        con = self._connect()
        try:
            row = con.execute(
                'SELECT COUNT(*) FROM order_intents WHERE asset=? AND interval_sec=? AND intent_state=?',
                (str(asset), int(interval_sec), 'accepted_open'),
            ).fetchone()
            return int((row[0] if row else 0) or 0)
        finally:
            con.close()

    def last_consuming_signal_ts(self, *, asset: str, interval_sec: int, day: str) -> int | None:
        con = self._connect()
        try:
            marks = ','.join('?' for _ in CONSUMES_QUOTA_STATES)
            row = con.execute(
                f'SELECT MAX(signal_ts) FROM order_intents WHERE asset=? AND interval_sec=? AND day=? AND intent_state IN ({marks})',
                (str(asset), int(interval_sec), str(day), *sorted(CONSUMES_QUOTA_STATES)),
            ).fetchone()
            value = row[0] if row else None
            return int(value) if value is not None else None
        finally:
            con.close()

    def count_orphan_orders(self, *, asset: str | None = None, interval_sec: int | None = None) -> int:
        sql = 'SELECT COUNT(*) FROM broker_orders WHERE intent_id IS NULL'
        params: list[Any] = []
        if asset is not None:
            sql += ' AND asset=?'
            params.append(str(asset))
        # Orphan snapshots do not have a reliable interval column on their own.
        # Keep the filter intentionally conservative and scoped by asset only.
        _ = interval_sec
        con = self._connect()
        try:
            row = con.execute(sql, tuple(params)).fetchone()
            return int((row[0] if row else 0) or 0)
        finally:
            con.close()

    def execution_summary(self, *, asset: str, interval_sec: int, day: str) -> dict[str, Any]:
        intents = self.list_recent_intents(asset=asset, interval_sec=interval_sec, limit=200)
        by_state: dict[str, int] = {}
        for item in intents:
            by_state[item.intent_state] = by_state.get(item.intent_state, 0) + 1
        return {
            'asset': str(asset),
            'interval_sec': int(interval_sec),
            'day': str(day),
            'consuming_today': self.count_consuming_intents(asset=asset, interval_sec=interval_sec, day=day),
            'pending_unknown': self.count_pending_unknown(asset=asset, interval_sec=interval_sec),
            'open_positions': self.count_open_positions(asset=asset, interval_sec=interval_sec),
            'recent_states': by_state,
        }
