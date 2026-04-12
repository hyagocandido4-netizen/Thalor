from __future__ import annotations

"""Daily request volume tracking for external provider calls.

The :class:`RequestMetrics` utility is intentionally lightweight and dependency-
free. It keeps an in-memory, thread-safe daily counter that can be used around
external provider calls (HTTP/WebSocket/API) to answer operational questions
such as:

* How many outbound requests were executed today?
* How many of them succeeded or failed?
* Which operations produced the traffic?
* What is the current latency profile for the day?

The counter rolls over automatically when the day changes and emits a structured
summary log for the completed day. Callers can also emit an on-demand summary
(via :meth:`emit_summary`) or a final summary during shutdown (via
:meth:`close`).
"""

import json
import logging
import os
import threading
import uuid
import atexit
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo


__all__ = [
    'RequestMetrics',
    'RequestMetricsConfig',
]


_TRUE_VALUES = {'1', 'true', 't', 'yes', 'y', 'on'}
_FALSE_VALUES = {'0', 'false', 'f', 'no', 'n', 'off'}
_ENV_LOG_PATH_KEYS = (
    'REQUEST_METRICS_LOG_PATH',
    'REQUEST_METRICS_STRUCTURED_LOG_PATH',
    'THALOR__OBSERVABILITY__REQUEST_METRICS__LOG_PATH',
    'THALOR__OBSERVABILITY__REQUEST_METRICS__STRUCTURED_LOG_PATH',
    'THALOR__NETWORK__REQUEST_METRICS__LOG_PATH',
    'THALOR__NETWORK__REQUEST_METRICS__STRUCTURED_LOG_PATH',
    'THALOR__REQUEST_METRICS__LOG_PATH',
    'THALOR__REQUEST_METRICS__STRUCTURED_LOG_PATH',
)


def _coerce_positive_int(value: Any, default: int = 0) -> int:
    if value is None:
        return int(default)
    if isinstance(value, bool):
        return int(default)
    if isinstance(value, int):
        return max(0, int(value))
    text = str(value).strip()
    if not text:
        return int(default)
    try:
        return max(0, int(float(text)))
    except Exception:
        return int(default)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return default


def _coerce_log_level(value: Any, default: int = logging.INFO) -> int:
    if value is None:
        return int(default)
    if isinstance(value, int):
        return int(value)
    text = str(value).strip()
    if not text:
        return int(default)
    if text.isdigit() or (text.startswith('-') and text[1:].isdigit()):
        return int(text)
    mapped = logging.getLevelName(text.upper())
    if isinstance(mapped, int):
        return int(mapped)
    return int(default)


def _first_env_raw(keys: tuple[str, ...]) -> str | None:
    for key in keys:
        raw = os.getenv(key)
        if raw is None:
            continue
        if raw.strip() == '':
            continue
        return raw
    return None


