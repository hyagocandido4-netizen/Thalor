from __future__ import annotations

"""Health-aware network transport abstraction.

This module provides a production-friendly transport manager that can be used as
an opt-in middleware in front of external providers. The focus is resiliency:
endpoint selection, retry with exponential backoff + jitter, circuit-breaker
style quarantine, and optional health checks.

The implementation is intentionally dependency-light and thread-safe. Callers can
use it in two modes:

1. Passive mode: select an endpoint/binding and forward the proxy metadata to the
   underlying client.
2. Managed mode: execute a callable through :class:`NetworkTransportManager`
   and let the manager account for retries and endpoint health automatically.

Notes
-----
* Environment overlays are process-global by nature. When callers need a
  temporary ``os.environ`` mutation, they should use ``apply_environment()``
  which serializes those mutations behind a dedicated lock.
* SOCKS endpoints are supported at the configuration/binding level. Active HTTP
  health checks fall back to TCP probing for SOCKS endpoints.
"""

import importlib.util
import json
import logging
import os
import random
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, TypeVar
from urllib.parse import parse_qs, quote, unquote, urlparse
import base64
from urllib.request import ProxyHandler, Request, build_opener


__all__ = [
    'NetworkTransportBinding',
    'NetworkTransportConfig',
    'NetworkTransportConfigurationError',
    'NetworkTransportError',
    'NetworkTransportManager',
    'NetworkTransportUnavailable',
    'TransportEndpoint',
]


_ALLOWED_SCHEMES = {'http', 'https', 'socks', 'socks4', 'socks4a', 'socks5', 'socks5h'}
_SOCKS_SCHEMES = {'socks', 'socks4', 'socks4a', 'socks5', 'socks5h'}
_TRUE_VALUES = {'1', 'true', 't', 'yes', 'y', 'on'}
_FALSE_VALUES = {'0', 'false', 'f', 'no', 'n', 'off'}
_T = TypeVar('_T')


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')



def _mask_secret(value: str | None) -> str | None:
    if value in (None, ''):
        return None
    return '***'



def _safe_error(error: BaseException | str | None) -> str | None:
    if error is None:
        return None
    if isinstance(error, BaseException):
        text = f'{type(error).__name__}: {error}'
    else:
        text = str(error)
    text = text.strip()
    return text or None


def _pysocks_available() -> bool:
    return importlib.util.find_spec('socks') is not None


def _transport_dependency_status(*, requires_pysocks: bool) -> dict[str, Any]:
    pysocks_available = _pysocks_available()
    if not requires_pysocks:
        return {
            'available': True,
            'reason': None,
            'requires_pysocks': False,
            'pysocks_available': pysocks_available,
            'module': 'socks',
        }
    if pysocks_available:
        return {
            'available': True,
            'reason': None,
            'requires_pysocks': True,
            'pysocks_available': True,
            'module': 'socks',
        }
    return {
        'available': False,
        'reason': 'transport_socks_dependency_missing: PySocks is required for SOCKS transport endpoints. Install PySocks before enabling socks/socks4/socks5 transport.',
        'requires_pysocks': True,
        'pysocks_available': False,
        'module': 'socks',
    }


