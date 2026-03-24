from __future__ import annotations

from natbin.intelligence.ops_state import build_intelligence_ops_state


def test_build_intelligence_ops_state_marks_review_only_tuning_as_expected_after_rejected_restore() -> None:
    payload = build_intelligence_ops_state(
        scope_tag='EURUSD-OTC_300s',
        asset='EURUSD-OTC',
        interval_sec=300,
        eval_payload={'anti_overfit': {'available': True, 'accepted': False, 'robustness_score': 0.41, 'penalty': 0.12}},
        retrain_plan={'state': 'cooldown', 'priority': 'high', 'cooldown_active': True, 'cooldown_until_utc': '2099-03-24T00:00:00+00:00'},
        retrain_status={'state': 'rejected', 'priority': 'high', 'plan_state': 'cooldown', 'plan_priority': 'high'},
        retrain_review={'verdict': 'rejected', 'reason': 'hard_regression', 'executed': True, 'restored_previous_artifacts': True},
        anti_overfit_tuning_review={
            'kind': 'anti_overfit_tuning_review',
            'verdict': 'rejected',
            'tuning': {
                'selected_variant': 'recent_balanced_relief',
                'baseline_variant': 'baseline',
                'improved': False,
                'selection_reason': 'rollback_context',
            },
        },
    )

    assert payload['consistency']['ok'] is True
    assert payload['consistency']['expected_rejected_cooldown'] is True
    assert payload['consistency']['expected_review_only_tuning'] is True
    assert payload['anti_overfit']['tuning']['source'] == 'review'
    assert payload['anti_overfit']['tuning']['review_only'] is True
    assert payload['retrain']['state'] == 'rejected'
    assert payload['retrain']['plan_state'] == 'cooldown'
