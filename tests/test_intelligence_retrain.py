from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from natbin.intelligence.paths import retrain_plan_path, retrain_status_path
from natbin.intelligence.retrain import orchestrate_retrain


def _policy() -> dict:
    return {
        'portfolio_weight': 1.0,
        'allocator_block_regime': True,
        'allocator_retrain_penalty': 0.05,
    }


def test_orchestrate_retrain_queues_and_writes_artifacts(tmp_path: Path):
    payload = orchestrate_retrain(
        repo_root=tmp_path,
        scope_tag='EURUSD-OTC_300s',
        artifact_dir='runs/intelligence',
        trigger_payload={'priority': 'high', 'reason': 'drift_block_streak'},
        drift_state={'level': 'block', 'retrain_recommended': True},
        regime={'level': 'block'},
        coverage={'pressure': 'over_target'},
        learned_reliability=0.42,
        anti_overfit={'available': True, 'accepted': True},
        policy=_policy(),
        cooldown_hours=12,
        watch_reliability_below=0.55,
    )
    assert payload['state'] == 'queued'
    assert payload['priority'] == 'high'
    assert retrain_plan_path(repo_root=tmp_path, scope_tag='EURUSD-OTC_300s', artifact_dir='runs/intelligence').exists()
    assert retrain_status_path(repo_root=tmp_path, scope_tag='EURUSD-OTC_300s', artifact_dir='runs/intelligence').exists()


def test_orchestrate_retrain_respects_cooldown_on_repeat(tmp_path: Path):
    now = datetime.now(tz=UTC)
    first = orchestrate_retrain(
        repo_root=tmp_path,
        scope_tag='EURUSD-OTC_300s',
        artifact_dir='runs/intelligence',
        trigger_payload={'priority': 'medium', 'reason': 'drift_warn_streak'},
        drift_state={'level': 'warn', 'retrain_recommended': True},
        regime={'level': 'warn'},
        coverage={'pressure': 'balanced'},
        learned_reliability=0.60,
        anti_overfit={'available': True, 'accepted': True},
        policy=_policy(),
        cooldown_hours=24,
        watch_reliability_below=0.55,
        now_utc=now,
    )
    second = orchestrate_retrain(
        repo_root=tmp_path,
        scope_tag='EURUSD-OTC_300s',
        artifact_dir='runs/intelligence',
        trigger_payload={'priority': 'medium', 'reason': 'drift_warn_streak'},
        drift_state={'level': 'warn', 'retrain_recommended': True},
        regime={'level': 'warn'},
        coverage={'pressure': 'balanced'},
        learned_reliability=0.60,
        anti_overfit={'available': True, 'accepted': True},
        policy=_policy(),
        cooldown_hours=24,
        watch_reliability_below=0.55,
        now_utc=now + timedelta(hours=1),
    )
    assert first['state'] == 'queued'
    assert second['state'] == 'cooldown'
    status = json.loads(retrain_status_path(repo_root=tmp_path, scope_tag='EURUSD-OTC_300s', artifact_dir='runs/intelligence').read_text(encoding='utf-8'))
    assert status['state'] == 'cooldown'
