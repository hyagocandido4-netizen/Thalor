from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config.loader import load_thalor_config
from ..config.paths import resolve_config_path, resolve_repo_root
from ..intelligence.paths import (
    anti_overfit_data_summary_path,
    anti_overfit_summary_path,
    anti_overfit_tuning_path,
    anti_overfit_tuning_review_path,
    intelligence_ops_state_path,
    latest_eval_path,
    pack_path,
    retrain_plan_path,
    retrain_review_path,
    retrain_status_path,
)
from ..intelligence.ops_state import build_intelligence_ops_state, write_intelligence_ops_state
from ..intelligence.refresh import refresh_config_intelligence
from ..portfolio.latest import (
    portfolio_profile_key,
    scoped_portfolio_allocation_latest_path,
    scoped_portfolio_cycle_latest_path,
    write_portfolio_latest_payload,
)
from ..portfolio.models import PortfolioScope
from ..portfolio.paths import scope_tag as compute_scope_tag
from ..state.control_repo import write_control_artifact

_PRIORITY_ORDER = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
_TRADE_ACTIONS = {'CALL', 'PUT'}


def _now_dt() -> datetime:
    return datetime.now(tz=UTC)


def _now_iso() -> str:
    return _now_dt().isoformat(timespec='seconds')


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return dict(obj) if isinstance(obj, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


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


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    txt = str(value).strip().lower()
    if txt in {'1', 'true', 'yes', 'on'}:
        return True
    if txt in {'0', 'false', 'no', 'off', ''}:
        return False
    return bool(default)




def _unique_strings(values: list[Any] | tuple[Any, ...]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        txt = str(value).strip()
        if not txt or txt in seen:
            continue
        seen.add(txt)
        out.append(txt)
    return out


def _cfg_rejection_backoff_hours(cfg: Any) -> int:
    int_cfg = getattr(cfg, 'intelligence', None)
    raw = getattr(int_cfg, 'retrain_rejection_backoff_hours', None) if int_cfg is not None else None
    try:
        hours = int(raw) if raw is not None else 6
    except Exception:
        hours = 6
    return max(0, hours)


def _patch_retrain_metadata_in_item(
    item: dict[str, Any],
    *,
    scope_tag: str,
    retrain_state: Any,
    retrain_priority: Any,
    plan_state: Any,
    plan_priority: Any,
    review_verdict: Any,
    review_reason: Any,
    review_at_utc: Any,
) -> bool:
    if str(item.get('scope_tag') or '') != str(scope_tag):
        return False
    item['retrain_state'] = retrain_state
    item['retrain_priority'] = retrain_priority
    item['retrain_plan_state'] = plan_state
    item['retrain_plan_priority'] = plan_priority
    item['retrain_review_verdict'] = review_verdict
    item['retrain_review_reason'] = review_reason
    item['retrain_review_at_utc'] = review_at_utc
    raw = item.get('raw')
    if isinstance(raw, dict):
        raw['retrain_state'] = retrain_state
        raw['retrain_priority'] = retrain_priority
        raw['retrain_plan_state'] = plan_state
        raw['retrain_plan_priority'] = plan_priority
        raw['retrain_review_verdict'] = review_verdict
        raw['retrain_review_reason'] = review_reason
        raw['retrain_review_at_utc'] = review_at_utc
    intelligence = item.get('intelligence')
    if isinstance(intelligence, dict):
        intelligence['retrain_state'] = retrain_state
        intelligence['retrain_priority'] = retrain_priority
        intelligence['retrain_review_verdict'] = review_verdict
        intelligence['retrain_review_reason'] = review_reason
        intelligence['retrain_review_at_utc'] = review_at_utc
        orch = dict(intelligence.get('retrain_orchestration') or {})
        orch['state'] = plan_state
        orch['priority'] = plan_priority
        orch['last_review_verdict'] = review_verdict
        orch['last_review_reason'] = review_reason
        orch['last_review_at_utc'] = review_at_utc
        intelligence['retrain_orchestration'] = orch
    feedback = item.get('portfolio_feedback')
    if isinstance(feedback, dict):
        feedback['retrain_state'] = retrain_state
        feedback['retrain_priority'] = retrain_priority
        feedback['retrain_plan_state'] = plan_state
        feedback['retrain_plan_priority'] = plan_priority
        feedback['retrain_review_verdict'] = review_verdict
        feedback['retrain_review_reason'] = review_reason
        feedback['retrain_review_at_utc'] = review_at_utc
    return True


def _resync_portfolio_after_review(
    *,
    repo_root: Path,
    cfg_path: Path,
    runtime_profile: str,
    artifact_dir: str | Path,
    scope: PortfolioScope,
    review_payload: dict[str, Any],
) -> dict[str, Any]:
    refresh_payload: dict[str, Any] | None
    try:
        refresh_payload = refresh_config_intelligence(
            repo_root=repo_root,
            config_path=cfg_path,
            asset=scope.asset,
            interval_sec=int(scope.interval_sec),
            rebuild_pack=False,
            materialize_portfolio=True,
            write_legacy_portfolio=False,
        )
    except Exception as exc:  # pragma: no cover - operational guard
        refresh_payload = {
            'ok': False,
            'message': 'post_review_resync_failed',
            'error': f'{type(exc).__name__}:{exc}',
        }

    plan_payload = _read_json(retrain_plan_path(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)) or {}
    status_payload = _read_json(retrain_status_path(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)) or {}
    retrain_state = _first_nonempty(status_payload.get('state'), plan_payload.get('state'))
    retrain_priority = _first_nonempty(status_payload.get('priority'), plan_payload.get('priority'))
    plan_state = plan_payload.get('state')
    plan_priority = plan_payload.get('priority')
    review_verdict = review_payload.get('verdict')
    review_reason = review_payload.get('reason')
    review_at = _first_nonempty(review_payload.get('finished_at_utc'), review_payload.get('generated_at_utc'), status_payload.get('updated_at_utc'))

    cycle_path = scoped_portfolio_cycle_latest_path(repo_root, config_path=cfg_path, profile=runtime_profile)
    allocation_path = scoped_portfolio_allocation_latest_path(repo_root, config_path=cfg_path, profile=runtime_profile)
    cycle_payload = _read_json(cycle_path) or {}
    allocation_payload = _read_json(allocation_path) or {}

    cycle_changed = False
    for item in list(cycle_payload.get('candidates') or []):
        if isinstance(item, dict):
            cycle_changed = _patch_retrain_metadata_in_item(
                item,
                scope_tag=scope.scope_tag,
                retrain_state=retrain_state,
                retrain_priority=retrain_priority,
                plan_state=plan_state,
                plan_priority=plan_priority,
                review_verdict=review_verdict,
                review_reason=review_reason,
                review_at_utc=review_at,
            ) or cycle_changed
    for item in list(cycle_payload.get('candidate_results') or []):
        if isinstance(item, dict) and str(item.get('scope_tag') or '') == str(scope.scope_tag):
            item['retrain_state'] = retrain_state
            item['retrain_priority'] = retrain_priority
            item['retrain_plan_state'] = plan_state
            item['retrain_plan_priority'] = plan_priority
            item['retrain_review_verdict'] = review_verdict
            item['retrain_review_reason'] = review_reason
            item['retrain_review_at_utc'] = review_at
            cycle_changed = True
    if cycle_payload:
        cycle_payload['retrain_review'] = {
            'scope_tag': scope.scope_tag,
            'verdict': review_verdict,
            'reason': review_reason,
            'at_utc': review_at,
            'retrain_state': retrain_state,
            'retrain_plan_state': plan_state,
            'retrain_plan_priority': plan_priority,
        }
        cycle_changed = True

    alloc_changed = False
    for bucket in ('selected', 'suppressed'):
        for item in list(allocation_payload.get(bucket) or []):
            if isinstance(item, dict):
                alloc_changed = _patch_retrain_metadata_in_item(
                    item,
                    scope_tag=scope.scope_tag,
                    retrain_state=retrain_state,
                    retrain_priority=retrain_priority,
                    plan_state=plan_state,
                    plan_priority=plan_priority,
                    review_verdict=review_verdict,
                    review_reason=review_reason,
                    review_at_utc=review_at,
                ) or alloc_changed
    if allocation_payload:
        allocation_payload['retrain_review'] = {
            'scope_tag': scope.scope_tag,
            'verdict': review_verdict,
            'reason': review_reason,
            'at_utc': review_at,
            'retrain_state': retrain_state,
            'retrain_plan_state': plan_state,
            'retrain_plan_priority': plan_priority,
        }
        alloc_changed = True

    persisted_paths: dict[str, Any] = {}
    if cycle_payload:
        persisted_paths['cycle'] = write_portfolio_latest_payload(
            repo_root,
            name='portfolio_cycle_latest.json',
            payload=cycle_payload,
            config_path=cfg_path,
            profile=runtime_profile,
            write_legacy=False,
        )
    if allocation_payload:
        persisted_paths['allocation'] = write_portfolio_latest_payload(
            repo_root,
            name='portfolio_allocation_latest.json',
            payload=allocation_payload,
            config_path=cfg_path,
            profile=runtime_profile,
            write_legacy=False,
        )

    return {
        'ok': True,
        'message': 'post_review_resync_ok',
        'refresh': refresh_payload,
        'cycle_changed': bool(cycle_changed),
        'allocation_changed': bool(alloc_changed),
        'persisted_paths': persisted_paths,
        'retrain_state': retrain_state,
        'retrain_priority': retrain_priority,
        'retrain_plan_state': plan_state,
        'retrain_plan_priority': plan_priority,
        'review_verdict': review_verdict,
        'review_reason': review_reason,
    }

def _cooldown_state_details(
    plan_payload: dict[str, Any] | None,
    status_payload: dict[str, Any] | None,
    *,
    now_dt: datetime,
) -> dict[str, Any]:
    plan = dict(plan_payload or {})
    status = dict(status_payload or {})
    plan_state = str(plan.get('state') or status.get('plan_state') or '').strip().lower()
    status_state = str(status.get('state') or '').strip().lower()
    cooldown_until = _parse_iso(_first_nonempty(plan.get('cooldown_until_utc'), status.get('cooldown_until_utc')))
    in_cooldown = plan_state == 'cooldown' or status_state == 'cooldown'
    cooldown_active = bool(cooldown_until is not None and now_dt < cooldown_until)
    cooldown_expired = bool(in_cooldown and cooldown_until is not None and not cooldown_active)
    return {
        'plan_state': plan_state or None,
        'status_state': status_state or None,
        'cooldown_until': cooldown_until,
        'cooldown_active': cooldown_active,
        'cooldown_expired': cooldown_expired,
    }


def _recommended_post_cooldown_state(plan_payload: dict[str, Any] | None, status_payload: dict[str, Any] | None) -> str:
    plan = dict(plan_payload or {})
    status = dict(status_payload or {})
    queue_recommended = _safe_bool(_first_nonempty(plan.get('queue_recommended'), status.get('queue_recommended')), False)
    watch_recommended = _safe_bool(_first_nonempty(plan.get('watch_recommended'), status.get('watch_recommended')), False)
    if queue_recommended:
        return 'queued'
    if watch_recommended:
        return 'watch'
    return 'idle'


def _refresh_expired_cooldown_for_scope(
    *,
    repo_root: Path,
    cfg_path: Path,
    runtime_profile: str,
    artifact_dir: str | Path,
    scope: PortfolioScope,
    force: bool = False,
) -> dict[str, Any]:
    paths = _artifact_paths(repo_root=repo_root, cfg_path=cfg_path, runtime_profile=runtime_profile, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)
    now_dt = _now_dt()
    plan_payload = _read_json(paths['retrain_plan']) or {}
    status_payload = _read_json(paths['retrain_status']) or {}
    details = _cooldown_state_details(plan_payload, status_payload, now_dt=now_dt)
    if bool(force) or not bool(details.get('cooldown_expired')):
        return {
            'changed': False,
            'expired': bool(details.get('cooldown_expired')),
            'refreshed': False,
            'normalized': False,
            'refresh_payload': None,
            'plan': plan_payload,
            'status': status_payload,
            'paths': {name: str(path) for name, path in paths.items()},
        }

    refresh_payload: dict[str, Any] | None
    try:
        refresh_payload = refresh_config_intelligence(
            repo_root=repo_root,
            config_path=cfg_path,
            asset=scope.asset,
            interval_sec=int(scope.interval_sec),
            rebuild_pack=False,
            materialize_portfolio=True,
            write_legacy_portfolio=False,
        )
    except Exception as exc:  # pragma: no cover - defensive guard for operational paths
        refresh_payload = {
            'ok': False,
            'message': 'cooldown_refresh_failed',
            'error': f'{type(exc).__name__}:{exc}',
        }

    plan_payload = _read_json(paths['retrain_plan']) or plan_payload
    status_payload = _read_json(paths['retrain_status']) or status_payload
    details = _cooldown_state_details(plan_payload, status_payload, now_dt=now_dt)

    normalized = False
    if bool(details.get('cooldown_expired')):
        normalized = True
        next_state = _recommended_post_cooldown_state(plan_payload, status_payload)
        priority = str(_first_nonempty(plan_payload.get('priority'), status_payload.get('plan_priority'), status_payload.get('priority'), 'low') or 'low')
        reasons = list(plan_payload.get('reasons') or status_payload.get('plan_reasons') or [])
        cooldown_until = details.get('cooldown_until')

        normalized_plan = dict(plan_payload)
        if not normalized_plan:
            normalized_plan = {'kind': 'retrain_plan'}
        normalized_plan.update({
            'kind': 'retrain_plan',
            'schema_version': str(normalized_plan.get('schema_version') or 'phase1-retrain-plan-v1'),
            'generated_at_utc': _now_iso(),
            'scope_tag': str(scope.scope_tag),
            'state': next_state,
            'priority': priority,
            'queue_recommended': _safe_bool(_first_nonempty(plan_payload.get('queue_recommended'), status_payload.get('queue_recommended')), next_state == 'queued'),
            'watch_recommended': _safe_bool(_first_nonempty(plan_payload.get('watch_recommended'), status_payload.get('watch_recommended')), next_state in {'queued', 'watch'}),
            'reasons': reasons,
            'cooldown_active': False,
            'recommended_action': 'fit_intelligence_pack' if next_state in {'queued', 'watch'} else None,
            'cooldown_expired_at_utc': _now_iso(),
        })
        if cooldown_until is not None:
            normalized_plan['cooldown_until_utc'] = cooldown_until.isoformat(timespec='seconds')
        _write_json(paths['retrain_plan'], normalized_plan)

        previous_state = _first_nonempty(status_payload.get('state'), plan_payload.get('state'))
        normalized_status = dict(status_payload)
        if not normalized_status:
            normalized_status = {'kind': 'retrain_status'}
        normalized_status.update({
            'kind': 'retrain_status',
            'schema_version': str(normalized_status.get('schema_version') or 'phase1-retrain-status-v3'),
            'updated_at_utc': _now_iso(),
            'scope_tag': str(scope.scope_tag),
            'asset': str(scope.asset),
            'interval_sec': int(scope.interval_sec),
            'state': next_state,
            'priority': str(_first_nonempty(status_payload.get('priority'), priority, 'low') or 'low'),
            'previous_state': previous_state,
            'plan_state': next_state,
            'plan_priority': priority,
            'plan_reasons': reasons,
            'status_reason': 'cooldown_expired',
            'cooldown_active': False,
            'cooldown_expired_at_utc': _now_iso(),
        })
        if cooldown_until is not None:
            normalized_status['cooldown_until_utc'] = cooldown_until.isoformat(timespec='seconds')
        _write_json(paths['retrain_status'], normalized_status)
        plan_payload = normalized_plan
        status_payload = normalized_status

    return {
        'changed': bool(refresh_payload is not None) or bool(normalized),
        'expired': True,
        'refreshed': True,
        'normalized': bool(normalized),
        'refresh_payload': refresh_payload,
        'plan': plan_payload,
        'status': status_payload,
        'paths': {name: str(path) for name, path in paths.items()},
    }


def _delete_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _select_scopes(cfg: Any, *, asset: str | None, interval_sec: int | None) -> list[Any]:
    out: list[Any] = []
    for item in list(getattr(cfg, 'assets', []) or []):
        if asset is not None and str(item.asset) != str(asset):
            continue
        if interval_sec is not None and int(item.interval_sec) != int(interval_sec):
            continue
        out.append(item)
    return out


def _to_scope(item: Any) -> PortfolioScope:
    return PortfolioScope(
        asset=str(item.asset),
        interval_sec=int(item.interval_sec),
        timezone=str(getattr(item, 'timezone', 'UTC')),
        scope_tag=compute_scope_tag(str(item.asset), int(item.interval_sec)),
        weight=float(getattr(item, 'weight', 1.0) or 1.0),
        cluster_key=str(getattr(item, 'cluster_key', 'default') or 'default'),
        topk_k=int(getattr(item, 'topk_k', 3) or 3),
        hard_max_trades_per_day=getattr(item, 'hard_max_trades_per_day', None),
        max_open_positions=getattr(item, 'max_open_positions', None),
        max_pending_unknown=getattr(item, 'max_pending_unknown', None),
    )


def _priority_rank(level: Any) -> int:
    return int(_PRIORITY_ORDER.get(str(level or 'low').strip().lower(), 0))


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _cycle_candidate_for_scope(payload: dict[str, Any] | None, scope_tag: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for item in list(payload.get('candidates') or []):
        if not isinstance(item, dict):
            continue
        if str(item.get('scope_tag') or '') == str(scope_tag):
            return dict(item)
    return {}


def _allocation_for_scope(payload: dict[str, Any] | None, scope_tag: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for bucket in ('selected', 'suppressed'):
        for item in list(payload.get(bucket) or []):
            if not isinstance(item, dict):
                continue
            if str(item.get('scope_tag') or '') != str(scope_tag):
                continue
            out = dict(item)
            out['_bucket'] = bucket
            out['_selected'] = bucket == 'selected'
            return out
    return {}


def _artifact_paths(*, repo_root: Path, cfg_path: Path, runtime_profile: str, scope_tag: str, artifact_dir: str | Path) -> dict[str, Path]:
    return {
        'pack': pack_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'latest_eval': latest_eval_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'retrain_plan': retrain_plan_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'retrain_status': retrain_status_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'retrain_review': retrain_review_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'ops_state': intelligence_ops_state_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'anti_overfit_summary': anti_overfit_summary_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'anti_overfit_data_summary': anti_overfit_data_summary_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'anti_overfit_tuning': anti_overfit_tuning_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'anti_overfit_tuning_review': anti_overfit_tuning_review_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'portfolio_cycle': scoped_portfolio_cycle_latest_path(repo_root, config_path=cfg_path, profile=runtime_profile),
        'portfolio_allocation': scoped_portfolio_allocation_latest_path(repo_root, config_path=cfg_path, profile=runtime_profile),
    }


def _capture_snapshot(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    snap: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        if name in {'anti_overfit_tuning_review', 'ops_state'}:
            continue
        entry: dict[str, Any] = {'path': str(path), 'exists': bool(path.exists())}
        if path.exists():
            try:
                entry['text'] = path.read_text(encoding='utf-8')
            except Exception:
                entry['text'] = None
        else:
            entry['text'] = None
        snap[name] = entry
    return snap


def _restore_snapshot(snapshot: dict[str, dict[str, Any]], paths: dict[str, Path]) -> None:
    for name, entry in snapshot.items():
        path = paths.get(name)
        if path is None:
            continue
        if bool(entry.get('exists')) and entry.get('text') is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(entry.get('text')), encoding='utf-8')
        else:
            _delete_if_exists(path)


def _materialize_ops_state(*, repo_root: Path, scope: PortfolioScope, paths: dict[str, Path], artifact_dir: str | Path) -> dict[str, Any]:
    pack_payload = _read_json(paths['pack']) or {}
    eval_payload = _read_json(paths['latest_eval']) or {}
    plan_payload = _read_json(paths['retrain_plan']) or {}
    status_payload = _read_json(paths['retrain_status']) or {}
    review_payload = _read_json(paths['retrain_review']) or {}
    anti_summary_payload = _read_json(paths['anti_overfit_summary']) or {}
    anti_data_summary_payload = _read_json(paths['anti_overfit_data_summary']) or {}
    anti_tuning_payload = _read_json(paths['anti_overfit_tuning']) or {}
    anti_tuning_review_payload = _read_json(paths['anti_overfit_tuning_review']) or {}
    cycle_payload = _read_json(paths['portfolio_cycle']) or {}
    alloc_payload = _read_json(paths['portfolio_allocation']) or {}

    payload = build_intelligence_ops_state(
        scope_tag=scope.scope_tag,
        asset=scope.asset,
        interval_sec=int(scope.interval_sec),
        pack_payload=pack_payload,
        eval_payload=eval_payload,
        retrain_plan=plan_payload,
        retrain_status=status_payload,
        retrain_review=review_payload,
        anti_overfit_summary=anti_summary_payload,
        anti_overfit_data_summary=anti_data_summary_payload,
        anti_overfit_tuning=anti_tuning_payload,
        anti_overfit_tuning_review=anti_tuning_review_payload,
        candidate_item=_cycle_candidate_for_scope(cycle_payload, scope.scope_tag),
        allocation_item=_allocation_for_scope(alloc_payload, scope.scope_tag),
    )
    write_intelligence_ops_state(repo_root=repo_root, scope_tag=scope.scope_tag, payload=payload, artifact_dir=artifact_dir)
    return payload


def _metrics_from_paths(paths: dict[str, Path], *, scope_tag: str) -> dict[str, Any]:
    pack_payload = _read_json(paths['pack']) or {}
    eval_payload = _read_json(paths['latest_eval']) or {}
    plan_payload = _read_json(paths['retrain_plan']) or {}
    status_payload = _read_json(paths['retrain_status']) or {}
    review_payload = _read_json(paths['retrain_review']) or {}
    anti_summary_payload = _read_json(paths['anti_overfit_summary']) or {}
    anti_data_summary_payload = _read_json(paths['anti_overfit_data_summary']) or {}
    anti_tuning_payload = _read_json(paths['anti_overfit_tuning']) or {}
    anti_tuning_review_payload = _read_json(paths['anti_overfit_tuning_review']) or {}
    anti_tuning_payload_effective = anti_tuning_payload if isinstance(anti_tuning_payload, dict) and anti_tuning_payload else ((anti_tuning_review_payload.get('tuning') if isinstance(anti_tuning_review_payload, dict) else None) or anti_tuning_review_payload or {})
    cycle_payload = _read_json(paths['portfolio_cycle']) or {}
    alloc_payload = _read_json(paths['portfolio_allocation']) or {}

    meta = dict(pack_payload.get('metadata') or {}) if isinstance(pack_payload, dict) else {}
    learned_gate = dict(pack_payload.get('learned_gate') or {}) if isinstance(pack_payload, dict) else {}
    anti = dict(eval_payload.get('anti_overfit') or pack_payload.get('anti_overfit') or {}) if isinstance(eval_payload, dict) or isinstance(pack_payload, dict) else {}
    stack = dict(eval_payload.get('stack') or {}) if isinstance(eval_payload, dict) else {}
    drift = dict(eval_payload.get('drift') or {}) if isinstance(eval_payload, dict) else {}
    regime = dict(eval_payload.get('regime') or drift.get('regime') or {}) if isinstance(eval_payload, dict) else {}
    candidate = _cycle_candidate_for_scope(cycle_payload, scope_tag)
    allocation = _allocation_for_scope(alloc_payload, scope_tag)

    return {
        'pack_present': bool(paths['pack'].exists()),
        'latest_eval_present': bool(paths['latest_eval'].exists()),
        'portfolio_cycle_present': bool(paths['portfolio_cycle'].exists()),
        'portfolio_allocation_present': bool(paths['portfolio_allocation'].exists()),
        'pack_training_rows': int(meta.get('training_rows') or 0),
        'pack_training_strategy': meta.get('training_strategy'),
        'learned_gate_available': bool(pack_payload.get('learned_gate')) if isinstance(pack_payload, dict) else False,
        'learned_reliability': _safe_float(_first_nonempty(eval_payload.get('learned_reliability'), learned_gate.get('reliability_score'))),
        'intelligence_score': _safe_float(eval_payload.get('intelligence_score')),
        'portfolio_score': _safe_float(eval_payload.get('portfolio_score')),
        'allow_trade': eval_payload.get('allow_trade') if isinstance(eval_payload, dict) else None,
        'stack_decision': stack.get('decision'),
        'stack_available': bool(stack.get('available')),
        'drift_level': drift.get('level'),
        'regime_level': regime.get('level'),
        'anti_overfit_available': bool(anti.get('available')),
        'anti_overfit_accepted': bool(anti.get('accepted', True)),
        'anti_overfit_robustness_score': _safe_float(anti.get('robustness_score')),
        'anti_overfit_penalty': _safe_float(anti.get('penalty')),
        'anti_overfit_summary_present': bool(paths['anti_overfit_summary'].exists()),
        'anti_overfit_summary_source': anti_summary_payload.get('source') if isinstance(anti_summary_payload, dict) else None,
        'anti_overfit_data_summary_present': bool(paths['anti_overfit_data_summary'].exists()),
        'anti_overfit_data_summary_source': anti_data_summary_payload.get('source') if isinstance(anti_data_summary_payload, dict) else None,
        'anti_overfit_tuning_present': bool(paths['anti_overfit_tuning'].exists()) or bool(paths['anti_overfit_tuning_review'].exists()),
        'anti_overfit_tuning_source': 'review' if bool(paths['anti_overfit_tuning_review'].exists()) and not bool(paths['anti_overfit_tuning'].exists()) else ('live' if bool(paths['anti_overfit_tuning'].exists()) else None),
        'anti_overfit_tuning_selected_variant': anti_tuning_payload_effective.get('selected_variant') if isinstance(anti_tuning_payload_effective, dict) else None,
        'anti_overfit_tuning_baseline_variant': anti_tuning_payload_effective.get('baseline_variant') if isinstance(anti_tuning_payload_effective, dict) else None,
        'anti_overfit_tuning_improved': bool(anti_tuning_payload_effective.get('improved', False)) if isinstance(anti_tuning_payload_effective, dict) else False,
        'anti_overfit_tuning_objective': _safe_float(((anti_tuning_payload_effective.get('selected') or {}).get('objective')) if isinstance(anti_tuning_payload_effective, dict) else None),
        'anti_overfit_tuning_baseline_objective': _safe_float(((anti_tuning_payload_effective.get('baseline') or {}).get('objective')) if isinstance(anti_tuning_payload_effective, dict) else None),
        'anti_overfit_tuning_selection_reason': anti_tuning_payload_effective.get('selection_reason') if isinstance(anti_tuning_payload_effective, dict) else None,
        'anti_overfit_tuning_review_verdict': anti_tuning_review_payload.get('verdict') if isinstance(anti_tuning_review_payload, dict) else None,
        'retrain_state': _first_nonempty(status_payload.get('state'), plan_payload.get('state'), (eval_payload.get('retrain_orchestration') or {}).get('state') if isinstance(eval_payload.get('retrain_orchestration'), dict) else None),
        'retrain_priority': _first_nonempty(status_payload.get('priority'), plan_payload.get('priority'), (eval_payload.get('retrain_orchestration') or {}).get('priority') if isinstance(eval_payload.get('retrain_orchestration'), dict) else None),
        'retrain_plan_state': plan_payload.get('state'),
        'retrain_plan_priority': plan_payload.get('priority'),
        'retrain_review_verdict': review_payload.get('verdict') if isinstance(review_payload, dict) else None,
        'candidate_action': candidate.get('action'),
        'candidate_reason': candidate.get('reason'),
        'allocation_bucket': allocation.get('_bucket'),
        'allocation_selected': bool(allocation.get('_selected')),
        'allocation_reason': allocation.get('reason'),
    }


def _comparison(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    improvements: list[str] = []
    regressions: list[str] = []
    hard_regressions: list[str] = []
    score = 0.0

    def _delta(name: str) -> float | None:
        b = _safe_float(before.get(name))
        a = _safe_float(after.get(name))
        if a is None or b is None:
            return None
        return float(a - b)

    robust_delta = _delta('anti_overfit_robustness_score')
    portfolio_delta = _delta('portfolio_score')
    intelligence_delta = _delta('intelligence_score')

    if not bool(before.get('anti_overfit_accepted', True)) and bool(after.get('anti_overfit_accepted', False)):
        improvements.append('anti_overfit_accepted')
        score += 2.0
    elif bool(before.get('anti_overfit_accepted', True)) and not bool(after.get('anti_overfit_accepted', True)):
        regressions.append('anti_overfit_rejected')
        hard_regressions.append('anti_overfit_rejected')
        score -= 2.0

    if robust_delta is not None:
        if robust_delta >= 0.03:
            improvements.append('anti_overfit_robustness_up')
            score += 1.0
        elif robust_delta <= -0.03:
            regressions.append('anti_overfit_robustness_down')
            score -= 1.0

    if portfolio_delta is not None:
        if portfolio_delta >= 0.02:
            improvements.append('portfolio_score_up')
            score += 1.0
        elif portfolio_delta <= -0.02:
            regressions.append('portfolio_score_down')
            score -= 1.0

    if intelligence_delta is not None:
        if intelligence_delta >= 0.02:
            improvements.append('intelligence_score_up')
            score += 0.5
        elif intelligence_delta <= -0.02:
            regressions.append('intelligence_score_down')
            score -= 0.5

    if bool(after.get('allow_trade')) and not bool(before.get('allow_trade')):
        improvements.append('allow_trade_enabled')
        score += 0.5
    elif bool(before.get('allow_trade')) and not bool(after.get('allow_trade')):
        regressions.append('allow_trade_disabled')
        score -= 0.5

    if bool(after.get('allocation_selected')) and not bool(before.get('allocation_selected')):
        improvements.append('allocation_selected')
        score += 0.5
    elif bool(before.get('allocation_selected')) and not bool(after.get('allocation_selected')):
        regressions.append('allocation_suppressed')
        score -= 0.5

    before_pr = _priority_rank(before.get('retrain_priority'))
    after_pr = _priority_rank(after.get('retrain_priority'))
    if after_pr and before_pr:
        if after_pr < before_pr:
            improvements.append('retrain_priority_reduced')
            score += 0.5
        elif after_pr > before_pr:
            regressions.append('retrain_priority_increased')
            score -= 0.5

    if bool(after.get('learned_gate_available')) and not bool(before.get('learned_gate_available')):
        improvements.append('learned_gate_available')
        score += 0.25

    if str(before.get('stack_decision') or '') != 'promote' and str(after.get('stack_decision') or '') == 'promote':
        improvements.append('stack_promote')
        score += 0.25
    elif str(before.get('stack_decision') or '') == 'promote' and str(after.get('stack_decision') or '') != 'promote':
        regressions.append('stack_not_promote')
        score -= 0.25

    before_tuning_obj = _safe_float(before.get('anti_overfit_tuning_objective'))
    after_tuning_obj = _safe_float(after.get('anti_overfit_tuning_objective'))
    if before_tuning_obj is not None and after_tuning_obj is not None:
        tuning_delta = float(after_tuning_obj - before_tuning_obj)
        if tuning_delta >= 0.03 or (bool(after.get('anti_overfit_tuning_improved')) and tuning_delta >= 0.01):
            improvements.append('anti_overfit_tuning_objective_up')
            score += 0.25
        elif tuning_delta <= -0.03:
            regressions.append('anti_overfit_tuning_objective_down')
            score -= 0.25

    return {
        'score': float(score),
        'improvements': improvements,
        'regressions': regressions,
        'hard_regressions': hard_regressions,
        'deltas': {
            'anti_overfit_robustness_score': robust_delta,
            'portfolio_score': portfolio_delta,
            'intelligence_score': intelligence_delta,
        },
    }


def _status_payload(
    *,
    scope: PortfolioScope,
    state: str,
    priority: str | None,
    previous_state: Any,
    run_id: str,
    reason: str,
    review_path: Path | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        'kind': 'retrain_status',
        'schema_version': 'phase1-retrain-status-v3',
        'updated_at_utc': _now_iso(),
        'scope_tag': str(scope.scope_tag),
        'asset': str(scope.asset),
        'interval_sec': int(scope.interval_sec),
        'state': str(state),
        'priority': str(priority or 'low'),
        'previous_state': previous_state,
        'run_id': str(run_id),
        'status_reason': str(reason),
        'review_path': str(review_path) if review_path is not None else None,
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _write_round_report_files(*, repo_root: Path, scope_tag: str, payload: dict[str, Any]) -> dict[str, str]:
    at_utc = str(payload.get('finished_at_utc') or payload.get('at_utc') or _now_iso())
    safe_stamp = at_utc.replace(':', '').replace('-', '').replace('T', 'T').replace('+', '+').replace('Z', '+0000')
    safe_stamp = safe_stamp.replace('.', '')
    report_dir = repo_root / 'runs' / 'tests' / 'retrain_runs'
    report_dir.mkdir(parents=True, exist_ok=True)
    latest = report_dir / f'retrain_run_latest_{scope_tag}.json'
    stamped = report_dir / f'retrain_run_{safe_stamp}_{scope_tag}.json'
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    latest.write_text(body, encoding='utf-8')
    stamped.write_text(body, encoding='utf-8')
    return {'latest_report_path': str(latest), 'report_path': str(stamped)}


def _run_retrain_for_scope(
    *,
    repo_root: Path,
    cfg_path: Path,
    runtime_profile: str,
    artifact_dir: str | Path,
    scope: PortfolioScope,
    force: bool = False,
    promote_threshold: float = 0.5,
    rejection_backoff_hours: int = 6,
) -> dict[str, Any]:
    cooldown_refresh = _refresh_expired_cooldown_for_scope(
        repo_root=repo_root,
        cfg_path=cfg_path,
        runtime_profile=runtime_profile,
        artifact_dir=artifact_dir,
        scope=scope,
        force=force,
    )
    paths = _artifact_paths(repo_root=repo_root, cfg_path=cfg_path, runtime_profile=runtime_profile, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)
    before = _metrics_from_paths(paths, scope_tag=scope.scope_tag)
    before_plan = _read_json(paths['retrain_plan']) or dict(cooldown_refresh.get('plan') or {})
    before_status = _read_json(paths['retrain_status']) or dict(cooldown_refresh.get('status') or {})
    plan_state = str(before_plan.get('state') or before_status.get('plan_state') or before_status.get('state') or 'idle')
    plan_priority = str(before_plan.get('priority') or before_status.get('plan_priority') or before_status.get('priority') or 'low')

    run_id = datetime.now(tz=UTC).strftime('retrain_%Y%m%dT%H%M%SZ')
    started_at = _now_iso()
    snapshot = _capture_snapshot(paths)

    if not force and plan_state == 'cooldown':
        review_payload = {
            'kind': 'retrain_review',
            'schema_version': 'phase1-retrain-review-v1',
            'generated_at_utc': started_at,
            'scope_tag': scope.scope_tag,
            'asset': scope.asset,
            'interval_sec': int(scope.interval_sec),
            'runtime_profile': runtime_profile,
            'config_path': str(cfg_path),
            'profile_key': portfolio_profile_key(repo_root, config_path=cfg_path, profile=runtime_profile),
            'run_id': run_id,
            'executed': False,
            'verdict': 'cooldown',
            'reason': 'cooldown_active',
            'plan_state_before': plan_state,
            'plan_priority_before': plan_priority,
            'before': before,
            'after': None,
            'final': before,
            'comparison': None,
            'cooldown_refresh': cooldown_refresh,
        }
        _write_json(paths['retrain_review'], review_payload)
        status_payload = _status_payload(
            scope=scope,
            state='cooldown',
            priority=plan_priority,
            previous_state=before_status.get('state'),
            run_id=run_id,
            reason='cooldown_active',
            review_path=paths['retrain_review'],
            extra={'plan_state': plan_state, 'plan_priority': plan_priority, 'executed': False},
        )
        _write_json(paths['retrain_status'], status_payload)
        ops_state_payload = _materialize_ops_state(repo_root=repo_root, scope=scope, paths=paths, artifact_dir=artifact_dir)
        return {
            'ok': True,
            'scope_tag': scope.scope_tag,
            'asset': scope.asset,
            'interval_sec': int(scope.interval_sec),
            'executed': False,
            'state': 'cooldown',
            'reason': 'cooldown_active',
            'review_path': str(paths['retrain_review']),
            'before': before,
            'after': None,
            'final': before,
            'ops_state': ops_state_payload,
            'cooldown_refresh': cooldown_refresh,
        }

    if not force and plan_state not in {'queued', 'watch', 'cooldown'} and _priority_rank(plan_priority) < _PRIORITY_ORDER['medium']:
        review_payload = {
            'kind': 'retrain_review',
            'schema_version': 'phase1-retrain-review-v1',
            'generated_at_utc': started_at,
            'scope_tag': scope.scope_tag,
            'asset': scope.asset,
            'interval_sec': int(scope.interval_sec),
            'runtime_profile': runtime_profile,
            'config_path': str(cfg_path),
            'profile_key': portfolio_profile_key(repo_root, config_path=cfg_path, profile=runtime_profile),
            'run_id': run_id,
            'executed': False,
            'verdict': 'skipped',
            'reason': 'no_retrain_recommended',
            'plan_state_before': plan_state,
            'plan_priority_before': plan_priority,
            'before': before,
            'after': None,
            'final': before,
            'comparison': None,
            'cooldown_refresh': cooldown_refresh,
        }
        _write_json(paths['retrain_review'], review_payload)
        status_payload = _status_payload(
            scope=scope,
            state='idle',
            priority=plan_priority,
            previous_state=before_status.get('state'),
            run_id=run_id,
            reason='no_retrain_recommended',
            review_path=paths['retrain_review'],
            extra={'plan_state': plan_state, 'plan_priority': plan_priority, 'executed': False},
        )
        _write_json(paths['retrain_status'], status_payload)
        ops_state_payload = _materialize_ops_state(repo_root=repo_root, scope=scope, paths=paths, artifact_dir=artifact_dir)
        return {
            'ok': True,
            'scope_tag': scope.scope_tag,
            'asset': scope.asset,
            'interval_sec': int(scope.interval_sec),
            'executed': False,
            'state': 'idle',
            'reason': 'no_retrain_recommended',
            'review_path': str(paths['retrain_review']),
            'before': before,
            'after': None,
            'final': before,
            'ops_state': ops_state_payload,
            'cooldown_refresh': cooldown_refresh,
        }

    fitting_status = _status_payload(
        scope=scope,
        state='fitting',
        priority=plan_priority,
        previous_state=before_status.get('state'),
        run_id=run_id,
        reason='retrain_fitting_started',
        review_path=paths['retrain_review'],
        extra={'plan_state': plan_state, 'plan_priority': plan_priority, 'executed': True, 'started_at_utc': started_at},
    )
    _write_json(paths['retrain_status'], fitting_status)

    refresh_payload = refresh_config_intelligence(
        repo_root=repo_root,
        config_path=cfg_path,
        asset=scope.asset,
        interval_sec=int(scope.interval_sec),
        rebuild_pack=True,
        materialize_portfolio=True,
        write_legacy_portfolio=False,
    )

    evaluated_status = _status_payload(
        scope=scope,
        state='evaluated',
        priority=plan_priority,
        previous_state='fitting',
        run_id=run_id,
        reason='retrain_evaluated',
        review_path=paths['retrain_review'],
        extra={'plan_state': plan_state, 'plan_priority': plan_priority, 'executed': True, 'started_at_utc': started_at},
    )
    _write_json(paths['retrain_status'], evaluated_status)

    after = _metrics_from_paths(paths, scope_tag=scope.scope_tag)
    comparison = _comparison(before, after)
    anti_tuning_attempt = _read_json(paths['anti_overfit_tuning']) or {}
    hard_regressions = list(comparison.get('hard_regressions') or [])
    verdict = 'promoted'
    reason = 'improved_or_stable'
    if not bool(refresh_payload.get('ok', False)):
        verdict = 'rejected'
        reason = 'refresh_failed'
    elif hard_regressions or float(comparison.get('score') or 0.0) < float(promote_threshold):
        verdict = 'rejected'
        reason = 'hard_regression' if hard_regressions else 'no_material_improvement'

    restored_previous_artifacts = False
    current_plan_payload = _read_json(paths['retrain_plan']) or before_plan
    current_status_payload = _read_json(paths['retrain_status']) or before_status
    rejection_backoff = None

    if verdict == 'rejected':
        _restore_snapshot(snapshot, paths)
        restored_previous_artifacts = True
        current_plan_payload = _read_json(paths['retrain_plan']) or before_plan
        current_status_payload = _read_json(paths['retrain_status']) or before_status
        backoff_until = None
        if int(rejection_backoff_hours) > 0:
            backoff_until = _now_dt() + timedelta(hours=int(rejection_backoff_hours))
        rejection_backoff = {
            'kind': 'retrain_rejection_backoff',
            'active': bool(backoff_until is not None),
            'hours': int(rejection_backoff_hours),
            'until_utc': backoff_until.isoformat(timespec='seconds') if backoff_until is not None else None,
            'reason': reason,
            'run_id': run_id,
        }
        rejected_plan = dict(current_plan_payload or {})
        rejected_plan.update({
            'kind': 'retrain_plan',
            'schema_version': str(rejected_plan.get('schema_version') or 'phase1-retrain-plan-v1'),
            'generated_at_utc': _now_iso(),
            'scope_tag': str(scope.scope_tag),
            'state': 'cooldown' if backoff_until is not None else 'watch',
            'priority': str(_first_nonempty(current_plan_payload.get('priority'), plan_priority, 'high') or 'high'),
            'queue_recommended': bool(_safe_bool(_first_nonempty(current_plan_payload.get('queue_recommended'), True), True)),
            'watch_recommended': True,
            'reasons': _unique_strings(list(current_plan_payload.get('reasons') or []) + ['retrain_rejected', reason]),
            'cooldown_active': bool(backoff_until is not None),
            'cooldown_until_utc': backoff_until.isoformat(timespec='seconds') if backoff_until is not None else None,
            'recommended_action': 'wait_for_rejection_backoff' if backoff_until is not None else 'review_retrain_rejection',
            'backoff_kind': 'rejection',
            'backoff_reason': reason,
            'backoff_hours': int(rejection_backoff_hours),
            'last_review_verdict': 'rejected',
            'last_review_reason': reason,
            'last_review_run_id': run_id,
        })
        _write_json(paths['retrain_plan'], rejected_plan)
        current_plan_payload = rejected_plan
    elif verdict == 'promoted':
        promoted_plan = dict(current_plan_payload or {})
        promoted_plan.update({
            'kind': 'retrain_plan',
            'schema_version': str(promoted_plan.get('schema_version') or 'phase1-retrain-plan-v1'),
            'generated_at_utc': _now_iso(),
            'scope_tag': str(scope.scope_tag),
            'state': 'idle',
            'priority': 'low',
            'queue_recommended': False,
            'watch_recommended': True,
            'reasons': _unique_strings(list(promoted_plan.get('reasons') or []) + ['retrain_promoted']),
            'cooldown_active': False,
            'cooldown_until_utc': None,
            'recommended_action': 'monitor_promoted_pack',
            'last_review_verdict': 'promoted',
            'last_review_reason': reason,
            'last_review_run_id': run_id,
        })
        _write_json(paths['retrain_plan'], promoted_plan)
        current_plan_payload = promoted_plan

    finished_at = _now_iso()
    tuning_review_payload = {
        'kind': 'anti_overfit_tuning_review',
        'schema_version': 'phase1-anti-overfit-tuning-review-v1',
        'generated_at_utc': finished_at,
        'scope_tag': scope.scope_tag,
        'asset': scope.asset,
        'interval_sec': int(scope.interval_sec),
        'runtime_profile': runtime_profile,
        'config_path': str(cfg_path),
        'run_id': run_id,
        'verdict': verdict,
        'reason': reason,
        'restored_previous_artifacts': bool(restored_previous_artifacts),
        'source_path': str(paths['anti_overfit_tuning']),
        'tuning': anti_tuning_attempt if isinstance(anti_tuning_attempt, dict) else {},
        'selected_variant': (anti_tuning_attempt or {}).get('selected_variant') if isinstance(anti_tuning_attempt, dict) else None,
        'baseline_variant': (anti_tuning_attempt or {}).get('baseline_variant') if isinstance(anti_tuning_attempt, dict) else None,
        'selection_reason': (anti_tuning_attempt or {}).get('selection_reason') if isinstance(anti_tuning_attempt, dict) else None,
        'improved': bool((anti_tuning_attempt or {}).get('improved', False)) if isinstance(anti_tuning_attempt, dict) else False,
        'selected_objective': _safe_float((((anti_tuning_attempt or {}).get('selected') or {}).get('objective')) if isinstance(anti_tuning_attempt, dict) else None),
        'baseline_objective': _safe_float((((anti_tuning_attempt or {}).get('baseline') or {}).get('objective')) if isinstance(anti_tuning_attempt, dict) else None),
    }
    _write_json(paths['anti_overfit_tuning_review'], tuning_review_payload)
    preliminary_review = {
        'kind': 'retrain_review',
        'schema_version': 'phase1-retrain-review-v1',
        'generated_at_utc': finished_at,
        'started_at_utc': started_at,
        'finished_at_utc': finished_at,
        'scope_tag': scope.scope_tag,
        'asset': scope.asset,
        'interval_sec': int(scope.interval_sec),
        'runtime_profile': runtime_profile,
        'config_path': str(cfg_path),
        'profile_key': portfolio_profile_key(repo_root, config_path=cfg_path, profile=runtime_profile),
        'run_id': run_id,
        'executed': True,
        'verdict': verdict,
        'reason': reason,
        'restored_previous_artifacts': bool(restored_previous_artifacts),
        'plan_state_before': plan_state,
        'plan_priority_before': plan_priority,
        'before': before,
        'after': after,
        'final': None,
        'comparison': comparison,
        'refresh': refresh_payload,
        'cooldown_refresh': cooldown_refresh,
        'rejection_backoff': rejection_backoff,
        'anti_overfit_tuning_review': tuning_review_payload,
        'artifact_paths': {name: str(path) for name, path in paths.items()},
    }
    _write_json(paths['retrain_review'], preliminary_review)

    provisional_status = _status_payload(
        scope=scope,
        state=verdict,
        priority=str(_first_nonempty(after.get('retrain_priority'), current_plan_payload.get('priority'), plan_priority, 'low') or 'low'),
        previous_state='evaluated',
        run_id=run_id,
        reason=reason,
        review_path=paths['retrain_review'],
        extra={
            'plan_state': current_plan_payload.get('state'),
            'plan_priority': current_plan_payload.get('priority'),
            'executed': True,
            'started_at_utc': started_at,
            'finished_at_utc': finished_at,
            'verdict': verdict,
            'refresh_ok': bool(refresh_payload.get('ok', False)),
            'restored_previous_artifacts': bool(restored_previous_artifacts),
            'cooldown_active': current_plan_payload.get('cooldown_active'),
            'cooldown_until_utc': current_plan_payload.get('cooldown_until_utc'),
            'rejection_backoff': rejection_backoff,
            'before_metrics': before,
            'after_metrics': after,
        },
    )
    _write_json(paths['retrain_status'], provisional_status)

    post_review_resync = _resync_portfolio_after_review(
        repo_root=repo_root,
        cfg_path=cfg_path,
        runtime_profile=runtime_profile,
        artifact_dir=artifact_dir,
        scope=scope,
        review_payload=preliminary_review,
    )

    final_plan_payload = _read_json(paths['retrain_plan']) or current_plan_payload
    final = _metrics_from_paths(paths, scope_tag=scope.scope_tag)
    final_status = _status_payload(
        scope=scope,
        state=verdict,
        priority=str(_first_nonempty(final.get('retrain_priority'), provisional_status.get('priority'), final_plan_payload.get('priority'), plan_priority, 'low') or 'low'),
        previous_state='evaluated',
        run_id=run_id,
        reason=reason,
        review_path=paths['retrain_review'],
        extra={
            'plan_state': final_plan_payload.get('state'),
            'plan_priority': final_plan_payload.get('priority'),
            'executed': True,
            'started_at_utc': started_at,
            'finished_at_utc': finished_at,
            'verdict': verdict,
            'refresh_ok': bool(refresh_payload.get('ok', False)),
            'restored_previous_artifacts': bool(restored_previous_artifacts),
            'cooldown_active': final_plan_payload.get('cooldown_active'),
            'cooldown_until_utc': final_plan_payload.get('cooldown_until_utc'),
            'rejection_backoff': rejection_backoff,
            'post_review_resync': post_review_resync,
            'before_metrics': before,
            'after_metrics': after,
            'final_metrics': final,
        },
    )
    _write_json(paths['retrain_status'], final_status)

    review_payload = dict(preliminary_review)
    review_payload.update({
        'final': final,
        'final_plan': final_plan_payload,
        'final_status': final_status,
        'post_review_resync': post_review_resync,
    })
    _write_json(paths['retrain_review'], review_payload)
    ops_state_payload = _materialize_ops_state(repo_root=repo_root, scope=scope, paths=paths, artifact_dir=artifact_dir)

    payload = {
        'kind': 'retrain_run',
        'schema_version': 'phase1-retrain-run-v1',
        'ok': True,
        'scope_tag': scope.scope_tag,
        'asset': scope.asset,
        'interval_sec': int(scope.interval_sec),
        'runtime_profile': runtime_profile,
        'config_path': str(cfg_path),
        'run_id': run_id,
        'started_at_utc': started_at,
        'finished_at_utc': finished_at,
        'executed': True,
        'verdict': verdict,
        'reason': reason,
        'restored_previous_artifacts': bool(restored_previous_artifacts),
        'before': before,
        'after': after,
        'final': final,
        'comparison': comparison,
        'refresh': refresh_payload,
        'ops_state': ops_state_payload,
        'cooldown_refresh': cooldown_refresh,
        'rejection_backoff': rejection_backoff,
        'post_review_resync': post_review_resync,
        'artifacts': {
            'retrain_review_path': str(paths['retrain_review']),
            'retrain_status_path': str(paths['retrain_status']),
            'ops_state_path': str(paths['ops_state']),
            'pack_path': str(paths['pack']),
            'latest_eval_path': str(paths['latest_eval']),
            'anti_overfit_tuning_review_path': str(paths['anti_overfit_tuning_review']),
            'portfolio_cycle_path': str(paths['portfolio_cycle']),
            'portfolio_allocation_path': str(paths['portfolio_allocation']),
        },
    }
    payload['artifacts'].update(_write_round_report_files(repo_root=repo_root, scope_tag=scope.scope_tag, payload=payload))
    write_control_artifact(repo_root=repo_root, asset=scope.asset, interval_sec=int(scope.interval_sec), name='retrain', payload=payload)
    return payload


def build_retrain_status_payload(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    asset: str | None = None,
    interval_sec: int | None = None,
) -> dict[str, Any]:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    cfg = load_thalor_config(config_path=cfg_path, repo_root=root)
    runtime_profile = str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default')
    int_cfg = getattr(cfg, 'intelligence', None)
    artifact_dir = getattr(int_cfg, 'artifact_dir', 'runs/intelligence') if int_cfg is not None else 'runs/intelligence'
    rejection_backoff_hours = _cfg_rejection_backoff_hours(cfg)

    chosen = _select_scopes(cfg, asset=asset, interval_sec=interval_sec)
    items: list[dict[str, Any]] = []
    for item in chosen:
        scope = _to_scope(item)
        cooldown_refresh = _refresh_expired_cooldown_for_scope(
            repo_root=root,
            cfg_path=cfg_path,
            runtime_profile=runtime_profile,
            artifact_dir=artifact_dir,
            scope=scope,
            force=False,
        )
        paths = _artifact_paths(repo_root=root, cfg_path=cfg_path, runtime_profile=runtime_profile, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)
        plan_payload = _read_json(paths['retrain_plan']) or dict(cooldown_refresh.get('plan') or {})
        status_payload = _read_json(paths['retrain_status']) or dict(cooldown_refresh.get('status') or {})
        review_payload = _read_json(paths['retrain_review']) or {}
        items.append({
            'scope': asdict(scope),
            'plan': plan_payload,
            'status': status_payload,
            'review': review_payload,
            'ops_state': _materialize_ops_state(repo_root=root, scope=scope, paths=paths, artifact_dir=artifact_dir),
            'metrics': _metrics_from_paths(paths, scope_tag=scope.scope_tag),
            'paths': {name: str(path) for name, path in paths.items()},
            'cooldown_refresh': cooldown_refresh,
        })

    payload = {
        'kind': 'retrain_status_snapshot',
        'schema_version': 'phase1-retrain-status-snapshot-v1',
        'at_utc': _now_iso(),
        'ok': True,
        'repo_root': str(root),
        'config_path': str(cfg_path),
        'runtime_profile': runtime_profile,
        'profile_key': portfolio_profile_key(root, config_path=cfg_path, profile=runtime_profile),
        'items': items,
    }
    if len(chosen) == 1:
        scope = _to_scope(chosen[0])
        write_control_artifact(repo_root=root, asset=scope.asset, interval_sec=int(scope.interval_sec), name='retrain', payload=payload)
    return payload


def build_retrain_run_payload(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    asset: str | None = None,
    interval_sec: int | None = None,
    force: bool = False,
    promote_threshold: float = 0.5,
) -> dict[str, Any]:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    cfg = load_thalor_config(config_path=cfg_path, repo_root=root)
    runtime_profile = str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default')
    int_cfg = getattr(cfg, 'intelligence', None)
    artifact_dir = getattr(int_cfg, 'artifact_dir', 'runs/intelligence') if int_cfg is not None else 'runs/intelligence'
    rejection_backoff_hours = _cfg_rejection_backoff_hours(cfg)

    chosen = _select_scopes(cfg, asset=asset, interval_sec=interval_sec)
    if not chosen:
        return {
            'kind': 'retrain_run_batch',
            'schema_version': 'phase1-retrain-run-batch-v1',
            'at_utc': _now_iso(),
            'ok': False,
            'message': 'scope_not_found',
            'repo_root': str(root),
            'config_path': str(cfg_path),
            'runtime_profile': runtime_profile,
            'items': [],
        }

    items: list[dict[str, Any]] = []
    ok = True
    for item in chosen:
        scope = _to_scope(item)
        try:
            result = _run_retrain_for_scope(
                repo_root=root,
                cfg_path=cfg_path,
                runtime_profile=runtime_profile,
                artifact_dir=artifact_dir,
                scope=scope,
                force=bool(force),
                promote_threshold=float(promote_threshold),
                rejection_backoff_hours=int(rejection_backoff_hours),
            )
        except Exception as exc:
            ok = False
            result = {
                'ok': False,
                'scope_tag': scope.scope_tag,
                'asset': scope.asset,
                'interval_sec': int(scope.interval_sec),
                'executed': False,
                'state': 'error',
                'reason': f'{type(exc).__name__}:{exc}',
            }
            write_control_artifact(repo_root=root, asset=scope.asset, interval_sec=int(scope.interval_sec), name='retrain', payload=result)
        items.append(result)
        ok = ok and bool(result.get('ok', False))

    promoted = int(sum(1 for item in items if str(item.get('verdict') or '') == 'promoted'))
    rejected = int(sum(1 for item in items if str(item.get('verdict') or '') == 'rejected'))
    cooldown = int(sum(1 for item in items if str(item.get('state') or '') == 'cooldown'))
    skipped = int(sum(1 for item in items if str(item.get('reason') or '') == 'no_retrain_recommended'))
    payload = {
        'kind': 'retrain_run_batch',
        'schema_version': 'phase1-retrain-run-batch-v1',
        'at_utc': _now_iso(),
        'ok': bool(ok),
        'repo_root': str(root),
        'config_path': str(cfg_path),
        'runtime_profile': runtime_profile,
        'profile_key': portfolio_profile_key(root, config_path=cfg_path, profile=runtime_profile),
        'force': bool(force),
        'promote_threshold': float(promote_threshold),
        'summary': {
            'scopes_total': int(len(items)),
            'promoted': promoted,
            'rejected': rejected,
            'cooldown': cooldown,
            'skipped': skipped,
        },
        'items': items,
    }
    return payload


__all__ = ['build_retrain_status_payload', 'build_retrain_run_payload']
