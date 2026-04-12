from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..state.control_repo import read_control_artifact, write_control_artifact
from .failsafe import CircuitBreakerSnapshot

_BOOTSTRAP_HINTS = ('collect_recent', 'refresh_market_context', 'collect_candles', 'backfill_candles', 'asset_prepare')
_EXECUTION_HINTS = ('execution', 'submit', 'reconcile', 'order', 'broker_guard')
_TRANSPORT_HINTS = ('jsondecodeerror', 'timeout', 'timed out', 'transport', 'proxy', 'socks', 'connect', 'connection', 'network', 'websocket', 'ws', 'http', 'https')

__all__ = ['build_breaker_artifact_payload', 'classify_breaker_reason', 'latest_transport_error', 'write_breaker_artifact']


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _safe_str(value: Any) -> str | None:
    if value in (None, ''):
        return None
    text = ' '.join(str(value).strip().split())
    return text or None


def _parse_iso(value: Any) -> datetime | None:
    text = _safe_str(value)
    if text is None:
        return None
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _is_transportish(value: Any) -> bool:
    text = str(value or '').strip().lower()
    return bool(text) and any(hint in text for hint in _TRANSPORT_HINTS)


def classify_breaker_reason(reason: str | None, *, failure_domain: str | None = None) -> dict[str, Any]:
    raw = _safe_str(reason)
    domain = str(failure_domain or '').strip().lower()
    lowered = str(raw or '').lower()
    detail = raw
    if _is_transportish(raw):
        return {'code': 'broker_transport_failure', 'category': 'broker_transport', 'detail': detail}
    if domain == 'broker_bootstrap' or any(hint in lowered for hint in _BOOTSTRAP_HINTS):
        return {'code': 'broker_bootstrap_failure', 'category': 'broker_bootstrap', 'detail': detail}
    if domain == 'execution' or any(hint in lowered for hint in _EXECUTION_HINTS):
        return {'code': 'execution_failure', 'category': 'execution', 'detail': detail}
    if domain == 'decision' or 'decision' in lowered:
        return {'code': 'decision_failure', 'category': 'decision', 'detail': detail}
    if domain == 'prepare' or 'prepare' in lowered:
        return {'code': 'prepare_failure', 'category': 'prepare', 'detail': detail}
    if domain == 'portfolio' or 'portfolio' in lowered:
        return {'code': 'portfolio_failure', 'category': 'portfolio', 'detail': detail}
    if 'cycle' in lowered:
        return {'code': 'runtime_cycle_failure', 'category': 'runtime', 'detail': detail}
    return {'code': 'runtime_failure', 'category': 'runtime', 'detail': detail}


