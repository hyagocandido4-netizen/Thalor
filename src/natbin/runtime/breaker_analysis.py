from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

_BROKER_BOOTSTRAP_STEPS = {'collect_recent', 'refresh_market_context', 'collect_candles', 'backfill_candles'}
_PREPARE_STEPS = {'make_dataset', 'refresh_daily_summary', 'auto_volume', 'auto_isoblend', 'auto_hourthr'}
_TRANSPORT_HINTS = (
    'jsondecodeerror',
    'non-json',
    'proxy',
    'socks',
    'bad gateway',
    'gateway',
    'timeout',
    'timed out',
    'connection',
    'websocket',
    'broker unavailable',
    'broker_unavailable',
    'transport',
    'connect failed',
    'connect returned',
)


@dataclass(frozen=True)
class BreakerFailureContext:
    primary_cause: str
    failure_domain: str
    failure_step: str | None = None
    transport_error: str | None = None
    source: str | None = None
    detail: str | None = None


def _clean_text(value: Any) -> str:
    if value in (None, ''):
        return ''
    return ' '.join(str(value).strip().split())


def _prefer_nonempty(*values: Any) -> str:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return ''


def _extract_transport_error(*values: Any) -> str | None:
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if any(hint in lowered for hint in _TRANSPORT_HINTS):
            return cleaned
    return None


def _classify_step_failure(step_name: str, *, kind: str, stdout_tail: str = '', stderr_tail: str = '') -> BreakerFailureContext:
    step = str(step_name or '').strip()
    normalized_step = step or 'unknown'
    outcome_kind = str(kind or 'error').strip().lower()
    detail = _prefer_nonempty(stderr_tail, stdout_tail)
    transport_error = _extract_transport_error(stderr_tail, stdout_tail)

    if normalized_step in _BROKER_BOOTSTRAP_STEPS:
        suffix = 'timeout' if outcome_kind == 'timeout' else ('failed' if transport_error else 'error')
        return BreakerFailureContext(
            primary_cause=f'broker_bootstrap_{normalized_step}_{suffix}',
            failure_domain='broker_bootstrap',
            failure_step=normalized_step,
            transport_error=transport_error,
            source='cycle',
            detail=detail or None,
        )

    if normalized_step in _PREPARE_STEPS:
        return BreakerFailureContext(
            primary_cause=f'prepare_{normalized_step}_{outcome_kind}',
            failure_domain='prepare',
            failure_step=normalized_step,
            transport_error=transport_error,
            source='cycle',
            detail=detail or None,
        )

    if normalized_step == 'observe_loop_once':
        suffix = 'timeout' if outcome_kind == 'timeout' else 'failed'
        return BreakerFailureContext(
            primary_cause=f'decision_cycle_{suffix}',
            failure_domain='decision',
            failure_step=normalized_step,
            transport_error=transport_error,
            source='cycle',
            detail=detail or None,
        )

    return BreakerFailureContext(
        primary_cause=f'runtime_{normalized_step}_{outcome_kind}',
        failure_domain='runtime',
        failure_step=normalized_step,
        transport_error=transport_error,
        source='cycle',
        detail=detail or None,
    )


def classify_cycle_outcomes(outcomes: Iterable[dict[str, Any]]) -> BreakerFailureContext:
    for item in outcomes:
        if not isinstance(item, dict):
            continue
        if bool(item.get('ok')):
            continue
        return _classify_step_failure(
            str(item.get('name') or ''),
            kind=str(item.get('kind') or 'error'),
            stdout_tail=str(item.get('stdout_tail') or ''),
            stderr_tail=str(item.get('stderr_tail') or ''),
        )
    return BreakerFailureContext(
        primary_cause='runtime_cycle_failed',
        failure_domain='runtime',
        source='cycle',
    )


def _find_scope_outcome(results: Iterable[dict[str, Any]], scope_tag: str) -> dict[str, Any] | None:
    for item in results:
        if not isinstance(item, dict):
            continue
        if str(item.get('scope_tag') or '') == str(scope_tag):
            return item
    return None


def classify_portfolio_scope_failure(
    *,
    scope_tag: str,
    errors: Iterable[str],
    prepare_results: Iterable[dict[str, Any]],
    candidate_results: Iterable[dict[str, Any]],
    execution_results: Iterable[dict[str, Any]],
) -> BreakerFailureContext | None:
    prepare_outcome = _find_scope_outcome(prepare_results, scope_tag)
    if isinstance(prepare_outcome, dict):
        outcome = dict(prepare_outcome.get('outcome') or {})
        if int(outcome.get('returncode') or 0) != 0:
            step_name = str(outcome.get('name') or 'asset_prepare')
            return _classify_step_failure(
                step_name,
                kind='timeout' if 'timeout' in str(outcome.get('error') or '').lower() else 'nonzero_exit',
                stdout_tail=str(outcome.get('stdout_tail') or ''),
                stderr_tail=str(outcome.get('stderr_tail') or ''),
            )

    candidate_outcome = _find_scope_outcome(candidate_results, scope_tag)
    if isinstance(candidate_outcome, dict):
        outcome = dict(candidate_outcome.get('outcome') or {})
        if int(outcome.get('returncode') or 0) != 0:
            return BreakerFailureContext(
                primary_cause='decision_cycle_failed',
                failure_domain='decision',
                failure_step='observe_loop_once',
                transport_error=_extract_transport_error(outcome.get('stderr_tail'), outcome.get('stdout_tail')),
                source='portfolio',
                detail=_prefer_nonempty(outcome.get('stderr_tail'), outcome.get('stdout_tail')) or None,
            )

    execution_outcome = _find_scope_outcome(execution_results, scope_tag)
    if isinstance(execution_outcome, dict):
        outcome = dict(execution_outcome.get('outcome') or {})
        if int(outcome.get('returncode') or 0) != 0:
            return BreakerFailureContext(
                primary_cause='execution_cycle_failed',
                failure_domain='execution',
                failure_step='execution',
                transport_error=_extract_transport_error(outcome.get('stderr_tail'), outcome.get('stdout_tail')),
                source='portfolio',
                detail=_prefer_nonempty(outcome.get('stderr_tail'), outcome.get('stdout_tail')) or None,
            )

    for raw in errors:
        text = str(raw or '')
        if scope_tag not in text:
            continue
        lowered = text.lower()
        if 'prepare_failed' in lowered or 'prepare_exception' in lowered:
            return BreakerFailureContext(
                primary_cause='prepare_asset_prepare_failed',
                failure_domain='prepare',
                failure_step='asset_prepare',
                transport_error=_extract_transport_error(text),
                source='portfolio',
                detail=text,
            )
        if 'candidate_failed' in lowered or 'candidate_exception' in lowered:
            return BreakerFailureContext(
                primary_cause='decision_cycle_failed',
                failure_domain='decision',
                failure_step='observe_loop_once',
                transport_error=_extract_transport_error(text),
                source='portfolio',
                detail=text,
            )
        if 'execution_failed' in lowered or 'execution_exception' in lowered:
            return BreakerFailureContext(
                primary_cause='execution_cycle_failed',
                failure_domain='execution',
                failure_step='execution',
                transport_error=_extract_transport_error(text),
                source='portfolio',
                detail=text,
            )

    return None
