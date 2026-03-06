from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from .execution_contracts import (
    BROKER_CANCELLED,
    BROKER_CLOSED_LOSS,
    BROKER_CLOSED_REFUND,
    BROKER_CLOSED_WIN,
    BROKER_OPEN,
    BROKER_REJECTED,
    BROKER_UNKNOWN,
    CONSUMES_QUOTA_STATES,
    INTENT_ACCEPTED_OPEN,
    INTENT_EXPIRED_UNCONFIRMED,
    INTENT_EXPIRED_UNSUBMITTED,
    INTENT_REJECTED,
    INTENT_SETTLED,
    SETTLEMENT_LOSS,
    SETTLEMENT_REFUND,
    SETTLEMENT_WIN,
    TERMINAL_INTENT_STATES,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec='seconds')


def ensure_utc_iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat(timespec='seconds')
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec='seconds')


def parse_utc_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def signal_day_from_ts(ts: int, *, timezone_name: str = 'UTC') -> str:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(str(timezone_name or 'UTC'))
    except Exception:
        tz = UTC
    dt = datetime.fromtimestamp(int(ts), tz=UTC).astimezone(tz)
    return dt.date().isoformat()


def make_intent_id(*, broker_name: str, account_mode: str, asset: str, interval_sec: int, day: str, signal_ts: int, action: str) -> str:
    seed = f'{broker_name}|{account_mode}|{asset}|{int(interval_sec)}|{day}|{int(signal_ts)}|{action}'
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]


def make_client_order_key(*, prefix: str, intent_id: str) -> str:
    return f'{str(prefix).strip() or "thalor"}-{intent_id}'


def make_attempt_id(*, intent_id: str, attempt_no: int) -> str:
    seed = f'{intent_id}|attempt|{int(attempt_no)}'
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]


def compute_expiry_ts(*, signal_ts: int, interval_sec: int) -> int:
    return int(signal_ts) + max(1, int(interval_sec))


def compute_entry_deadline_utc(*, signal_ts: int, interval_sec: int, grace_sec: int = 2) -> str:
    ts = int(signal_ts) + max(0, int(interval_sec)) + max(0, int(grace_sec))
    return datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec='seconds')


def intent_consumes_quota(intent_state: str) -> bool:
    return str(intent_state) in CONSUMES_QUOTA_STATES


def intent_is_terminal(intent_state: str) -> bool:
    return str(intent_state) in TERMINAL_INTENT_STATES


def settlement_from_broker_status(broker_status: str) -> str | None:
    status = str(broker_status or '').strip().lower()
    if status == BROKER_CLOSED_WIN:
        return SETTLEMENT_WIN
    if status == BROKER_CLOSED_LOSS:
        return SETTLEMENT_LOSS
    if status == BROKER_CLOSED_REFUND:
        return SETTLEMENT_REFUND
    return None


def intent_state_from_broker_status(broker_status: str) -> str:
    status = str(broker_status or '').strip().lower()
    if status == BROKER_OPEN:
        return INTENT_ACCEPTED_OPEN
    if status in {BROKER_CLOSED_WIN, BROKER_CLOSED_LOSS, BROKER_CLOSED_REFUND}:
        return INTENT_SETTLED
    if status in {BROKER_REJECTED, BROKER_CANCELLED}:
        return INTENT_REJECTED
    if status == 'not_found':
        return INTENT_EXPIRED_UNCONFIRMED
    return BROKER_UNKNOWN


def match_fingerprint(*, asset: str, side: str, amount: float, expiry_ts: int, opened_at_utc: str | None, snapshot_asset: str, snapshot_side: str, snapshot_amount: float, snapshot_expires_at_utc: str | None, delta_sec: int = 15) -> bool:
    if str(asset) != str(snapshot_asset):
        return False
    if str(side).upper() != str(snapshot_side).upper():
        return False
    if abs(float(amount) - float(snapshot_amount)) > 1e-9:
        return False
    snap_exp = parse_utc_iso(snapshot_expires_at_utc)
    if snap_exp is not None:
        desired = datetime.fromtimestamp(int(expiry_ts), tz=UTC)
        if abs(int((snap_exp - desired).total_seconds())) > max(1, int(delta_sec)):
            return False
    if opened_at_utc:
        opened = parse_utc_iso(opened_at_utc)
        if opened is not None:
            lower = datetime.fromtimestamp(int(expiry_ts), tz=UTC) - timedelta(seconds=max(1, int(delta_sec)) + 3600)
            upper = datetime.fromtimestamp(int(expiry_ts), tz=UTC) + timedelta(seconds=max(1, int(delta_sec)))
            if opened < lower or opened > upper:
                return False
    return True


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def min_signal_ts(rows: Iterable[dict[str, Any]]) -> int | None:
    vals = [int(r.get('signal_ts') or 0) for r in rows if int(r.get('signal_ts') or 0) > 0]
    return min(vals) if vals else None


def max_signal_ts(rows: Iterable[dict[str, Any]]) -> int | None:
    vals = [int(r.get('signal_ts') or 0) for r in rows if int(r.get('signal_ts') or 0) > 0]
    return max(vals) if vals else None
