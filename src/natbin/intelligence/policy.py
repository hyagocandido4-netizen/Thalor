from __future__ import annotations

from typing import Any, Mapping

from ..portfolio.models import PortfolioScope


_DEFAULTS = {
    'name': 'default',
    'learned_weight': 0.60,
    'promote_above': 0.62,
    'suppress_below': 0.42,
    'abstain_band': 0.03,
    'min_reliability': 0.50,
    'neutralize_low_reliability': True,
    'stack_max_bonus': 0.05,
    'stack_max_penalty': 0.05,
    'learned_fail_closed': False,
    'drift_fail_closed': False,
    'portfolio_weight': 1.0,
    'allocator_block_regime': True,
    'allocator_warn_penalty': 0.04,
    'allocator_block_penalty': 0.12,
    'allocator_under_target_bonus': 0.03,
    'allocator_over_target_penalty': 0.04,
    'allocator_retrain_penalty': 0.05,
    'allocator_reliability_penalty': 0.03,
}


def _safe_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)



def _safe_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return bool(value)
    txt = str(value).strip().lower()
    if txt in {'1', 'true', 'yes', 'on'}:
        return True
    if txt in {'0', 'false', 'no', 'off'}:
        return False
    return bool(default)



def _policy_to_dict(policy: Any) -> dict[str, Any]:
    if policy is None:
        return {}
    if isinstance(policy, dict):
        return dict(policy)
    out: dict[str, Any] = {}
    for name in (
        'name',
        'scope_tag',
        'asset',
        'interval_sec',
        'learned_weight',
        'promote_above',
        'suppress_below',
        'abstain_band',
        'min_reliability',
        'neutralize_low_reliability',
        'stack_max_bonus',
        'stack_max_penalty',
        'learned_fail_closed',
        'drift_fail_closed',
        'portfolio_weight',
        'allocator_block_regime',
        'allocator_warn_penalty',
        'allocator_block_penalty',
        'allocator_under_target_bonus',
        'allocator_over_target_penalty',
        'allocator_retrain_penalty',
        'allocator_reliability_penalty',
    ):
        if hasattr(policy, name):
            out[name] = getattr(policy, name)
    return out



def _matches(policy: dict[str, Any], scope: PortfolioScope) -> bool:
    scope_tag = str(policy.get('scope_tag') or '').strip()
    asset = str(policy.get('asset') or '').strip()
    interval = policy.get('interval_sec')
    if scope_tag and scope_tag != str(scope.scope_tag):
        return False
    if asset and asset != str(scope.asset):
        return False
    if interval is not None:
        try:
            if int(interval) != int(scope.interval_sec):
                return False
        except Exception:
            return False
    return True



def _specificity(policy: dict[str, Any]) -> tuple[int, int]:
    exact = 0
    if str(policy.get('scope_tag') or '').strip():
        exact += 3
    if str(policy.get('asset') or '').strip():
        exact += 1
    if policy.get('interval_sec') is not None:
        exact += 1
    return (exact, len([k for k, v in policy.items() if v is not None]))