def _utc_now_iso(dt: datetime | None = None) -> str:
    value = dt or datetime.now(tz=UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat(timespec='seconds')


@dataclass(frozen=True, slots=True)
class RequestMetricsConfig:
    """Configuration for :class:`RequestMetrics`."""

    enabled: bool = True
    timezone: str = 'UTC'
    structured_log_path: Path | None = None
    summary_log_level: int = logging.INFO
    emit_summary_on_rollover: bool = True
    emit_summary_on_close: bool = True
    emit_request_events: bool = True
    emit_summary_every_requests: int = 25

    def __post_init__(self) -> None:
        object.__setattr__(self, 'enabled', bool(self.enabled))
        tz_name = str(self.timezone or 'UTC').strip() or 'UTC'
        ZoneInfo(tz_name)
        object.__setattr__(self, 'timezone', tz_name)
        object.__setattr__(self, 'summary_log_level', int(self.summary_log_level))
        if self.structured_log_path in (None, ''):
            object.__setattr__(self, 'structured_log_path', None)
        elif not isinstance(self.structured_log_path, Path):
            object.__setattr__(self, 'structured_log_path', Path(str(self.structured_log_path)))
        object.__setattr__(self, 'emit_summary_on_rollover', bool(self.emit_summary_on_rollover))
        object.__setattr__(self, 'emit_summary_on_close', bool(self.emit_summary_on_close))
        object.__setattr__(self, 'emit_request_events', bool(self.emit_request_events))
        object.__setattr__(self, 'emit_summary_every_requests', max(0, int(self.emit_summary_every_requests)))

    @classmethod
    def disabled(cls) -> 'RequestMetricsConfig':
        return cls(enabled=False)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None) -> 'RequestMetricsConfig':
        payload = dict(data or {})
        return cls(
            enabled=_coerce_bool(payload.get('enabled'), True),
            timezone=str(payload.get('timezone') or 'UTC').strip() or 'UTC',
            structured_log_path=Path(str(payload.get('structured_log_path'))) if payload.get('structured_log_path') not in (None, '') else None,
            summary_log_level=_coerce_log_level(payload.get('summary_log_level'), logging.INFO),
            emit_summary_on_rollover=_coerce_bool(payload.get('emit_summary_on_rollover'), True),
            emit_summary_on_close=_coerce_bool(payload.get('emit_summary_on_close'), True),
            emit_request_events=_coerce_bool(payload.get('emit_request_events'), True),
            emit_summary_every_requests=_coerce_positive_int(payload.get('emit_summary_every_requests'), 25),
        )

    @classmethod
    def from_sources(cls, data: Mapping[str, Any] | None = None) -> 'RequestMetricsConfig':
        payload = dict(data or {})
        log_path_raw = _first_env_raw(_ENV_LOG_PATH_KEYS)
        if log_path_raw not in (None, ''):
            payload['structured_log_path'] = log_path_raw

        enabled_raw = _first_env_raw((
            'REQUEST_METRICS_ENABLED',
            'THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED',
            'THALOR__NETWORK__REQUEST_METRICS__ENABLED',
            'THALOR__REQUEST_METRICS__ENABLED',
        ))
        if enabled_raw is not None:
            payload['enabled'] = enabled_raw

        timezone_raw = _first_env_raw((
            'REQUEST_METRICS_TIMEZONE',
            'THALOR__OBSERVABILITY__REQUEST_METRICS__TIMEZONE',
            'THALOR__NETWORK__REQUEST_METRICS__TIMEZONE',
            'THALOR__REQUEST_METRICS__TIMEZONE',
        ))
        if timezone_raw is not None:
            payload['timezone'] = timezone_raw

        summary_log_level_raw = _first_env_raw((
            'REQUEST_METRICS_SUMMARY_LOG_LEVEL',
            'THALOR__OBSERVABILITY__REQUEST_METRICS__SUMMARY_LOG_LEVEL',
            'THALOR__NETWORK__REQUEST_METRICS__SUMMARY_LOG_LEVEL',
            'THALOR__REQUEST_METRICS__SUMMARY_LOG_LEVEL',
        ))
        if summary_log_level_raw is not None:
            payload['summary_log_level'] = summary_log_level_raw

        rollover_raw = _first_env_raw((
            'REQUEST_METRICS_EMIT_SUMMARY_ON_ROLLOVER',
            'THALOR__OBSERVABILITY__REQUEST_METRICS__EMIT_SUMMARY_ON_ROLLOVER',
            'THALOR__NETWORK__REQUEST_METRICS__EMIT_SUMMARY_ON_ROLLOVER',
            'THALOR__REQUEST_METRICS__EMIT_SUMMARY_ON_ROLLOVER',
        ))
        if rollover_raw is not None:
            payload['emit_summary_on_rollover'] = rollover_raw

        close_raw = _first_env_raw((
            'REQUEST_METRICS_EMIT_SUMMARY_ON_CLOSE',
            'THALOR__OBSERVABILITY__REQUEST_METRICS__EMIT_SUMMARY_ON_CLOSE',
            'THALOR__NETWORK__REQUEST_METRICS__EMIT_SUMMARY_ON_CLOSE',
            'THALOR__REQUEST_METRICS__EMIT_SUMMARY_ON_CLOSE',
        ))
        if close_raw is not None:
            payload['emit_summary_on_close'] = close_raw

        request_events_raw = _first_env_raw((
            'REQUEST_METRICS_EMIT_REQUEST_EVENTS',
            'THALOR__OBSERVABILITY__REQUEST_METRICS__EMIT_REQUEST_EVENTS',
            'THALOR__NETWORK__REQUEST_METRICS__EMIT_REQUEST_EVENTS',
            'THALOR__REQUEST_METRICS__EMIT_REQUEST_EVENTS',
        ))
        if request_events_raw is not None:
            payload['emit_request_events'] = request_events_raw

        summary_every_raw = _first_env_raw((
            'REQUEST_METRICS_EMIT_SUMMARY_EVERY_REQUESTS',
            'THALOR__OBSERVABILITY__REQUEST_METRICS__EMIT_SUMMARY_EVERY_REQUESTS',
            'THALOR__NETWORK__REQUEST_METRICS__EMIT_SUMMARY_EVERY_REQUESTS',
            'THALOR__REQUEST_METRICS__EMIT_SUMMARY_EVERY_REQUESTS',
        ))
        if summary_every_raw is not None:
            payload['emit_summary_every_requests'] = summary_every_raw

        return cls.from_mapping(payload)

    @classmethod
    def from_env(cls) -> 'RequestMetricsConfig':
        return cls.from_sources(None)

    def as_dict(self) -> dict[str, Any]:
        return {
            'enabled': self.enabled,
            'timezone': self.timezone,
            'structured_log_path': str(self.structured_log_path) if self.structured_log_path is not None else None,
            'summary_log_level': int(self.summary_log_level),
            'emit_summary_on_rollover': self.emit_summary_on_rollover,
            'emit_summary_on_close': self.emit_summary_on_close,
            'emit_request_events': self.emit_request_events,
            'emit_summary_every_requests': self.emit_summary_every_requests,
        }


