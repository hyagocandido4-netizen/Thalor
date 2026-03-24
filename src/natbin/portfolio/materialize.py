from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ..config.loader import load_thalor_config
from ..config.paths import resolve_config_path, resolve_repo_root
from .latest import write_portfolio_latest_payload
from .models import CandidateDecision, PortfolioScope
from .quota import compute_asset_quotas, compute_portfolio_quota


TRADE_ACTIONS = {'CALL', 'PUT'}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _candidate_reason(candidate: CandidateDecision) -> str:
    reason = str(candidate.reason or '').strip()
    if reason:
        return reason
    if str(candidate.action or '').upper() not in TRADE_ACTIONS:
        return 'not_trade_action'
    return 'selected'


def _allocation_reason(candidate: CandidateDecision) -> tuple[bool, str]:
    feedback = dict(candidate.portfolio_feedback or {})
    blocked = bool(feedback.get('allocator_blocked'))
    block_reason = str(feedback.get('block_reason') or '').strip() or None
    action = str(candidate.action or '').upper()
    if blocked:
        return False, f"portfolio_feedback_block:{block_reason or _candidate_reason(candidate)}"
    if action not in TRADE_ACTIONS:
        return False, _candidate_reason(candidate)
    return True, _candidate_reason(candidate)


def _allocation_item(scope: PortfolioScope, candidate: CandidateDecision, *, selected: bool, reason: str, rank: int | None) -> dict[str, Any]:
    return {
        'scope_tag': scope.scope_tag,
        'asset': scope.asset,
        'interval_sec': int(scope.interval_sec),
        'action': str(candidate.action or 'HOLD').upper(),
        'score': candidate.score,
        'conf': candidate.conf,
        'ev': candidate.ev,
        'rank_value': float(candidate.rank_value(weight=float(scope.weight or 1.0))),
        'selected': bool(selected),
        'reason': str(reason),
        'intelligence_score': candidate.intelligence_score,
        'learned_gate_prob': candidate.learned_gate_prob,
        'slot_multiplier': candidate.slot_multiplier,
        'drift_level': candidate.drift_level,
        'coverage_bias': candidate.coverage_bias,
        'stack_decision': candidate.stack_decision,
        'regime_level': candidate.regime_level,
        'portfolio_score': candidate.portfolio_score,
        'retrain_state': candidate.retrain_state,
        'retrain_priority': candidate.retrain_priority,
        'rank': rank,
        'cluster_key': str(scope.cluster_key or 'default'),
        'risk_context': None,
        'intelligence': dict(candidate.intelligence or {}),
        'portfolio_feedback': dict(candidate.portfolio_feedback or {}),
    }


def materialize_portfolio_latest_payloads(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    scopes: Iterable[PortfolioScope],
    candidates: Iterable[CandidateDecision],
    message: str = 'materialized_from_scope_artifacts',
    write_legacy: bool = False,
) -> dict[str, Any]:
    """Persist scoped portfolio latest payloads for the current config/profile.

    This is intentionally profile-scoped and defaults to *not* overwriting the
    global legacy latest files. It is used by recovery flows where we want the
    current profile to have a canonical portfolio view without contaminating the
    legacy cross-profile fallback.
    """

    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    cfg = load_thalor_config(config_path=cfg_path, repo_root=root)
    runtime_profile = str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default')

    scope_list = list(scopes)
    cand_list = list(candidates)
    if not scope_list or not cand_list:
        return {
            'ok': False,
            'message': 'no_candidates',
            'config_path': str(cfg_path),
            'runtime_profile': runtime_profile,
        }

    scope_by_tag = {str(s.scope_tag): s for s in scope_list}
    ranked = sorted(cand_list, key=lambda item: float(item.rank_value(weight=float((scope_by_tag.get(str(item.scope_tag)) or scope_list[0]).weight or 1.0))), reverse=True)

    selected_items: list[dict[str, Any]] = []
    suppressed_items: list[dict[str, Any]] = []
    selected_rank = 0
    for cand in ranked:
        scope = scope_by_tag.get(str(cand.scope_tag))
        if scope is None:
            continue
        selected, reason = _allocation_reason(cand)
        rank = None
        if selected:
            selected_rank += 1
            rank = selected_rank
            selected_items.append(_allocation_item(scope, cand, selected=True, reason=reason, rank=rank))
        else:
            suppressed_items.append(_allocation_item(scope, cand, selected=False, reason=reason, rank=None))

    asset_quotas = compute_asset_quotas(root, scope_list, config_path=cfg_path)
    portfolio_quota = compute_portfolio_quota(root, scope_list, config_path=cfg_path)

    stamp = _now_iso()
    cycle_id = datetime.now(tz=UTC).strftime('recovered_%Y%m%dT%H%M%SZ')
    allocation_payload: dict[str, Any] = {
        'allocation_id': cycle_id,
        'at_utc': stamp,
        'max_select': int(getattr(getattr(cfg, 'multi_asset', None), 'portfolio_topk_total', 1) or 1),
        'selected': selected_items,
        'suppressed': suppressed_items,
        'portfolio_quota': portfolio_quota.as_dict(),
        'asset_quotas': [q.as_dict() for q in asset_quotas],
        'risk_summary': {
            'materialized': True,
            'selected_count': int(len(selected_items)),
            'suppressed_count': int(len(suppressed_items)),
        },
        'recovered_from': 'scope_artifacts',
        'message': str(message),
    }
    allocation_paths = write_portfolio_latest_payload(
        root,
        name='portfolio_allocation_latest.json',
        payload=allocation_payload,
        config_path=cfg_path,
        profile=runtime_profile,
        write_legacy=write_legacy,
    )
    allocation_payload['persisted_paths'] = dict(allocation_paths)

    cycle_payload: dict[str, Any] = {
        'cycle_id': cycle_id,
        'started_at_utc': stamp,
        'finished_at_utc': stamp,
        'ok': True,
        'message': str(message),
        'scopes': [s.as_dict() for s in scope_list],
        'prepare': [],
        'candidate_results': [
            {
                'scope_tag': cand.scope_tag,
                'asset': cand.asset,
                'interval_sec': int(cand.interval_sec),
                'source': 'decision_latest',
                'decision_path': cand.decision_path,
                'recovered': True,
            }
            for cand in ranked
        ],
        'candidates': [cand.as_dict() for cand in ranked],
        'allocation': allocation_payload,
        'execution': [],
        'errors': [],
        'gates': {'materialized': True},
        'failsafe_blocks': {},
        'recovered_from': 'scope_artifacts',
    }
    cycle_paths = write_portfolio_latest_payload(
        root,
        name='portfolio_cycle_latest.json',
        payload=cycle_payload,
        config_path=cfg_path,
        profile=runtime_profile,
        write_legacy=write_legacy,
    )
    cycle_payload['persisted_paths'] = dict(cycle_paths)

    return {
        'ok': True,
        'message': str(message),
        'config_path': str(cfg_path),
        'runtime_profile': runtime_profile,
        'cycle': cycle_payload,
        'allocation': allocation_payload,
        'paths': {
            'cycle': cycle_paths,
            'allocation': allocation_paths,
        },
    }


__all__ = ['materialize_portfolio_latest_payloads', 'TRADE_ACTIONS']