def resolve_scope_policy(int_cfg: Any, scope: PortfolioScope) -> dict[str, Any]:
    base = dict(_DEFAULTS)
    base.update(
        {
            'learned_weight': _safe_float(getattr(int_cfg, 'learned_gating_weight', None), _DEFAULTS['learned_weight']),
            'promote_above': _safe_float(getattr(int_cfg, 'learned_promote_above', None), _DEFAULTS['promote_above']),
            'suppress_below': _safe_float(getattr(int_cfg, 'learned_suppress_below', None), _DEFAULTS['suppress_below']),
            'abstain_band': _safe_float(getattr(int_cfg, 'learned_abstain_band', None), _DEFAULTS['abstain_band']),
            'min_reliability': _safe_float(getattr(int_cfg, 'learned_min_reliability', None), _DEFAULTS['min_reliability']),
            'neutralize_low_reliability': _safe_bool(getattr(int_cfg, 'learned_neutralize_low_reliability', None), _DEFAULTS['neutralize_low_reliability']),
            'stack_max_bonus': _safe_float(getattr(int_cfg, 'stack_max_bonus', None), _DEFAULTS['stack_max_bonus']),
            'stack_max_penalty': _safe_float(getattr(int_cfg, 'stack_max_penalty', None), _DEFAULTS['stack_max_penalty']),
            'learned_fail_closed': _safe_bool(getattr(int_cfg, 'learned_fail_closed', None), _DEFAULTS['learned_fail_closed']),
            'drift_fail_closed': _safe_bool(getattr(int_cfg, 'drift_fail_closed', None), _DEFAULTS['drift_fail_closed']),
            'portfolio_weight': _safe_float(getattr(int_cfg, 'portfolio_weight', None), _DEFAULTS['portfolio_weight']),
            'allocator_block_regime': _safe_bool(getattr(int_cfg, 'allocator_block_regime', None), _DEFAULTS['allocator_block_regime']),
            'allocator_warn_penalty': _safe_float(getattr(int_cfg, 'allocator_warn_penalty', None), _DEFAULTS['allocator_warn_penalty']),
            'allocator_block_penalty': _safe_float(getattr(int_cfg, 'allocator_block_penalty', None), _DEFAULTS['allocator_block_penalty']),
            'allocator_under_target_bonus': _safe_float(getattr(int_cfg, 'allocator_under_target_bonus', None), _DEFAULTS['allocator_under_target_bonus']),
            'allocator_over_target_penalty': _safe_float(getattr(int_cfg, 'allocator_over_target_penalty', None), _DEFAULTS['allocator_over_target_penalty']),
            'allocator_retrain_penalty': _safe_float(getattr(int_cfg, 'allocator_retrain_penalty', None), _DEFAULTS['allocator_retrain_penalty']),
            'allocator_reliability_penalty': _safe_float(getattr(int_cfg, 'allocator_reliability_penalty', None), _DEFAULTS['allocator_reliability_penalty']),
        }
    )

    matched: dict[str, Any] | None = None
    for item in list(getattr(int_cfg, 'scope_policies', []) or []):
        payload = _policy_to_dict(item)
        if not _matches(payload, scope):
            continue
        if matched is None or _specificity(payload) > _specificity(matched):
            matched = payload

    if matched:
        base['name'] = str(matched.get('name') or matched.get('scope_tag') or matched.get('asset') or 'scope_policy')
        for key in (
            'learned_weight',
            'promote_above',
            'suppress_below',
            'abstain_band',
            'min_reliability',
            'stack_max_bonus',
            'stack_max_penalty',
            'portfolio_weight',
            'allocator_warn_penalty',
            'allocator_block_penalty',
            'allocator_under_target_bonus',
            'allocator_over_target_penalty',
            'allocator_retrain_penalty',
            'allocator_reliability_penalty',
        ):
            if matched.get(key) is not None:
                base[key] = _safe_float(matched.get(key), float(base[key]))
        for key in ('neutralize_low_reliability', 'learned_fail_closed', 'drift_fail_closed', 'allocator_block_regime'):
            if matched.get(key) is not None:
                base[key] = _safe_bool(matched.get(key), bool(base[key]))
        base['match'] = {
            'scope_tag': matched.get('scope_tag'),
            'asset': matched.get('asset'),
            'interval_sec': matched.get('interval_sec'),
        }
    else:
        base['match'] = None

    base['promote_above'] = max(0.0, min(1.0, float(base['promote_above'])))
    base['suppress_below'] = max(0.0, min(1.0, float(base['suppress_below'])))
    if float(base['promote_above']) < float(base['suppress_below']):
        base['promote_above'] = float(base['suppress_below'])
    base['abstain_band'] = max(0.0, min(0.50, float(base['abstain_band'])))
    base['learned_weight'] = max(0.0, min(1.0, float(base['learned_weight'])))
    base['min_reliability'] = max(0.0, min(1.0, float(base['min_reliability'])))
    base['stack_max_bonus'] = max(0.0, float(base['stack_max_bonus']))
    base['stack_max_penalty'] = max(0.0, float(base['stack_max_penalty']))
    base['portfolio_weight'] = max(0.0, min(2.0, float(base['portfolio_weight'])))
    base['allocator_warn_penalty'] = max(0.0, float(base['allocator_warn_penalty']))
    base['allocator_block_penalty'] = max(0.0, float(base['allocator_block_penalty']))
    base['allocator_under_target_bonus'] = max(0.0, float(base['allocator_under_target_bonus']))
    base['allocator_over_target_penalty'] = max(0.0, float(base['allocator_over_target_penalty']))
    base['allocator_retrain_penalty'] = max(0.0, float(base['allocator_retrain_penalty']))
    base['allocator_reliability_penalty'] = max(0.0, float(base['allocator_reliability_penalty']))
    return base



