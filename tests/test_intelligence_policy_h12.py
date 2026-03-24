from __future__ import annotations

from types import SimpleNamespace

from natbin.intelligence.policy import build_portfolio_feedback, resolve_scope_policy
from natbin.portfolio.models import PortfolioScope


class _Policy(SimpleNamespace):
    pass


def test_resolve_scope_policy_includes_portfolio_allocator_overrides():
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
        portfolio_weight=1.0,
        allocator_block_regime=True,
        allocator_warn_penalty=0.04,
        allocator_block_penalty=0.12,
        allocator_under_target_bonus=0.03,
        allocator_over_target_penalty=0.04,
        allocator_retrain_penalty=0.05,
        allocator_reliability_penalty=0.03,
        scope_policies=[
            _Policy(name='scope_exact', scope_tag='EURUSD-OTC_300s', portfolio_weight=1.25, allocator_warn_penalty=0.02)
        ],
    )
    policy = resolve_scope_policy(int_cfg, scope)
    assert policy['portfolio_weight'] == 1.25
    assert policy['allocator_warn_penalty'] == 0.02
    assert policy['allocator_block_regime'] is True


def test_build_portfolio_feedback_penalizes_regime_and_retrain():
    feedback = build_portfolio_feedback(
        intelligence_score=0.50,
        coverage={'pressure': 'over_target'},
        regime={'level': 'block'},
        learned_reliability=0.40,
        retrain_plan={'state': 'queued', 'priority': 'high'},
        policy={
            'portfolio_weight': 1.0,
            'allocator_block_regime': True,
            'allocator_warn_penalty': 0.04,
            'allocator_block_penalty': 0.12,
            'allocator_under_target_bonus': 0.03,
            'allocator_over_target_penalty': 0.04,
            'allocator_retrain_penalty': 0.05,
            'allocator_reliability_penalty': 0.03,
            'min_reliability': 0.50,
        },
    )
    assert feedback['allocator_blocked'] is True
    assert feedback['block_reason'] == 'regime_block'
    assert feedback['portfolio_score'] < 0.50
