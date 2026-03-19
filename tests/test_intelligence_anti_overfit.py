from __future__ import annotations

from natbin.intelligence.anti_overfit import build_anti_overfit_report


def test_anti_overfit_penalizes_generalization_gap() -> None:
    payload = {
        'per_window': [
            {'train_hit': 0.85, 'valid_hit': 0.58, 'topk_taken': 20},
            {'train_hit': 0.83, 'valid_hit': 0.57, 'topk_taken': 18},
            {'train_hit': 0.84, 'valid_hit': 0.56, 'topk_taken': 22},
        ]
    }
    report = build_anti_overfit_report(
        payload,
        min_robustness=0.60,
        min_windows=3,
        gap_penalty_weight=0.20,
    )
    assert report['available'] is True
    assert report['generalization_gap'] > 0.20
    assert report['gap_penalty'] > 0.0
    assert report['accepted'] is False
