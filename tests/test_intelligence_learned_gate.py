
from __future__ import annotations

from natbin.intelligence.learned_gate import FEATURE_NAMES, fit_learned_gate, predict_probability


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