def _transport_errors_from_connectivity(connectivity: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    if not isinstance(connectivity, dict):
        return out
    transport = dict(connectivity.get('transport') or {})
    for item in list(transport.get('endpoints') or []):
        if not isinstance(item, dict):
            continue
        err = _safe_str(item.get('last_error'))
        if err and _is_transportish(err):
            out.append(err)
    return out


def latest_transport_error(connectivity: dict[str, Any] | None, *, market_context: dict[str, Any] | None = None) -> str | None:
    if isinstance(market_context, dict):
        dependency_reason = _safe_str(market_context.get('dependency_reason'))
        if dependency_reason and _is_transportish(dependency_reason):
            return dependency_reason
    for err in _transport_errors_from_connectivity(connectivity):
        return err
    return None


def _symptom_from_breaker(breaker: CircuitBreakerSnapshot | None, failsafe_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if breaker is None:
        return {'code': 'none', 'detail': None}
    state = str(breaker.state)
    reason = breaker.primary_cause or breaker.reason
    if state == 'open':
        return {'code': 'circuit_open', 'detail': reason}
    if state == 'half_open':
        blocked_reason = str((failsafe_snapshot or {}).get('blocked_reason') or '')
        if blocked_reason == 'circuit_half_open_blocked':
            return {'code': 'circuit_half_open_blocked', 'detail': reason}
        return {'code': 'circuit_half_open', 'detail': reason}
    return {'code': 'circuit_closed', 'detail': reason}


def _primary_cause(
    breaker: CircuitBreakerSnapshot | None,
    market_context: dict[str, Any] | None,
    connectivity: dict[str, Any] | None,
) -> dict[str, Any]:
    if breaker is not None and breaker.last_transport_error:
        return {
            'code': 'broker_transport_failure',
            'category': 'broker_transport',
            'detail': breaker.last_transport_error,
        }
    if isinstance(market_context, dict) and bool(market_context.get('degraded')):
        dependency_reason = _safe_str(market_context.get('dependency_reason'))
        if _is_transportish(dependency_reason):
            return {'code': 'broker_transport_failure', 'category': 'broker_transport', 'detail': dependency_reason}
        if dependency_reason:
            return {'code': 'broker_dependency_failure', 'category': 'broker_dependency', 'detail': dependency_reason}
        return {'code': 'broker_bootstrap_failure', 'category': 'broker_bootstrap', 'detail': _safe_str(market_context.get('failure_kind'))}
    transport_error = latest_transport_error(connectivity, market_context=market_context)
    if transport_error:
        return {'code': 'broker_transport_failure', 'category': 'broker_transport', 'detail': transport_error}
    if breaker is None:
        return {'code': 'none', 'category': 'none', 'detail': None}
    if not _safe_str(breaker.primary_cause) and not _safe_str(breaker.reason):
        return {'code': 'none', 'category': 'none', 'detail': None}
    return classify_breaker_reason((breaker.primary_cause or breaker.reason), failure_domain=breaker.failure_domain)


def _scope_payload(asset: str, interval_sec: int) -> dict[str, Any]:
    return {'asset': str(asset), 'interval_sec': int(interval_sec)}


def build_breaker_artifact_payload(
    *,
    repo_root: str | Path,
    asset: str,
    interval_sec: int,
    breaker: CircuitBreakerSnapshot | None,
    market_context: dict[str, Any] | None = None,
    failsafe_snapshot: dict[str, Any] | None = None,
    connectivity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    connectivity_payload = connectivity
    if not isinstance(connectivity_payload, dict):
        connectivity_payload = read_control_artifact(repo_root=repo, asset=asset, interval_sec=interval_sec, name='connectivity')
    primary_cause = _primary_cause(breaker, market_context, connectivity_payload)
    symptom = _symptom_from_breaker(breaker, failsafe_snapshot)
    transport = dict((connectivity_payload or {}).get('transport') or {})
    mc = dict(market_context or {})
    payload = {
        'kind': 'breaker_status',
        'at_utc': _utcnow_iso(),
        'scope': _scope_payload(asset, interval_sec),
        'breaker': breaker.as_dict() if breaker is not None else None,
        'last_transport_error': breaker.last_transport_error if breaker is not None else None,
        'failsafe_snapshot': failsafe_snapshot or None,
        'primary_cause': primary_cause,
        'symptom': symptom,
        'half_open': {
            'trial_available': bool((failsafe_snapshot or {}).get('half_open_trial_available')),
            'trials_remaining': (failsafe_snapshot or {}).get('half_open_trials_remaining'),
            'trial_in_flight': bool((failsafe_snapshot or {}).get('half_open_trial_in_flight')),
        },
        'market_context': {
            'degraded': bool(mc.get('degraded')),
            'dependency_available': mc.get('dependency_available'),
            'failure_kind': mc.get('failure_kind'),
            'dependency_reason': mc.get('dependency_reason'),
            'open_source': mc.get('open_source'),
            'at_utc': mc.get('at_utc'),
        },
        'connectivity': {
            'transport_enabled': transport.get('enabled'),
            'transport_ready': transport.get('ready'),
            'available_endpoint_count': transport.get('available_endpoint_count'),
            'endpoint_count': transport.get('endpoint_count'),
            'last_transport_error': latest_transport_error(connectivity_payload, market_context=market_context),
        },
    }
    return payload


def write_breaker_artifact(
    *,
    repo_root: str | Path,
    asset: str,
    interval_sec: int,
    breaker: CircuitBreakerSnapshot | None,
    market_context: dict[str, Any] | None = None,
    failsafe_snapshot: dict[str, Any] | None = None,
    connectivity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = build_breaker_artifact_payload(
        repo_root=repo_root,
        asset=asset,
        interval_sec=interval_sec,
        breaker=breaker,
        market_context=market_context,
        failsafe_snapshot=failsafe_snapshot,
        connectivity=connectivity,
    )
    write_control_artifact(
        repo_root=repo_root,
        asset=asset,
        interval_sec=interval_sec,
        name='breaker',
        payload=payload,
    )
    return payload
