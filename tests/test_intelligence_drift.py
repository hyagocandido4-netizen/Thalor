
from __future__ import annotations

import json
from pathlib import Path

from natbin.intelligence.drift import assess_drift, build_drift_baseline, update_drift_state


def test_drift_assessment_detects_shift():
    baseline = build_drift_baseline(
        [{'score': 0.85, 'conf': 0.90, 'ev': 0.20} for _ in range(80)]
        + [{'score': 0.75, 'conf': 0.80, 'ev': 0.12} for _ in range(40)]
    )
    recent = [{'score': 0.15, 'conf': 0.20, 'ev': -0.10} for _ in range(80)]
    report = assess_drift(baseline, recent, warn_psi=0.10, block_psi=0.20)
    assert report['level'] == 'block'
    assert report['penalty'] > 0.0
    assert report['fields']['score']['psi'] >= 0.20


def test_drift_state_recommends_retrain_on_warn_streak(tmp_path: Path):
    path = tmp_path / 'drift_state.json'
    s1 = update_drift_state(path, level='warn', warn_streak_threshold=2, block_streak_threshold=1)
    assert s1['retrain_recommended'] is False
    s2 = update_drift_state(path, level='warn', warn_streak_threshold=2, block_streak_threshold=1)
    assert s2['retrain_recommended'] is True
    assert s2['retrain_reason'] == 'drift_warn_streak'
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['warn_streak'] == 2