def _mask_proxy_url(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return text
    try:
        parsed = urlparse(text)
    except Exception:
        return text
    if parsed.username is None and parsed.password is None:
        return text
    scheme = parsed.scheme or 'http'
    host = parsed.hostname or ''
    port = parsed.port
    auth = ''
    if parsed.username is not None:
        auth = '***'
        if parsed.password is not None:
            auth = '***:***'
        auth += '@'
    host_part = host
    if port is not None:
        host_part = f'{host_part}:{port}'
    path_part = parsed.path or ''
    query_part = f'?{parsed.query}' if parsed.query else ''
    fragment_part = f'#{parsed.fragment}' if parsed.fragment else ''
    return f'{scheme}://{auth}{host_part}{path_part}{query_part}{fragment_part}'



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



def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return int(default)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return int(default)
    try:
        return int(float(text.replace(',', '.')))
    except Exception:
        return int(default)



def _coerce_float(value: Any, default: float) -> float:
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return float(default)
    try:
        return float(text.replace(',', '.'))
    except Exception:
        return float(default)



def _split_multi_value(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = value.replace('\n', ';').replace(',', ';').split(';')
    else:
        raw_items = []
        for item in value:
            raw_items.extend(str(item).replace('\n', ';').replace(',', ';').split(';'))
    items: list[str] = []
    for raw in raw_items:
        text = str(raw).strip()
        if text and text not in items:
            items.append(text)
    return tuple(items)



def _default_port_for_scheme(scheme: str) -> int:
    normalized = str(scheme or 'http').strip().lower()
    if normalized == 'https':
        return 443
    if normalized in _SOCKS_SCHEMES:
        return 1080
    return 80



def _healthcheck_target(url: str | None) -> tuple[str, int]:
    text = str(url or '').strip()
    if not text:
        return 'iqoption.com', 443
    if '://' not in text:
        parsed = urlparse(f'https://{text}')
    else:
        parsed = urlparse(text)
    host = str(parsed.hostname or '').strip() or 'iqoption.com'
    scheme = str(parsed.scheme or 'https').strip().lower()
    port = int(parsed.port or _default_port_for_scheme(scheme))
    return host, port


def _normalize_scheme(value: str) -> str:
    scheme = str(value or 'http').strip().lower()
    if not scheme:
        scheme = 'http'
    if scheme == 'socks':
        return 'socks5'
    return scheme



def _first_env_raw(keys: Iterable[str]) -> str | None:
    for key in keys:
        raw = os.getenv(str(key))
        if raw is None:
            continue
        if raw.strip() == '':
            continue
        return raw
    return None



def _read_env_text_file(raw_path: str | Path | None) -> str | None:
    if raw_path in (None, ''):
        return None
    path = Path(str(raw_path).strip())
    if not str(path):
        return None
    try:
        text = path.read_text(encoding='utf-8').strip()
    except Exception:
        return None
    return text or None



def _first_env_file_raw(keys: Iterable[str]) -> str | None:
    for key in keys:
        raw_path = os.getenv(str(key))
        if raw_path is None:
            continue
        if raw_path.strip() == '':
            continue
        text = _read_env_text_file(raw_path)
        if text in (None, ''):
            continue
        return text
    return None



def _first_env_text(value_keys: Iterable[str], *, file_keys: Iterable[str] | None = None) -> str | None:
    if file_keys is not None:
        raw_from_file = _first_env_file_raw(file_keys)
        if raw_from_file is not None:
            return raw_from_file
    return _first_env_raw(value_keys)


def _read_mapping_text_file(raw_path: str | Path | None) -> str | None:
    if raw_path in (None, ''):
        return None
    path = Path(str(raw_path).strip())
    if not str(path):
        return None
    try:
        text = path.read_text(encoding='utf-8').strip()
    except Exception:
        return None
    return text or None


def _first_mapping_text(payload: Mapping[str, Any], value_keys: Iterable[str], *, file_keys: Iterable[str] | None = None) -> str | None:
    if file_keys is not None:
        for key in file_keys:
            if key not in payload:
                continue
            raw_from_file = _read_mapping_text_file(payload.get(key))
            if raw_from_file is not None:
                return raw_from_file
    for key in value_keys:
        if key not in payload:
            continue
        raw = payload.get(key)
        if raw in (None, ''):
            continue
        if isinstance(raw, (str, Path)):
            text = str(raw).strip()
            if text:
                return text
    return None


def _first_env_bool(keys: Iterable[str], default: bool) -> bool:
    raw = _first_env_raw(keys)
    if raw is None:
        return default
    return _coerce_bool(raw, default)



def _first_env_int(keys: Iterable[str], default: int) -> int:
    raw = _first_env_raw(keys)
    if raw is None:
        return default
    return _coerce_int(raw, default)



def _first_env_float(keys: Iterable[str], default: float) -> float:
    raw = _first_env_raw(keys)
    if raw is None:
        return default
    return _coerce_float(raw, default)



def _merge_no_proxy(values: Iterable[str] | None, extra: Iterable[str] | None = None) -> tuple[str, ...]:
    merged: list[str] = []
    for group in (values or (), extra or ()):
        for item in _split_multi_value(group):
            if item not in merged:
                merged.append(item)
    return tuple(merged)


def _sanitize_env_overlay(overlay: Mapping[str, Any], *, mask_secret: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in dict(overlay).items():
        if mask_secret and str(key).lower().endswith('proxy') and isinstance(value, str) and '://' in value and '@' in value:
            out[str(key)] = _mask_proxy_url(value)
            continue
        out[str(key)] = value
    return out



def _sanitize_websocket_options(options: Mapping[str, Any], *, mask_secret: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in dict(options).items():
        normalized = str(key).lower()
        if mask_secret and normalized == 'http_proxy_auth':
            if isinstance(value, (list, tuple)):
                out[str(key)] = ['***' for _ in value]
            elif value not in (None, ''):
                out[str(key)] = '***'
            else:
                out[str(key)] = value
            continue
        if mask_secret and 'proxy' in normalized and isinstance(value, str) and '://' in value and '@' in value:
            out[str(key)] = _mask_proxy_url(value)
            continue
        out[str(key)] = value
    return out



class NetworkTransportError(RuntimeError):
    """Base error for the transport layer."""



class NetworkTransportConfigurationError(NetworkTransportError):
    """Raised when the transport configuration is invalid."""



class NetworkTransportUnavailable(NetworkTransportError):
    """Raised when the transport layer has no usable endpoint."""



@dataclass(frozen=True, slots=True)
class TransportEndpoint:
    """Immutable proxy/transport endpoint description."""

    scheme: str
    host: str
    port: int
    name: str
    username: str | None = None
    password: str | None = None
    priority: int = 100
    verify_tls: bool = True
    connect_timeout_s: float = 5.0
    no_proxy: tuple[str, ...] = field(default_factory=tuple)
    source: str = 'config'

    def __post_init__(self) -> None:
        scheme = _normalize_scheme(self.scheme)
        if scheme not in _ALLOWED_SCHEMES:
            raise NetworkTransportConfigurationError(f'unsupported transport scheme: {self.scheme!r}')
        host = str(self.host or '').strip()
        if not host:
            raise NetworkTransportConfigurationError('transport endpoint host must be non-empty')
        port = int(self.port)
        if port <= 0 or port > 65535:
            raise NetworkTransportConfigurationError(f'invalid transport endpoint port: {self.port!r}')
        name = str(self.name or '').strip()
        if not name:
            raise NetworkTransportConfigurationError('transport endpoint name must be non-empty')
        object.__setattr__(self, 'scheme', scheme)
        object.__setattr__(self, 'host', host)
        object.__setattr__(self, 'port', port)
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'priority', max(0, int(self.priority)))
        object.__setattr__(self, 'verify_tls', bool(self.verify_tls))
        object.__setattr__(self, 'connect_timeout_s', max(0.05, float(self.connect_timeout_s)))
        object.__setattr__(self, 'no_proxy', _split_multi_value(self.no_proxy))
        object.__setattr__(self, 'source', str(self.source or 'config'))

    @property
    def identity(self) -> str:
        return f'{self.name}|{self.proxy_url(mask_secret=False)}'

    @property
    def transport_type(self) -> str:
        if self.scheme in _SOCKS_SCHEMES:
            return 'socks'
        return self.scheme

    @property
    def requires_pysocks(self) -> bool:
        return self.scheme in _SOCKS_SCHEMES

    @property
    def display_name(self) -> str:
        return self.name

    def proxy_url(self, *, mask_secret: bool = False) -> str:
        auth = ''
        if self.username not in (None, ''):
            if mask_secret:
                auth = '***'
                if self.password not in (None, ''):
                    auth = '***:***'
                auth += '@'
            else:
                user = quote(str(self.username), safe='')
                if self.password not in (None, ''):
                    auth = f'{user}:{quote(str(self.password), safe="")}@'
                else:
                    auth = f'{user}@'
        return f'{self.scheme}://{auth}{self.host}:{self.port}'

    def to_env_overlay(self, *, default_no_proxy: Iterable[str] | None = None) -> dict[str, str]:
        overlay: dict[str, str] = {}
        proxy_url = self.proxy_url(mask_secret=False)
        no_proxy = _merge_no_proxy(self.no_proxy, default_no_proxy)
        if self.transport_type in {'http', 'https'}:
            overlay['HTTP_PROXY'] = proxy_url
            overlay['HTTPS_PROXY'] = proxy_url
            overlay['http_proxy'] = proxy_url
            overlay['https_proxy'] = proxy_url
        else:
            overlay['ALL_PROXY'] = proxy_url
            overlay['all_proxy'] = proxy_url
        if no_proxy:
            value = ','.join(no_proxy)
            overlay['NO_PROXY'] = value
            overlay['no_proxy'] = value
        return overlay

    def to_websocket_options(self, *, default_no_proxy: Iterable[str] | None = None) -> dict[str, Any]:
        """Return keyword arguments compatible with ``websocket-client``.

        ``websocket-client`` expects ``proxy_type='http'`` for HTTP-like proxies.
        HTTPS proxies therefore map to ``http`` here and can still rely on the
        environment overlay when a stricter transport path is needed.
        """

        proxy_type = self.scheme if self.scheme in _SOCKS_SCHEMES else 'http'
        options: dict[str, Any] = {
            'http_proxy_host': self.host,
            'http_proxy_port': self.port,
            'proxy_type': proxy_type,
        }
        if self.username not in (None, '') or self.password not in (None, ''):
            options['http_proxy_auth'] = (str(self.username or ''), str(self.password or ''))
        no_proxy = _merge_no_proxy(self.no_proxy, default_no_proxy)
        if no_proxy:
            options['http_no_proxy'] = list(no_proxy)
        return options

    def to_safe_dict(self, *, mask_secret: bool = True) -> dict[str, Any]:
        return {
            'name': self.name,
            'scheme': self.scheme,
            'transport_type': self.transport_type,
            'host': self.host,
            'port': self.port,
            'username': _mask_secret(self.username) if mask_secret else self.username,
            'password': _mask_secret(self.password) if mask_secret else self.password,
            'proxy_url': self.proxy_url(mask_secret=mask_secret),
            'priority': self.priority,
            'verify_tls': self.verify_tls,
            'connect_timeout_s': self.connect_timeout_s,
            'no_proxy': list(self.no_proxy),
            'source': self.source,
        }

    @classmethod
    def parse(
        cls,
        raw: str,
        *,
        name: str | None = None,
        default_scheme: str = 'http',
        priority: int = 100,
        verify_tls: bool = True,
        connect_timeout_s: float = 5.0,
        no_proxy: Iterable[str] | None = None,
        source: str = 'config',
    ) -> 'TransportEndpoint':
        text = str(raw or '').strip()
        if not text:
            raise NetworkTransportConfigurationError('transport endpoint string must be non-empty')
        if '://' not in text:
            text = f'{default_scheme}://{text}'
        parsed = urlparse(text)
        scheme = _normalize_scheme(parsed.scheme or default_scheme)
        if scheme not in _ALLOWED_SCHEMES:
            raise NetworkTransportConfigurationError(f'unsupported transport scheme: {scheme!r}')
        host = str(parsed.hostname or '').strip()
        if not host:
            raise NetworkTransportConfigurationError(f'could not resolve host from endpoint: {raw!r}')
        query = parse_qs(parsed.query, keep_blank_values=True)
        endpoint_name = str((query.get('name') or [name or ''])[0]).strip() or f'{scheme}://{host}:{parsed.port or _default_port_for_scheme(scheme)}'
        endpoint_priority = _coerce_int((query.get('priority') or [priority])[0], int(priority))
        endpoint_verify_tls = _coerce_bool((query.get('verify_tls') or [verify_tls])[0], bool(verify_tls))
        endpoint_connect_timeout_s = _coerce_float((query.get('timeout_s') or [connect_timeout_s])[0], float(connect_timeout_s))
        endpoint_no_proxy = _merge_no_proxy(no_proxy, query.get('no_proxy') or query.get('bypass') or ())
        return cls(
            scheme=scheme,
            host=host,
            port=int(parsed.port or _default_port_for_scheme(scheme)),
            name=endpoint_name,
            username=unquote(parsed.username) if parsed.username is not None else None,
            password=unquote(parsed.password) if parsed.password is not None else None,
            priority=endpoint_priority,
            verify_tls=endpoint_verify_tls,
            connect_timeout_s=endpoint_connect_timeout_s,
            no_proxy=endpoint_no_proxy,
            source=source,
        )

    @classmethod
    def from_value(
        cls,
        value: 'TransportEndpoint | Mapping[str, Any] | str',
        *,
        default_no_proxy: Iterable[str] | None = None,
        source: str = 'config',
    ) -> 'TransportEndpoint':
        if isinstance(value, TransportEndpoint):
            return value
        if isinstance(value, Mapping):
            raw_endpoint = value.get('url') or value.get('endpoint')
            if raw_endpoint not in (None, ''):
                endpoint = cls.parse(
                    str(raw_endpoint),
                    name=str(value.get('name') or '').strip() or None,
                    default_scheme=str(value.get('scheme') or 'http'),
                    priority=_coerce_int(value.get('priority'), 100),
                    verify_tls=_coerce_bool(value.get('verify_tls'), True),
                    connect_timeout_s=_coerce_float(value.get('connect_timeout_s'), 5.0),
                    no_proxy=_merge_no_proxy(default_no_proxy, value.get('no_proxy') or ()),
                    source=str(value.get('source') or source),
                )
                if value.get('name'):
                    endpoint = replace(endpoint, name=str(value.get('name')).strip())
                if value.get('priority') is not None:
                    endpoint = replace(endpoint, priority=_coerce_int(value.get('priority'), endpoint.priority))
                if value.get('verify_tls') is not None:
                    endpoint = replace(endpoint, verify_tls=_coerce_bool(value.get('verify_tls'), endpoint.verify_tls))
                if value.get('connect_timeout_s') is not None:
                    endpoint = replace(endpoint, connect_timeout_s=_coerce_float(value.get('connect_timeout_s'), endpoint.connect_timeout_s))
                if value.get('no_proxy') not in (None, ''):
                    endpoint = replace(endpoint, no_proxy=_merge_no_proxy(endpoint.no_proxy, value.get('no_proxy')))
                return endpoint
            scheme = _normalize_scheme(str(value.get('scheme') or 'http'))
            host = str(value.get('host') or '').strip()
            if not host:
                raise NetworkTransportConfigurationError('mapping transport endpoint requires host or url/endpoint')
            port = _coerce_int(value.get('port'), _default_port_for_scheme(scheme))
            name = str(value.get('name') or f'{scheme}://{host}:{port}').strip()
            return cls(
                scheme=scheme,
                host=host,
                port=port,
                name=name,
                username=str(value.get('username')) if value.get('username') not in (None, '') else None,
                password=str(value.get('password')) if value.get('password') not in (None, '') else None,
                priority=_coerce_int(value.get('priority'), 100),
                verify_tls=_coerce_bool(value.get('verify_tls'), True),
                connect_timeout_s=_coerce_float(value.get('connect_timeout_s'), 5.0),
                no_proxy=_merge_no_proxy(default_no_proxy, value.get('no_proxy') or ()),
                source=str(value.get('source') or source),
            )
        return cls.parse(str(value), no_proxy=default_no_proxy, source=source)


@dataclass(frozen=True, slots=True)
class NetworkTransportBinding:
    """Runtime binding selected for a transport-aware operation."""

    operation: str
    endpoint: TransportEndpoint | None
    env_overlay: dict[str, str]
    websocket_options: dict[str, Any]
    selected_at_utc: str

    @property
    def enabled(self) -> bool:
        return self.endpoint is not None

    def as_dict(self, *, mask_secret: bool = True) -> dict[str, Any]:
        return {
            'operation': self.operation,
            'enabled': self.enabled,
            'selected_at_utc': self.selected_at_utc,
            'endpoint': self.endpoint.to_safe_dict(mask_secret=mask_secret) if self.endpoint is not None else None,
            'env_overlay': _sanitize_env_overlay(self.env_overlay, mask_secret=mask_secret),
            'websocket_options': _sanitize_websocket_options(self.websocket_options, mask_secret=mask_secret),
        }


@dataclass(frozen=True, slots=True)
class NetworkTransportConfig:
    """Configuration for :class:`NetworkTransportManager`."""

    enabled: bool = False
    endpoints: tuple[TransportEndpoint, ...] = field(default_factory=tuple)
    max_retries: int = 3
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0
    jitter_ratio: float = 0.2
    failure_threshold: int = 3
    quarantine_base_s: float = 30.0
    quarantine_max_s: float = 300.0
    healthcheck_interval_s: float = 60.0
    healthcheck_timeout_s: float = 3.0
    healthcheck_mode: str = 'tcp'
    healthcheck_url: str | None = None
    no_proxy: tuple[str, ...] = field(default_factory=tuple)
    fail_open_when_exhausted: bool = True
    structured_log_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'enabled', bool(self.enabled))
        object.__setattr__(self, 'endpoints', tuple(self.endpoints))
        object.__setattr__(self, 'max_retries', max(1, int(self.max_retries)))
        object.__setattr__(self, 'backoff_base_s', max(0.01, float(self.backoff_base_s)))
        object.__setattr__(self, 'backoff_max_s', max(float(self.backoff_base_s), float(self.backoff_max_s)))
        object.__setattr__(self, 'jitter_ratio', min(1.0, max(0.0, float(self.jitter_ratio))))
        object.__setattr__(self, 'failure_threshold', max(1, int(self.failure_threshold)))
        object.__setattr__(self, 'quarantine_base_s', max(0.5, float(self.quarantine_base_s)))
        object.__setattr__(self, 'quarantine_max_s', max(float(self.quarantine_base_s), float(self.quarantine_max_s)))
        object.__setattr__(self, 'healthcheck_interval_s', max(0.0, float(self.healthcheck_interval_s)))
        object.__setattr__(self, 'healthcheck_timeout_s', max(0.05, float(self.healthcheck_timeout_s)))
        mode = str(self.healthcheck_mode or 'tcp').strip().lower()
        if mode not in {'tcp', 'http'}:
            raise NetworkTransportConfigurationError(f'unsupported healthcheck_mode: {self.healthcheck_mode!r}')
        object.__setattr__(self, 'healthcheck_mode', mode)
        object.__setattr__(self, 'no_proxy', _split_multi_value(self.no_proxy))
        if self.structured_log_path in (None, ''):
            object.__setattr__(self, 'structured_log_path', None)
        elif not isinstance(self.structured_log_path, Path):
            object.__setattr__(self, 'structured_log_path', Path(str(self.structured_log_path)))

    @property
    def ready(self) -> bool:
        return self.enabled and len(self.endpoints) > 0

    @property
    def requires_pysocks(self) -> bool:
        return any(endpoint.requires_pysocks for endpoint in self.endpoints)

    def dependency_status(self) -> dict[str, Any]:
        return _transport_dependency_status(requires_pysocks=self.enabled and self.requires_pysocks)

    def as_dict(self, *, mask_secret: bool = True) -> dict[str, Any]:
        return {
            'enabled': self.enabled,
            'ready': self.ready,
            'requires_pysocks': self.requires_pysocks,
            'dependency_status': self.dependency_status(),
            'max_retries': self.max_retries,
            'backoff_base_s': self.backoff_base_s,
            'backoff_max_s': self.backoff_max_s,
            'jitter_ratio': self.jitter_ratio,
            'failure_threshold': self.failure_threshold,
            'quarantine_base_s': self.quarantine_base_s,
            'quarantine_max_s': self.quarantine_max_s,
            'healthcheck_interval_s': self.healthcheck_interval_s,
            'healthcheck_timeout_s': self.healthcheck_timeout_s,
            'healthcheck_mode': self.healthcheck_mode,
            'healthcheck_url': self.healthcheck_url,
            'no_proxy': list(self.no_proxy),
            'fail_open_when_exhausted': self.fail_open_when_exhausted,
            'structured_log_path': str(self.structured_log_path) if self.structured_log_path is not None else None,
            'endpoint_count': len(self.endpoints),
            'endpoints': [endpoint.to_safe_dict(mask_secret=mask_secret) for endpoint in self.endpoints],
        }

    @classmethod
    def disabled(cls) -> 'NetworkTransportConfig':
        return cls(enabled=False, endpoints=())

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None) -> 'NetworkTransportConfig':
        payload = dict(data or {})
        default_no_proxy = _split_multi_value(payload.get('no_proxy'))
        raw_endpoints = payload.get('endpoints')
        if raw_endpoints in (None, ''):
            raw_endpoints = payload.get('endpoint')
        file_raw = _first_mapping_text(
            payload,
            ['endpoints', 'endpoint'],
            file_keys=['endpoints_file', 'endpoint_file'],
        )
        if file_raw is not None:
            raw_endpoints = file_raw
        endpoint_values: list[TransportEndpoint] = []
        if isinstance(raw_endpoints, Mapping):
            endpoint_values.append(TransportEndpoint.from_value(raw_endpoints, default_no_proxy=default_no_proxy))
        elif isinstance(raw_endpoints, str):
            for item in _split_multi_value(raw_endpoints):
                endpoint_values.append(TransportEndpoint.from_value(item, default_no_proxy=default_no_proxy))
        elif isinstance(raw_endpoints, Iterable):
            for item in raw_endpoints:
                endpoint_values.append(TransportEndpoint.from_value(item, default_no_proxy=default_no_proxy))
        enabled_default = len(endpoint_values) > 0
        return cls(
            enabled=_coerce_bool(payload.get('enabled'), enabled_default),
            endpoints=tuple(endpoint_values),
            max_retries=_coerce_int(payload.get('max_retries'), 3),
            backoff_base_s=_coerce_float(payload.get('backoff_base_s'), 0.5),
            backoff_max_s=_coerce_float(payload.get('backoff_max_s'), 8.0),
            jitter_ratio=_coerce_float(payload.get('jitter_ratio'), 0.2),
            failure_threshold=_coerce_int(payload.get('failure_threshold'), 3),
            quarantine_base_s=_coerce_float(payload.get('quarantine_base_s'), 30.0),
            quarantine_max_s=_coerce_float(payload.get('quarantine_max_s'), 300.0),
            healthcheck_interval_s=_coerce_float(payload.get('healthcheck_interval_s'), 60.0),
            healthcheck_timeout_s=_coerce_float(payload.get('healthcheck_timeout_s'), 3.0),
            healthcheck_mode=str(payload.get('healthcheck_mode') or 'tcp'),
            healthcheck_url=str(payload.get('healthcheck_url')).strip() if payload.get('healthcheck_url') not in (None, '') else None,
            no_proxy=default_no_proxy,
            fail_open_when_exhausted=_coerce_bool(payload.get('fail_open_when_exhausted'), True),
            structured_log_path=Path(str(payload.get('structured_log_path'))) if payload.get('structured_log_path') not in (None, '') else None,
        )

    @classmethod
    def from_sources(cls, data: Mapping[str, Any] | None = None) -> 'NetworkTransportConfig':
        payload = dict(data or {})

        endpoint_raw = _first_env_text(
            [
                'TRANSPORT_ENDPOINTS',
                'TRANSPORT_ENDPOINT',
                'THALOR__NETWORK__TRANSPORT__ENDPOINTS',
                'THALOR__NETWORK__TRANSPORT__ENDPOINT',
                'THALOR__TRANSPORT__ENDPOINTS',
                'THALOR__TRANSPORT__ENDPOINT',
            ],
            file_keys=[
                'TRANSPORT_ENDPOINTS_FILE',
                'TRANSPORT_ENDPOINT_FILE',
                'THALOR__NETWORK__TRANSPORT__ENDPOINTS_FILE',
                'THALOR__NETWORK__TRANSPORT__ENDPOINT_FILE',
                'THALOR__TRANSPORT__ENDPOINTS_FILE',
                'THALOR__TRANSPORT__ENDPOINT_FILE',
            ],
        )
        if endpoint_raw is not None:
            payload['endpoint'] = endpoint_raw
            payload.pop('endpoints', None)

        enabled_raw = _first_env_raw([
            'TRANSPORT_ENABLED',
            'THALOR__NETWORK__TRANSPORT__ENABLED',
            'THALOR__TRANSPORT__ENABLED',
        ])
        if enabled_raw is not None:
            payload['enabled'] = enabled_raw

        no_proxy_raw = _first_env_raw([
            'TRANSPORT_NO_PROXY',
            'THALOR__NETWORK__TRANSPORT__NO_PROXY',
            'THALOR__TRANSPORT__NO_PROXY',
        ])
        if no_proxy_raw is not None:
            payload['no_proxy'] = no_proxy_raw

        max_retries_raw = _first_env_raw([
            'TRANSPORT_MAX_RETRIES',
            'THALOR__NETWORK__TRANSPORT__MAX_RETRIES',
            'THALOR__TRANSPORT__MAX_RETRIES',
        ])
        if max_retries_raw is not None:
            payload['max_retries'] = max_retries_raw

        backoff_base_raw = _first_env_raw([
            'TRANSPORT_BACKOFF_BASE_S',
            'THALOR__NETWORK__TRANSPORT__BACKOFF_BASE_S',
            'THALOR__TRANSPORT__BACKOFF_BASE_S',
        ])
        if backoff_base_raw is not None:
            payload['backoff_base_s'] = backoff_base_raw

        backoff_max_raw = _first_env_raw([
            'TRANSPORT_BACKOFF_MAX_S',
            'THALOR__NETWORK__TRANSPORT__BACKOFF_MAX_S',
            'THALOR__TRANSPORT__BACKOFF_MAX_S',
        ])
        if backoff_max_raw is not None:
            payload['backoff_max_s'] = backoff_max_raw

        jitter_ratio_raw = _first_env_raw([
            'TRANSPORT_JITTER_RATIO',
            'THALOR__NETWORK__TRANSPORT__JITTER_RATIO',
            'THALOR__TRANSPORT__JITTER_RATIO',
        ])
        if jitter_ratio_raw is not None:
            payload['jitter_ratio'] = jitter_ratio_raw

        failure_threshold_raw = _first_env_raw([
            'TRANSPORT_FAILURE_THRESHOLD',
            'THALOR__NETWORK__TRANSPORT__FAILURE_THRESHOLD',
            'THALOR__TRANSPORT__FAILURE_THRESHOLD',
        ])
        if failure_threshold_raw is not None:
            payload['failure_threshold'] = failure_threshold_raw

        quarantine_base_raw = _first_env_raw([
            'TRANSPORT_QUARANTINE_BASE_S',
            'THALOR__NETWORK__TRANSPORT__QUARANTINE_BASE_S',
            'THALOR__TRANSPORT__QUARANTINE_BASE_S',
        ])
        if quarantine_base_raw is not None:
            payload['quarantine_base_s'] = quarantine_base_raw

        quarantine_max_raw = _first_env_raw([
            'TRANSPORT_QUARANTINE_MAX_S',
            'THALOR__NETWORK__TRANSPORT__QUARANTINE_MAX_S',
            'THALOR__TRANSPORT__QUARANTINE_MAX_S',
        ])
        if quarantine_max_raw is not None:
            payload['quarantine_max_s'] = quarantine_max_raw

        healthcheck_interval_raw = _first_env_raw([
            'TRANSPORT_HEALTHCHECK_INTERVAL_S',
            'THALOR__NETWORK__TRANSPORT__HEALTHCHECK_INTERVAL_S',
            'THALOR__TRANSPORT__HEALTHCHECK_INTERVAL_S',
        ])
        if healthcheck_interval_raw is not None:
            payload['healthcheck_interval_s'] = healthcheck_interval_raw

        healthcheck_timeout_raw = _first_env_raw([
            'TRANSPORT_HEALTHCHECK_TIMEOUT_S',
            'THALOR__NETWORK__TRANSPORT__HEALTHCHECK_TIMEOUT_S',
            'THALOR__TRANSPORT__HEALTHCHECK_TIMEOUT_S',
        ])
        if healthcheck_timeout_raw is not None:
            payload['healthcheck_timeout_s'] = healthcheck_timeout_raw

        healthcheck_mode_raw = _first_env_raw([
            'TRANSPORT_HEALTHCHECK_MODE',
            'THALOR__NETWORK__TRANSPORT__HEALTHCHECK_MODE',
            'THALOR__TRANSPORT__HEALTHCHECK_MODE',
        ])
        if healthcheck_mode_raw is not None:
            payload['healthcheck_mode'] = healthcheck_mode_raw

        healthcheck_url_raw = _first_env_raw([
            'TRANSPORT_HEALTHCHECK_URL',
            'THALOR__NETWORK__TRANSPORT__HEALTHCHECK_URL',
            'THALOR__TRANSPORT__HEALTHCHECK_URL',
        ])
        if healthcheck_url_raw is not None:
            payload['healthcheck_url'] = healthcheck_url_raw

        fail_open_raw = _first_env_raw([
            'TRANSPORT_FAIL_OPEN_WHEN_EXHAUSTED',
            'THALOR__NETWORK__TRANSPORT__FAIL_OPEN_WHEN_EXHAUSTED',
            'THALOR__TRANSPORT__FAIL_OPEN_WHEN_EXHAUSTED',
        ])
        if fail_open_raw is not None:
            payload['fail_open_when_exhausted'] = fail_open_raw

        structured_log_path_raw = _first_env_raw([
            'TRANSPORT_LOG_PATH',
            'THALOR__NETWORK__TRANSPORT__LOG_PATH',
            'THALOR__TRANSPORT__LOG_PATH',
        ])
        if structured_log_path_raw is not None:
            payload['structured_log_path'] = structured_log_path_raw

        return cls.from_mapping(payload)

    @classmethod
    def from_env(cls) -> 'NetworkTransportConfig':
        return cls.from_sources(None)


@dataclass(slots=True)
class _EndpointRuntimeState:
    endpoint: TransportEndpoint
    inflight: int = 0
    total_selections: int = 0
    total_successes: int = 0
    total_failures: int = 0
    total_healthcheck_failures: int = 0
    consecutive_failures: int = 0
    last_selected_at_monotonic: float = 0.0
    last_selected_at_utc: str | None = None
    last_success_at_utc: str | None = None
    last_failure_at_utc: str | None = None
    last_healthcheck_at_monotonic: float = 0.0
    last_healthcheck_at_utc: str | None = None
    last_healthcheck_status: str | None = None
    last_error: str | None = None
    quarantined_until_monotonic: float = 0.0

    def is_available(self, now: float) -> bool:
        return self.quarantined_until_monotonic <= now


class NetworkTransportManager:
    """Thread-safe transport endpoint manager with circuit-breaker semantics."""

    def __init__(
        self,
        config: NetworkTransportConfig | None = None,
        *,
        logger: logging.Logger | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config or NetworkTransportConfig.disabled()
        self._logger = logger or logging.getLogger('natbin.network_transport')
        self._sleep = sleep_fn
        self._random = random_fn
        self._monotonic = monotonic_fn
        self._lock = threading.RLock()
        self._env_lock = threading.RLock()
        self._states: dict[str, _EndpointRuntimeState] = {}
        for endpoint in self._config.endpoints:
            if endpoint.identity not in self._states:
                self._states[endpoint.identity] = _EndpointRuntimeState(endpoint=endpoint)
        if self._config.enabled:
            self._emit(
                logging.INFO,
                'network_transport_initialized',
                config=self._config.as_dict(mask_secret=True),
            )

    @classmethod
    def disabled(cls, *, logger: logging.Logger | None = None) -> 'NetworkTransportManager':
        return cls(NetworkTransportConfig.disabled(), logger=logger)

    @classmethod
    def from_env(cls, *, logger: logging.Logger | None = None) -> 'NetworkTransportManager':
        return cls(NetworkTransportConfig.from_env(), logger=logger)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None, *, logger: logging.Logger | None = None) -> 'NetworkTransportManager':
        return cls(NetworkTransportConfig.from_mapping(data), logger=logger)

    @property
    def config(self) -> NetworkTransportConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def ready(self) -> bool:
        return self._config.ready

    @property
    def endpoint_count(self) -> int:
        return len(self._states)

    def dependency_status(self) -> dict[str, Any]:
        return self._config.dependency_status()

    def ensure_runtime_dependencies(self) -> None:
        dependency = self.dependency_status()
        if bool(dependency.get('available', True)):
            return
        raise NetworkTransportConfigurationError(str(dependency.get('reason') or 'network_transport_dependency_missing'))

    def compute_backoff_delay(self, attempt: int) -> float:
        capped = min(self._config.backoff_max_s, self._config.backoff_base_s * (2 ** max(0, int(attempt) - 1)))
        jitter = capped * self._config.jitter_ratio * self._random()
        return min(self._config.backoff_max_s, capped + jitter)

    def build_binding(self, endpoint: TransportEndpoint | None, *, operation: str = 'default') -> NetworkTransportBinding:
        if endpoint is None:
            return NetworkTransportBinding(
                operation=operation,
                endpoint=None,
                env_overlay={},
                websocket_options={},
                selected_at_utc=_utc_now_iso(),
            )
        return NetworkTransportBinding(
            operation=operation,
            endpoint=endpoint,
            env_overlay=endpoint.to_env_overlay(default_no_proxy=self._config.no_proxy),
            websocket_options=endpoint.to_websocket_options(default_no_proxy=self._config.no_proxy),
            selected_at_utc=_utc_now_iso(),
        )

    def select_binding(self, *, operation: str = 'default', allow_fail_open: bool | None = None) -> NetworkTransportBinding:
        endpoint = self._select_endpoint(operation=operation, allow_fail_open=allow_fail_open)
        binding = self.build_binding(endpoint, operation=operation)
        self._emit(
            logging.DEBUG,
            'network_transport_binding_selected',
            operation=operation,
            binding=binding.as_dict(mask_secret=True),
        )
        return binding

    @contextmanager
    def apply_environment(self, binding: NetworkTransportBinding) -> Iterator[None]:
        """Temporarily apply a binding environment overlay.

        The operation is serialized by an environment lock because ``os.environ``
        is process-global. This makes temporary env mutation thread-safe at the
        process level.
        """

        if not binding.env_overlay:
            yield
            return
        with self._env_lock:
            previous = {key: os.environ.get(key) for key in binding.env_overlay}
            try:
                for key, value in binding.env_overlay.items():
                    os.environ[key] = value
                yield
            finally:
                for key, old_value in previous.items():
                    if old_value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = old_value

    def execute(
        self,
        *,
        operation: str,
        func: Callable[[NetworkTransportBinding], _T],
        retry_exceptions: type[BaseException] | tuple[type[BaseException], ...] = Exception,
        max_attempts: int | None = None,
        apply_environment_overlay: bool = False,
    ) -> _T:
        attempts = max(1, int(max_attempts or self._config.max_retries))
        retry_tuple = retry_exceptions if isinstance(retry_exceptions, tuple) else (retry_exceptions,)
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            binding = self.select_binding(operation=operation)
            started = self._monotonic()
            try:
                if apply_environment_overlay:
                    with self.apply_environment(binding):
                        result = func(binding)
                else:
                    result = func(binding)
            except BaseException as exc:
                latency_s = max(0.0, self._monotonic() - started)
                if isinstance(exc, Exception):
                    self.record_failure(binding.endpoint, operation=operation, error=exc, latency_s=latency_s)
                    last_error = exc
                    if not isinstance(exc, retry_tuple) or attempt >= attempts:
                        raise
                    wait_s = self.compute_backoff_delay(attempt)
                    self._emit(
                        logging.WARNING,
                        'network_transport_retry_scheduled',
                        operation=operation,
                        attempt=attempt,
                        max_attempts=attempts,
                        wait_s=round(wait_s, 6),
                        endpoint=binding.endpoint.to_safe_dict(mask_secret=True) if binding.endpoint is not None else None,
                        reason=_safe_error(exc),
                    )
                    self._sleep(wait_s)
                    continue
                self._release_inflight(binding.endpoint)
                raise
            latency_s = max(0.0, self._monotonic() - started)
            self.record_success(binding.endpoint, operation=operation, latency_s=latency_s)
            return result
        raise NetworkTransportUnavailable(
            f'network transport exhausted after {attempts} attempts for operation={operation!r}; '
            f'last_error={_safe_error(last_error)}'
        ) from last_error

    def record_success(self, endpoint: TransportEndpoint | None, *, operation: str = 'default', latency_s: float | None = None) -> None:
        if endpoint is None:
            return
        with self._lock:
            state = self._states.get(endpoint.identity)
            if state is None:
                return
            state.total_successes += 1
            state.consecutive_failures = 0
            state.last_success_at_utc = _utc_now_iso()
            state.last_error = None
            state.last_healthcheck_status = 'passive_success'
            state.quarantined_until_monotonic = 0.0
            self._decrement_inflight_locked(state)
        self._emit(
            logging.INFO,
            'network_transport_success',
            operation=operation,
            endpoint=endpoint.to_safe_dict(mask_secret=True),
            latency_s=round(float(latency_s or 0.0), 6),
        )

    def record_failure(
        self,
        endpoint: TransportEndpoint | None,
        *,
        operation: str = 'default',
        error: BaseException | str | None = None,
        latency_s: float | None = None,
    ) -> None:
        if endpoint is None:
            return
        now = self._monotonic()
        quarantined = False
        quarantine_for_s = 0.0
        error_text = _safe_error(error)
        with self._lock:
            state = self._states.get(endpoint.identity)
            if state is None:
                return
            state.total_failures += 1
            state.consecutive_failures += 1
            state.last_failure_at_utc = _utc_now_iso()
            state.last_error = error_text
            self._decrement_inflight_locked(state)
            if state.consecutive_failures >= self._config.failure_threshold:
                penalty_exp = max(0, state.consecutive_failures - self._config.failure_threshold)
                quarantine_for_s = min(self._config.quarantine_max_s, self._config.quarantine_base_s * (2 ** penalty_exp))
                state.quarantined_until_monotonic = max(state.quarantined_until_monotonic, now + quarantine_for_s)
                state.last_healthcheck_status = 'quarantined'
                quarantined = True
        self._emit(
            logging.WARNING if quarantined else logging.INFO,
            'network_transport_failure',
            operation=operation,
            endpoint=endpoint.to_safe_dict(mask_secret=True),
            latency_s=round(float(latency_s or 0.0), 6),
            reason=error_text,
            quarantined=quarantined,
            quarantine_for_s=round(quarantine_for_s, 6),
        )

    def run_health_checks(self, *, only_unavailable: bool = True) -> dict[str, Any]:
        if not self.ready:
            return {
                'enabled': self.enabled,
                'checked': 0,
                'healthy': 0,
                'unhealthy': 0,
                'results': [],
            }
        with self._lock:
            now = self._monotonic()
            candidates = [
                state.endpoint
                for state in self._states.values()
                if self._should_probe_locked(state, now, only_unavailable=only_unavailable)
            ]
        results: list[dict[str, Any]] = []
        healthy = 0
        for endpoint in candidates:
            ok, reason = self._probe_endpoint(endpoint)
            self._apply_probe_result(endpoint, ok=ok, reason=reason)
            if ok:
                healthy += 1
            results.append({
                'endpoint': endpoint.to_safe_dict(mask_secret=True),
                'healthy': ok,
                'reason': reason,
            })
        payload = {
            'enabled': self.enabled,
            'checked': len(results),
            'healthy': healthy,
            'unhealthy': len(results) - healthy,
            'results': results,
        }
        self._emit(logging.INFO, 'network_transport_healthcheck_batch', **payload)
        return payload

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._monotonic()
            endpoints: list[dict[str, Any]] = []
            for state in self._states.values():
                available = state.is_available(now)
                endpoints.append({
                    'endpoint': state.endpoint.to_safe_dict(mask_secret=True),
                    'available': available,
                    'quarantined': not available,
                    'quarantined_for_s': round(max(0.0, state.quarantined_until_monotonic - now), 6),
                    'inflight': state.inflight,
                    'total_selections': state.total_selections,
                    'total_successes': state.total_successes,
                    'total_failures': state.total_failures,
                    'total_healthcheck_failures': state.total_healthcheck_failures,
                    'consecutive_failures': state.consecutive_failures,
                    'last_selected_at_utc': state.last_selected_at_utc,
                    'last_success_at_utc': state.last_success_at_utc,
                    'last_failure_at_utc': state.last_failure_at_utc,
                    'last_healthcheck_at_utc': state.last_healthcheck_at_utc,
                    'last_healthcheck_status': state.last_healthcheck_status,
                    'last_error': state.last_error,
                })
        available_count = sum(1 for item in endpoints if item['available'])
        return {
            'enabled': self.enabled,
            'ready': self.ready,
            'requires_pysocks': self._config.requires_pysocks,
            'dependency_status': self.dependency_status(),
            'endpoint_count': len(endpoints),
            'available_endpoint_count': available_count,
            'quarantined_endpoint_count': len(endpoints) - available_count,
            'config': self._config.as_dict(mask_secret=True),
            'endpoints': endpoints,
        }

    def _select_endpoint(self, *, operation: str, allow_fail_open: bool | None = None) -> TransportEndpoint | None:
        if not self.enabled:
            return None
        if not self._states:
            raise NetworkTransportUnavailable('network transport is enabled but no endpoints are configured')
        endpoint = self._try_select_endpoint()
        if endpoint is not None:
            return endpoint
        self.run_health_checks(only_unavailable=True)
        endpoint = self._try_select_endpoint()
        if endpoint is not None:
            return endpoint
        if bool(self._config.fail_open_when_exhausted if allow_fail_open is None else allow_fail_open):
            endpoint = self._select_fail_open_endpoint()
            self._emit(
                logging.WARNING,
                'network_transport_fail_open',
                operation=operation,
                endpoint=endpoint.to_safe_dict(mask_secret=True),
            )
            return endpoint
        raise NetworkTransportUnavailable(f'no healthy network transport endpoint available for operation={operation!r}')

    def _try_select_endpoint(self) -> TransportEndpoint | None:
        with self._lock:
            now = self._monotonic()
            available_states = [state for state in self._states.values() if state.is_available(now)]
            if not available_states:
                return None
            available_states.sort(
                key=lambda state: (
                    int(state.endpoint.priority),
                    int(state.consecutive_failures),
                    int(state.inflight),
                    float(state.last_selected_at_monotonic),
                    str(state.endpoint.name),
                )
            )
            chosen = available_states[0]
            chosen.inflight += 1
            chosen.total_selections += 1
            chosen.last_selected_at_monotonic = now
            chosen.last_selected_at_utc = _utc_now_iso()
            return chosen.endpoint

    def _select_fail_open_endpoint(self) -> TransportEndpoint:
        with self._lock:
            now = self._monotonic()
            states = sorted(
                self._states.values(),
                key=lambda state: (
                    float(state.quarantined_until_monotonic),
                    int(state.consecutive_failures),
                    int(state.inflight),
                    str(state.endpoint.name),
                ),
            )
            if not states:
                raise NetworkTransportUnavailable('network transport has no configured endpoint')
            chosen = states[0]
            chosen.inflight += 1
            chosen.total_selections += 1
            chosen.last_selected_at_monotonic = max(now, chosen.last_selected_at_monotonic)
            chosen.last_selected_at_utc = _utc_now_iso()
            return chosen.endpoint

    def _probe_endpoint(self, endpoint: TransportEndpoint) -> tuple[bool, str | None]:
        mode = self._config.healthcheck_mode
        timeout_s = max(self._config.healthcheck_timeout_s, endpoint.connect_timeout_s)
        if mode == 'http':
            target_host, target_port = _healthcheck_target(self._config.healthcheck_url)
            if endpoint.transport_type in {'http', 'https'}:
                return self._probe_via_http_connect(endpoint, timeout_s=timeout_s, target_host=target_host, target_port=target_port)
            if endpoint.transport_type == 'socks':
                return self._probe_via_socks_connect(endpoint, timeout_s=timeout_s, target_host=target_host, target_port=target_port)
        return self._probe_via_tcp(endpoint, timeout_s=timeout_s)

    def _probe_via_tcp(self, endpoint: TransportEndpoint, *, timeout_s: float) -> tuple[bool, str | None]:
        try:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout_s):
                pass
        except Exception as exc:
            return False, _safe_error(exc)
        return True, None

    def _probe_via_http_connect(self, endpoint: TransportEndpoint, *, timeout_s: float, target_host: str, target_port: int) -> tuple[bool, str | None]:
        try:
            raw_sock = socket.create_connection((endpoint.host, endpoint.port), timeout=timeout_s)
            sock = raw_sock
            if endpoint.scheme == 'https':
                context = ssl.create_default_context()
                if not endpoint.verify_tls:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                sock = context.wrap_socket(raw_sock, server_hostname=endpoint.host if endpoint.verify_tls else None)
            sock.settimeout(timeout_s)
            headers = [
                f'CONNECT {target_host}:{int(target_port)} HTTP/1.1',
                f'Host: {target_host}:{int(target_port)}',
                'Proxy-Connection: Keep-Alive',
            ]
            if endpoint.username not in (None, '') or endpoint.password not in (None, ''):
                user = str(endpoint.username or '')
                pwd = str(endpoint.password or '')
                token = base64.b64encode(f'{user}:{pwd}'.encode('utf-8')).decode('ascii')
                headers.append(f'Proxy-Authorization: Basic {token}')
            request = ('\r\n'.join(headers) + '\r\n\r\n').encode('ascii', errors='ignore')
            sock.sendall(request)
            response = b''
            while b'\r\n\r\n' not in response and len(response) < 8192:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            try:
                sock.close()
            except Exception:
                pass
            first_line = response.split(b'\r\n', 1)[0].decode('iso-8859-1', errors='replace').strip()
            parts = first_line.split()
            status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
            if 200 <= status < 300:
                return True, None
            if status == 407:
                return False, 'http_status:407'
            if status > 0:
                return False, f'http_status:{status}'
            return False, first_line or 'http_connect_failed'
        except Exception as exc:
            return False, _safe_error(exc)

    def _probe_via_socks_connect(self, endpoint: TransportEndpoint, *, timeout_s: float, target_host: str, target_port: int) -> tuple[bool, str | None]:
        try:
            import socks  # type: ignore
        except Exception as exc:
            return False, _safe_error(exc)
        proxy_type = {
            'socks4': getattr(socks, 'SOCKS4', None),
            'socks4a': getattr(socks, 'SOCKS4', None),
            'socks5': getattr(socks, 'SOCKS5', None),
            'socks5h': getattr(socks, 'SOCKS5', None),
            'socks': getattr(socks, 'SOCKS5', None),
        }.get(endpoint.scheme, getattr(socks, 'SOCKS5', None))
        rdns = endpoint.scheme in {'socks4a', 'socks5h'}
        try:
            sock = socks.socksocket()
            sock.settimeout(timeout_s)
            sock.set_proxy(
                proxy_type,
                endpoint.host,
                int(endpoint.port),
                rdns=rdns,
                username=str(endpoint.username) if endpoint.username not in (None, '') else None,
                password=str(endpoint.password) if endpoint.password not in (None, '') else None,
            )
            sock.connect((target_host, int(target_port)))
            sock.close()
            return True, None
        except Exception as exc:
            return False, _safe_error(exc)

    def _apply_probe_result(self, endpoint: TransportEndpoint, *, ok: bool, reason: str | None) -> None:
        with self._lock:
            state = self._states.get(endpoint.identity)
            if state is None:
                return
            now = self._monotonic()
            was_quarantined = not state.is_available(now)
            state.last_healthcheck_at_monotonic = now
            state.last_healthcheck_at_utc = _utc_now_iso()
            state.last_error = reason
            if ok:
                state.consecutive_failures = 0
                state.quarantined_until_monotonic = 0.0
                state.last_healthcheck_status = 'healthy'
            else:
                state.total_healthcheck_failures += 1
                state.last_healthcheck_status = 'unhealthy'
                penalty = min(self._config.quarantine_max_s, max(self._config.quarantine_base_s, self._config.healthcheck_interval_s or 0.0))
                state.quarantined_until_monotonic = max(state.quarantined_until_monotonic, now + penalty)
        self._emit(
            logging.INFO if ok else logging.WARNING,
            'network_transport_healthcheck_result',
            endpoint=endpoint.to_safe_dict(mask_secret=True),
            healthy=ok,
            recovered=bool(ok and was_quarantined),
            reason=reason,
        )

    def _should_probe_locked(self, state: _EndpointRuntimeState, now: float, *, only_unavailable: bool) -> bool:
        if state.inflight > 0:
            return False
        if only_unavailable and state.is_available(now):
            return False
        interval_s = self._config.healthcheck_interval_s
        if interval_s <= 0.0:
            return True
        if state.last_healthcheck_at_monotonic <= 0.0:
            return True
        return (now - state.last_healthcheck_at_monotonic) >= interval_s

    def _release_inflight(self, endpoint: TransportEndpoint | None) -> None:
        if endpoint is None:
            return
        with self._lock:
            state = self._states.get(endpoint.identity)
            if state is None:
                return
            self._decrement_inflight_locked(state)

    @staticmethod
    def _decrement_inflight_locked(state: _EndpointRuntimeState) -> None:
        state.inflight = max(0, int(state.inflight) - 1)

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        payload = {
            'kind': 'network_transport',
            'event': event,
            'at_utc': _utc_now_iso(),
            **fields,
        }
        try:
            self._logger.log(level, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        except Exception:
            pass
        if self._config.structured_log_path is None:
            return
        try:
            from ..ops.structured_log import append_jsonl

            append_jsonl(self._config.structured_log_path, payload)
        except Exception:
            pass
