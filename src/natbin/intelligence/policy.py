from __future__ import annotations

from typing import Any

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
        ):
            if matched.get(key) is not None:
                base[key] = _safe_float(matched.get(key), float(base[key]))
        for key in ('neutralize_low_reliability', 'learned_fail_closed', 'drift_fail_closed'):
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
    return base
