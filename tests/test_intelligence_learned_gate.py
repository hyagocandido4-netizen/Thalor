from __future__ import annotations

from natbin.intelligence.learned_gate import FEATURE_NAMES, fit_learned_gate, predict_probability, stack_decision


def test_fit_and_predict_learned_gate():
    rows = []
    for i in range(80):
        rows.append(
            {
                'base_ev': 0.10,
                'base_score': 0.82,
                'base_conf': 0.80,
                'proba_side': 0.85,
                'payout': 0.80,
                'hour_sin': 0.0,
                'hour_cos': 1.0,
                'dow_sin': 0.0,
                'dow_cos': 1.0,
                'slot_multiplier': 1.08,
                'executed_today_norm': 0.2,
                'correct': 1,
            }
        )
    for i in range(80):
        rows.append(
            {
                'base_ev': -0.05,
                'base_score': 0.35,
                'base_conf': 0.52,
                'proba_side': 0.40,
                'payout': 0.80,
                'hour_sin': 0.0,
                'hour_cos': 1.0,
                'dow_sin': 0.0,
                'dow_cos': 1.0,
                'slot_multiplier': 0.92,
                'executed_today_norm': 0.8,
                'correct': 0,
            }
        )
    model = fit_learned_gate(rows, min_rows=50)
    assert model is not None
    assert model['schema_version'] == 'phase1-learned-gate-v3'
    assert model['probability_source'] in {'calibrated_isotonic', 'raw_logistic'}
    assert 0.0 <= float(model['reliability_score']) <= 1.0
    good = predict_probability(
        model,
        {
            'base_ev': 0.12,
            'base_score': 0.85,
            'base_conf': 0.82,
            'proba_side': 0.90,
            'payout': 0.80,
            'hour_sin': 0.0,
            'hour_cos': 1.0,
            'dow_sin': 0.0,
            'dow_cos': 1.0,
            'slot_multiplier': 1.10,
            'executed_today_norm': 0.1,
        },
    )
    bad = predict_probability(
        model,
        {
            'base_ev': -0.08,
            'base_score': 0.30,
            'base_conf': 0.51,
            'proba_side': 0.35,
            'payout': 0.80,
            'hour_sin': 0.0,
            'hour_cos': 1.0,
            'dow_sin': 0.0,
            'dow_cos': 1.0,
            'slot_multiplier': 0.90,
            'executed_today_norm': 0.9,
        },
    )
    assert good is not None and bad is not None
    assert good > bad


def test_stack_decision_promote_and_suppress():
    promote = stack_decision(
        base_quality=0.55,
        learned_prob=0.80,
        weight=0.60,
        promote_above=0.62,
        suppress_below=0.42,
        abstain_band=0.03,
        reliability_score=0.90,
        min_reliability=0.50,
        max_bonus=0.20,
        max_penalty=0.20,
    )
    suppress = stack_decision(
        base_quality=0.55,
        learned_prob=0.20,
        weight=0.60,
        promote_above=0.62,
        suppress_below=0.42,
        abstain_band=0.03,
        reliability_score=0.90,
        min_reliability=0.50,
        max_bonus=0.20,
        max_penalty=0.20,
    )
    assert promote['decision'] == 'promote'
    assert promote['blended_quality'] > 0.62
    assert suppress['decision'] == 'suppress'
    assert suppress['blended_quality'] < 0.42


def test_stack_decision_neutralizes_when_reliability_low():
    out = stack_decision(
        base_quality=0.55,
        learned_prob=0.85,
        weight=0.60,
        promote_above=0.62,
        suppress_below=0.42,
        abstain_band=0.03,
        reliability_score=0.20,
        min_reliability=0.50,
        neutralize_low_reliability=True,
    )
    assert out['decision'] == 'abstain'
    assert out['reason'] == 'learned_low_reliability'
    assert out['delta'] == 0.0
