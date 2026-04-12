from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..portfolio.latest import load_portfolio_latest_payload, portfolio_profile_key
from ..intelligence.paths import (
    anti_overfit_data_summary_path,
    anti_overfit_summary_path,
    anti_overfit_tuning_path,
    anti_overfit_tuning_review_path,
    drift_state_path,
    intelligence_ops_state_path,
    latest_eval_path,
    pack_path,
    retrain_plan_path,
    retrain_status_path,
    retrain_trigger_path,
    retrain_review_path,
)
from ..intelligence.ops_state import build_intelligence_ops_state
from ..state.control_repo import write_control_artifact
from ..state.execution_repo import ExecutionRepository


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec='seconds')


def _parse_iso(raw: Any) -> datetime | None:
    if raw in (None, ''):
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _first_dict(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict):
            return dict(value)
    return None


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


def _json_loads_maybe(raw: Any) -> Any:
    if raw in (None, ''):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return None


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return None


def _artifact_timestamp(payload: dict[str, Any] | None, path: Path) -> datetime | None:
    if isinstance(payload, dict):
        for key in (
            'updated_at_utc',
            'evaluated_at_utc',
            'generated_at_utc',
            'at_utc',
            'checked_at_utc',
            'recorded_at_utc',
        ):
            dt = _parse_iso(payload.get(key))
            if dt is not None:
                return dt
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except Exception:
        return None


