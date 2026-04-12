from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from .base import BrokerScope
from ..runtime.execution_contracts import (
    BROKER_CANCELLED,
    BROKER_CLOSED_LOSS,
    BROKER_CLOSED_REFUND,
    BROKER_CLOSED_WIN,
    BROKER_NOT_FOUND,
    BROKER_OPEN,
    BROKER_REJECTED,
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
from ..config.execution_mode import execution_mode_uses_broker_submit, execution_mode_is_practice, normalize_execution_mode
from ..runtime.execution_policy import ensure_utc_iso, json_dumps, parse_utc_iso, utc_now, utc_now_iso
from ..config.env import env_bool


class IQExecutionClient(Protocol):
    def connect(self, retries: int | None = None, sleep_s: float | None = None, connect_timeout_s: float | None = None) -> None: ...

    def ensure_connection(self) -> None: ...

    def submit_binary_option(self, *, asset: str, amount: float, side: str, duration_min: int): ...

    def get_async_order(self, order_id): ...

    def get_betinfo_safe(self, order_id): ...

    def get_recent_closed_options(self, limit: int = 20): ...

    def get_option_open_by_other_pc(self): ...

    def list_async_orders(self) -> dict[str, Any]: ...

    def list_socket_opened_orders(self) -> dict[str, Any]: ...

    def list_socket_closed_orders(self) -> dict[str, Any]: ...

    def asset_name_from_opcode(self, opcode: Any) -> str | None: ...


@dataclass(frozen=True)
class _BridgeRecord:
    external_order_id: str
    client_order_key: str | None
    account_mode: str
    asset: str
    side: str
    amount: float
    currency: str
    interval_sec: int
    scope_tag: str
    signal_ts: int
    expiry_ts: int
    opened_at_utc: str
    expires_at_utc: str | None
    entry_deadline_utc: str | None
    raw_request: dict[str, Any]
    raw_submit_response: dict[str, Any]
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            'external_order_id': self.external_order_id,
            'client_order_key': self.client_order_key,
            'account_mode': self.account_mode,
            'asset': self.asset,
            'side': self.side,
            'amount': self.amount,
            'currency': self.currency,
            'interval_sec': self.interval_sec,
            'scope_tag': self.scope_tag,
            'signal_ts': self.signal_ts,
            'expiry_ts': self.expiry_ts,
            'opened_at_utc': self.opened_at_utc,
            'expires_at_utc': self.expires_at_utc,
            'entry_deadline_utc': self.entry_deadline_utc,
            'raw_request': self.raw_request,
            'raw_submit_response': self.raw_submit_response,
            'metadata': self.metadata,
        }


@contextmanager
def _file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt  # type: ignore

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl  # type: ignore

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield fh
    finally:
        try:
            if os.name == 'nt':
                import msvcrt  # type: ignore

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # type: ignore

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass


class IQOptionAdapter:
    """Live bridge for IQ Option binary/turbo execution.

    The adapter stays lazy-imported so CI and paper mode do not require the
    community ``iqoptionapi`` package at module import time. Package M2 adds
    real submit/fetch/reconcile support while keeping the contract deterministic
    through a small local bridge-state file under ``runs/``.
    """

    def __init__(
        self,
        *,
        repo_root: str | Path = '.',
        account_mode: str = 'PRACTICE',
        execution_mode: str = 'live',
        broker_config: dict[str, Any] | None = None,
        transport_config: dict[str, Any] | None = None,
        request_metrics_config: dict[str, Any] | None = None,
        state_path: str | Path | None = None,
        settle_grace_sec: int = 30,
        history_limit: int = 20,
        client_factory: Callable[[], IQExecutionClient] | None = None,
        backend: IQExecutionClient | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.account_mode = str(account_mode or 'PRACTICE').upper()
        self.execution_mode = normalize_execution_mode(execution_mode or 'live', default='live')
        self.broker_config = dict(broker_config or {})
        self.transport_config = dict(transport_config or {})
        self.request_metrics_config = dict(request_metrics_config or {})
        self.path = Path(state_path) if state_path is not None else (self.repo_root / 'runs' / 'iqoption_bridge_state.json')
        if not self.path.is_absolute():
            self.path = self.repo_root / self.path
        self.settle_grace_sec = max(1, int(settle_grace_sec))
        self.history_limit = max(1, int(history_limit))
        self._client_factory = client_factory
        self._backend = backend

    def broker_name(self) -> str:
        return 'iqoption'

    def _live_enabled(self) -> bool:
        return execution_mode_uses_broker_submit(self.execution_mode)

    def _real_account_guard_reason(self) -> str | None:
        """Fail closed unless REAL mode was explicitly enabled.

        Safety contract for Phase 2 package 2.1:
        - PRACTICE remains the default and always works.
        - REAL never activates implicitly; the operator must opt in via
          ``THALOR_EXECUTION_ALLOW_REAL=1``.
        """

        if execution_mode_is_practice(self.execution_mode) and str(self.account_mode or 'PRACTICE').upper() != 'PRACTICE':
            return 'iqoption_practice_mode_requires_practice_account'
        if str(self.account_mode or 'PRACTICE').upper() != 'REAL':
            return None
        if env_bool('THALOR_EXECUTION_ALLOW_REAL', False):
            return None
        return 'iqoption_real_account_blocked'

    @staticmethod
    def _extract_secret(value: Any) -> str | None:
        if value is None:
            return None
        try:
            if hasattr(value, 'get_secret_value'):
                raw = value.get_secret_value()
            else:
                raw = value
        except Exception:
            raw = value
        s = str(raw).strip()
        return s or None

    def _credentials(self) -> tuple[str | None, str | None]:
        email = self._extract_secret(self.broker_config.get('email')) or (os.getenv('IQ_EMAIL') or '').strip() or None
        password = self._extract_secret(self.broker_config.get('password')) or (os.getenv('IQ_PASSWORD') or '').strip() or None
        return email, password

    def _transport_manager(self):
        payload = dict(self.transport_config or {})
        if not payload:
            return None
        try:
            from ..utils.network_transport import NetworkTransportConfig, NetworkTransportManager
        except Exception:
            return None
        for key in ('endpoint_file', 'endpoints_file', 'structured_log_path'):
            raw = payload.get(key)
            if raw in (None, ''):
                continue
            candidate = Path(str(raw))
            if not candidate.is_absolute():
                candidate = self.repo_root / candidate
            payload[key] = str(candidate)
        config = NetworkTransportConfig.from_sources(payload)
        return NetworkTransportManager(config)

    def _request_metrics(self):
        payload = dict(self.request_metrics_config or {})
        if not payload:
            return None
        try:
            from ..utils.request_metrics import RequestMetrics, RequestMetricsConfig
        except Exception:
            return None
        raw = payload.get('structured_log_path')
        if raw not in (None, ''):
            candidate = Path(str(raw))
            if not candidate.is_absolute():
                candidate = self.repo_root / candidate
            payload['structured_log_path'] = candidate
        config = RequestMetricsConfig.from_sources(payload)
        return RequestMetrics.from_config(config)

    def _dependency_status(self) -> dict[str, Any]:
        try:
            from ..adapters.iq_client import iqoption_dependency_status
            transport_manager = None
            try:
                transport_manager = self._transport_manager()
            except Exception:
                transport_manager = None
            return iqoption_dependency_status(transport_manager=transport_manager)
        except Exception as exc:
            return {'available': False, 'reason': f'{type(exc).__name__}: {exc}'}

    def _import_client_classes(self):
        from ..adapters.iq_client import IQClient, IQDependencyUnavailable, iqoption_dependency_status  # lazy import
        from ..adapters.iq_client import IQConfig

        status = iqoption_dependency_status()
        if not bool(status.get('available', True)):
            raise IQDependencyUnavailable(str(status.get('reason') or 'iqoption_dependency_missing'))
        return IQClient, IQConfig

    def _make_client(self) -> IQExecutionClient:
        if self._client_factory is not None:
            return self._client_factory()
        email, password = self._credentials()
        if not email or not password:
            raise RuntimeError('iqoption_missing_credentials')
        IQClient, IQConfig = self._import_client_classes()
        balance_mode = self._extract_secret(self.broker_config.get('balance_mode')) or self.account_mode
        cfg = IQConfig(
            email=email,
            password=password,
            balance_mode=str(balance_mode or self.account_mode).upper(),
            transport=self.transport_config or None,
            connect_timeout_s=float(self.broker_config.get('timeout_connect_s') or 25),
        )
        transport_manager = None
        request_metrics = None
        try:
            transport_manager = self._transport_manager()
        except Exception:
            transport_manager = None
        try:
            request_metrics = self._request_metrics()
        except Exception:
            request_metrics = None
        return IQClient(cfg, transport_manager=transport_manager, request_metrics=request_metrics)

    def _client(self) -> IQExecutionClient:
        if self._backend is None:
            self._backend = self._make_client()
        return self._backend

    def _connect_kwargs(self) -> dict[str, Any]:
        retries = self._as_int(self.broker_config.get('connect_retries'))
        sleep_s = self._as_float(self.broker_config.get('connect_sleep_s'))
        timeout_s = self._as_float(self.broker_config.get('timeout_connect_s'))
        out: dict[str, Any] = {}
        if retries is not None:
            out['retries'] = retries
        if sleep_s is not None:
            out['sleep_s'] = sleep_s
        if timeout_s is not None:
            out['connect_timeout_s'] = timeout_s
        return out

    @contextmanager
    def _temporary_client_env(self):
        updates: dict[str, str] = {}
        if self._as_float(self.broker_config.get('connect_sleep_max_s')) is not None:
            updates['IQ_CONNECT_SLEEP_MAX_S'] = str(float(self.broker_config.get('connect_sleep_max_s')))
        if self._as_float(self.broker_config.get('timeout_connect_s')) is not None:
            updates['IQ_CONNECT_TIMEOUT_S'] = str(float(self.broker_config.get('timeout_connect_s')))
        if self._as_float(self.broker_config.get('api_throttle_min_interval_s')) is not None:
            updates['IQ_THROTTLE_MIN_INTERVAL_S'] = str(float(self.broker_config.get('api_throttle_min_interval_s')))
        if self._as_float(self.broker_config.get('api_throttle_jitter_s')) is not None:
            updates['IQ_THROTTLE_JITTER_S'] = str(float(self.broker_config.get('api_throttle_jitter_s')))
        previous: dict[str, str | None] = {k: os.environ.get(k) for k in updates}
        try:
            for k, v in updates.items():
                os.environ[k] = v
            yield
        finally:
            for k, prev in previous.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev

    def _ensure_client_connected(self, client: IQExecutionClient) -> None:
        with self._temporary_client_env():
            client.connect(**self._connect_kwargs())
            client.ensure_connection()

    def _load_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return {'orders': {}}
        lock_path = self.path.with_suffix(self.path.suffix + '.lock')
        with _file_lock(lock_path):
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
            except Exception:
                raw = {'orders': {}}
        if not isinstance(raw, dict):
            return {'orders': {}}
        raw.setdefault('orders', {})
        return raw

    def _save_state(self, state: dict[str, Any]) -> None:
        state.setdefault('orders', {})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + '.tmp')
        lock_path = self.path.with_suffix(self.path.suffix + '.lock')
        with _file_lock(lock_path):
            tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
            tmp.replace(self.path)

    def _record_from_dict(self, data: dict[str, Any] | None) -> _BridgeRecord | None:
        if not isinstance(data, dict):
            return None
        try:
            return _BridgeRecord(
                external_order_id=str(data.get('external_order_id') or ''),
                client_order_key=data.get('client_order_key'),
                account_mode=str(data.get('account_mode') or self.account_mode),
                asset=str(data.get('asset') or ''),
                side=str(data.get('side') or ''),
                amount=float(data.get('amount') or 0.0),
                currency=str(data.get('currency') or 'BRL'),
                interval_sec=int(data.get('interval_sec') or 0),
                scope_tag=str(data.get('scope_tag') or ''),
                signal_ts=int(data.get('signal_ts') or 0),
                expiry_ts=int(data.get('expiry_ts') or 0),
                opened_at_utc=str(data.get('opened_at_utc') or utc_now_iso()),
                expires_at_utc=ensure_utc_iso(data.get('expires_at_utc')),
                entry_deadline_utc=ensure_utc_iso(data.get('entry_deadline_utc')),
                raw_request=dict(data.get('raw_request') or {}),
                raw_submit_response=dict(data.get('raw_submit_response') or {}),
                metadata=dict(data.get('metadata') or {}),
            )
        except Exception:
            return None

    def _get_record(self, external_order_id: str) -> _BridgeRecord | None:
        state = self._load_state()
        return self._record_from_dict((state.get('orders') or {}).get(str(external_order_id)))

    def _upsert_record(self, record: _BridgeRecord) -> None:
        state = self._load_state()
        orders = dict(state.get('orders') or {})
        orders[str(record.external_order_id)] = record.as_dict()
        state['orders'] = orders
        self._save_state(state)

    def _record_scope_match(self, record: _BridgeRecord, scope: BrokerScope) -> bool:
        if str(record.account_mode).upper() != str(scope.account_mode).upper():
            return False
        if str(record.asset) != str(scope.asset):
            return False
        if int(record.interval_sec or 0) not in {0, int(scope.interval_sec)}:
            return False
        return True

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value in (None, ''):
            return None
        try:
            if isinstance(value, bool):
                return int(value)
            return int(float(value))
        except Exception:
            return None

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value in (None, ''):
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_side(value: Any) -> str | None:
        s = str(value or '').strip().upper()
        if not s:
            return None
        if s in {'CALL', 'C'}:
            return 'CALL'
        if s in {'PUT', 'P'}:
            return 'PUT'
        return s

    @staticmethod
    def _normalize_win_token(value: Any) -> str | None:
        if value is None:
            return None
        s = str(value).strip().lower()
        if not s:
            return None
        if s in {'win', 'won', 'true', '1'}:
            return 'win'
        if s in {'loose', 'lose', 'loss', 'lost', 'false', '0'}:
            return 'loss'
        if s in {'equal', 'refund', 'draw'}:
            return 'refund'
        return s

    @staticmethod
    def _normalize_health_reason(exc: Exception) -> str:
        raw = f'{type(exc).__name__}: {exc}'
        low = raw.lower()
        if 'invalid_credentials' in low or 'wrong credentials' in low:
            return 'iqoption_invalid_credentials'
        if 'missing_credentials' in low:
            return 'iqoption_missing_credentials'
        if 'timeout' in low:
            return 'iqoption_connect_timeout'
        if 'dependency_missing' in low or 'no module named' in low:
            return 'iqoption_dependency_missing'
        return f'iqoption_connect_failed:{type(exc).__name__}'

    @classmethod
    def _ts_to_iso(cls, value: Any) -> str | None:
        if value in (None, ''):
            return None
        if isinstance(value, str) and 'T' in value:
            return ensure_utc_iso(value)
        ts = cls._as_int(value)
        if ts is None:
            return None
        if ts > 1_000_000_000_000:
            ts = int(ts / 1000)
        return datetime.fromtimestamp(int(ts), tz=UTC).isoformat(timespec='seconds')

    @staticmethod
    def _error_code_from_message(msg: str) -> str:
        m = str(msg or '').strip().lower()
        if not m:
            return 'iqoption_error'
        safe = []
        for ch in m:
            safe.append(ch if ch.isalnum() else '_')
        return ''.join(safe)[:64].strip('_') or 'iqoption_error'

    @staticmethod
    def _transport_from_exception(exc: Exception) -> str:
        msg = f'{type(exc).__name__}: {exc}'.lower()
        if 'timeout' in msg or 'timed out' in msg:
            return TRANSPORT_TIMEOUT
        return TRANSPORT_EXCEPTION

    @staticmethod
    def _duration_min(interval_sec: int) -> int:
        interval = int(interval_sec)
        if interval <= 0 or interval % 60 != 0:
            raise ValueError(f'unsupported_iqoption_interval_sec:{interval}')
        return max(1, interval // 60)

    def _payload_msg(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        msg = payload.get('msg')
        if isinstance(msg, dict):
            return msg
        return payload

    def _record_asset(self, record: _BridgeRecord | None, msg: dict[str, Any], client: IQExecutionClient | None = None) -> str:
        if record is not None and record.asset:
            return str(record.asset)
        asset = msg.get('asset') or msg.get('active')
        if asset:
            return str(asset)
        active_id = msg.get('active_id') or msg.get('activeId') or msg.get('activeid')
        if client is not None:
            try:
                name = client.asset_name_from_opcode(active_id)
                if name:
                    return str(name)
            except Exception:
                pass
        return ''

    def _record_side(self, record: _BridgeRecord | None, msg: dict[str, Any]) -> str:
        if record is not None and record.side:
            return str(record.side)
        return self._normalize_side(msg.get('direction') or msg.get('side') or msg.get('action')) or ''

    def _record_amount(self, record: _BridgeRecord | None, msg: dict[str, Any]) -> float:
        if record is not None:
            return float(record.amount)
        for key in ('sum', 'value', 'amount', 'deposit', 'price'):
            val = self._as_float(msg.get(key))
            if val is not None:
                return float(val)
        return 0.0

    def _record_currency(self, record: _BridgeRecord | None, msg: dict[str, Any]) -> str:
        if record is not None and record.currency:
            return str(record.currency)
        cur = msg.get('currency') or msg.get('currency_id') or 'BRL'
        return str(cur)

    def _record_opened_at(self, record: _BridgeRecord | None, msg: dict[str, Any]) -> str | None:
        if record is not None and record.opened_at_utc:
            return ensure_utc_iso(record.opened_at_utc)
        for key in ('created', 'created_at', 'open_time', 'open_at', 'purchase_time', 'timestamp'):
            iso = self._ts_to_iso(msg.get(key))
            if iso:
                return iso
        return None

    def _record_expires_at(self, record: _BridgeRecord | None, msg: dict[str, Any]) -> str | None:
        if record is not None and record.expires_at_utc:
            return ensure_utc_iso(record.expires_at_utc)
        for key in ('expiration_time', 'expired', 'exp_value', 'close_time', 'expired_at'):
            iso = self._ts_to_iso(msg.get(key))
            if iso:
                return iso
        return None

    def _terminal_from_values(self, *, amount: float, win_token: Any, gross_value: Any) -> tuple[str, str | None, float | None, float | None]:
        token = self._normalize_win_token(win_token)
        gross = self._as_float(gross_value)
        if token == 'win':
            gross = float(gross if gross is not None else amount)
            return BROKER_CLOSED_WIN, SETTLEMENT_WIN, gross, round(gross - amount, 8)
        if token == 'loss':
            gross = float(gross if gross is not None else 0.0)
            return BROKER_CLOSED_LOSS, SETTLEMENT_LOSS, gross, round(gross - amount, 8)
        if token == 'refund':
            gross = float(gross if gross is not None else amount)
            return BROKER_CLOSED_REFUND, SETTLEMENT_REFUND, gross, 0.0
        if gross is None:
            return BROKER_UNKNOWN, None, None, None
        if gross > amount:
            return BROKER_CLOSED_WIN, SETTLEMENT_WIN, gross, round(gross - amount, 8)
        if abs(gross - amount) <= 1e-9:
            return BROKER_CLOSED_REFUND, SETTLEMENT_REFUND, gross, 0.0
        return BROKER_CLOSED_LOSS, SETTLEMENT_LOSS, gross, round(gross - amount, 8)

    def _open_snapshot(self, *, external_order_id: str, record: _BridgeRecord | None, raw_payload: Any, client: IQExecutionClient | None = None, source: str = 'broker') -> BrokerOrderSnapshot:
        msg = self._payload_msg(raw_payload)
        return BrokerOrderSnapshot(
            broker_name=self.broker_name(),
            account_mode=(record.account_mode if record is not None else self.account_mode),
            external_order_id=str(external_order_id),
            client_order_key=(record.client_order_key if record is not None else None),
            asset=self._record_asset(record, msg, client),
            side=self._record_side(record, msg),
            amount=self._record_amount(record, msg),
            currency=self._record_currency(record, msg),
            broker_status=BROKER_OPEN,
            opened_at_utc=self._record_opened_at(record, msg),
            expires_at_utc=self._record_expires_at(record, msg),
            closed_at_utc=None,
            gross_payout=None,
            net_pnl=None,
            settlement_status=None,
            estimated_pnl=False,
            raw_snapshot_json=json_dumps({'source': source, 'payload': raw_payload, 'record': record.as_dict() if record else None}),
            last_seen_at_utc=utc_now_iso(),
        )

    def _closed_snapshot(self, *, external_order_id: str, record: _BridgeRecord | None, raw_payload: Any, gross_value: Any, win_token: Any, closed_at_hint: Any = None, client: IQExecutionClient | None = None, source: str = 'broker') -> BrokerOrderSnapshot:
        msg = self._payload_msg(raw_payload)
        amount = self._record_amount(record, msg)
        broker_status, settlement_status, gross_payout, net_pnl = self._terminal_from_values(amount=amount, win_token=win_token, gross_value=gross_value)
        closed_at = self._ts_to_iso(closed_at_hint)
        if closed_at is None:
            for key in ('close_time', 'expiration_time', 'expired', 'closed_at', 'updated_at'):
                closed_at = self._ts_to_iso(msg.get(key))
                if closed_at:
                    break
        if closed_at is None and record is not None and record.expires_at_utc:
            closed_at = ensure_utc_iso(record.expires_at_utc)
        return BrokerOrderSnapshot(
            broker_name=self.broker_name(),
            account_mode=(record.account_mode if record is not None else self.account_mode),
            external_order_id=str(external_order_id),
            client_order_key=(record.client_order_key if record is not None else None),
            asset=self._record_asset(record, msg, client),
            side=self._record_side(record, msg),
            amount=amount,
            currency=self._record_currency(record, msg),
            broker_status=broker_status,
            opened_at_utc=self._record_opened_at(record, msg),
            expires_at_utc=self._record_expires_at(record, msg),
            closed_at_utc=closed_at,
            gross_payout=gross_payout,
            net_pnl=net_pnl,
            settlement_status=settlement_status,
            estimated_pnl=False,
            raw_snapshot_json=json_dumps({'source': source, 'payload': raw_payload, 'record': record.as_dict() if record else None}),
            last_seen_at_utc=utc_now_iso(),
        )

    def _find_closed_history_entry(self, client: IQExecutionClient, external_order_id: str) -> dict[str, Any] | None:
        try:
            payload = client.get_recent_closed_options(self.history_limit) or {}
        except Exception:
            return None
        rows = ((payload or {}).get('msg') or {}).get('closed_options') or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = row.get('id')
            if isinstance(rid, list):
                rid = rid[0] if rid else None
            if str(rid or '') == str(external_order_id):
                return row
        return None

    def _find_open_event(self, client: IQExecutionClient, external_order_id: str) -> dict[str, Any] | None:
        order_id_str = str(external_order_id)
        try:
            async_order = client.get_async_order(external_order_id) or {}
        except Exception:
            async_order = {}
        if isinstance(async_order, dict):
            for key in ('option-opened', 'socket-option-opened', 'option-closed', 'socket-option-closed'):
                item = async_order.get(key)
                if isinstance(item, dict):
                    return item
        for getter in (client.list_socket_opened_orders, client.list_async_orders):
            try:
                items = getter() or {}
            except Exception:
                items = {}
            entry = items.get(order_id_str) or items.get(self._as_int(order_id_str))
            if isinstance(entry, dict):
                return entry
            if isinstance(items, dict) and order_id_str in items:
                v = items[order_id_str]
                if isinstance(v, dict):
                    return v
        try:
            other_pc = client.get_option_open_by_other_pc() or {}
        except Exception:
            other_pc = {}
        entry = other_pc.get(order_id_str) or other_pc.get(self._as_int(order_id_str))
        if isinstance(entry, dict):
            return entry
        return None

    def _snapshot_for_record(self, record: _BridgeRecord, client: IQExecutionClient | None = None) -> BrokerOrderSnapshot | None:
        client = client or self._client()
        order_id = str(record.external_order_id)

        # 1) Strongest signal: async/socket closed/opened data in the current live session.
        try:
            async_payload = client.get_async_order(order_id) or {}
        except Exception:
            async_payload = {}
        if isinstance(async_payload, dict):
            closed_payload = async_payload.get('option-closed') or async_payload.get('socket-option-closed')
            if isinstance(closed_payload, dict):
                msg = self._payload_msg(closed_payload)
                return self._closed_snapshot(
                    external_order_id=order_id,
                    record=record,
                    raw_payload=closed_payload,
                    gross_value=msg.get('win_amount') or msg.get('profit') or msg.get('profit_amount'),
                    win_token=msg.get('win'),
                    closed_at_hint=msg.get('close_time') or msg.get('expiration_time'),
                    client=client,
                    source='async_closed',
                )
            open_payload = async_payload.get('option-opened') or async_payload.get('socket-option-opened')
            if isinstance(open_payload, dict):
                return self._open_snapshot(external_order_id=order_id, record=record, raw_payload=open_payload, client=client, source='async_open')

        # 2) Broker betinfo: authoritative terminal result for binary options.
        try:
            ok, betinfo = client.get_betinfo_safe(order_id)
        except Exception:
            ok, betinfo = False, None
        if ok and isinstance(betinfo, dict):
            data = (((betinfo or {}).get('result') or {}).get('data') or {})
            row = data.get(str(order_id)) or data.get(self._as_int(order_id)) or {}
            if isinstance(row, dict):
                return self._closed_snapshot(
                    external_order_id=order_id,
                    record=record,
                    raw_payload=betinfo,
                    gross_value=row.get('profit') or row.get('win_amount'),
                    win_token=row.get('win'),
                    closed_at_hint=row.get('close_at') or row.get('expired_at') or row.get('expiration_time'),
                    client=client,
                    source='betinfo',
                )

        # 3) Recent closed history: survives session restarts better than async state.
        row = self._find_closed_history_entry(client, order_id)
        if isinstance(row, dict):
            rid = row.get('id')
            if isinstance(rid, list):
                rid = rid[0] if rid else order_id
            return self._closed_snapshot(
                external_order_id=str(rid or order_id),
                record=record,
                raw_payload=row,
                gross_value=row.get('win_amount') or row.get('profit'),
                win_token=row.get('win'),
                closed_at_hint=row.get('close_time') or row.get('expiration_time'),
                client=client,
                source='history_closed',
            )

        # 4) Current open evidence from socket/open-by-other-pc streams.
        open_payload = self._find_open_event(client, order_id)
        if isinstance(open_payload, dict):
            return self._open_snapshot(external_order_id=order_id, record=record, raw_payload=open_payload, client=client, source='open_stream')

        # 5) No broker payload yet: keep accepted orders OPEN until expiry+grace.
        expiry = parse_utc_iso(record.expires_at_utc) or datetime.fromtimestamp(int(record.expiry_ts), tz=UTC)
        if utc_now() < expiry + timedelta(seconds=max(1, self.settle_grace_sec)):
            return self._open_snapshot(
                external_order_id=order_id,
                record=record,
                raw_payload={'source': 'local_grace_window', 'expiry_ts': record.expiry_ts},
                client=client,
                source='local_grace_window',
            )

        return BrokerOrderSnapshot(
            broker_name=self.broker_name(),
            account_mode=record.account_mode,
            external_order_id=order_id,
            client_order_key=record.client_order_key,
            asset=record.asset,
            side=record.side,
            amount=float(record.amount),
            currency=record.currency,
            broker_status=BROKER_NOT_FOUND,
            opened_at_utc=ensure_utc_iso(record.opened_at_utc),
            expires_at_utc=ensure_utc_iso(record.expires_at_utc),
            closed_at_utc=None,
            gross_payout=None,
            net_pnl=None,
            settlement_status=None,
            estimated_pnl=False,
            raw_snapshot_json=json_dumps({'source': 'not_found_after_grace', 'record': record.as_dict()}),
            last_seen_at_utc=utc_now_iso(),
        )

    def healthcheck(self) -> BrokerSessionStatus:
        if not self._live_enabled():
            return BrokerSessionStatus(
                broker_name=self.broker_name(),
                account_mode=self.account_mode,
                ready=False,
                healthy=True,
                reason='iqoption_live_bridge_disabled_for_non_broker_mode',
                checked_at_utc=utc_now_iso(),
            )
        real_guard = self._real_account_guard_reason()
        if real_guard is not None:
            return BrokerSessionStatus(
                broker_name=self.broker_name(),
                account_mode=self.account_mode,
                ready=False,
                healthy=False,
                reason=real_guard,
                checked_at_utc=utc_now_iso(),
            )
        if self._backend is None:
            dep = self._dependency_status()
            if not bool(dep.get('available', True)):
                return BrokerSessionStatus(
                    broker_name=self.broker_name(),
                    account_mode=self.account_mode,
                    ready=False,
                    healthy=False,
                    reason='iqoption_dependency_missing',
                    checked_at_utc=utc_now_iso(),
                )
            email, password = self._credentials()
            if not email or not password:
                return BrokerSessionStatus(
                    broker_name=self.broker_name(),
                    account_mode=self.account_mode,
                    ready=False,
                    healthy=False,
                    reason='iqoption_missing_credentials',
                    checked_at_utc=utc_now_iso(),
                )
        try:
            client = self._client()
            self._ensure_client_connected(client)
            return BrokerSessionStatus(
                broker_name=self.broker_name(),
                account_mode=self.account_mode,
                ready=True,
                healthy=True,
                reason=None,
                checked_at_utc=utc_now_iso(),
            )
        except Exception as exc:  # pragma: no cover - depends on external runtime
            return BrokerSessionStatus(
                broker_name=self.broker_name(),
                account_mode=self.account_mode,
                ready=False,
                healthy=False,
                reason=self._normalize_health_reason(exc),
                checked_at_utc=utc_now_iso(),
            )

    def submit_order(self, req: SubmitOrderRequest) -> SubmitOrderResult:
        if not self._live_enabled():
            return SubmitOrderResult(
                transport_status=TRANSPORT_REJECT,
                external_order_id=None,
                broker_status=BROKER_REJECTED,
                error_code='iqoption_live_mode_required',
                error_message='execution.mode must be live/practice for iqoption submits',
                response={'mode': self.execution_mode},
            )
        real_guard = self._real_account_guard_reason()
        if real_guard is not None:
            return SubmitOrderResult(
                transport_status=TRANSPORT_REJECT,
                external_order_id=None,
                broker_status=BROKER_REJECTED,
                accepted_at_utc=None,
                error_code=real_guard,
                error_message='REAL account execution requires THALOR_EXECUTION_ALLOW_REAL=1',
                response={
                    'account_mode': self.account_mode,
                    'required_env': 'THALOR_EXECUTION_ALLOW_REAL=1',
                },
            )
        try:
            duration_min = self._duration_min(int(req.interval_sec))
        except Exception as exc:
            return SubmitOrderResult(
                transport_status=TRANSPORT_REJECT,
                external_order_id=None,
                broker_status=BROKER_REJECTED,
                error_code='unsupported_interval',
                error_message=str(exc),
                response={'interval_sec': int(req.interval_sec)},
            )
        side = self._normalize_side(req.side)
        if side not in {'CALL', 'PUT'}:
            return SubmitOrderResult(
                transport_status=TRANSPORT_REJECT,
                external_order_id=None,
                broker_status=BROKER_REJECTED,
                error_code='invalid_side',
                error_message=f'invalid side: {req.side}',
                response={'side': req.side},
            )
        try:
            client = self._client()
            self._ensure_client_connected(client)
            ok, order_ref = client.submit_binary_option(
                asset=str(req.asset),
                amount=float(req.amount),
                side=side.lower(),
                duration_min=duration_min,
            )
        except Exception as exc:  # pragma: no cover - depends on external runtime
            transport = self._transport_from_exception(exc)
            return SubmitOrderResult(
                transport_status=transport,
                external_order_id=None,
                broker_status=BROKER_UNKNOWN,
                accepted_at_utc=None,
                error_code=type(exc).__name__,
                error_message=str(exc),
                response={'asset': req.asset, 'side': side, 'duration_min': duration_min},
            )
        if not ok:
            msg = str(order_ref or 'iqoption_submit_rejected')
            return SubmitOrderResult(
                transport_status=TRANSPORT_REJECT,
                external_order_id=None,
                broker_status=BROKER_REJECTED,
                accepted_at_utc=None,
                error_code=self._error_code_from_message(msg),
                error_message=msg,
                response={'asset': req.asset, 'side': side, 'duration_min': duration_min},
            )
        external_order_id = str(order_ref or '').strip()
        if not external_order_id:
            return SubmitOrderResult(
                transport_status=TRANSPORT_TIMEOUT,
                external_order_id=None,
                broker_status=BROKER_UNKNOWN,
                accepted_at_utc=None,
                error_code='missing_order_id',
                error_message='broker acknowledged submit without external order id',
                response={'asset': req.asset, 'side': side, 'duration_min': duration_min},
            )

        now_iso = utc_now_iso()
        record = _BridgeRecord(
            external_order_id=external_order_id,
            client_order_key=req.client_order_key,
            account_mode=self.account_mode,
            asset=str(req.asset),
            side=side,
            amount=float(req.amount),
            currency=str(req.currency),
            interval_sec=int(req.interval_sec),
            scope_tag=str(req.scope_tag),
            signal_ts=int(req.signal_ts),
            expiry_ts=int(req.expiry_ts),
            opened_at_utc=now_iso,
            expires_at_utc=ensure_utc_iso(datetime.fromtimestamp(int(req.expiry_ts), tz=UTC)),
            entry_deadline_utc=ensure_utc_iso(req.entry_deadline_utc),
            raw_request=req.as_dict(),
            raw_submit_response={'ok': bool(ok), 'order_ref': external_order_id, 'duration_min': duration_min},
            metadata=dict(req.metadata or {}),
        )
        self._upsert_record(record)
        return SubmitOrderResult(
            transport_status=TRANSPORT_ACK,
            external_order_id=external_order_id,
            broker_status=BROKER_OPEN,
            accepted_at_utc=now_iso,
            response={'asset': req.asset, 'side': side, 'duration_min': duration_min, 'order_ref': external_order_id},
        )

    def fetch_order(self, external_order_id: str) -> BrokerOrderSnapshot | None:
        order_id = str(external_order_id or '').strip()
        if not order_id:
            return None
        record = self._get_record(order_id)
        if record is None:
            try:
                client = self._client()
                self._ensure_client_connected(client)
            except Exception:
                return None
            # best effort for orders not yet present in local bridge state
            row = self._find_closed_history_entry(client, order_id)
            if isinstance(row, dict):
                rid = row.get('id')
                if isinstance(rid, list):
                    rid = rid[0] if rid else order_id
                return self._closed_snapshot(
                    external_order_id=str(rid or order_id),
                    record=None,
                    raw_payload=row,
                    gross_value=row.get('win_amount') or row.get('profit'),
                    win_token=row.get('win'),
                    closed_at_hint=row.get('close_time') or row.get('expiration_time'),
                    client=client,
                    source='history_closed_orphan',
                )
            open_payload = self._find_open_event(client, order_id)
            if isinstance(open_payload, dict):
                return self._open_snapshot(external_order_id=order_id, record=None, raw_payload=open_payload, client=client, source='open_stream_orphan')
            return None
        return self._snapshot_for_record(record)

    def _scope_records(self, scope: BrokerScope) -> list[_BridgeRecord]:
        state = self._load_state()
        out: list[_BridgeRecord] = []
        for item in (state.get('orders') or {}).values():
            record = self._record_from_dict(item)
            if record is None:
                continue
            if self._record_scope_match(record, scope):
                out.append(record)
        return out

    def _scoped_unknown_stream_snapshots(self, scope: BrokerScope, client: IQExecutionClient) -> list[BrokerOrderSnapshot]:
        snapshots: list[BrokerOrderSnapshot] = []
        seen: set[str] = set()
        streams: list[dict[str, Any]] = []
        for getter in (client.list_socket_opened_orders, client.list_socket_closed_orders, client.list_async_orders):
            try:
                payload = getter() or {}
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                streams.append(payload)
        try:
            other_pc = client.get_option_open_by_other_pc() or {}
            if isinstance(other_pc, dict):
                streams.append(other_pc)
        except Exception:
            pass

        for payload in streams:
            for raw_id, item in payload.items():
                order_id = str(raw_id)
                if not order_id or order_id in seen or self._get_record(order_id) is not None:
                    continue
                if not isinstance(item, dict):
                    continue
                msg = self._payload_msg(item)
                asset = self._record_asset(None, msg, client)
                if asset != str(scope.asset):
                    continue
                side = self._record_side(None, msg)
                amount = self._record_amount(None, msg)
                currency = self._record_currency(None, msg)
                opened_at = self._record_opened_at(None, msg)
                expires_at = self._record_expires_at(None, msg)
                if item.get('name') in {'option-closed', 'socket-option-closed'} or msg.get('win') not in (None, ''):
                    snap = self._closed_snapshot(
                        external_order_id=order_id,
                        record=None,
                        raw_payload=item,
                        gross_value=msg.get('win_amount') or msg.get('profit') or msg.get('profit_amount'),
                        win_token=msg.get('win'),
                        closed_at_hint=msg.get('close_time') or msg.get('expiration_time'),
                        client=client,
                        source='stream_orphan_closed',
                    )
                else:
                    snap = BrokerOrderSnapshot(
                        broker_name=self.broker_name(),
                        account_mode=self.account_mode,
                        external_order_id=order_id,
                        client_order_key=None,
                        asset=asset,
                        side=side,
                        amount=amount,
                        currency=currency,
                        broker_status=BROKER_OPEN,
                        opened_at_utc=opened_at,
                        expires_at_utc=expires_at,
                        closed_at_utc=None,
                        gross_payout=None,
                        net_pnl=None,
                        settlement_status=None,
                        estimated_pnl=False,
                        raw_snapshot_json=json_dumps({'source': 'stream_orphan_open', 'payload': item}),
                        last_seen_at_utc=utc_now_iso(),
                    )
                snapshots.append(snap)
                seen.add(order_id)
        return snapshots

    def fetch_open_orders(self, scope: BrokerScope) -> list[BrokerOrderSnapshot]:
        try:
            client = self._client()
            self._ensure_client_connected(client)
        except Exception:
            client = None
        out: list[BrokerOrderSnapshot] = []
        for record in self._scope_records(scope):
            snap = self._snapshot_for_record(record, client=client) if client is not None else None
            if snap is None:
                continue
            if snap.broker_status == BROKER_OPEN:
                out.append(snap)
        if client is not None:
            out.extend([s for s in self._scoped_unknown_stream_snapshots(scope, client) if s.broker_status == BROKER_OPEN])
        dedup: dict[str, BrokerOrderSnapshot] = {s.external_order_id: s for s in out}
        return list(dedup.values())

    def fetch_closed_orders(self, scope: BrokerScope, since_utc: datetime) -> list[BrokerOrderSnapshot]:
        since = since_utc.astimezone(UTC)
        try:
            client = self._client()
            self._ensure_client_connected(client)
        except Exception:
            client = None
        out: list[BrokerOrderSnapshot] = []
        for record in self._scope_records(scope):
            snap = self._snapshot_for_record(record, client=client) if client is not None else None
            if snap is None or snap.broker_status in {BROKER_OPEN, BROKER_UNKNOWN}:
                continue
            closed = parse_utc_iso(snap.closed_at_utc) or parse_utc_iso(snap.expires_at_utc) or parse_utc_iso(snap.opened_at_utc)
            if closed is None or closed >= since:
                out.append(snap)
        if client is not None:
            out.extend([s for s in self._scoped_unknown_stream_snapshots(scope, client) if s.broker_status not in {BROKER_OPEN, BROKER_UNKNOWN}])
            try:
                payload = client.get_recent_closed_options(self.history_limit) or {}
            except Exception:
                payload = {}
            rows = ((payload or {}).get('msg') or {}).get('closed_options') or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rid = row.get('id')
                if isinstance(rid, list):
                    rid = rid[0] if rid else None
                order_id = str(rid or '')
                if not order_id or self._get_record(order_id) is not None:
                    continue
                asset = str(row.get('active') or '')
                if not asset:
                    try:
                        asset = str(client.asset_name_from_opcode(row.get('active_id')) or '')
                    except Exception:
                        asset = ''
                if asset != str(scope.asset):
                    continue
                snap = self._closed_snapshot(
                    external_order_id=order_id,
                    record=None,
                    raw_payload=row,
                    gross_value=row.get('win_amount') or row.get('profit'),
                    win_token=row.get('win'),
                    closed_at_hint=row.get('close_time') or row.get('expiration_time'),
                    client=client,
                    source='history_orphan_closed',
                )
                closed = parse_utc_iso(snap.closed_at_utc) or parse_utc_iso(snap.expires_at_utc) or parse_utc_iso(snap.opened_at_utc)
                if closed is None or closed >= since:
                    out.append(snap)
        dedup: dict[str, BrokerOrderSnapshot] = {s.external_order_id: s for s in out}
        return list(dedup.values())