@dataclass(slots=True)
class _DailyRequestState:
    day: str
    started_at_utc: str
    first_request_at_utc: str | None = None
    last_request_at_utc: str | None = None
    total_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0
    operation_counts: dict[str, int] = field(default_factory=dict)
    target_counts: dict[str, int] = field(default_factory=dict)
    latency_observations: int = 0
    latency_total_ms: float = 0.0
    latency_max_ms: float = 0.0

    def record(
        self,
        *,
        operation: str,
        target: str | None,
        success: bool | None,
        latency_s: float | None,
        observed_at_utc: str,
    ) -> dict[str, Any]:
        self.total_requests += 1
        if self.first_request_at_utc is None:
            self.first_request_at_utc = observed_at_utc
        self.last_request_at_utc = observed_at_utc

        op_name = str(operation or 'default').strip() or 'default'
        self.operation_counts[op_name] = int(self.operation_counts.get(op_name, 0)) + 1

        target_name = str(target).strip() if target not in (None, '') else None
        if target_name:
            self.target_counts[target_name] = int(self.target_counts.get(target_name, 0)) + 1

        if success is True:
            self.total_successes += 1
        elif success is False:
            self.total_failures += 1

        latency_ms: float | None = None
        if latency_s is not None:
            latency_ms = max(0.0, float(latency_s) * 1000.0)
            self.latency_observations += 1
            self.latency_total_ms += latency_ms
            self.latency_max_ms = max(self.latency_max_ms, latency_ms)
        return {
            'day': self.day,
            'request_index': self.total_requests,
            'operation': op_name,
            'target': target_name,
            'success': success,
            'latency_ms': round(latency_ms, 3) if latency_ms is not None else None,
            'total_successes': self.total_successes,
            'total_failures': self.total_failures,
        }

    def as_dict(self) -> dict[str, Any]:
        avg_latency_ms = 0.0
        if self.latency_observations > 0:
            avg_latency_ms = self.latency_total_ms / float(self.latency_observations)
        return {
            'day': self.day,
            'started_at_utc': self.started_at_utc,
            'first_request_at_utc': self.first_request_at_utc,
            'last_request_at_utc': self.last_request_at_utc,
            'total_requests': self.total_requests,
            'total_successes': self.total_successes,
            'total_failures': self.total_failures,
            'total_unknown_outcome': max(0, self.total_requests - self.total_successes - self.total_failures),
            'operation_counts': dict(sorted(self.operation_counts.items())),
            'target_counts': dict(sorted(self.target_counts.items())),
            'latency_observations': self.latency_observations,
            'avg_latency_ms': round(avg_latency_ms, 3),
            'max_latency_ms': round(self.latency_max_ms, 3),
        }


