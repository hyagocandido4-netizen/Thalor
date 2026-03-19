
from __future__ import annotations

from natbin.intelligence.coverage import build_coverage_profile, coverage_bias
from natbin.intelligence.slot_profile import build_slot_profile, slot_stats_for_ts


def _summary(hour_good_wins: int, hour_bad_wins: int) -> dict:
    return {
        'by_hour': {
            '10': {'trades': 10, 'wins': hour_good_wins, 'losses': 10 - hour_good_wins, 'ev_mean': 0.08},
            '11': {'trades': 10, 'wins': hour_bad_wins, 'losses': 10 - hour_bad_wins, 'ev_mean': -0.04},
        },
        'trades_by_hour': {
            '10': {'total': 10, 'CALL': 5, 'PUT': 5},
            '11': {'total': 10, 'CALL': 5, 'PUT': 5},
        },
        'observations_by_hour': {'10': 50, '11': 50},
    }


def test_slot_profile_prefers_better_hour():
    profile = build_slot_profile(
        [_summary(8, 3), _summary(9, 2)],
        min_trades=4,
        prior_weight=2.0,
    )
    h10 = profile['hours']['10']
    h11 = profile['hours']['11']
    assert h10['multiplier'] > 1.0
    assert h11['multiplier'] < 1.0
    assert h10['multiplier'] > h11['multiplier']
    # 2026-03-10 10:00:00 UTC
    stats = slot_stats_for_ts(profile, 1773136800, timezone_name='UTC')
    assert stats['hour'] == '10'
    assert stats['multiplier'] == h10['multiplier']


def test_coverage_profile_cumulative_share_and_bias():
    profile = build_coverage_profile([_summary(8, 3), _summary(9, 2)], target_trades_per_day=4.0)
    cumulative = profile['cumulative_trade_share']
    assert cumulative['10'] > 0.0
    assert cumulative['11'] >= cumulative['10']
    bias = coverage_bias(
        profile,
        ts=1773140400,  # 2026-03-10 11:00:00 UTC
        timezone_name='UTC',
        executed_today=0,
        target_trades_per_day=4.0,
        tolerance=0.25,
        bias_weight=0.05,
    )
    assert bias['bias'] > 0.0



def test_slot_profile_recommendations_and_threshold_delta():
    profile = build_slot_profile(
        [_summary(8, 3), _summary(9, 2)],
        min_trades=4,
        prior_weight=2.0,
        score_delta_cap=0.05,
        threshold_delta_cap=0.03,
    )
    h10 = profile['hours']['10']
    h11 = profile['hours']['11']
    assert h10['recommendation']['state'] == 'promote'
    assert h10['recommendation']['score_delta'] > 0.0
    assert h10['recommendation']['threshold_delta'] < 0.0
    assert h11['recommendation']['state'] == 'suppress'
    assert h11['recommendation']['score_delta'] < 0.0
    assert h11['recommendation']['threshold_delta'] > 0.0
