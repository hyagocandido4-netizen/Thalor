from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Mapping

from .paths import intelligence_ops_state_path

_TERMINAL_REVIEW_VERDICTS = {'promoted', 'rejected', 'cooldown', 'skipped'}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _resolve_now(*, timezone: str | None = None, now_utc: datetime | None = None) -> datetime:
    if now_utc is not None:
        dt = now_utc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    tz_name = str(timezone or 'UTC').strip() or 'UTC'
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = UTC
    return datetime.now(tz=tzinfo).astimezone(UTC)


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


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    txt = str(value).strip().lower()
    if txt in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    if txt in {'0', 'false', 'no', 'n', 'off'}:
        return False
    return bool(default)


def _clone_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _recommended_post_cooldown_state(plan: Mapping[str, Any], status: Mapping[str, Any]) -> str:
    queue_recommended = _safe_bool(_first_nonempty(plan.get('queue_recommended'), status.get('queue_recommended')), default=False)
    watch_recommended = _safe_bool(_first_nonempty(plan.get('watch_recommended'), status.get('watch_recommended')), default=False)
    return 'ready' if (queue_recommended or watch_recommended) else 'idle'


def resolve_anti_overfit_tuning(
    live_payload: Mapping[str, Any] | None,
    review_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    live = _clone_mapping(live_payload)
    review = _clone_mapping(review_payload)
    review_tuning = _clone_mapping(review.get('tuning')) if review else {}

    if live:
        return {
            'payload': live,
            'source': 'live',
            'review_only': False,
            'review_verdict': str(review.get('verdict') or '').strip() or None,
        }

    fallback = review_tuning or review
    if fallback:
        return {
            'payload': dict(fallback),
            'source': 'review',
            'review_only': True,
            'review_verdict': str(review.get('verdict') or '').strip() or None,
        }

    return {
        'payload': {},
        'source': None,
        'review_only': False,
        'review_verdict': None,
    }


def build_intelligence_ops_state(
    *,
    scope_tag: str,
    asset: str,
    interval_sec: int,
    pack_payload: Mapping[str, Any] | None = None,
    eval_payload: Mapping[str, Any] | None = None,
    retrain_plan: Mapping[str, Any] | None = None,
    retrain_status: Mapping[str, Any] | None = None,
    retrain_review: Mapping[str, Any] | None = None,
    anti_overfit_summary: Mapping[str, Any] | None = None,
    anti_overfit_data_summary: Mapping[str, Any] | None = None,
    anti_overfit_tuning: Mapping[str, Any] | None = None,
    anti_overfit_tuning_review: Mapping[str, Any] | None = None,
    candidate_item: Mapping[str, Any] | None = None,
    allocation_item: Mapping[str, Any] | None = None,
    latest_intent: Mapping[str, Any] | None = None,
    timezone: str | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    pack = _clone_mapping(pack_payload)
    latest_eval = _clone_mapping(eval_payload)
    plan = _clone_mapping(retrain_plan)
    status = _clone_mapping(retrain_status)
    review = _clone_mapping(retrain_review)
    anti_summary = _clone_mapping(anti_overfit_summary)
    anti_data_summary = _clone_mapping(anti_overfit_data_summary)
    candidate = _clone_mapping(candidate_item)
    allocation = _clone_mapping(allocation_item)
    intent = _clone_mapping(latest_intent)
    now_dt = _resolve_now(timezone=timezone, now_utc=now_utc)
    eval_retrain = _clone_mapping(latest_eval.get('retrain_orchestration'))

    tuning_info = resolve_anti_overfit_tuning(anti_overfit_tuning, anti_overfit_tuning_review)
    tuning_payload = _clone_mapping(tuning_info.get('payload'))
    tuning_source = str(tuning_info.get('source') or '').strip() or None
    tuning_review_only = bool(tuning_info.get('review_only'))
    tuning_review_verdict = str(tuning_info.get('review_verdict') or '').strip() or None

    plan_state = _first_nonempty(plan.get('state'), status.get('plan_state'), eval_retrain.get('state'))
    plan_priority = _first_nonempty(plan.get('priority'), status.get('plan_priority'), eval_retrain.get('priority'))
    state = _first_nonempty(
        status.get('state'),
        plan_state,
        eval_retrain.get('state'),
        allocation.get('retrain_state'),
        candidate.get('retrain_state'),
        intent.get('retrain_state'),
    )
    priority = _first_nonempty(
        status.get('priority'),
        plan_priority,
        eval_retrain.get('priority'),
        allocation.get('retrain_priority'),
        candidate.get('retrain_priority'),
        intent.get('retrain_priority'),
    )

    review_verdict = _first_nonempty(review.get('verdict'), status.get('verdict'))
    review_reason = _first_nonempty(review.get('reason'), status.get('status_reason'))
    review_at = _first_nonempty(review.get('finished_at_utc'), review.get('generated_at_utc'))

    cooldown_until = _first_nonempty(plan.get('cooldown_until_utc'), status.get('cooldown_until_utc'))
    cooldown_flag = _safe_bool(
        _first_nonempty(plan.get('cooldown_active'), status.get('cooldown_active')),
        default=False,
    )
    cooldown_until_dt = _parse_iso(cooldown_until)
    cooldown_active = bool(cooldown_until_dt is not None and cooldown_until_dt > now_dt)
    cooldown_expired = bool(
        cooldown_until_dt is not None
        and cooldown_until_dt <= now_dt
        and (
            cooldown_flag
            or str(plan_state or '').strip().lower() == 'cooldown'
            or str(state or '').strip().lower() in {'cooldown', 'rejected'}
        )
    )
    if cooldown_expired:
        next_state = _recommended_post_cooldown_state(plan, status)
        if str(plan_state or '').strip().lower() == 'cooldown':
            plan_state = next_state
        if str(state or '').strip().lower() in {'cooldown', 'rejected'}:
            state = next_state

    restored_previous_artifacts = _safe_bool(review.get('restored_previous_artifacts'), default=False)
    review_executed = _safe_bool(review.get('executed'), default=bool(review))

    anti_payload = _clone_mapping(latest_eval.get('anti_overfit')) or _clone_mapping(pack.get('anti_overfit'))
    anti_available = _safe_bool(anti_payload.get('available'), default=False)
    anti_accepted = _safe_bool(anti_payload.get('accepted'), default=True)
    anti_robustness = _safe_float(_first_nonempty(anti_payload.get('robustness_score'), anti_summary.get('robustness_score')))
    anti_penalty = _safe_float(_first_nonempty(anti_payload.get('penalty'), anti_summary.get('penalty')))

    issues: list[str] = []
    verdict_txt = str(review_verdict or '').strip().lower()
    state_txt = str(state or '').strip().lower()
    plan_state_txt = str(plan_state or '').strip().lower()

    if status and plan_state and status.get('plan_state') not in (None, '') and str(status.get('plan_state') or '').strip().lower() != plan_state_txt:
        issues.append('status_plan_state_mismatch')
    if status and plan_priority and status.get('plan_priority') not in (None, '') and str(status.get('plan_priority') or '').strip().lower() != str(plan_priority or '').strip().lower():
        issues.append('status_plan_priority_mismatch')

    if verdict_txt == 'rejected':
        if not cooldown_expired:
            if state_txt != 'rejected':
                issues.append('rejected_review_without_rejected_status')
            if plan_state_txt != 'cooldown':
                issues.append('rejected_review_without_cooldown_plan')
    elif verdict_txt == 'promoted':
        if state_txt != 'promoted':
            issues.append('promoted_review_without_promoted_status')
        if plan_state_txt not in {'idle', 'watch', ''}:
            issues.append('promoted_review_without_idle_or_watch_plan')
    elif verdict_txt == 'cooldown' and plan_state_txt != 'cooldown':
        issues.append('cooldown_review_without_cooldown_plan')

    expected_rejected_cooldown = bool(
        verdict_txt == 'rejected'
        and not cooldown_expired
        and state_txt == 'rejected'
        and plan_state_txt == 'cooldown'
        and (cooldown_active or cooldown_until_dt is not None)
    )
    expected_review_only_tuning = bool(
        tuning_source == 'review'
        and tuning_review_only
        and (restored_previous_artifacts or expected_rejected_cooldown or tuning_review_verdict == 'rejected')
    )

    if tuning_source == 'review' and tuning_review_only and not expected_review_only_tuning and tuning_payload:
        issues.append('review_only_tuning_without_restore_context')

    consistency = {
        'ok': not issues,
        'issues': issues,
        'expected_rejected_cooldown': expected_rejected_cooldown,
        'expected_review_only_tuning': expected_review_only_tuning,
        'cooldown_expired': cooldown_expired,
        'terminal_review_present': verdict_txt in _TERMINAL_REVIEW_VERDICTS,
    }

    return {
        'kind': 'intelligence_ops_state',
        'schema_version': 'phase1-intelligence-ops-state-v1',
        'generated_at_utc': _now_iso(),
        'scope_tag': str(scope_tag),
        'asset': str(asset),
        'interval_sec': int(interval_sec),
        'retrain': {
            'state': state,
            'priority': priority,
            'plan_state': plan_state,
            'plan_priority': plan_priority,
            'plan_reasons': list(plan.get('reasons') or status.get('plan_reasons') or []),
            'review_verdict': review_verdict,
            'review_reason': review_reason,
            'review_at_utc': review_at,
            'review_executed': review_executed,
            'restored_previous_artifacts': restored_previous_artifacts,
            'cooldown_active': cooldown_active,
            'cooldown_until_utc': cooldown_until,
            'cooldown_expired': cooldown_expired,
            'status_reason': status.get('status_reason'),
        },
        'anti_overfit': {
            'available': anti_available,
            'accepted': anti_accepted,
            'robustness_score': anti_robustness,
            'penalty': anti_penalty,
            'summary_source': anti_summary.get('source'),
            'data_summary_source': anti_data_summary.get('source'),
            'summary_present': bool(anti_summary),
            'data_summary_present': bool(anti_data_summary),
            'tuning': {
                'present': bool(tuning_payload),
                'source': tuning_source,
                'review_only': tuning_review_only,
                'selected_variant': tuning_payload.get('selected_variant'),
                'baseline_variant': tuning_payload.get('baseline_variant'),
                'selection_reason': tuning_payload.get('selection_reason'),
                'improved': _safe_bool(tuning_payload.get('improved'), default=False),
                'objective': _safe_float(_clone_mapping(tuning_payload.get('selected')).get('objective')),
                'baseline_objective': _safe_float(_clone_mapping(tuning_payload.get('baseline')).get('objective')),
                'review_verdict': tuning_review_verdict,
            },
        },
        'consistency': consistency,
    }


def write_intelligence_ops_state(
    *,
    repo_root: str | Path,
    scope_tag: str,
    payload: Mapping[str, Any],
    artifact_dir: str | Path = 'runs/intelligence',
) -> Path:
    path = intelligence_ops_state_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False), encoding='utf-8')
    return path


def read_intelligence_ops_state(
    *,
    repo_root: str | Path,
    scope_tag: str,
    artifact_dir: str | Path = 'runs/intelligence',
) -> dict[str, Any] | None:
    path = intelligence_ops_state_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return dict(payload) if isinstance(payload, dict) else None