class RequestMetrics:
    """Thread-safe daily request volume tracker.

    The instance keeps exactly one active daily window in memory. When the
    calendar day changes in the configured timezone, the current window is
    summarized and reset automatically.
    """

    def __init__(
        self,
        config: RequestMetricsConfig | None = None,
        *,
        logger: logging.Logger | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config or RequestMetricsConfig()
        self._logger = logger or logging.getLogger('natbin.request_metrics')
        self._timezone = ZoneInfo(self._config.timezone)
        self._now_fn = now_fn or (lambda: datetime.now(tz=UTC))
        self._lock = threading.RLock()
        self._session_id = uuid.uuid4().hex[:16]
        self._pid = os.getpid()
        self._atexit_registered = False
        day, now_utc = self._current_day_and_now_utc()
        self._state = _DailyRequestState(day=day, started_at_utc=_utc_now_iso(now_utc))
        if self._config.enabled:
            self._register_atexit_close()
            self._emit(logging.INFO, 'request_metrics_initialized', config=self._config.as_dict())

    @classmethod
    def disabled(cls, *, logger: logging.Logger | None = None) -> 'RequestMetrics':
        return cls(RequestMetricsConfig.disabled(), logger=logger)

    @classmethod
    def from_env(cls, *, logger: logging.Logger | None = None) -> 'RequestMetrics':
        return cls(RequestMetricsConfig.from_env(), logger=logger)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None, *, logger: logging.Logger | None = None) -> 'RequestMetrics':
        return cls(RequestMetricsConfig.from_mapping(data), logger=logger)

    @classmethod
    def from_config(cls, config: RequestMetricsConfig, *, logger: logging.Logger | None = None) -> 'RequestMetrics':
        return cls(config, logger=logger)

    @property
    def config(self) -> RequestMetricsConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def record_request(
        self,
        *,
        operation: str = 'default',
        target: str | None = None,
        success: bool | None = None,
        latency_s: float | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock:
            day, now_utc = self._current_day_and_now_utc()
            self._rollover_if_needed_locked(day=day, now_utc=now_utc)
            event_payload = self._state.record(
                operation=operation,
                target=target,
                success=success,
                latency_s=latency_s,
                observed_at_utc=_utc_now_iso(now_utc),
            )
            if self._config.emit_request_events:
                payload = dict(event_payload)
                if extra:
                    payload.update({str(key): value for key, value in dict(extra).items()})
                self._emit_request_event_locked(payload)
            if self._config.emit_summary_every_requests > 0 and (self._state.total_requests % self._config.emit_summary_every_requests) == 0:
                summary = self._build_summary_locked(self._state, reason='periodic')
                if summary is not None:
                    self._emit(self._config.summary_log_level, 'request_metrics_summary', **summary)

    def record_success(
        self,
        *,
        operation: str = 'default',
        target: str | None = None,
        latency_s: float | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self.record_request(operation=operation, target=target, success=True, latency_s=latency_s, extra=extra)

    def record_failure(
        self,
        *,
        operation: str = 'default',
        target: str | None = None,
        latency_s: float | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self.record_request(operation=operation, target=target, success=False, latency_s=latency_s, extra=extra)

    def rollover_if_needed(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        with self._lock:
            day, now_utc = self._current_day_and_now_utc()
            return self._rollover_if_needed_locked(day=day, now_utc=now_utc)

    def snapshot(self) -> dict[str, Any]:
        if self.enabled:
            self.rollover_if_needed()
        with self._lock:
            return {
                'enabled': self.enabled,
                'timezone': self._config.timezone,
                'config': self._config.as_dict(),
                'current': self._state.as_dict(),
            }

    def build_summary(self, *, reason: str = 'manual') -> dict[str, Any] | None:
        if not self.enabled:
            return None
        self.rollover_if_needed()
        with self._lock:
            return self._build_summary_locked(self._state, reason=reason)

    def emit_summary(self, *, reason: str = 'manual') -> dict[str, Any] | None:
        summary = self.build_summary(reason=reason)
        if summary is None:
            return None
        self._emit(self._config.summary_log_level, 'request_metrics_summary', **summary)
        return summary

    def close(self) -> dict[str, Any] | None:
        if not self.enabled or not self._config.emit_summary_on_close:
            return None
        return self.emit_summary(reason='close')

    def _current_day_and_now_utc(self) -> tuple[str, datetime]:
        raw_now = self._now_fn()
        if raw_now.tzinfo is None:
            now_utc = raw_now.replace(tzinfo=UTC)
        else:
            now_utc = raw_now.astimezone(UTC)
        day = now_utc.astimezone(self._timezone).date().isoformat()
        return day, now_utc

    def _rollover_if_needed_locked(self, *, day: str, now_utc: datetime) -> dict[str, Any] | None:
        if day == self._state.day:
            return None
        completed = self._state
        summary = self._build_summary_locked(completed, reason='day_rollover')
        self._state = _DailyRequestState(day=day, started_at_utc=_utc_now_iso(now_utc))
        if summary is not None and self._config.emit_summary_on_rollover:
            self._emit(self._config.summary_log_level, 'request_metrics_summary', **summary)
        return summary

    @staticmethod
    def _build_summary_locked(state: _DailyRequestState, *, reason: str) -> dict[str, Any] | None:
        payload = state.as_dict()
        if int(payload.get('total_requests') or 0) <= 0:
            return None
        return {
            'reason': reason,
            **payload,
        }

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        payload = {
            'kind': 'request_metrics',
            'event': event,
            'at_utc': _utc_now_iso(),
            'session_id': self._session_id,
            'pid': self._pid,
            **fields,
        }
        try:
            self._logger.log(level, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        except Exception:
            pass
        self._append_structured_log(payload)

    def _append_structured_log(self, payload: Mapping[str, Any]) -> None:
        if self._config.structured_log_path is None:
            return
        try:
            from ..ops.structured_log import append_jsonl

            append_jsonl(self._config.structured_log_path, payload)
        except Exception:
            pass

    def _emit_request_event_locked(self, fields: Mapping[str, Any]) -> None:
        payload = {
            'kind': 'request_metrics',
            'event': 'request_metrics_request',
            'at_utc': _utc_now_iso(),
            'session_id': self._session_id,
            'pid': self._pid,
            **dict(fields),
        }
        self._append_structured_log(payload)

    def _register_atexit_close(self) -> None:
        if self._atexit_registered:
            return
        try:
            atexit.register(self.close)
            self._atexit_registered = True
        except Exception:
            self._atexit_registered = False