def _artifact_entry(path: Path, *, expected_kind: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    payload = _read_json(path) if path.exists() else None
    kind = payload.get('kind') if isinstance(payload, dict) else None
    stamp = _artifact_timestamp(payload, path) if path.exists() else None
    age_sec = None if stamp is None else max(0, int(((now or _now()) - stamp).total_seconds()))
    size_bytes = None
    try:
        if path.exists():
            size_bytes = int(path.stat().st_size)
    except Exception:
        size_bytes = None
    status = 'missing'
    message = 'artifact ausente'
    if path.exists() and isinstance(payload, dict):
        status = 'ok'
        message = 'artifact carregado'
        if expected_kind and str(kind or '') != str(expected_kind):
            status = 'warn'
            message = 'kind inesperado'
    elif path.exists():
        status = 'warn'
        message = 'artifact não é um JSON object válido'
    return {
        'path': str(path),
        'exists': bool(path.exists()),
        'status': status,
        'message': message,
        'kind': kind,
        'expected_kind': expected_kind,
        'at_utc': _iso(stamp),
        'age_sec': age_sec,
        'size_bytes': size_bytes,
        'data': payload,
    }


def _load_allocation_entry(
    *,
    repo: Path,
    scope_tag: str,
    asset: str,
    interval_sec: int,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    payload, source = load_portfolio_latest_payload(
        repo,
        name='portfolio_allocation_latest.json',
        config_path=config_path,
        profile=profile,
        allow_legacy_fallback=True,
    )
    if not isinstance(payload, dict):
        return None, source
    for bucket in ('selected', 'suppressed'):
        for item in list(payload.get(bucket) or []):
            if not isinstance(item, dict):
                continue
            if str(item.get('scope_tag') or '') != str(scope_tag):
                continue
            if str(item.get('asset') or '') != str(asset):
                continue
            try:
                item_interval = int(item.get('interval_sec') or 0)
            except Exception:
                item_interval = 0
            if int(interval_sec) != item_interval:
                continue
            return {
                'allocation_id': str(payload.get('allocation_id') or '') or None,
                'at_utc': str(payload.get('at_utc') or '') or None,
                'bucket': bucket,
                'selected': bucket == 'selected',
                'source': source,
                'payload_meta': {
                    'profile_key': payload.get('profile_key'),
                    'runtime_profile': payload.get('runtime_profile'),
                    'config_path': payload.get('config_path'),
                },
                'item': dict(item),
            }, source
    return None, source


def _load_candidate_entry(
    *,
    repo: Path,
    scope_tag: str,
    asset: str,
    interval_sec: int,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    payload, source = load_portfolio_latest_payload(
        repo,
        name='portfolio_cycle_latest.json',
        config_path=config_path,
        profile=profile,
        allow_legacy_fallback=True,
    )
    if not isinstance(payload, dict):
        return None, source
    for item in list(payload.get('candidates') or []):
        if not isinstance(item, dict):
            continue
        if str(item.get('scope_tag') or '') != str(scope_tag):
            continue
        if str(item.get('asset') or '') != str(asset):
            continue
        try:
            item_interval = int(item.get('interval_sec') or 0)
        except Exception:
            item_interval = 0
        if int(interval_sec) != item_interval:
            continue
        return {
            'cycle_id': str(payload.get('cycle_id') or '') or None,
            'finished_at_utc': str(payload.get('finished_at_utc') or '') or None,
            'source': source,
            'payload_meta': {
                'profile_key': payload.get('profile_key'),
                'runtime_profile': payload.get('runtime_profile'),
                'config_path': payload.get('config_path'),
            },
            'item': dict(item),
        }, source
    return None, source


def _build_execution_surface(*, repo: Path, scope_tag: str, asset: str, interval_sec: int) -> dict[str, Any]:
    db_path = repo / 'runs' / 'runtime_execution.sqlite3'
    if not db_path.exists():
        return {
            'db_path': str(db_path),
            'recent_intent_count': 0,
            'recent_states': {},
            'latest_intent': None,
            'missing_fields': [],
        }
    execution_repo = ExecutionRepository(db_path)
    recent = [
        item
        for item in execution_repo.list_recent_intents(asset=asset, interval_sec=interval_sec, limit=20)
        if str(getattr(item, 'scope_tag', '') or '') == str(scope_tag)
    ]
    recent = recent[:10]
    state_counts: dict[str, int] = {}
    for item in recent:
        key = str(getattr(item, 'intent_state', '') or 'unknown')
        state_counts[key] = int(state_counts.get(key, 0)) + 1
    latest = recent[0] if recent else None
    latest_payload: dict[str, Any] | None = None
    if latest is not None:
        latest_feedback = _json_loads_maybe(getattr(latest, 'portfolio_feedback_json', None))
        latest_payload = {
            'intent_id': latest.intent_id,
            'intent_state': latest.intent_state,
            'broker_status': latest.broker_status,
            'signal_ts': latest.signal_ts,
            'created_at_utc': latest.created_at_utc,
            'updated_at_utc': latest.updated_at_utc,
            'allocation_batch_id': getattr(latest, 'allocation_batch_id', None),
            'cluster_key': getattr(latest, 'cluster_key', None),
            'portfolio_score': getattr(latest, 'portfolio_score', None),
            'intelligence_score': getattr(latest, 'intelligence_score', None),
            'retrain_state': getattr(latest, 'retrain_state', None),
            'retrain_priority': getattr(latest, 'retrain_priority', None),
            'allocation_reason': getattr(latest, 'allocation_reason', None),
            'allocation_rank': getattr(latest, 'allocation_rank', None),
            'portfolio_feedback': latest_feedback if isinstance(latest_feedback, dict) else None,
        }
    return {
        'db_path': str(db_path),
        'recent_intent_count': len(recent),
        'recent_states': dict(sorted(state_counts.items())),
        'latest_intent': latest_payload,
        'missing_fields': [],
    }


def _check(name: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {'name': str(name), 'status': str(status), 'message': str(message)}
    if extra:
        item.update(extra)
    return item


def _severity_from_checks(checks: list[dict[str, Any]]) -> str:
    if any(str(item.get('status')) == 'error' for item in checks):
        return 'error'
    if any(str(item.get('status')) == 'warn' for item in checks):
        return 'warn'
    return 'ok'


def _public_artifacts(items: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, payload in items.items():
        out[name] = {k: v for k, v in payload.items() if k != 'data'}
    return out


def _build_scope_surface(
    *,
    repo: Path,
    scope_tag: str,
    asset: str,
    interval_sec: int,
    timezone: str,
    intelligence_enabled: bool,
    artifact_dir: str | Path,
    config_path: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    now = _now()
    paths = {
        'pack': pack_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'latest_eval': latest_eval_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'drift_state': drift_state_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'retrain_trigger': retrain_trigger_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'retrain_plan': retrain_plan_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'retrain_status': retrain_status_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'retrain_review': retrain_review_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'ops_state': intelligence_ops_state_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'anti_overfit_summary': anti_overfit_summary_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'anti_overfit_data_summary': anti_overfit_data_summary_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'anti_overfit_tuning': anti_overfit_tuning_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
        'anti_overfit_tuning_review': anti_overfit_tuning_review_path(repo_root=repo, scope_tag=scope_tag, artifact_dir=artifact_dir),
    }
    artifacts = {
        'pack': _artifact_entry(paths['pack'], expected_kind='intelligence_pack', now=now),
        'latest_eval': _artifact_entry(paths['latest_eval'], expected_kind='intelligence_eval', now=now),
        'drift_state': _artifact_entry(paths['drift_state'], expected_kind='drift_state', now=now),
        'retrain_trigger': _artifact_entry(paths['retrain_trigger'], expected_kind='retrain_trigger', now=now),
        'retrain_plan': _artifact_entry(paths['retrain_plan'], expected_kind='retrain_plan', now=now),
        'retrain_status': _artifact_entry(paths['retrain_status'], expected_kind='retrain_status', now=now),
        'retrain_review': _artifact_entry(paths['retrain_review'], expected_kind='retrain_review', now=now),
        'ops_state': _artifact_entry(paths['ops_state'], expected_kind='intelligence_ops_state', now=now),
        'anti_overfit_summary': _artifact_entry(paths['anti_overfit_summary'], now=now),
        'anti_overfit_data_summary': _artifact_entry(paths['anti_overfit_data_summary'], now=now),
        'anti_overfit_tuning': _artifact_entry(paths['anti_overfit_tuning'], expected_kind='anti_overfit_tuning', now=now),
        'anti_overfit_tuning_review': _artifact_entry(paths['anti_overfit_tuning_review'], expected_kind='anti_overfit_tuning_review', now=now),
    }

    pack_payload = artifacts['pack']['data'] if isinstance(artifacts['pack'].get('data'), dict) else {}
    eval_payload = artifacts['latest_eval']['data'] if isinstance(artifacts['latest_eval'].get('data'), dict) else {}
    retrain_plan = artifacts['retrain_plan']['data'] if isinstance(artifacts['retrain_plan'].get('data'), dict) else {}
    retrain_status = artifacts['retrain_status']['data'] if isinstance(artifacts['retrain_status'].get('data'), dict) else {}
    retrain_review = artifacts['retrain_review']['data'] if isinstance(artifacts['retrain_review'].get('data'), dict) else {}
    anti_overfit_tuning_live = artifacts['anti_overfit_tuning']['data'] if isinstance(artifacts['anti_overfit_tuning'].get('data'), dict) else {}
    anti_overfit_tuning_review = artifacts['anti_overfit_tuning_review']['data'] if isinstance(artifacts['anti_overfit_tuning_review'].get('data'), dict) else {}
    anti_overfit_tuning = anti_overfit_tuning_live if anti_overfit_tuning_live else (dict(anti_overfit_tuning_review.get('tuning') or {}) if isinstance(anti_overfit_tuning_review.get('tuning'), dict) else anti_overfit_tuning_review)
    candidate_entry, candidate_source = _load_candidate_entry(
        repo=repo,
        scope_tag=scope_tag,
        asset=asset,
        interval_sec=interval_sec,
        config_path=config_path,
        profile=profile,
    )
    candidate_item = dict(candidate_entry.get('item') or {}) if isinstance(candidate_entry, dict) else {}
    allocation_entry, allocation_source = _load_allocation_entry(
        repo=repo,
        scope_tag=scope_tag,
        asset=asset,
        interval_sec=interval_sec,
        config_path=config_path,
        profile=profile,
    )
    allocation_item = dict(allocation_entry.get('item') or {}) if isinstance(allocation_entry, dict) else {}
    execution = _build_execution_surface(repo=repo, scope_tag=scope_tag, asset=asset, interval_sec=interval_sec)
    latest_intent = dict(execution.get('latest_intent') or {}) if isinstance(execution.get('latest_intent'), dict) else {}

    eval_retrain = dict(eval_payload.get('retrain_orchestration') or {}) if isinstance(eval_payload.get('retrain_orchestration'), dict) else {}
    portfolio_feedback = _first_dict(
        eval_payload.get('portfolio_feedback'),
        allocation_item.get('portfolio_feedback'),
        candidate_item.get('portfolio_feedback'),
        latest_intent.get('portfolio_feedback'),
    )

    ops_state_payload = artifacts['ops_state']['data'] if isinstance(artifacts['ops_state'].get('data'), dict) else {}
    effective_ops = build_intelligence_ops_state(
        scope_tag=scope_tag,
        asset=asset,
        interval_sec=int(interval_sec),
        pack_payload=pack_payload,
        eval_payload=eval_payload,
        retrain_plan=retrain_plan,
        retrain_status=retrain_status,
        retrain_review=retrain_review,
        anti_overfit_summary=artifacts['anti_overfit_summary'].get('data'),
        anti_overfit_data_summary=artifacts['anti_overfit_data_summary'].get('data'),
        anti_overfit_tuning=anti_overfit_tuning_live,
        anti_overfit_tuning_review=anti_overfit_tuning_review,
        candidate_item=candidate_item,
        allocation_item=allocation_item,
        latest_intent=latest_intent,
        timezone=timezone,
        now_utc=now,
    )
    effective_retrain = dict(effective_ops.get('retrain') or {})
    effective_anti = dict(effective_ops.get('anti_overfit') or {})
    effective_tuning = dict(effective_anti.get('tuning') or {})
    effective_consistency = dict(effective_ops.get('consistency') or {})

    retrain_state = _first_nonempty(
        effective_retrain.get('state'),
        retrain_status.get('state'),
        retrain_plan.get('state'),
        eval_retrain.get('state'),
        allocation_item.get('retrain_state'),
        candidate_item.get('retrain_state'),
        latest_intent.get('retrain_state'),
    )
    retrain_priority = _first_nonempty(
        effective_retrain.get('priority'),
        retrain_status.get('priority'),
        retrain_plan.get('priority'),
        eval_retrain.get('priority'),
        allocation_item.get('retrain_priority'),
        candidate_item.get('retrain_priority'),
        latest_intent.get('retrain_priority'),
    )
    intelligence_score = _first_nonempty(
        _safe_float(eval_payload.get('intelligence_score')),
        _safe_float(allocation_item.get('intelligence_score')),
        _safe_float(candidate_item.get('intelligence_score')),
        _safe_float(latest_intent.get('intelligence_score')),
    )
    portfolio_score = _first_nonempty(
        _safe_float(eval_payload.get('portfolio_score')),
        _safe_float(allocation_item.get('portfolio_score')),
        _safe_float(candidate_item.get('portfolio_score')),
        _safe_float(latest_intent.get('portfolio_score')),
    )
    allow_trade = eval_payload.get('allow_trade') if 'allow_trade' in eval_payload else None
    block_reason = _first_nonempty(
        portfolio_feedback.get('block_reason') if isinstance(portfolio_feedback, dict) else None,
        eval_payload.get('block_reason'),
        allocation_item.get('reason'),
    )
    feedback_blocked = bool((portfolio_feedback or {}).get('allocator_blocked')) if isinstance(portfolio_feedback, dict) else False
    if allow_trade is False:
        feedback_blocked = True

    candidate_summary = {
        'cycle_id': candidate_entry.get('cycle_id') if isinstance(candidate_entry, dict) else None,
        'finished_at_utc': candidate_entry.get('finished_at_utc') if isinstance(candidate_entry, dict) else None,
        'action': candidate_item.get('action'),
        'reason': candidate_item.get('reason'),
        'intelligence_score': _safe_float(candidate_item.get('intelligence_score')),
        'portfolio_score': _safe_float(candidate_item.get('portfolio_score')),
        'retrain_state': candidate_item.get('retrain_state'),
        'retrain_priority': candidate_item.get('retrain_priority'),
        'source': candidate_source,
        'payload_meta': candidate_entry.get('payload_meta') if isinstance(candidate_entry, dict) else None,
    }

    allocation_summary = {
        'allocation_id': allocation_entry.get('allocation_id') if isinstance(allocation_entry, dict) else None,
        'at_utc': allocation_entry.get('at_utc') if isinstance(allocation_entry, dict) else None,
        'bucket': allocation_entry.get('bucket') if isinstance(allocation_entry, dict) else None,
        'selected': bool(allocation_entry.get('selected')) if isinstance(allocation_entry, dict) else False,
        'reason': allocation_item.get('reason'),
        'rank': _safe_int(allocation_item.get('rank')),
        'cluster_key': allocation_item.get('cluster_key'),
        'portfolio_score': _safe_float(allocation_item.get('portfolio_score')),
        'intelligence_score': _safe_float(allocation_item.get('intelligence_score')),
        'retrain_state': allocation_item.get('retrain_state'),
        'retrain_priority': allocation_item.get('retrain_priority'),
        'portfolio_feedback': dict(allocation_item.get('portfolio_feedback') or {}) if isinstance(allocation_item.get('portfolio_feedback'), dict) else None,
        'source': allocation_source,
        'payload_meta': allocation_entry.get('payload_meta') if isinstance(allocation_entry, dict) else None,
    }

    summary = {
        'pack_available': bool(artifacts['pack'].get('exists')),
        'eval_available': bool(artifacts['latest_eval'].get('exists')),
        'allow_trade': allow_trade,
        'intelligence_score': intelligence_score,
        'portfolio_score': portfolio_score,
        'retrain_state': retrain_state,
        'retrain_priority': retrain_priority,
        'portfolio_feedback_blocked': bool(feedback_blocked),
        'portfolio_feedback_reason': block_reason,
        'block_reason': eval_payload.get('block_reason'),
        'coverage': eval_payload.get('coverage'),
        'drift': eval_payload.get('drift'),
        'anti_overfit': eval_payload.get('anti_overfit') or {
            'available': effective_anti.get('available'),
            'accepted': effective_anti.get('accepted'),
            'robustness_score': effective_anti.get('robustness_score'),
            'penalty': effective_anti.get('penalty'),
        },
        'anti_overfit_source': {
            'summary_present': bool(effective_anti.get('summary_present')),
            'summary_source': effective_anti.get('summary_source'),
            'data_summary_present': bool(effective_anti.get('data_summary_present')),
            'data_summary_source': effective_anti.get('data_summary_source'),
        },
        'anti_overfit_tuning': {
            'selected_variant': effective_tuning.get('selected_variant'),
            'baseline_variant': effective_tuning.get('baseline_variant'),
            'improved': bool(effective_tuning.get('improved', False)),
            'selection_reason': effective_tuning.get('selection_reason'),
            'source': effective_tuning.get('source'),
            'review_only': bool(effective_tuning.get('review_only')),
            'review_verdict': effective_tuning.get('review_verdict'),
        } if bool(effective_tuning.get('present')) else None,
        'stack': eval_payload.get('stack'),
        'slot': eval_payload.get('slot'),
        'retrain_review_verdict': effective_retrain.get('review_verdict'),
        'retrain_review_reason': effective_retrain.get('review_reason'),
        'retrain_review_at_utc': effective_retrain.get('review_at_utc'),
    }

    checks: list[dict[str, Any]] = []
    if not intelligence_enabled:
        checks.append(_check('intelligence_config', 'ok', 'Intelligence desabilitada no profile atual.'))
    else:
        checks.append(_check('intelligence_config', 'ok', 'Intelligence habilitada.', artifact_dir=str(artifact_dir), profile=profile, profile_key=portfolio_profile_key(repo, config_path=config_path, profile=profile)))
        if str((candidate_source or {}).get('source') or '') == 'legacy_mismatch' or str((allocation_source or {}).get('source') or '') == 'legacy_mismatch':
            checks.append(_check('portfolio_artifact_scope', 'warn', 'Artifacts globais de portfolio existem, mas não pertencem ao profile/config atual; ignorando fallback legado.', candidate_source=(candidate_source or {}).get('source'), allocation_source=(allocation_source or {}).get('source')))
        else:
            checks.append(_check('portfolio_artifact_scope', 'ok', 'Artifacts de portfolio alinhados ao profile/config atual.', candidate_source=(candidate_source or {}).get('source'), allocation_source=(allocation_source or {}).get('source')))
        if bool(artifacts['pack'].get('exists')):
            checks.append(_check('pack_artifact', 'ok', 'Pack de inteligência presente.', path=artifacts['pack'].get('path')))
        else:
            checks.append(_check('pack_artifact', 'warn', 'Pack de inteligência ausente para o scope.', path=artifacts['pack'].get('path')))
        if bool(artifacts['latest_eval'].get('exists')):
            checks.append(_check('latest_eval_artifact', 'ok', 'latest_eval.json presente.', path=artifacts['latest_eval'].get('path')))
        else:
            checks.append(_check('latest_eval_artifact', 'warn', 'latest_eval.json ausente para o scope.', path=artifacts['latest_eval'].get('path')))
        if bool(artifacts['anti_overfit_data_summary'].get('exists')):
            checks.append(_check('anti_overfit_data_summary', 'ok', 'anti_overfit_data_summary.json presente.', path=artifacts['anti_overfit_data_summary'].get('path'), source=((artifacts['anti_overfit_data_summary'].get('data') or {}).get('source')) if isinstance(artifacts['anti_overfit_data_summary'].get('data'), dict) else None))
        elif bool(artifacts['anti_overfit_summary'].get('exists')):
            checks.append(_check('anti_overfit_data_summary', 'ok', 'anti_overfit_summary.json materializado para o scope.', path=artifacts['anti_overfit_summary'].get('path'), source=((artifacts['anti_overfit_summary'].get('data') or {}).get('source')) if isinstance(artifacts['anti_overfit_summary'].get('data'), dict) else None))
        else:
            checks.append(_check('anti_overfit_data_summary', 'ok', 'Sem fonte materializada adicional; anti-overfit segue usando artifacts atuais.', path=artifacts['anti_overfit_summary'].get('path')))
        if bool(artifacts['anti_overfit_tuning'].get('exists')):
            checks.append(_check('anti_overfit_tuning', 'ok', 'anti_overfit_tuning.json presente.', path=artifacts['anti_overfit_tuning'].get('path'), selected_variant=effective_tuning.get('selected_variant'), improved=bool(effective_tuning.get('improved', False))))
        elif bool(artifacts['anti_overfit_tuning_review'].get('exists')):
            if bool(effective_consistency.get('expected_review_only_tuning')):
                checks.append(_check('anti_overfit_tuning', 'ok', 'Último tuning de anti-overfit ficou preservado apenas no review por rollback/cooldown consistente.', path=artifacts['anti_overfit_tuning_review'].get('path'), selected_variant=effective_tuning.get('selected_variant'), improved=bool(effective_tuning.get('improved', False)), verdict=effective_tuning.get('review_verdict')))
            else:
                checks.append(_check('anti_overfit_tuning', 'warn', 'Último tuning de anti-overfit preservado apenas no review.', path=artifacts['anti_overfit_tuning_review'].get('path'), selected_variant=effective_tuning.get('selected_variant'), improved=bool(effective_tuning.get('improved', False)), verdict=effective_tuning.get('review_verdict')))
        else:
            checks.append(_check('anti_overfit_tuning', 'ok', 'Sem tuning adicional materializado; baseline permanece ativa.', path=artifacts['anti_overfit_tuning'].get('path')))
        if bool(artifacts['ops_state'].get('exists')):
            if bool(effective_consistency.get('ok', False)):
                checks.append(_check('ops_state', 'ok', 'intelligence_ops_state.json presente e consistente.', path=artifacts['ops_state'].get('path')))
            else:
                checks.append(_check('ops_state', 'warn', 'intelligence_ops_state.json presente, mas com inconsistências semânticas.', path=artifacts['ops_state'].get('path'), issues=effective_consistency.get('issues')))
        else:
            checks.append(_check('ops_state', 'ok', 'Sem artifact materializado; state efetivo foi reconstituído em memória.'))
        if bool(artifacts['latest_eval'].get('exists')):
            if bool(artifacts['retrain_plan'].get('exists')) and bool(artifacts['retrain_status'].get('exists')):
                checks.append(_check('retrain_artifacts', 'ok', 'retrain_plan/status presentes.', state=retrain_state, priority=retrain_priority))
            else:
                checks.append(_check('retrain_artifacts', 'warn', 'latest_eval existe, mas retrain_plan/status não estão completos.', plan_exists=bool(artifacts['retrain_plan'].get('exists')), status_exists=bool(artifacts['retrain_status'].get('exists'))))
        else:
            checks.append(_check('retrain_artifacts', 'ok', 'Sem eval recente; retrain artifacts ainda não são obrigatórios.'))

        if bool(artifacts['retrain_review'].get('exists')):
            verdict = str(effective_retrain.get('review_verdict') or '').strip().lower()
            if verdict == 'rejected' and bool(effective_consistency.get('expected_rejected_cooldown')) and bool(effective_consistency.get('ok', False)):
                checks.append(_check('retrain_review', 'ok', 'Último retrain foi rejeitado, mas rollback e cooldown ficaram consistentes.', verdict=verdict, reason=effective_retrain.get('review_reason')))
            elif verdict == 'rejected':
                checks.append(_check('retrain_review', 'warn', 'Último retrain foi rejeitado; revisar comparação before/after.', verdict=verdict, reason=effective_retrain.get('review_reason')))
            else:
                checks.append(_check('retrain_review', 'ok', 'Último retrain/review registrado.', verdict=effective_retrain.get('review_verdict'), reason=effective_retrain.get('review_reason')))
        else:
            checks.append(_check('retrain_review', 'ok', 'Sem review operacional recente de retrain para o scope.'))

        normalized_block_reason = str(block_reason or '').strip().lower().replace('portfolio_feedback_block:', '')
        informational_regime_block = bool(feedback_blocked) and normalized_block_reason == 'regime_block'
        if informational_regime_block:
            checks.append(_check('portfolio_feedback', 'ok', 'Portfolio feedback bloqueou o trade por regime atual; tratado como no-trade operacional.', reason=block_reason, retrain_state=retrain_state, retrain_priority=retrain_priority))
        elif bool(feedback_blocked):
            checks.append(_check('portfolio_feedback', 'warn', 'Portfolio feedback está bloqueando o trade no scope.', reason=block_reason, retrain_state=retrain_state, retrain_priority=retrain_priority))
        elif str(retrain_priority or '').lower() == 'high' or str(retrain_state or '').lower() in {'queued', 'cooldown'}:
            checks.append(_check('portfolio_feedback', 'warn', 'Scope requer atenção de retrain / monitoramento.', retrain_state=retrain_state, retrain_priority=retrain_priority))
        else:
            checks.append(_check('portfolio_feedback', 'ok', 'Portfolio feedback não bloqueia o scope no momento.', retrain_state=retrain_state, retrain_priority=retrain_priority))

        if allocation_entry is None:
            checks.append(_check('allocation_linkage', 'ok', 'Sem allocation portfolio recente para o scope.'))
        else:
            if allocation_summary['selected'] and allocation_summary['portfolio_score'] is None:
                checks.append(_check('allocation_linkage', 'warn', 'Allocation selecionada sem portfolio_score explícito.', allocation_id=allocation_summary['allocation_id']))
            else:
                checks.append(_check('allocation_linkage', 'ok', 'Allocation recente localizada para o scope.', allocation_id=allocation_summary['allocation_id'], bucket=allocation_summary['bucket'], rank=allocation_summary['rank']))

        missing_fields: list[str] = []
        if latest_intent:
            if allocation_summary['allocation_id'] and not latest_intent.get('allocation_batch_id'):
                missing_fields.append('allocation_batch_id')
            if portfolio_score is not None and latest_intent.get('portfolio_score') is None:
                missing_fields.append('portfolio_score')
            if intelligence_score is not None and latest_intent.get('intelligence_score') is None:
                missing_fields.append('intelligence_score')
            if retrain_state and not latest_intent.get('retrain_state'):
                missing_fields.append('retrain_state')
            if retrain_priority and not latest_intent.get('retrain_priority'):
                missing_fields.append('retrain_priority')
            if isinstance(portfolio_feedback, dict) and not latest_intent.get('portfolio_feedback'):
                missing_fields.append('portfolio_feedback_json')
            execution['missing_fields'] = list(missing_fields)
            if missing_fields:
                checks.append(_check('execution_traceability', 'warn', 'Intent recente sem trilha completa de inteligência/alocação.', missing_fields=missing_fields, latest_intent_state=latest_intent.get('intent_state')))
            else:
                checks.append(_check('execution_traceability', 'ok', 'Intent recente contém trilha de inteligência/alocação.', latest_intent_state=latest_intent.get('intent_state')))
        else:
            checks.append(_check('execution_traceability', 'ok', 'Sem intents recentes para o scope.'))

    severity = _severity_from_checks(checks)
    warnings = [str(item.get('name')) for item in checks if str(item.get('status')) == 'warn']
    payload = {
        'at_utc': _iso(now),
        'kind': 'intelligence_surface',
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(repo),
        'config_path': str(config_path) if config_path is not None else None,
        'scope': {
            'asset': str(asset),
            'interval_sec': int(interval_sec),
            'timezone': str(timezone),
            'scope_tag': str(scope_tag),
        },
        'enabled': bool(intelligence_enabled),
        'artifact_dir': str(artifact_dir),
        'profile_key': portfolio_profile_key(repo, config_path=config_path, profile=profile),
        'runtime_profile': str(profile or '').strip() or None,
        'artifacts': _public_artifacts(artifacts),
        'sources': {
            'candidate': candidate_source,
            'allocation': allocation_source,
        },
        'effective_state': effective_ops,
        'summary': summary,
        'candidate': candidate_summary,
        'allocation': allocation_summary,
        'execution': execution,
        'checks': checks,
        'warnings': warnings,
    }
    return payload


def build_intelligence_surface_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    write_artifact: bool = True,
) -> dict[str, Any]:
    ctx = build_context(repo_root=repo_root, config_path=config_path, dump_snapshot=False)
    repo = Path(ctx.repo_root).resolve()
    int_cfg = dict(ctx.resolved_config.get('intelligence') or {})
    payload = _build_scope_surface(
        repo=repo,
        scope_tag=str(ctx.scope.scope_tag),
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        timezone=str(ctx.config.timezone),
        intelligence_enabled=bool(int_cfg.get('enabled', False)),
        artifact_dir=str(int_cfg.get('artifact_dir') or 'runs/intelligence'),
        config_path=str(ctx.config.config_path),
        profile=str(ctx.resolved_config.get('profile') or 'default'),
    )
    if write_artifact:
        write_control_artifact(
            repo_root=repo,
            asset=ctx.config.asset,
            interval_sec=ctx.config.interval_sec,
            name='intelligence',
            payload=payload,
        )
    return payload


def build_portfolio_intelligence_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    from ..config.paths import resolve_config_path, resolve_repo_root
    from ..portfolio.runner import load_scopes

    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    scopes, cfg = load_scopes(repo_root=root, config_path=cfg_path)
    int_cfg = getattr(cfg, 'intelligence', None)
    enabled = bool(getattr(int_cfg, 'enabled', False))
    artifact_dir = str(getattr(int_cfg, 'artifact_dir', 'runs/intelligence'))
    runtime_profile = str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default')

    items: list[dict[str, Any]] = []
    severities: list[str] = []
    for scope in scopes:
        surf = _build_scope_surface(
            repo=Path(root).resolve(),
            scope_tag=str(scope.scope_tag),
            asset=str(scope.asset),
            interval_sec=int(scope.interval_sec),
            timezone=str(scope.timezone),
            intelligence_enabled=enabled,
            artifact_dir=artifact_dir,
            config_path=str(cfg_path),
            profile=runtime_profile,
        )
        severities.append(str(surf.get('severity') or 'ok'))
        latest_intent = dict((surf.get('execution') or {}).get('latest_intent') or {})
        items.append(
            {
                'scope_tag': scope.scope_tag,
                'asset': scope.asset,
                'interval_sec': int(scope.interval_sec),
                'enabled': bool(surf.get('enabled')),
                'severity': str(surf.get('severity') or 'ok'),
                'pack_available': bool((surf.get('summary') or {}).get('pack_available')),
                'eval_available': bool((surf.get('summary') or {}).get('eval_available')),
                'allow_trade': (surf.get('summary') or {}).get('allow_trade'),
                'portfolio_score': (surf.get('summary') or {}).get('portfolio_score'),
                'intelligence_score': (surf.get('summary') or {}).get('intelligence_score'),
                'retrain_state': (surf.get('summary') or {}).get('retrain_state'),
                'retrain_priority': (surf.get('summary') or {}).get('retrain_priority'),
                'feedback_blocked': bool((surf.get('summary') or {}).get('portfolio_feedback_blocked')),
                'feedback_reason': (surf.get('summary') or {}).get('portfolio_feedback_reason'),
                'allocation_bucket': (surf.get('allocation') or {}).get('bucket'),
                'allocation_reason': (surf.get('allocation') or {}).get('reason'),
                'selected': bool((surf.get('allocation') or {}).get('selected')),
                'recent_intent_state': latest_intent.get('intent_state'),
                'recent_intent_trace_ok': len(list((surf.get('execution') or {}).get('missing_fields') or [])) == 0,
                'warnings': list(surf.get('warnings') or []),
                'candidate_source': ((surf.get('sources') or {}).get('candidate') or {}).get('source'),
                'allocation_source': ((surf.get('sources') or {}).get('allocation') or {}).get('source'),
            }
        )

    summary = {
        'scopes_total': len(items),
        'enabled_scopes': int(sum(1 for item in items if bool(item.get('enabled')))),
        'pack_available': int(sum(1 for item in items if bool(item.get('pack_available')))),
        'eval_available': int(sum(1 for item in items if bool(item.get('eval_available')))),
        'selected_scopes': int(sum(1 for item in items if bool(item.get('selected')))),
        'feedback_blocked_scopes': int(sum(1 for item in items if bool(item.get('feedback_blocked')))),
        'retrain_attention_scopes': int(
            sum(
                1
                for item in items
                if str(item.get('retrain_priority') or '').lower() == 'high'
                or str(item.get('retrain_state') or '').lower() in {'queued', 'cooldown'}
            )
        ),
        'recent_intent_scopes': int(sum(1 for item in items if item.get('recent_intent_state') is not None)),
        'traceability_warn_scopes': int(sum(1 for item in items if not bool(item.get('recent_intent_trace_ok')))),
    }

    severity = 'ok'
    if any(level == 'error' for level in severities):
        severity = 'error'
    elif any(level == 'warn' for level in severities):
        severity = 'warn'

    return {
        'at_utc': _iso(_now()),
        'kind': 'portfolio_intelligence_surface',
        'ok': severity != 'error',
        'severity': severity,
        'repo_root': str(Path(root).resolve()),
        'config_path': str(cfg_path),
        'enabled': enabled,
        'runtime_profile': runtime_profile,
        'profile_key': portfolio_profile_key(root, config_path=cfg_path, profile=runtime_profile),
        'artifact_dir': artifact_dir,
        'summary': summary,
        'items': items,
    }
