from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .paths import retrain_plan_path, retrain_status_path


_PRIORITY_ORDER = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
_TERMINAL_OPERATIONAL_STATES = {'fitting', 'evaluated', 'promoted', 'rejected'}
_TERMINAL_STATE_MAX_AGE_HOURS = 6


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _now_dt(now_utc: datetime | None = None) -> datetime:
    return (now_utc or datetime.now(tz=UTC)).astimezone(UTC)


def _parse_iso(raw: Any) -> datetime | None:
    if raw in (None, ''):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _preserve_terminal_status(prev: dict[str, Any], *, now_dt: datetime) -> bool:
    state = str(prev.get('state') or '').strip().lower()
    if state not in _TERMINAL_OPERATIONAL_STATES:
        return False
    stamp = _parse_iso(prev.get('updated_at_utc') or prev.get('generated_at_utc'))
    if stamp is None:
        return False
    age_sec = max(0.0, float((now_dt - stamp).total_seconds()))
    return age_sec <= float(_TERMINAL_STATE_MAX_AGE_HOURS * 3600)


def _priority_max(*levels: str | None) -> str:
    current = 'low'
    for level in levels:
        txt = str(level or 'low').strip().lower()
        if _PRIORITY_ORDER.get(txt, 0) > _PRIORITY_ORDER.get(current, 0):
            current = txt
    return current


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def orchestrate_retrain(
    *,
    repo_root: str | Path,
    scope_tag: str,
    artifact_dir: str | Path,
    trigger_payload: dict[str, Any] | None,
    drift_state: dict[str, Any] | None,
    regime: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
    learned_reliability: float | None,
    anti_overfit: dict[str, Any] | None,
    policy: dict[str, Any],
    cooldown_hours: int = 24,
    watch_reliability_below: float = 0.55,
    queue_on_regime_block: bool = True,
    queue_on_anti_overfit_reject: bool = True,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now_dt = _now_dt(now_utc)
    now_iso = now_dt.isoformat(timespec='seconds')
    status_path = retrain_status_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir)
    prev = _load_json(status_path)

    prev_cooldown_until = None
    try:
        raw_prev_cooldown = prev.get('cooldown_until_utc')
        if raw_prev_cooldown:
            prev_cooldown_until = datetime.fromisoformat(str(raw_prev_cooldown))
            if prev_cooldown_until.tzinfo is None:
                prev_cooldown_until = prev_cooldown_until.replace(tzinfo=UTC)
            prev_cooldown_until = prev_cooldown_until.astimezone(UTC)
    except Exception:
        prev_cooldown_until = None

    reasons: list[str] = []
    priority = 'low'
    queue_recommended = False
    watch_recommended = False

    if isinstance(trigger_payload, dict):
        queue_recommended = True
        priority = _priority_max(priority, str(trigger_payload.get('priority') or 'medium'))
        reasons.append(str(trigger_payload.get('reason') or 'drift_trigger'))

    regime_level = str(((regime or {}).get('level')) or 'ok')
    if regime_level == 'block':
        watch_recommended = True
        reasons.append('regime_block')
        if bool(queue_on_regime_block):
            queue_recommended = True
            priority = _priority_max(priority, 'high')
    elif regime_level == 'warn':
        watch_recommended = True
        reasons.append('regime_warn')
        priority = _priority_max(priority, 'medium')

    anti_accepted = bool((anti_overfit or {}).get('accepted', True))
    anti_available = bool((anti_overfit or {}).get('available', False))
    if anti_available and not anti_accepted:
        watch_recommended = True
        reasons.append('anti_overfit_reject')
        priority = _priority_max(priority, 'high')
        if bool(queue_on_anti_overfit_reject):
            queue_recommended = True

    reliability = learned_reliability
    if reliability is not None and float(reliability) < float(watch_reliability_below):
        watch_recommended = True
        reasons.append('low_reliability')
        priority = _priority_max(priority, 'medium')
        if regime_level == 'block':
            queue_recommended = True
            priority = _priority_max(priority, 'high')

    pressure = str((coverage or {}).get('pressure') or 'balanced')
    if pressure == 'over_target':
        watch_recommended = True
        reasons.append('coverage_over_target')

    desired_state = 'idle'
    if queue_recommended:
        desired_state = 'queued'
    elif watch_recommended:
        desired_state = 'watch'

    cooldown_active = bool(prev_cooldown_until is not None and now_dt < prev_cooldown_until)
    cooldown_until = prev_cooldown_until
    state = desired_state
    if desired_state == 'queued' and cooldown_active:
        state = 'cooldown'
    elif desired_state == 'queued' and int(cooldown_hours) > 0:
        cooldown_until = now_dt + timedelta(hours=int(cooldown_hours))

    payload = {
        'kind': 'retrain_plan',
        'schema_version': 'phase1-retrain-plan-v1',
        'generated_at_utc': now_iso,
        'scope_tag': str(scope_tag),
        'state': state,
        'queue_recommended': bool(queue_recommended),
        'watch_recommended': bool(watch_recommended),
        'priority': priority,
        'reasons': sorted(set([r for r in reasons if r])),
        'regime_level': regime_level,
        'coverage_pressure': pressure,
        'learned_reliability': None if reliability is None else float(reliability),
        'anti_overfit_accepted': bool(anti_accepted),
        'cooldown_active': bool(state == 'cooldown'),
        'cooldown_until_utc': cooldown_until.astimezone(UTC).isoformat(timespec='seconds') if cooldown_until is not None else None,
        'trigger_reason': None if not isinstance(trigger_payload, dict) else trigger_payload.get('reason'),
        'trigger_priority': None if not isinstance(trigger_payload, dict) else trigger_payload.get('priority'),
        'drift_level': None if not isinstance(drift_state, dict) else drift_state.get('level'),
        'drift_retrain_recommended': bool((drift_state or {}).get('retrain_recommended', False)),
        'policy': {
            'portfolio_weight': float(policy.get('portfolio_weight') or 1.0),
            'allocator_block_regime': bool(policy.get('allocator_block_regime', True)),
            'allocator_retrain_penalty': float(policy.get('allocator_retrain_penalty') or 0.0),
        },
        'recommended_action': 'fit_intelligence_pack' if state in {'queued', 'watch'} else None,
    }
    preserve_terminal = _preserve_terminal_status(prev, now_dt=now_dt)
    status_payload = {
        **payload,
        'kind': 'retrain_status',
        'schema_version': 'phase1-retrain-status-v2',
        'updated_at_utc': now_iso,
        'previous_state': prev.get('state'),
        'plan_state': payload.get('state'),
        'plan_priority': payload.get('priority'),
        'plan_reasons': list(payload.get('reasons') or []),
        'operational_state_preserved': bool(preserve_terminal),
    }
    if preserve_terminal:
        status_payload['state'] = str(prev.get('state') or payload.get('state') or 'idle')
        status_payload['priority'] = str(prev.get('priority') or payload.get('priority') or 'low')
        if prev.get('status_reason') is not None:
            status_payload['status_reason'] = prev.get('status_reason')
        if prev.get('review_path') is not None:
            status_payload['review_path'] = prev.get('review_path')
        if prev.get('run_id') is not None:
            status_payload['run_id'] = prev.get('run_id')
        if prev.get('verdict') is not None:
            status_payload['verdict'] = prev.get('verdict')

    _write_json(retrain_plan_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir), payload)
    _write_json(status_path, status_payload)
    return payload
