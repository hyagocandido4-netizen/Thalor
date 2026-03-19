from __future__ import annotations

from types import SimpleNamespace

from natbin.intelligence.policy import resolve_scope_policy
from natbin.portfolio.models import PortfolioScope


class _Policy(SimpleNamespace):
    pass


def test_resolve_scope_policy_prefers_specific_match():
    scope = PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s')
    int_cfg = SimpleNamespace(
        learned_gating_weight=0.60,
        learned_promote_above=0.62,
        learned_suppress_below=0.42,
        learned_abstain_band=0.03,
        learned_min_reliability=0.50,
        learned_neutralize_low_reliability=True,
        stack_max_bonus=0.05,
        stack_max_penalty=0.05,
        learned_fail_closed=False,
        drift_fail_closed=False,
        scope_policies=[
            _Policy(name='asset_default', asset='EURUSD-OTC', learned_weight=0.55),
            _Policy(name='scope_exact', scope_tag='EURUSD-OTC_300s', learned_weight=0.75, promote_above=0.58),
        ],
    )
    policy = resolve_scope_policy(int_cfg, scope)
    assert policy['name'] == 'scope_exact'
    assert policy['learned_weight'] == 0.75
    assert policy['promote_above'] == 0.58


def test_resolve_scope_policy_falls_back_to_defaults():
    scope = PortfolioScope(asset='EURUSD-OTC', interval_sec=300, timezone='UTC', scope_tag='EURUSD-OTC_300s')
    int_cfg = SimpleNamespace(
        learned_gating_weight=0.60,
        learned_promote_above=0.62,
        learned_suppress_below=0.42,
        learned_abstain_band=0.03,
        learned_min_reliability=0.50,
        learned_neutralize_low_reliability=True,
        stack_max_bonus=0.05,
        stack_max_penalty=0.05,
        learned_fail_closed=False,
        drift_fail_closed=False,
        scope_policies=[],
    )
    policy = resolve_scope_policy(int_cfg, scope)
    assert policy['name'] == 'default'
    assert policy['match'] is None
    assert policy['learned_weight'] == 0.60
