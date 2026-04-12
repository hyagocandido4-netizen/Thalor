from __future__ import annotations

import warnings

from natbin.intelligence.learned_gate import fit_learned_gate


def test_fit_learned_gate_emits_no_scipy_lbfgs_deprecation_warning() -> None:
    rows = []
    for i in range(80):
        correct = 1 if i % 2 == 0 else 0
        rows.append(
            {
                "correct": correct,
                "base_conf": 0.75 if correct else 0.35,
                "base_score": 0.72 if correct else 0.31,
                "base_ev": 0.05 if correct else -0.01,
                "score": 0.7 if correct else 0.2,
                "cp_p": 0.85 if correct else 0.10,
                "meta_p": 0.78 if correct else 0.25,
                "iso_p": 0.74 if correct else 0.30,
                "hour": float(i % 24),
                "slot": float(i % 3),
                "vol": 0.002 + (0.0001 * (i % 5)),
                "bb_width": 0.01 + (0.001 * (i % 4)),
                "atr": 0.001 + (0.0001 * (i % 3)),
            }
        )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        payload = fit_learned_gate(rows, min_rows=50)

    assert payload is not None
    messages = [str(item.message).lower() for item in caught]
    assert not any("l-bfgs-b solver are deprecated" in msg for msg in messages)