def build_portfolio_feedback(
    *,
    intelligence_score: float,
    coverage: Mapping[str, Any] | None,
    regime: Mapping[str, Any] | None,
    learned_reliability: float | None,
    retrain_plan: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    pressure = str((coverage or {}).get('pressure') or 'balanced')
    regime_level = str((regime or {}).get('level') or 'ok')
    retrain_state = str((retrain_plan or {}).get('state') or 'idle')
    retrain_priority = str((retrain_plan or {}).get('priority') or 'low')

    score = float(intelligence_score) * max(0.0, min(2.0, float(policy.get('portfolio_weight') or 1.0)))
    adjustments: list[dict[str, Any]] = []

    if pressure == 'under_target':
        delta = max(0.0, float(policy.get('allocator_under_target_bonus') or 0.0))
        score += delta
        adjustments.append({'kind': 'coverage_under_target_bonus', 'delta': float(delta)})
    elif pressure == 'over_target':
        delta = max(0.0, float(policy.get('allocator_over_target_penalty') or 0.0))
        score -= delta
        adjustments.append({'kind': 'coverage_over_target_penalty', 'delta': -float(delta)})

    allocator_blocked = False
    block_reason = None
    if regime_level == 'warn':
        delta = max(0.0, float(policy.get('allocator_warn_penalty') or 0.0))
        score -= delta
        adjustments.append({'kind': 'regime_warn_penalty', 'delta': -float(delta)})
    elif regime_level == 'block':
        delta = max(0.0, float(policy.get('allocator_block_penalty') or 0.0))
        score -= delta
        adjustments.append({'kind': 'regime_block_penalty', 'delta': -float(delta)})
        if bool(policy.get('allocator_block_regime', True)):
            allocator_blocked = True
            block_reason = 'regime_block'

    if learned_reliability is not None and float(learned_reliability) < float(policy.get('min_reliability') or 0.50):
        delta = max(0.0, float(policy.get('allocator_reliability_penalty') or 0.0))
        score -= delta
        adjustments.append({'kind': 'low_reliability_penalty', 'delta': -float(delta)})

    if retrain_state in {'queued', 'cooldown'}:
        delta = max(0.0, float(policy.get('allocator_retrain_penalty') or 0.0))
        score -= delta
        adjustments.append({'kind': 'retrain_penalty', 'delta': -float(delta)})

    return {
        'kind': 'portfolio_feedback',
        'pressure': pressure,
        'regime_level': regime_level,
        'retrain_state': retrain_state,
        'retrain_priority': retrain_priority,
        'learned_reliability': None if learned_reliability is None else float(learned_reliability),
        'portfolio_score': float(score),
        'allocator_blocked': bool(allocator_blocked),
        'block_reason': block_reason,
        'adjustments': adjustments,
    }
